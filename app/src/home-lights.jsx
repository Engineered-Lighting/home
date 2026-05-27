// home-lights.jsx — /lights drawer
//
// One unified surface for tuning the Living Lights system without a long
// LLM conversation. Three vertical sections:
//   1. "Right now" status card for the active zone (read-only)
//   2. Quick adjustments — house-wide warmth + brightness bias sliders
//      with per-zone scoping toggle
//   3. Full cascade — grouped sliders for the 15+ promoted knobs,
//      organized in the same priority order as the brightness cascade
//      (asleep > gaming > working hours > movie > ToD defaults).
//
// Reads HA state via `client.call({ type: "get_states" })` (cached) +
// `client.subscribeEvents("state_changed", ...)` for the entities we
// own. Writes via `client.callService(domain, service, data)` — exact
// pattern documented in home-app.jsx where input_number / input_boolean /
// input_text services are invoked.
//
// Frozen nodes (anticipator constants, AL min/max, intelligence
// thresholds) appear as 🔒 cards with an "Ask Claude" button that
// pre-fills a structured chat prompt — bridging the seam between
// "live-tunable today" and "code change required" without forcing the
// user to remember which file holds what.

const { useState, useEffect, useRef, useCallback, useMemo } = React;

// ── Zone list (mirrors ZONES dict in build-living-lights-yaml.py) ─────
const LIGHTS_ZONES = [
  { slug: "office",            camera: "living_room", friendly: "Office" },
  { slug: "sofa",              camera: "living_room", friendly: "Sofa" },
  { slug: "front_left",        camera: "living_room", friendly: "Front Left" },
  { slug: "weights",           camera: "living_room", friendly: "Weights" },
  { slug: "front_door",        camera: "living_room", friendly: "Front Door" },
  { slug: "whole_living_room", camera: "living_room", friendly: "Living Room (whole)" },
  { slug: "sink",              camera: "kitchen",     friendly: "Kitchen Sink" },
  { slug: "island_left",       camera: "kitchen",     friendly: "Island Left" },
  { slug: "island_right",      camera: "kitchen",     friendly: "Island Right" },
  { slug: "whole_kitchen",     camera: "kitchen",     friendly: "Kitchen (whole)" },
  { slug: "dining_left",       camera: "dining_room", friendly: "Dining Left" },
  { slug: "dining_right",      camera: "dining_room", friendly: "Dining Right" },
  { slug: "whole_dining_room", camera: "dining_room", friendly: "Dining Room (whole)" },
  { slug: "workshop_zone",     camera: "workshop",    friendly: "Workshop" },
  { slug: "e28",               camera: "driveway",    friendly: "Driveway" },
];

// ── Helpers ────────────────────────────────────────────────────────────
const FONT_MONO = '"Geist Mono", "JetBrains Mono", "SF Mono", monospace';
const FONT_SANS = 'Inter, "SF Pro", -apple-system, sans-serif';

const HA_MIN_CT = 2000;   // Adaptive Lighting min_color_temp (matches AL config)
const HA_MAX_CT = 4000;   // Adaptive Lighting max_color_temp

// Read a single state from a map of states (built from get_states call).
function getState(statesByEntity, entity_id, fallback = null) {
  const s = statesByEntity?.[entity_id];
  if (!s) return fallback;
  return s.state;
}
function getAttr(statesByEntity, entity_id, attr, fallback = null) {
  const s = statesByEntity?.[entity_id];
  if (!s?.attributes) return fallback;
  const v = s.attributes[attr];
  return v == null ? fallback : v;
}
function toNum(v, fallback = 0) {
  if (v == null) return fallback;
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

// Compose the friendly description of the "winning modifier" for the
// status card. The cascade is gaming > working_hours > tv > activity >
// default; gaming/working_hours/tv each contribute a known floor or
// present override. Returns a string like "working-hours active (+30%)".
function describeWinningModifier(states, slug) {
  const sensorId = stateSensorFor(slug);
  const profile = getState(states, "sensor.living_lights_profile", "—");
  const predBri = toNum(getAttr(states, sensorId, "predicted_brightness_pct"), null);
  const asleep = getState(states, "input_boolean.living_lights_asleep") === "on";
  const wh = getState(states, "binary_sensor.living_lights_working_hours_active") === "on";
  const tv = ["on", "playing", "paused", "buffering"].includes(getState(states, "media_player.lg_tv"));
  const gameEnabled = getState(states, "input_boolean.living_lights_gaming_enabled") === "on";
  const game = getAttr(states, "sensor.steam_steam_76561198136331341", "game");
  const gaming = gameEnabled && game && !["unknown", "unavailable", ""].includes(String(game));
  if (asleep)  return { label: "asleep", detail: `ceiling ${toNum(getState(states, "input_number.living_lights_asleep_cap_pct"), 30)}%`, severity: "info" };
  if (gaming)  return { label: "gaming", detail: `Steam: ${game}`, severity: "warn" };
  if (wh)      return { label: "working-hours active", detail: `${profile} · floor ${toNum(getState(states, "input_number.living_lights_working_hours_floor_pct"), 80)}% / present ${toNum(getState(states, "input_number.living_lights_working_hours_present_pct"), 95)}%`, severity: "ok" };
  if (tv)      return { label: "TV playing (movie mode)", detail: `dim target ${toNum(getState(states, "input_number.living_lights_movie_dim_pct"), 8)}%`, severity: "info" };
  return { label: profile, detail: `default cascade · predicted ${predBri ?? "—"}%`, severity: "muted" };
}

function stateSensorFor(slug) {
  const z = LIGHTS_ZONES.find(z => z.slug === slug);
  return z ? `sensor.${z.camera}_${slug}_lighting_state` : null;
}

// Estimate the "effective" CT or brightness after applying bias. Used for
// the "effective: 2400 K" labels next to bias sliders so the user sees
// what the bulb will actually receive (post-clamp).
function effectiveBiasValue(kind, bias, classifier, states) {
  if (kind === "warmth") {
    const baseCT = toNum(getAttr(states, classifier, "predicted_color_temp_kelvin"),
                         toNum(getAttr(states, "sensor.living_lights_profile", "tod_color_warm"), 2700));
    // The classifier output ALREADY has bias applied; show what the
    // BASE (pre-bias) would be vs. what the effective value is now.
    return Math.max(HA_MIN_CT, Math.min(HA_MAX_CT, baseCT));
  }
  if (kind === "brightness") {
    const basePct = toNum(getAttr(states, classifier, "predicted_brightness_pct"), 0);
    return Math.max(0, Math.min(100, basePct));
  }
  return null;
}

// ── Primitive components ──────────────────────────────────────────────

function Slider({ label, value, min, max, step, unit, defaultValue, onChange, disabled, hint, fmt }) {
  const debRef = useRef(null);
  const [draft, setDraft] = useState(value);
  useEffect(() => { setDraft(value); }, [value]);
  const handle = (e) => {
    const v = Number(e.target.value);
    setDraft(v);
    if (debRef.current) clearTimeout(debRef.current);
    debRef.current = setTimeout(() => { onChange(v); }, 120);
  };
  const flush = () => {
    if (debRef.current) { clearTimeout(debRef.current); debRef.current = null; }
    if (draft !== value) onChange(draft);
  };
  const displayValue = fmt ? fmt(draft) : `${draft}${unit || ""}`;
  const isDefault = defaultValue != null && Number(draft) === Number(defaultValue);
  return (
    <div style={{ padding: "10px 16px", display: "flex", flexDirection: "column", gap: 6, opacity: disabled ? 0.45 : 1 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontFamily: FONT_SANS, fontSize: 13, color: "var(--hg-fg-1)" }}>{label}</span>
        <span style={{ fontFamily: FONT_MONO, fontSize: 13, color: isDefault ? "var(--hg-fg-2)" : "var(--hg-fg-0)" }}>
          {displayValue}
          {!isDefault && defaultValue != null && (
            <button
              type="button"
              onClick={(e) => { e.preventDefault(); setDraft(defaultValue); onChange(defaultValue); }}
              title={`Reset to default (${defaultValue}${unit || ""})`}
              style={{ background: "transparent", border: "none", color: "var(--hg-fg-2)", cursor: "pointer", fontSize: 12, marginLeft: 6 }}>
              ↺
            </button>
          )}
        </span>
      </div>
      <input
        type="range" min={min} max={max} step={step}
        value={draft == null ? min : draft}
        onChange={handle}
        onPointerUp={flush}
        onBlur={flush}
        disabled={disabled}
        style={{ width: "100%", accentColor: "var(--hg-accent, #3aa6ff)" }}
      />
      {hint && (
        <span style={{ fontFamily: FONT_SANS, fontSize: 11, color: "var(--hg-fg-2)" }}>{hint}</span>
      )}
    </div>
  );
}

function ToggleRow({ label, state, onToggle, hint, disabled }) {
  const isOn = state === "on";
  return (
    <div style={{ padding: "8px 16px", display: "flex", flexDirection: "column", gap: 4, opacity: disabled ? 0.45 : 1 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <span style={{ fontFamily: FONT_SANS, fontSize: 13, color: "var(--hg-fg-1)" }}>{label}</span>
        <button
          type="button"
          onClick={onToggle}
          disabled={disabled}
          style={{
            background: isOn ? "var(--hg-accent, #3aa6ff)" : "var(--hg-bg-2)",
            color: isOn ? "white" : "var(--hg-fg-1)",
            border: "1px solid " + (isOn ? "transparent" : "var(--hg-border)"),
            borderRadius: 12, padding: "3px 10px",
            fontFamily: FONT_MONO, fontSize: 11, cursor: disabled ? "not-allowed" : "pointer",
            minWidth: 40, textAlign: "center",
          }}>
          {state || "—"}
        </button>
      </div>
      {hint && <span style={{ fontFamily: FONT_SANS, fontSize: 11, color: "var(--hg-fg-2)" }}>{hint}</span>}
    </div>
  );
}

function CascadeCard({ title, subtitle, winning, locked, askClaude, children }) {
  return (
    <div style={{
      margin: "8px 16px",
      border: "1px solid " + (winning ? "var(--hg-accent, #3aa6ff)" : "var(--hg-border)"),
      borderLeft: "3px solid " + (locked ? "var(--hg-fg-2)" : winning ? "var(--hg-accent, #3aa6ff)" : "var(--hg-border)"),
      borderRadius: 6,
      background: "var(--hg-bg-1)",
      overflow: "hidden",
    }}>
      <div style={{ padding: "8px 14px", borderBottom: "1px solid var(--hg-border-soft)", display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, minWidth: 0 }}>
          <span style={{ fontFamily: FONT_MONO, fontSize: 11, color: "var(--hg-fg-2)", textTransform: "uppercase", letterSpacing: 0.5 }}>
            {locked ? "🔒 " : ""}{title}
          </span>
          {subtitle && <span style={{ fontFamily: FONT_SANS, fontSize: 12, color: "var(--hg-fg-1)" }}>{subtitle}</span>}
        </div>
        {winning && (
          <span style={{ fontFamily: FONT_MONO, fontSize: 10, color: "var(--hg-accent, #3aa6ff)", whiteSpace: "nowrap" }}>
            ◀ winning
          </span>
        )}
        {askClaude && (
          <button
            type="button" onClick={askClaude}
            style={{ background: "transparent", border: "1px solid var(--hg-border)", borderRadius: 4, padding: "2px 8px",
                     fontFamily: FONT_MONO, fontSize: 10, color: "var(--hg-fg-1)", cursor: "pointer", whiteSpace: "nowrap" }}>
            Ask Claude
          </button>
        )}
      </div>
      <div>{children}</div>
    </div>
  );
}

// ── Main drawer component ──────────────────────────────────────────────

function HomeLightsDrawer({ open, onClose, client, sim, askClaude }) {
  const [zone, setZone] = useState("office");
  const [states, setStates] = useState({});  // { entity_id: { state, attributes } }
  const [error, setError] = useState(null);
  const subRef = useRef(null);

  // Fetch + subscribe on open
  useEffect(() => {
    if (!open || !client) return undefined;
    let active = true;
    const fetch = async () => {
      try {
        const r = await client.call({ type: "get_states" });
        if (!active) return;
        const arr = Array.isArray(r) ? r : [];
        const map = {};
        for (const s of arr) {
          if (s && s.entity_id) map[s.entity_id] = s;
        }
        setStates(map);
        setError(null);
      } catch (e) {
        if (!active) return;
        setError(e?.message || String(e));
      }
    };
    fetch();
    // Subscribe to state changes for all the entities we own; merge into state.
    const ownedPrefixes = [
      "input_number.living_lights_",
      "input_boolean.living_lights_",
      "input_text.living_lights_",
      "binary_sensor.living_lights_",
      "sensor.living_lights_profile",
    ];
    try {
      const unsub = client.subscribeEvents((msg) => {
        const ev = msg?.event;
        if (!ev || ev.event_type !== "state_changed") return;
        const eid = ev.data?.entity_id;
        if (!eid) return;
        const owned = ownedPrefixes.some(p => eid.startsWith(p));
        // Always update classifier sensors too (for the active zone's status card).
        const isClassifier = eid.startsWith("sensor.") && eid.endsWith("_lighting_state");
        if (!owned && !isClassifier) return;
        const ns = ev.data?.new_state;
        if (!ns) return;
        setStates(prev => ({ ...prev, [eid]: ns }));
      }, "state_changed");
      subRef.current = unsub;
    } catch (e) {
      // subscribeEvents may not exist in all client variants; polling fallback.
    }
    // Also poll every 3s for the classifier (cheap REST safety net if subscribe misses).
    const poll = setInterval(fetch, 3000);
    return () => {
      active = false;
      clearInterval(poll);
      if (subRef.current) { try { subRef.current(); } catch (_) {} subRef.current = null; }
    };
  }, [open, client]);

  // Esc to close
  useEffect(() => {
    if (!open) return undefined;
    const h = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);

  if (!open) return null;

  const classifier = stateSensorFor(zone);
  const winning = useMemo(() => describeWinningModifier(states, zone), [states, zone]);

  // Helper writers
  const setNum = (entity_id, value) => {
    if (!client) return;
    client.callService("input_number", "set_value", { entity_id, value })
      .catch((e) => setError(`set ${entity_id}=${value}: ${e?.message || e}`));
  };
  const toggleBool = (entity_id) => {
    if (!client) return;
    const cur = getState(states, entity_id) === "on";
    client.callService("input_boolean", cur ? "turn_off" : "turn_on", { entity_id })
      .catch((e) => setError(`toggle ${entity_id}: ${e?.message || e}`));
  };
  const setText = (entity_id, value) => {
    if (!client) return;
    client.callService("input_text", "set_value", { entity_id, value })
      .catch((e) => setError(`set ${entity_id}=${value}: ${e?.message || e}`));
  };

  // Read entity helpers
  const n = (eid, fallback = 0) => toNum(getState(states, eid), fallback);
  const b = (eid) => getState(states, eid);
  const cls = stateSensorFor(zone);
  const predBri = toNum(getAttr(states, cls, "predicted_brightness_pct"), null);
  const predCT  = toNum(getAttr(states, cls, "predicted_color_temp_kelvin"), null);
  const zState  = getState(states, cls, "—");
  const rampInit = toNum(getAttr(states, cls, "ramp_initial_pct"), null);

  // Bias scope: 'all' or zone slug
  const scope = getState(states, "input_text.living_lights_bias_zone_scope", "all");
  const scopeIsAll = scope === "all";
  const scopeIsThisZone = scope === zone;

  // Winning modifier styling
  const winningColor = {
    ok: "var(--hg-accent, #3aa6ff)",
    warn: "#e88c30",
    info: "var(--hg-fg-1)",
    muted: "var(--hg-fg-2)",
  }[winning.severity] || "var(--hg-fg-1)";

  // Ask-Claude pre-populator (for frozen-knob handoff)
  const ask = (topic) => {
    if (askClaude) askClaude(topic);
    onClose();
  };

  // ── Render ──────────────────────────────────────────────────────────
  return (
    <div role="dialog" aria-modal="true" aria-label="Living Lights tuning"
      style={{
        position: "fixed", top: 0, right: 0, bottom: 0,
        width: "min(520px, 100vw)",
        background: "var(--hg-bg-0)",
        borderLeft: "1px solid var(--hg-border)",
        zIndex: 1100,
        display: "flex", flexDirection: "column",
        boxShadow: "-2px 0 16px rgba(0,0,0,0.18)",
      }}>
      {/* Header */}
      <div style={{ padding: "12px 18px", borderBottom: "1px solid var(--hg-border-soft)",
                    display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontFamily: FONT_MONO, fontSize: 12, color: "var(--hg-fg-1)", letterSpacing: 1, textTransform: "uppercase" }}>lights</span>
        <button onClick={onClose}
          style={{ background: "transparent", border: "none", color: "var(--hg-fg-2)",
                   fontFamily: FONT_MONO, fontSize: 11, cursor: "pointer" }}>
          close · esc
        </button>
      </div>

      {error && (
        <div style={{ padding: "8px 18px", borderBottom: "1px solid var(--hg-border-soft)", background: "var(--hg-bg-1)",
                      fontFamily: FONT_MONO, fontSize: 11, color: "#e88c30" }}>
          {error} <button onClick={() => setError(null)} style={{ background: "transparent", border: "none", color: "var(--hg-fg-2)", cursor: "pointer", fontFamily: FONT_MONO, fontSize: 11 }}>dismiss</button>
        </div>
      )}

      {/* Scrollable body */}
      <div style={{ flex: 1, overflowY: "auto", overflowX: "hidden" }}>

        {/* ── Right Now ─────────────────────────────────────────── */}
        <div style={{ margin: "12px 16px", padding: "12px 16px",
                      border: "1px solid var(--hg-border)", borderRadius: 8,
                      background: "var(--hg-bg-1)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
            <span style={{ fontFamily: FONT_MONO, fontSize: 10, color: "var(--hg-fg-2)", textTransform: "uppercase", letterSpacing: 0.8 }}>right now</span>
            <select value={zone} onChange={(e) => setZone(e.target.value)}
              style={{ background: "var(--hg-bg-0)", color: "var(--hg-fg-0)", border: "1px solid var(--hg-border)", borderRadius: 4,
                       padding: "2px 6px", fontFamily: FONT_MONO, fontSize: 11 }}>
              {LIGHTS_ZONES.map(z => <option key={z.slug} value={z.slug}>{z.friendly}</option>)}
            </select>
          </div>
          <div style={{ fontFamily: FONT_SANS, fontSize: 18, color: "var(--hg-fg-0)", marginBottom: 4 }}>
            {LIGHTS_ZONES.find(z => z.slug === zone)?.friendly}
          </div>
          <div style={{ fontFamily: FONT_MONO, fontSize: 12, color: "var(--hg-fg-1)" }}>
            {zState} · <strong>{predBri ?? "—"}%</strong> · <strong>{predCT ?? "—"} K</strong>
            {rampInit != null && <span style={{ color: "var(--hg-fg-2)" }}> · ramp_initial {rampInit}%</span>}
          </div>
          <div style={{ marginTop: 6, fontFamily: FONT_SANS, fontSize: 12, color: winningColor }}>
            ◆ {winning.label}
            {winning.detail && <span style={{ color: "var(--hg-fg-2)" }}> · {winning.detail}</span>}
          </div>
        </div>

        {/* ── Quick adjustments (bias knobs) ────────────────────── */}
        <div style={{ margin: "8px 16px", padding: "12px 0",
                      border: "1px solid var(--hg-border)", borderRadius: 8,
                      background: "var(--hg-bg-1)" }}>
          <div style={{ padding: "0 16px 8px 16px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontFamily: FONT_MONO, fontSize: 10, color: "var(--hg-fg-2)", textTransform: "uppercase", letterSpacing: 0.8 }}>
              quick adjustments
            </span>
            <div style={{ display: "flex", gap: 4 }}>
              <button onClick={() => setText("input_text.living_lights_bias_zone_scope", "all")}
                style={{ background: scopeIsAll ? "var(--hg-accent, #3aa6ff)" : "var(--hg-bg-2)",
                         color: scopeIsAll ? "white" : "var(--hg-fg-1)",
                         border: "1px solid " + (scopeIsAll ? "transparent" : "var(--hg-border)"),
                         borderRadius: 4, padding: "2px 8px",
                         fontFamily: FONT_MONO, fontSize: 10, cursor: "pointer" }}>
                all zones
              </button>
              <button onClick={() => setText("input_text.living_lights_bias_zone_scope", zone)}
                style={{ background: scopeIsThisZone ? "var(--hg-accent, #3aa6ff)" : "var(--hg-bg-2)",
                         color: scopeIsThisZone ? "white" : "var(--hg-fg-1)",
                         border: "1px solid " + (scopeIsThisZone ? "transparent" : "var(--hg-border)"),
                         borderRadius: 4, padding: "2px 8px",
                         fontFamily: FONT_MONO, fontSize: 10, cursor: "pointer" }}>
                {zone} only
              </button>
            </div>
          </div>
          <Slider label="Warmth bias" unit=" K" min={-500} max={500} step={50} defaultValue={0}
                  value={n("input_number.living_lights_warmth_bias_k", 0)}
                  onChange={(v) => setNum("input_number.living_lights_warmth_bias_k", v)}
                  hint={(scopeIsAll || scopeIsThisZone) ?
                        `effective ${predCT ?? "—"} K (clamped at ${HA_MIN_CT}–${HA_MAX_CT})` :
                        `not applied — scope is '${scope}'`}
                  fmt={(v) => (v >= 0 ? `+${v} K` : `${v} K`)} />
          <Slider label="Brightness bias" unit=" pp" min={-30} max={30} step={5} defaultValue={0}
                  value={n("input_number.living_lights_brightness_bias_pp", 0)}
                  onChange={(v) => setNum("input_number.living_lights_brightness_bias_pp", v)}
                  hint={(scopeIsAll || scopeIsThisZone) ?
                        `effective ${predBri ?? "—"}% (clamped at 0–cap)` :
                        `not applied — scope is '${scope}'`}
                  fmt={(v) => (v >= 0 ? `+${v} pp` : `${v} pp`)} />
        </div>

        {/* ── Cascade ──────────────────────────────────────────── */}
        <div style={{ padding: "8px 16px 4px 16px", fontFamily: FONT_MONO, fontSize: 10, color: "var(--hg-fg-2)", textTransform: "uppercase", letterSpacing: 0.8 }}>
          cascade · priority high → low
        </div>

        {/* Asleep */}
        <CascadeCard title="1. Asleep / night"
          subtitle='Wins over everything when the house is quiet overnight'
          winning={b("input_boolean.living_lights_asleep") === "on"}>
          <ToggleRow label="Auto-asleep latch" state={b("input_boolean.living_lights_asleep")}
                     onToggle={() => toggleBool("input_boolean.living_lights_asleep")}
                     hint="Set by 15-min quiet overnight; cleared by sustained presence." />
          <ToggleRow label="Night-safe mode (homeai_sleep)" state={b("input_boolean.homeai_sleep")}
                     onToggle={() => toggleBool("input_boolean.homeai_sleep")}
                     hint="Caps brightness at 8% house-wide." />
          <Slider label="Occupied ceiling while asleep" unit="%" min={0} max={100} step={5} defaultValue={30}
                  value={n("input_number.living_lights_asleep_cap_pct", 30)}
                  onChange={(v) => setNum("input_number.living_lights_asleep_cap_pct", v)}
                  hint="Maximum brightness in occupied zones while asleep." />
        </CascadeCard>

        {/* Gaming */}
        <CascadeCard title="2. Gaming (Steam)"
          subtitle="Office goes off, LR dims to 3% when an actual game is running"
          winning={(b("input_boolean.living_lights_gaming_enabled") === "on") &&
                   getAttr(states, "sensor.steam_steam_76561198136331341", "game")}>
          <ToggleRow label="Master toggle" state={b("input_boolean.living_lights_gaming_enabled")}
                     onToggle={() => toggleBool("input_boolean.living_lights_gaming_enabled")}
                     hint="Off = gaming detection ignored (suppresses dim during work)." />
          <Slider label="LR dim target (gaming)" unit="%" min={0} max={100} step={1} defaultValue={3}
                  value={n("input_number.living_lights_gaming_dim_pct", 3)}
                  onChange={(v) => setNum("input_number.living_lights_gaming_dim_pct", v)}
                  hint="Sofa, front_left, weights, front_door dim to this when gaming." />
        </CascadeCard>

        {/* Working hours */}
        <CascadeCard title="3. Working hours"
          subtitle="Weekday 08-18 + woke_up latched — boosts the workday surface"
          winning={b("binary_sensor.living_lights_working_hours_active") === "on"}>
          <ToggleRow label="Master toggle" state={b("input_boolean.living_lights_working_hours_enabled")}
                     onToggle={() => toggleBool("input_boolean.living_lights_working_hours_enabled")}
                     hint="Off for vacation days. Default on." />
          <ToggleRow label="Woke up today (latch)" state={b("input_boolean.living_lights_woke_up_today")}
                     onToggle={() => toggleBool("input_boolean.living_lights_woke_up_today")}
                     hint="Auto-set by sustained presence 05-12; reset 04:00 + on sustained asleep." />
          <Slider label="Vacant floor" unit="%" min={0} max={100} step={5} defaultValue={80}
                  value={n("input_number.living_lights_working_hours_floor_pct", 80)}
                  onChange={(v) => setNum("input_number.living_lights_working_hours_floor_pct", v)}
                  hint="When Frigate misses you at the desk, this is the floor everywhere." />
          <Slider label="Present target" unit="%" min={0} max={100} step={5} defaultValue={95}
                  value={n("input_number.living_lights_working_hours_present_pct", 95)}
                  onChange={(v) => setNum("input_number.living_lights_working_hours_present_pct", v)}
                  hint="When Frigate sees you, this is the ramp destination (cap-clamped)." />
        </CascadeCard>

        {/* Movie */}
        <CascadeCard title="4. Movie mode (TV playing)"
          subtitle="LG TV on → living-room watch zones dim to floor"
          winning={["on", "playing", "paused", "buffering"].includes(b("media_player.lg_tv") || "")}>
          <Slider label="TV-on dim target" unit="%" min={0} max={100} step={1} defaultValue={8}
                  value={n("input_number.living_lights_movie_dim_pct", 8)}
                  onChange={(v) => setNum("input_number.living_lights_movie_dim_pct", v)}
                  hint="Sofa + LR zones dim to this while the LG TV is on (working_hours wins)." />
        </CascadeCard>

        {/* Time of day */}
        <CascadeCard title="5. Time of day"
          subtitle={`Current bucket: ${b("sensor.living_lights_profile") || "—"} · tod_factor ${toNum(getAttr(states, "sensor.living_lights_profile", "tod_factor"), 0).toFixed(2)} · tod_color ${toNum(getAttr(states, "sensor.living_lights_profile", "tod_color_warm"), 0)} K`}>
          <div style={{ padding: "4px 16px 6px", fontFamily: FONT_MONO, fontSize: 10, color: "var(--hg-fg-2)", textTransform: "uppercase", letterSpacing: 0.5 }}>color temperature by bucket</div>
          <Slider label="Overnight (22:30–06:00)" unit=" K" min={1800} max={4500} step={50} defaultValue={2000}
                  value={n("input_number.living_lights_ct_overnight_k", 2000)}
                  onChange={(v) => setNum("input_number.living_lights_ct_overnight_k", v)} />
          <Slider label="Morning (06:00–09:00)" unit=" K" min={1800} max={4500} step={50} defaultValue={2200}
                  value={n("input_number.living_lights_ct_morning_k", 2200)}
                  onChange={(v) => setNum("input_number.living_lights_ct_morning_k", v)} />
          <Slider label="Midday (09:00–13:00)" unit=" K" min={1800} max={4500} step={50} defaultValue={2700}
                  value={n("input_number.living_lights_ct_midday_k", 2700)}
                  onChange={(v) => setNum("input_number.living_lights_ct_midday_k", v)} />
          <Slider label="Afternoon (13:00–17:00)" unit=" K" min={1800} max={4500} step={50} defaultValue={3000}
                  value={n("input_number.living_lights_ct_afternoon_k", 3000)}
                  onChange={(v) => setNum("input_number.living_lights_ct_afternoon_k", v)} />
          <Slider label="Evening (17:00–20:00)" unit=" K" min={1800} max={4500} step={50} defaultValue={2500}
                  value={n("input_number.living_lights_ct_evening_k", 2500)}
                  onChange={(v) => setNum("input_number.living_lights_ct_evening_k", v)} />
          <Slider label="Late evening (20:00–22:30)" unit=" K" min={1800} max={4500} step={50} defaultValue={2200}
                  value={n("input_number.living_lights_ct_late_evening_k", 2200)}
                  onChange={(v) => setNum("input_number.living_lights_ct_late_evening_k", v)} />
          <div style={{ padding: "4px 16px 6px", fontFamily: FONT_MONO, fontSize: 10, color: "var(--hg-fg-2)", textTransform: "uppercase", letterSpacing: 0.5 }}>brightness factor multipliers</div>
          <Slider label="Morning multiplier" min={0.5} max={1.5} step={0.05} defaultValue={1.0}
                  value={n("input_number.living_lights_tod_factor_morning_mult", 1.0)}
                  onChange={(v) => setNum("input_number.living_lights_tod_factor_morning_mult", v)}
                  fmt={(v) => `×${Number(v).toFixed(2)}`}
                  hint=">1.0 = brighter morning, <1.0 = dimmer." />
          <Slider label="Midday multiplier" min={0.5} max={1.5} step={0.05} defaultValue={1.0}
                  value={n("input_number.living_lights_tod_factor_midday_mult", 1.0)}
                  onChange={(v) => setNum("input_number.living_lights_tod_factor_midday_mult", v)}
                  fmt={(v) => `×${Number(v).toFixed(2)}`} />
        </CascadeCard>

        {/* Defaults */}
        <CascadeCard title="6. Defaults (vacant / present)"
          subtitle="Fallbacks when no modifier wins">
          <Slider label="Vacant baseline (day)" unit="%" min={0} max={100} step={5} defaultValue={50}
                  value={n("input_number.living_lights_vacant_day_pct", 50)}
                  onChange={(v) => setNum("input_number.living_lights_vacant_day_pct", v)}
                  hint="Brightness in empty zones during morning–evening." />
          <Slider label="Vacant baseline (night)" unit="%" min={0} max={100} step={5} defaultValue={20}
                  value={n("input_number.living_lights_vacant_night_pct", 20)}
                  onChange={(v) => setNum("input_number.living_lights_vacant_night_pct", v)}
                  hint="Brightness in empty zones late evening + overnight." />
          <Slider label="Present ramp target" unit="%" min={0} max={100} step={5} defaultValue={80}
                  value={n("input_number.living_lights_ramp_target_pct", 80)}
                  onChange={(v) => setNum("input_number.living_lights_ramp_target_pct", v)}
                  hint="Default destination when no modifier overrides." />
          <Slider label="Present ramp initial" unit="%" min={0} max={100} step={5} defaultValue={50}
                  value={n("input_number.living_lights_ramp_initial_pct", 50)}
                  onChange={(v) => setNum("input_number.living_lights_ramp_initial_pct", v)}
                  hint="Fast-reaction step before the slow ramp." />
        </CascadeCard>

        {/* Frozen layers */}
        <CascadeCard title="7. Anticipator" subtitle="Kinematic chain-of-zones predictor" locked
          askClaude={() => ask("anticipator")}>
          <div style={{ padding: "10px 16px", fontFamily: FONT_SANS, fontSize: 12, color: "var(--hg-fg-2)" }}>
            12 kinematic constants in <code>addons/predictive-lighting/anticipate.py</code> (Python container).
            Tune via "Ask Claude" to walk through the constants and propose a change.
          </div>
        </CascadeCard>

        <CascadeCard title="8. Pilot transitions" subtitle="Per-light ramp timings" locked
          askClaude={() => ask("pilot_transitions")}>
          <div style={{ padding: "10px 16px", fontFamily: FONT_SANS, fontSize: 12, color: "var(--hg-fg-2)" }}>
            RAMP_FAST_S (2s), RAMP_SLOW_S (10s), VACANT_TRANSITION_S (6s), etc. Baked in actuator generator.
          </div>
        </CascadeCard>

        <CascadeCard title="9. Adaptive Lighting" subtitle="House-wide CT curve" locked
          askClaude={() => ask("adaptive_lighting")}>
          <div style={{ padding: "10px 16px", fontFamily: FONT_SANS, fontSize: 12, color: "var(--hg-fg-2)" }}>
            <code>min_color_temp: {HA_MIN_CT}</code>, <code>max_color_temp: {HA_MAX_CT}</code>,
            interval 90s. HA restart required to change. AL currently targets <strong>{toNum(getAttr(states, "switch.home_adaptive_lighting_home", "color_temp_kelvin"), null) ?? "—"} K</strong>.
          </div>
        </CascadeCard>

        <div style={{ height: 24 }} />
      </div>

      {/* Footer */}
      <div style={{ padding: "10px 18px", borderTop: "1px solid var(--hg-border-soft)",
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                    background: "var(--hg-bg-0)" }}>
        <button onClick={() => {
          // Reset all promoted constants + bias knobs to defaults
          const resets = [
            ["input_number.living_lights_working_hours_floor_pct", 80],
            ["input_number.living_lights_working_hours_present_pct", 95],
            ["input_number.living_lights_warmth_bias_k", 0],
            ["input_number.living_lights_brightness_bias_pp", 0],
            ["input_number.living_lights_vacant_day_pct", 50],
            ["input_number.living_lights_vacant_night_pct", 20],
            ["input_number.living_lights_ramp_target_pct", 80],
            ["input_number.living_lights_ramp_initial_pct", 50],
            ["input_number.living_lights_asleep_cap_pct", 30],
            ["input_number.living_lights_movie_dim_pct", 8],
            ["input_number.living_lights_gaming_dim_pct", 3],
            ["input_number.living_lights_ct_overnight_k", 2000],
            ["input_number.living_lights_ct_morning_k", 2200],
            ["input_number.living_lights_ct_midday_k", 2700],
            ["input_number.living_lights_ct_afternoon_k", 3000],
            ["input_number.living_lights_ct_evening_k", 2500],
            ["input_number.living_lights_ct_late_evening_k", 2200],
            ["input_number.living_lights_tod_factor_morning_mult", 1.0],
            ["input_number.living_lights_tod_factor_midday_mult", 1.0],
          ];
          for (const [eid, v] of resets) setNum(eid, v);
          setText("input_text.living_lights_bias_zone_scope", "all");
        }}
          style={{ background: "transparent", border: "1px solid var(--hg-border)", borderRadius: 4,
                   color: "var(--hg-fg-1)", padding: "5px 10px",
                   fontFamily: FONT_MONO, fontSize: 11, cursor: "pointer" }}>
          ↺ reset all to defaults
        </button>
        <span style={{ fontFamily: FONT_MONO, fontSize: 10, color: "var(--hg-fg-2)" }}>
          scope: {scope}
        </span>
      </div>
    </div>
  );
}

window.HomeLightsDrawer = HomeLightsDrawer;
window.LIGHTS_ZONES = LIGHTS_ZONES;
