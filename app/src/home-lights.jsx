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

const TRAVEL_MODE_ENTITY = "input_boolean.living_lights_travel_mode";
const TRAVEL_MODE_CACHE_KEY = "home.lights.travelMode.state.v1";
const TRAVEL_MODE_CONTROLLED_LIGHTS = [
  "light.dining_table_left",
  "light.dining_table_right",
  "light.dining_light",
  "light.dining_light_2",
  "light.dining_room_floodlight_timed",
  "light.front_left",
  "light.front_right",
  "light.kitchen_floodlight_timed",
  "light.island_left",
  "light.island_right",
  "light.living_room_lights",
  "light.office",
  "light.outdoor_light",
  "light.rear_left",
  "light.rear_right",
  "light.sink",
  "light.sink_light",
  "light.sink_light_2",
  "light.ambient_light_left_mss110_main_channel",
  "light.ambient_light_right_mss110_main_channel",
];
const TRAVEL_MODE_CONTROLLED_SWITCHES = [
  "switch.ambient_light_left_mss110_main_channel",
  "switch.ambient_light_right_mss110_main_channel",
  "switch.smart_plug_mini_mss110_main_channel",
  "switch.workshop_light_left_mss110_main_channel",
  "switch.workshop_light_right_mss110_main_channel",
  "switch.smart_plug_mini_mss110_main_channel_2",
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
function normalizeTravelModeState(value) {
  return value === "on" || value === "off" ? value : null;
}
function makeTravelModeState(state, cached = false) {
  return {
    entity_id: TRAVEL_MODE_ENTITY,
    state,
    attributes: {
      friendly_name: "Living Lights - travel mode (force off)",
      _home_cached: cached,
    },
  };
}
function readCachedTravelModeState() {
  if (typeof window === "undefined" || !window.localStorage) return null;
  try {
    // Fail closed only. A cached "off" could hide a true-on state from
    // another device until HA sync completes, so we only seed from "on".
    return window.localStorage.getItem(TRAVEL_MODE_CACHE_KEY) === "on" ? "on" : null;
  } catch (_) {
    return null;
  }
}
function persistTravelModeState(state) {
  const normalized = normalizeTravelModeState(state);
  if (!normalized || typeof window === "undefined") return;
  window.__HOME_TRAVEL_MODE_STATE = normalized;
  try {
    window.dispatchEvent?.(new CustomEvent("home-travel-mode-change", { detail: { state: normalized } }));
  } catch (_) {}
  if (!window.localStorage) return;
  try {
    if (normalized === "on") window.localStorage.setItem(TRAVEL_MODE_CACHE_KEY, "on");
    else window.localStorage.removeItem(TRAVEL_MODE_CACHE_KEY);
  } catch (_) {
    // localStorage may be unavailable in private/locked-down webviews.
  }
}
function seedTravelModeState(map) {
  const states = map && typeof map === "object" ? { ...map } : {};
  const live = normalizeTravelModeState(states[TRAVEL_MODE_ENTITY]?.state);
  if (live) {
    persistTravelModeState(live);
    return states;
  }
  const cached = readCachedTravelModeState();
  if (cached) states[TRAVEL_MODE_ENTITY] = makeTravelModeState(cached, true);
  return states;
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
  if (getState(states, TRAVEL_MODE_ENTITY) === "on") {
    return { label: "travel mode", detail: "lighting output blocked", severity: "warn" };
  }
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
        aria-label={label}
        aria-valuetext={displayValue}
        value={draft == null ? min : draft}
        onChange={handle}
        onPointerUp={flush}
        onBlur={flush}
        disabled={disabled}
        style={{ width: "100%", accentColor: "var(--hg-accent)" }}
      />
      {hint && (
        <span style={{ fontFamily: FONT_SANS, fontSize: 11, color: "var(--hg-fg-2)" }}>{hint}</span>
      )}
    </div>
  );
}

// CTOverrideSlider — for the 4 per-modifier CT override sliders
// (asleep / gaming / working hours / movie). Differs from the plain
// Slider in two ways:
//
//   1. Visual position tracks the EFFECTIVE Kelvin the lights would
//      reach when this modifier wins. When override is 0 (inherit),
//      the knob sits at the current ToD color (e.g. 2700 K midday),
//      not at the slider's far-left minimum. That matches the user's
//      mental model: "the slider shows where the lights actually are."
//
//   2. The "↺ inherit" reset button is visible whenever override > 0
//      (since the only way to "go back to ToD" is to clear the
//      override). When override is 0, a small "inherit" badge replaces
//      the K reading next to the label.
//
// Range: min 1800 / max 4500 (matches AL clamp + the input_number).
// Drag always sets a non-zero override; click the ↺ to return to 0.
// ── HA service-call wrapper ────────────────────────────────────────────
// Prefer HAClient.callService when present, but keep a raw call_service
// fallback so older mocks and local probes can still drive the drawer.
// Older versions of HAClient exposed only a generic `call(payload)` method
// that sends raw WebSocket messages. They did NOT have a `callService`
// convenience method — calls like `client.callService("input_number",
// "set_value", { ... })` throw "is not a function" silently from
// promise-based catch handlers, which is exactly why our slider drags
// looked like they did nothing: the local draft state updated, but the
// commit threw before reaching HA. This wrapper builds the proper HA
// WebSocket `call_service` payload and uses `client.call`.
//
// Reference: https://developers.home-assistant.io/docs/api/websocket/#calling-a-service
function haCallService(client, domain, service, data) {
  if (!client) {
    return Promise.reject(new Error("Home Assistant client is not ready"));
  }
  if (Object.prototype.hasOwnProperty.call(client, "ws") &&
      (!client.ws || client.ws.readyState !== 1)) {
    return Promise.reject(new Error("Home Assistant websocket is offline"));
  }
  if (typeof client.callService === "function") {
    return client.callService(domain, service, data || {});
  }
  if (typeof client.call !== "function") {
    return Promise.reject(new Error("Home Assistant client cannot call services"));
  }
  const target = {};
  const serviceData = {};
  for (const [key, value] of Object.entries(data || {})) {
    if (value == null) continue;
    if (key === "entity_id" || key === "area_id" || key === "device_id") {
      target[key] = value;
    } else {
      serviceData[key] = value;
    }
  }
  const payload = {
    type: "call_service",
    domain,
    service,
    service_data: serviceData,
  };
  if (Object.keys(target).length > 0) payload.target = target;
  return client.call(payload);
}
window.haCallService = haCallService;

// Defensive computation of "what K should the slider show right now?"
// Pure function so the unit test can exercise it without React mounting.
// Exposed on `window` so the test harness in the bundled app context
// AND tools/probe-lights-drawer.py (via the integration mock) can verify
// the same code path that the UI uses.
function computeCTSliderEffective(override, todValue, min) {
  const minK = Number.isFinite(min) && min > 0 ? min : 1800;
  const ovr = Number(override);
  if (Number.isFinite(ovr) && ovr > 0) return ovr;
  const tod = Number(todValue);
  if (Number.isFinite(tod) && tod >= minK) return tod;
  // todValue missing / falsy / below min — fall back to a sensible ToD
  // default rather than the slider's bottom (which made the knob jump to
  // 1800 when the parent's todCT hadn't fetched yet, the bug the user
  // saw: slider at 1800 K with "inheriting ToD" badge).
  return 2700;
}
window.computeCTSliderEffective = computeCTSliderEffective;

function CTOverrideSlider({ label, override, todValue, min = 1800, max = 4500, step = 50, onSetOverride, hint }) {
  // Effective value the lights would receive: override if non-zero,
  // otherwise the ToD curve's current value. This is what the knob
  // tracks visually.
  const effective = computeCTSliderEffective(override, todValue, min);
  const debRef = useRef(null);
  const [draft, setDraft] = useState(effective);
  useEffect(() => { setDraft(effective); }, [effective]);
  const handle = (e) => {
    const v = Number(e.target.value);
    setDraft(v);
    if (debRef.current) clearTimeout(debRef.current);
    debRef.current = setTimeout(() => {
      if (window.__LIGHTS_DEBUG === true) console.log("[CTOverrideSlider]", label, "debounced commit", { draft: v });
      onSetOverride(v);
    }, 120);
  };
  const flush = () => {
    if (debRef.current) { clearTimeout(debRef.current); debRef.current = null; }
    if (draft !== effective) {
      if (window.__LIGHTS_DEBUG === true) console.log("[CTOverrideSlider]", label, "flush commit", { draft });
      onSetOverride(draft);
    }
  };
  const isInherit = !(override > 0);
  return (
    <div style={{ padding: "10px 16px", display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontFamily: FONT_SANS, fontSize: 13, color: "var(--hg-fg-1)" }}>{label}</span>
        <span style={{ fontFamily: FONT_MONO, fontSize: 13, color: isInherit ? "var(--hg-fg-2)" : "var(--hg-fg-0)" }}>
          {draft} K
          {isInherit && (
            <span style={{ marginLeft: 6, fontSize: 10, color: "var(--hg-fg-2)" }}>· inheriting ToD</span>
          )}
          {!isInherit && (
            <button
              type="button"
              onClick={(e) => { e.preventDefault(); onSetOverride(0); }}
              title="Return to ToD curve (clear override)"
              style={{ background: "transparent", border: "none", color: "var(--hg-fg-2)", cursor: "pointer", fontSize: 12, marginLeft: 6 }}>
              ↺
            </button>
          )}
        </span>
      </div>
      <input
        type="range" min={min} max={max} step={step}
        aria-label={label}
        aria-valuetext={`${draft} K`}
        value={draft}
        onChange={handle}
        onPointerUp={flush}
        onBlur={flush}
        style={{ width: "100%", accentColor: isInherit ? "var(--hg-fg-2)" : "var(--hg-accent)" }}
      />
      {hint && (
        <span style={{ fontFamily: FONT_SANS, fontSize: 11, color: "var(--hg-fg-2)" }}>{hint}</span>
      )}
    </div>
  );
}

// CTInheritanceNote — replaces the per-modifier CT override sliders that
// used to live on the Asleep / Gaming / Working hours / Movie cards.
// Those sliders had a fundamental tug-of-war problem: dragging set the
// cascade's predicted CT, the drawer pushed the value to the bulbs, but
// Adaptive Lighting's own 90-s cycle would overwrite within seconds. The
// only way to make the override stick would be to engage AL's
// `set_manual_control` for the affected lights while the modifier is
// active — V2 work. For V1 we just point the user at the right knob.
function CTInheritanceNote({ onJumpToToD }) {
  return (
    <div style={{ padding: "10px 16px", fontFamily: FONT_SANS, fontSize: 12, color: "var(--hg-fg-2)",
                   display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
      <span>
        <span style={{ fontFamily: FONT_MONO, color: "var(--hg-fg-2)", marginRight: 6 }}>↺</span>
        Color temperature inherited from the Time of Day curve.
      </span>
      <button
        type="button"
        onClick={onJumpToToD}
        style={{ background: "transparent", border: "1px solid var(--hg-border)", borderRadius: 4,
                 color: "var(--hg-fg-1)", padding: "3px 8px",
                 fontFamily: FONT_MONO, fontSize: 10, cursor: "pointer", whiteSpace: "nowrap" }}>
        tune ToD ↑
      </button>
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
            background: isOn ? "var(--hg-accent)" : "var(--hg-bg-2)",
            color: isOn ? "var(--hg-bg-0)" : "var(--hg-fg-1)",
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

function CascadeCard({ title, subtitle, intro, winning, locked, children }) {
  return (
    <div style={{
      margin: "8px 16px",
      border: "1px solid " + (winning ? "var(--hg-accent)" : "var(--hg-border)"),
      borderLeft: "3px solid " + (locked ? "var(--hg-fg-2)" : winning ? "var(--hg-accent)" : "var(--hg-border)"),
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
          <span style={{ fontFamily: FONT_MONO, fontSize: 10, color: "var(--hg-accent)", whiteSpace: "nowrap" }}>
            ◀ winning
          </span>
        )}
      </div>
      {intro && (
        <div style={{ padding: "10px 16px 4px", fontFamily: FONT_SANS, fontSize: 12, lineHeight: 1.45, color: "var(--hg-fg-1)" }}>
          {intro}
        </div>
      )}
      <div>{children}</div>
    </div>
  );
}

// ── FrozenCard ────────────────────────────────────────────────────────
// A locked cascade card with a rich explanation, list of tunable constants,
// a user-intent text input, and an "Ask ChatGPT to change this" button
// that pre-fills the main chat input with a `/ask`-prefixed message
// containing layer context + the user's intent. The `/ask` prefix forces
// routing to the external provider (ChatGPT) for that one turn, bypassing
// the local Qwen3-VL classifier (the local model has no code-editing
// tools; the external API has the headroom to propose concrete changes).
function TravelModeCard({ active, available, cached, busy, disabled, onToggle, compact = false }) {
  const cardBorder = active ? "rgba(232,140,48,0.82)" : "var(--hg-border)";
  const lockText = !available ? "checking travel mode" : active ? "travel mode on" : "travel mode off";
  const body = !available
    ? "Checking Home Assistant before showing the travel safety state."
    : cached
    ? active
      ? "Last known state is on; confirming with Home Assistant."
      : "Confirming with Home Assistant."
    : available
    ? active
      ? "Known lighting outputs are locked out and forced off."
      : "Turn this on before travel to block lighting automations and force known lights off."
    : "Travel Mode is not visible in the current HA state cache yet. Tap to try; reload the app if HA just restarted.";
  return (
    <div style={{
      margin: compact ? "10px 14px 8px" : "12px 16px 8px",
      padding: compact ? "10px 12px" : "14px 16px",
      border: "1px solid " + cardBorder,
      borderLeft: "3px solid " + cardBorder,
      borderRadius: 8,
      background: active ? "rgba(232,140,48,0.12)" : "var(--hg-bg-1)",
      display: "flex",
      flexDirection: "column",
      gap: compact ? 7 : 10,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
          <span style={{ fontFamily: FONT_MONO, fontSize: 10, color: active ? "var(--hg-warn)" : "var(--hg-fg-2)", textTransform: "uppercase", letterSpacing: 0.8 }}>
            travel mode
          </span>
          <span style={{ fontFamily: FONT_SANS, fontSize: compact ? 15 : 16, color: "var(--hg-fg-0)", lineHeight: 1.2 }}>
            {lockText}
          </span>
        </div>
        <button
          type="button"
          disabled={disabled || busy}
          onClick={() => onToggle(!active)}
          style={{
            background: active ? "var(--hg-warn)" : "var(--hg-bg-2)",
            color: active ? "#110b04" : "var(--hg-fg-1)",
            border: "1px solid " + (active ? "var(--hg-warn)" : "var(--hg-border)"),
            borderRadius: 999,
            padding: "7px 12px",
            fontFamily: FONT_MONO,
            fontSize: 11,
            cursor: disabled || busy ? "not-allowed" : "pointer",
            opacity: disabled || busy ? 0.5 : 1,
            whiteSpace: "nowrap",
          }}>
          {busy ? "saving" : active ? "turn off" : "turn on"}
        </button>
      </div>
      <div style={{ fontFamily: FONT_SANS, fontSize: compact ? 11.5 : 12, lineHeight: 1.4, color: "var(--hg-fg-1)" }}>
        {body}
      </div>
      <div style={{ fontFamily: FONT_MONO, fontSize: 10, lineHeight: 1.35, color: "var(--hg-fg-2)", textTransform: "uppercase", letterSpacing: 0.5 }}>
        blocks pilots / scenes / ambient switches
      </div>
    </div>
  );
}

function AdaptiveLightingDiagnosticsCard({ states, haOnline }) {
  const main = getState(states, "switch.home_adaptive_lighting_home", "unknown");
  const targetCT = toNum(getAttr(states, "switch.home_adaptive_lighting_home", "color_temp_kelvin"), null);
  const adaptBrightness = getState(states, "switch.adaptive_lighting_adapt_brightness_home", "unknown");
  const adaptColor = getState(states, "switch.adaptive_lighting_adapt_color_home", "unknown");
  const sleepMode = getState(states, "switch.adaptive_lighting_sleep_mode_home", "unknown");
  const takeOver = getAttr(states, "switch.home_adaptive_lighting_home", "take_over_control", "false");
  const mode = getAttr(states, "switch.home_adaptive_lighting_home", "take_over_control_mode", "pause_all");
  const danger = adaptBrightness === "on";
  const stale = !haOnline || main === "unknown";
  const cell = (label, value, tone = "normal") => (
    <div style={{
      minWidth: 0,
      padding: "7px 8px",
      border: "1px solid var(--hg-border-soft)",
      background: "rgba(255,255,255,0.015)",
    }}>
      <div style={{ fontFamily: FONT_MONO, fontSize: 9, color: "var(--hg-fg-2)", textTransform: "uppercase", letterSpacing: 1 }}>
        {label}
      </div>
      <div style={{
        marginTop: 3,
        fontFamily: FONT_MONO,
        fontSize: 12,
        color: tone === "danger" ? "var(--hg-crit)" : tone === "ok" ? "var(--hg-accent)" : "var(--hg-fg-0)",
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
      }}>
        {value}
      </div>
    </div>
  );

  return (
    <div style={{
      margin: "8px 16px",
      border: "1px solid " + (danger ? "rgba(255,119,119,0.65)" : "var(--hg-border)"),
      borderRadius: 6,
      background: "rgba(0,0,0,0.18)",
      overflow: "hidden",
    }}>
      <div style={{ padding: "10px 12px", display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontFamily: FONT_MONO, fontSize: 11, color: "var(--hg-fg-1)", textTransform: "uppercase", letterSpacing: 1.4 }}>
            adaptive lighting
          </div>
          <div style={{ marginTop: 4, fontFamily: FONT_SANS, fontSize: 12, color: "var(--hg-fg-2)", lineHeight: 1.35 }}>
            {danger
              ? "brightness adaptation is on; Living Lights should own brightness."
              : stale
              ? "waiting for Home Assistant state."
              : "color curve only; Living Lights owns brightness and on/off."}
          </div>
        </div>
        <div style={{
          flex: "0 0 auto",
          border: "1px solid " + (danger ? "rgba(255,119,119,0.7)" : "var(--hg-border)"),
          color: danger ? "var(--hg-crit)" : "var(--hg-fg-1)",
          padding: "5px 8px",
          fontFamily: FONT_MONO,
          fontSize: 10,
          textTransform: "uppercase",
          letterSpacing: 1,
        }}>
          {danger ? "check" : main}
        </div>
      </div>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
        gap: 6,
        padding: "0 12px 12px",
      }}>
        {cell("target", targetCT == null ? "unknown" : `${targetCT} K`, targetCT == null ? "normal" : "ok")}
        {cell("adapt color", adaptColor, adaptColor === "on" ? "ok" : "normal")}
        {cell("adapt brightness", adaptBrightness, danger ? "danger" : "normal")}
        {cell("takeover", `${takeOver} / ${mode}`)}
        {cell("sleep mode", sleepMode)}
        {cell("source", "HACS v1.31-ready")}
      </div>
    </div>
  );
}

function FrozenCard({ title, subtitle, intro, knobs, fileLocation, whyFrozen, currentValues, placeholder, topicKey, onAsk }) {
  const [intent, setIntent] = useState("");
  const submit = () => {
    // Always allow the click. If the user hasn't typed anything, send a
    // generic "explain this and what I could change" prompt — clicking
    // the button should always do SOMETHING (the disabled-when-empty
    // pattern confused users who saw the placeholder text and thought
    // the field was already filled in).
    const userIntent = intent.trim()
      || "I'd like to understand this layer better. What changes are possible, and what trade-offs should I consider?";
    onAsk({ topicKey, title, intro, knobs, fileLocation, whyFrozen, currentValues, userIntent });
    setIntent("");
  };
  return (
    <CascadeCard title={title} subtitle={subtitle} locked>
      {intro && (
        <div style={{ padding: "10px 16px 4px", fontFamily: FONT_SANS, fontSize: 12, lineHeight: 1.45, color: "var(--hg-fg-1)" }}>
          {intro}
        </div>
      )}
      {knobs && knobs.length > 0 && (
        <div style={{ padding: "6px 16px 6px", fontFamily: FONT_SANS, fontSize: 12, color: "var(--hg-fg-2)" }}>
          <div style={{ fontFamily: FONT_MONO, fontSize: 10, color: "var(--hg-fg-2)", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 4 }}>
            what's tunable
          </div>
          <ul style={{ margin: 0, padding: "0 0 0 18px", listStyle: "disc" }}>
            {knobs.map((k, i) => (
              <li key={i} style={{ marginBottom: 3 }}>
                <code style={{ fontFamily: FONT_MONO, fontSize: 11, color: "var(--hg-fg-1)" }}>{k.name}</code>
                {k.current != null && (
                  <span style={{ fontFamily: FONT_MONO, fontSize: 11, color: "var(--hg-fg-2)" }}> = {k.current}</span>
                )}
                {k.desc && (
                  <span style={{ color: "var(--hg-fg-2)" }}> — {k.desc}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {fileLocation && (
        <div style={{ padding: "4px 16px 6px", fontFamily: FONT_MONO, fontSize: 11, color: "var(--hg-fg-2)" }}>
          <code style={{ color: "var(--hg-fg-1)" }}>{fileLocation}</code>
          {whyFrozen && <span> · {whyFrozen}</span>}
        </div>
      )}
      <div style={{ padding: "8px 16px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
        <input
          type="text"
          aria-label="What would you like changed"
          placeholder={placeholder || "What would you like changed?"}
          value={intent}
          onChange={(e) => setIntent(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } }}
          style={{
            width: "100%", boxSizing: "border-box",
            padding: "6px 10px",
            background: "var(--hg-bg-0)", color: "var(--hg-fg-0)",
            border: "1px solid var(--hg-border)", borderRadius: 4,
            fontFamily: FONT_SANS, fontSize: 12,
          }}
        />
        <button
          type="button"
          onClick={submit}
          style={{
            alignSelf: "flex-end",
            background: "var(--hg-accent)",
            color: "var(--hg-bg-0)",
            border: "1px solid transparent",
            borderRadius: 4,
            padding: "5px 12px",
            fontFamily: FONT_MONO, fontSize: 11,
            cursor: "pointer",
          }}
        >
          ask ChatGPT to change this →
        </button>
      </div>
    </CascadeCard>
  );
}

// ── Main drawer component ──────────────────────────────────────────────

/* Two-click arm→confirm label (Pattern B1, copied from home-ai-stack.jsx's
 * _aiStackConfirmButtonLabel). Armed buttons swap to an explicit confirm
 * label that names the verb; un-armed buttons keep their resting label. */
function _lightsConfirmButtonLabel(confirmVerb, verb, confirmLabel, label) {
  return confirmVerb === verb ? confirmLabel : label;
}

function HomeLightsDrawer({ open, onClose, client, connection = null, sim, askExternal, spatialMode = false }) {
  const [zone, setZone] = useState("office");
  const [states, setStates] = useState(() => seedTravelModeState({}));  // { entity_id: { state, attributes } }
  const [error, setError] = useState(null);
  const [travelModeBusy, setTravelModeBusy] = useState(false);
  // Inline two-click confirmation for the destructive footer reset
  // (Pattern B1, copied from home-ai-stack.jsx confirmOrDispatch): first
  // click arms, second click within 3 s runs, otherwise the arm expires.
  const [confirmVerb, setConfirmVerb] = useState(null);
  const confirmTimerRef = useRef(null);
  const subRef = useRef(null);
  // Ref + scroll helper for the "tune ToD ↑" buttons on per-modifier
  // cards (where the CT slider used to live). Each modifier card carries
  // a CTInheritanceNote that calls jumpToToD() to scroll the user up to
  // the ToD bucket CT sliders — the actual right place to tune CT given
  // AL's authoritative role.
  const todSectionRef = useRef(null);
  const jumpToToD = useCallback(() => {
    todSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  // Cleanup any pending arm-expiry timer on unmount.
  useEffect(() => {
    return () => {
      if (confirmTimerRef.current) {
        try { clearTimeout(confirmTimerRef.current); } catch {}
        confirmTimerRef.current = null;
      }
    };
  }, []);

  const confirmOrRun = useCallback((verb, run) => {
    if (confirmVerb !== verb) {
      setConfirmVerb(verb);
      if (confirmTimerRef.current) {
        try { clearTimeout(confirmTimerRef.current); } catch {}
      }
      confirmTimerRef.current = setTimeout(() => {
        setConfirmVerb((current) => (current === verb ? null : current));
        confirmTimerRef.current = null;
      }, 3000);
      return;
    }
    if (confirmTimerRef.current) {
      try { clearTimeout(confirmTimerRef.current); } catch {}
      confirmTimerRef.current = null;
    }
    setConfirmVerb(null);
    run();
  }, [confirmVerb]);
  const haOnline = !!client && (connection == null || connection === "online");
  const offlineWriteMessage = "Home Assistant is reconnecting; light controls are disabled until it is online.";
  const mobile = typeof window !== "undefined" && window.innerWidth <= 699;

  const readHaStates = async () => {
    const r = await client.call({ type: "get_states" });
    const arr = Array.isArray(r) ? r : [];
    const map = {};
    for (const s of arr) {
      if (s && s.entity_id) map[s.entity_id] = s;
    }
    return map;
  };

  // Fetch + subscribe on open
  useEffect(() => {
    if (!open || !haOnline) return undefined;
    if (connection != null && connection !== "online") {
      setError(offlineWriteMessage);
      return undefined;
    }
    let active = true;
    const fetch = async () => {
      try {
        const map = await readHaStates();
        if (!active) return;
        setStates(seedTravelModeState(map));
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
    const ownedEntityIds = new Set([
      "input_boolean.homeai_sleep",
      "media_player.lg_tv",
      "sensor.steam_steam_76561198136331341",
      "switch.home_adaptive_lighting_home",
      "switch.adaptive_lighting_adapt_brightness_home",
      "switch.adaptive_lighting_adapt_color_home",
      "switch.adaptive_lighting_sleep_mode_home",
    ]);
    try {
      // Signature is subscribeEvents(eventType, onEvent) — eventType FIRST,
      // callback SECOND. Inverting the arguments here is what caused the
      // "TypeError: _onEvent2 is not a function" error spam in the console
      // (the runtime called the string "state_changed" as a function on
      // every state-change event the WebSocket received, ~hundreds/minute).
      const unsub = client.subscribeEvents("state_changed", (msg) => {
        const ev = msg?.event;
        if (!ev) return;
        const eid = ev.data?.entity_id;
        if (!eid) return;
        const owned = ownedEntityIds.has(eid) || ownedPrefixes.some(p => eid.startsWith(p));
        // Always update classifier sensors too (for the active zone's status card).
        const isClassifier = eid.startsWith("sensor.") && eid.endsWith("_lighting_state");
        if (!owned && !isClassifier) return;
        const ns = ev.data?.new_state;
        if (!ns) return;
        if (eid === TRAVEL_MODE_ENTITY) persistTravelModeState(ns.state);
        setStates(prev => ({ ...prev, [eid]: ns }));
      });
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
  }, [open, client, connection]);

  // Overlay layer: topmost-only Escape + focus management via HomeOverlay.
  const lightsRootRef = React.useRef(null);
  window.HomeOverlay.useOverlayLayer({
    key: "lights",
    active: !!open,
    onEscape: () => onClose(),
    rootRef: lightsRootRef,
  });

  // While the drawer is open, treat it as "tuning mode" — actively
  // suppress the per-zone manual-override cooldown. The pilot's brightness
  // ramp is 12 s end-to-end (2 s fast + 10 s slow); the manual-detection
  // automation samples the bulb on a 3-s settle window during that ramp
  // and easily mis-flags the slider-driven mid-ramp value as a manual
  // override, engaging a 15-min cooldown that blocks subsequent pilot
  // actuation — exactly the "sliders don't change my lights" failure mode.
  //
  // Single-shot clears on open + per-write are not enough because the
  // manual-detection can fire AFTER the clear during the ramp. Sweep
  // every 4 s while open so any false-positive cooldown re-engagements
  // get cleared before they can block the next pilot tick.
  useEffect(() => {
    if (!open || !client) return undefined;
    const cooldownEntities = LIGHTS_ZONES.map(z =>
      `input_datetime.living_lights_zone_${z.slug}_last_manual_at`
    );
    const sweep = () => {
      try {
        const p = haCallService(client, "input_datetime", "set_datetime", {
          entity_id: cooldownEntities,
          datetime: "1970-01-01 00:00:00",
        });
        if (p && typeof p.catch === "function") p.catch(() => { /* non-fatal */ });
      } catch (e) { /* non-fatal — client may be momentarily unavailable */ }
    };
    sweep();                              // immediately on open
    const t = setInterval(sweep, 4000);   // and every 4 s while open
    return () => clearInterval(t);
  }, [open, client, haOnline]);

  // Compute the winning-modifier description. MUST be called before any
  // conditional early return (rules of hooks). Cheap; runs every render.
  const winning = useMemo(() => describeWinningModifier(states, zone), [states, zone]);

  if (!open) return null;

  const classifier = stateSensorFor(zone);

  // Helper writers.
  //
  // Slider drags are "system-driven" intent — the user is actively tuning,
  // not manually touching a single bulb. But the manual-detection automation
  // doesn't know the difference: it watches every brightness change, and if
  // the bulb's actual value lags the classifier's prediction during the
  // pilot's 12-second ramp, it interprets the mismatch as a manual override
  // and engages a 15-minute cooldown that blocks subsequent pilot actuation.
  //
  // That cooldown is the dominant reason "lights don't respond to slider
  // drags in real time" — the HA cascade is updating correctly, but the
  // pilot is locked out for any zone with a hot last_manual_at.
  //
  // Workaround: every slider write also resets last_manual_at to the
  // sentinel for ALL light-controlled zones. Cost: a single input_datetime
  // service call per slider write. Trade-off: if the user touched a bulb
  // via Hue / wall switch in the last 15 minutes and then opens the drawer,
  // that manual touch is no longer "remembered" — but they're actively
  // tuning, which signals "I want the system to drive these lights now."
  const COOLDOWN_ENTITIES = LIGHTS_ZONES.map(z =>
    `input_datetime.living_lights_zone_${z.slug}_last_manual_at`
  );
  const clearAllCooldowns = () => {
    if (!haOnline) { setError(offlineWriteMessage); return; }
    haCallService(client, "input_datetime", "set_datetime", {
      entity_id: COOLDOWN_ENTITIES,
      datetime: "1970-01-01 00:00:00",
    }).catch((e) => setError(`clear cooldowns: ${e?.message || e}`));
  };
  const setNum = (entity_id, value) => {
    if (!haOnline) { setError(offlineWriteMessage); return; }
    haCallService(client, "input_number", "set_value", { entity_id, value })
      .catch((e) => setError(`set ${entity_id}=${value}: ${e?.message || e}`));
    // Clear cooldowns so the pilot can actually drive the lights to the
    // new value instead of being blocked by a stale "manual touch" flag.
    clearAllCooldowns();
  };

  // Direct CT push — fires light.turn_on(color_temp_kelvin=X) on every
  // controllable light. This bypasses:
  //   (a) the Living Lights brightness-pilot (which only fires on
  //       brightness state changes, not CT-only changes), AND
  //   (b) Adaptive Lighting's 90-s cycle (which would otherwise be
  //       authoritative for CT).
  //
  // Used by every CT slider in the drawer (per-modifier overrides + ToD
  // bucket anchors + warmth bias) so the user sees IMMEDIATE feedback
  // when they drag. AL will re-push its own curve on its next tick,
  // which may override the user's manual setting after ~90 s — that's
  // the trade-off for not engaging AL's `manual_control` mode. If the
  // user keeps tuning, the drawer keeps pushing; only sustained
  // single-value preferences would need AL's manual-control toggle.
  //
  // We push the SAME ct to every light. This is a simplification — the
  // proper behavior per-zone is to read each classifier's
  // predicted_color_temp_kelvin and push that. Since AL is house-wide
  // and the per-modifier overrides apply uniformly across all
  // light-controlled zones, pushing one CT to all lights matches what
  // the classifier would compute anyway, in the common case.
  const ALL_CONTROLLED_LIGHTS = TRAVEL_MODE_CONTROLLED_LIGHTS;
  const pushCTToAllLights = (ct) => {
    if (!haOnline) { setError(offlineWriteMessage); return; }
    if (getState(states, TRAVEL_MODE_ENTITY) === "on") {
      setError("travel mode is on: lighting output is blocked");
      return;
    }
    if (!Number.isFinite(ct) || ct < 1800 || ct > 4500) return;
    try {
      const p = haCallService(client, "light", "turn_on", {
        entity_id: ALL_CONTROLLED_LIGHTS,
        color_temp_kelvin: Math.round(ct),
        transition: 1.0,
      });
      if (p && typeof p.catch === "function") {
        p.catch((e) => setError(`push CT ${ct}K: ${e?.message || e}`));
      }
    } catch (e) { /* non-fatal */ }
  };

  // Setter used by CT sliders. Updates the input_number (so the cascade
  // sees the new value) AND immediately pushes the resulting CT to the
  // bulbs (so the user sees the change without waiting for the next
  // brightness-driven pilot fire). We push the EFFECTIVE CT computed
  // from the classifier (read fresh after a short settle), not the raw
  // slider value, so per-modifier overrides + warmth bias + clamps all
  // resolve correctly.
  const setCTAndPush = (entity_id, value) => {
    if (!haOnline) { setError(offlineWriteMessage); return; }
    haCallService(client, "input_number", "set_value", { entity_id, value })
      .catch((e) => setError(`set ${entity_id}=${value}: ${e?.message || e}`));
    clearAllCooldowns();
    // Wait briefly for the classifier to recompute, then push the
    // resulting CT to all lights. 700ms is enough — classifier
    // re-evaluates within ~1s via its time_pattern + input_number trigger.
    setTimeout(() => {
      // Pick any classifier sensor — they all read the same profile;
      // per-modifier overrides apply globally to all controlled lights.
      const cls = stateSensorFor(zone);
      const attrs = states[cls]?.attributes;
      const effective = toNum(attrs?.predicted_color_temp_kelvin, null);
      if (effective != null) pushCTToAllLights(effective);
    }, 700);
  };
  const toggleBool = (entity_id) => {
    if (!haOnline) { setError(offlineWriteMessage); return; }
    const cur = getState(states, entity_id) === "on";
    haCallService(client, "input_boolean", cur ? "turn_off" : "turn_on", { entity_id })
      .catch((e) => setError(`toggle ${entity_id}: ${e?.message || e}`));
  };
  const forceTravelLightsOff = async () => {
    await haCallService(client, "light", "turn_off", {
      entity_id: TRAVEL_MODE_CONTROLLED_LIGHTS,
      transition: 1,
    });
    await haCallService(client, "switch", "turn_off", {
      entity_id: TRAVEL_MODE_CONTROLLED_SWITCHES,
    });
  };
  const setTravelMode = async (enabled) => {
    if (!haOnline) { setError(offlineWriteMessage); return; }
    setTravelModeBusy(true);
    try {
      if (enabled) {
        persistTravelModeState("on");
        setStates(prev => ({ ...prev, [TRAVEL_MODE_ENTITY]: makeTravelModeState("on", true) }));
      }
      await haCallService(client, "input_boolean", enabled ? "turn_on" : "turn_off", { entity_id: TRAVEL_MODE_ENTITY });
      if (enabled) await forceTravelLightsOff();
      let confirmedStates = null;
      try {
        confirmedStates = await readHaStates();
      } catch (_) {
        confirmedStates = null;
      }
      const confirmedHelper = confirmedStates?.[TRAVEL_MODE_ENTITY];
      if (!confirmedHelper) {
        throw new Error("Travel Mode helper is not loaded in Home Assistant yet. Reload HA packages or restart HA Core.");
      }
      const targetTravelModeState = enabled ? "on" : "off";
      persistTravelModeState(targetTravelModeState);
      setStates(prev => ({
        ...(confirmedStates || prev),
        [TRAVEL_MODE_ENTITY]: {
          ...(confirmedHelper || prev[TRAVEL_MODE_ENTITY] || { entity_id: TRAVEL_MODE_ENTITY, attributes: {} }),
          state: targetTravelModeState,
        },
      }));
      setError(null);
    } catch (e) {
      setError("travel mode " + (enabled ? "on" : "off") + ": " + (e?.message || e));
    } finally {
      setTravelModeBusy(false);
    }
  };
  const setText = (entity_id, value) => {
    if (!haOnline) { setError(offlineWriteMessage); return; }
    haCallService(client, "input_text", "set_value", { entity_id, value })
      .catch((e) => setError(`set ${entity_id}=${value}: ${e?.message || e}`));
  };

  // Read entity helpers
  const n = (eid, fallback = 0) => toNum(getState(states, eid), fallback);
  const b = (eid) => getState(states, eid);
  const travelModeState = b(TRAVEL_MODE_ENTITY);
  const travelModeAvailable = travelModeState != null;
  const travelModeActive = travelModeState === "on";
  const travelModeCached = !!getAttr(states, TRAVEL_MODE_ENTITY, "_home_cached", false);
  const cls = stateSensorFor(zone);
  const predBri = toNum(getAttr(states, cls, "predicted_brightness_pct"), null);
  const predCT  = toNum(getAttr(states, cls, "predicted_color_temp_kelvin"), null);
  const zState  = getState(states, cls, "—");
  const rampInit = toNum(getAttr(states, cls, "ramp_initial_pct"), null);

  // Bias scope: 'all' or zone slug
  const scope = getState(states, "input_text.living_lights_bias_zone_scope", "all");
  const scopeIsAll = scope === "all";
  const scopeIsThisZone = scope === zone;

  // Current ToD CT — shown in the "inherit (ToD: XXXX K)" labels on
  // each per-modifier CT slider. Read fresh each render so the fallback
  // value tracks the time of day.
  const todCT = toNum(getAttr(states, "sensor.living_lights_profile", "tod_color_warm"), 2700);
  const ctFmt = (v) => (Number(v) === 0 ? `inherit (ToD: ${todCT} K)` : `${v} K`);
  // Diagnostic logging is off by default. Set window.__LIGHTS_DEBUG = true
  // in dev tools console to enable per-render state-snapshot logging.
  if (typeof window !== "undefined" && window.__LIGHTS_DEBUG === true) {
    console.log("[lights drawer] render", {
      states_count: Object.keys(states).length,
      profile_present: !!states["sensor.living_lights_profile"],
      profile_state: states["sensor.living_lights_profile"]?.state,
      tod_color_warm: states["sensor.living_lights_profile"]?.attributes?.tod_color_warm,
      todCT,
      working_hours_ct_k: states["input_number.living_lights_working_hours_ct_k"]?.state,
    });
  }

  // Winning modifier styling
  const winningColor = {
    ok: "var(--hg-accent)",
    warn: "var(--hg-warn)",
    info: "var(--hg-fg-1)",
    muted: "var(--hg-fg-2)",
  }[winning.severity] || "var(--hg-fg-1)";

  // Ask-ChatGPT pre-populator (for frozen-knob handoff). Sends the full
  // layer context + user's intent up to home-app.jsx, which composes a
  // /ask-prefixed message in the chat input. The /ask prefix forces
  // external-provider routing for that one turn.
  const ask = (payload) => {
    if (askExternal) askExternal(payload);
    onClose();
  };

  // ── Render ──────────────────────────────────────────────────────────
  return (
    <div ref={lightsRootRef} role="dialog" aria-modal="false" aria-label="Living Lights tuning"
      data-theme={spatialMode ? "dark" : undefined}
      style={{
        position: "fixed",
        ...(spatialMode
          ? {
              top: 84,
              right: "calc(clamp(352px, 19vw, 388px) + 18px)",
              bottom: 46,
              width: "min(392px, calc(100vw - clamp(352px, 19vw, 388px) - 118px))",
              border: "1px solid rgba(184,216,255,0.075)",
              borderRadius: 7,
              background: "rgba(5,7,11,0.86)",
              backdropFilter: "blur(16px)",
              boxShadow: "0 18px 56px rgba(0,0,0,0.36)",
            }
          : {
              top: 0,
              right: 0,
              bottom: 0,
              width: "min(520px, 100vw)",
              background: "var(--hg-bg-0)",
              borderLeft: "1px solid var(--hg-border)",
              boxShadow: "-2px 0 16px rgba(0,0,0,0.18)",
            }),
        zIndex: 1100,
        display: "flex", flexDirection: "column",
        overflow: "hidden",
      }}>
      {/* Header */}
      <div style={{ padding: mobile ? "12px 14px" : "12px 18px", borderBottom: "1px solid var(--hg-border-soft)",
                    display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontFamily: FONT_MONO, fontSize: 12, color: "var(--hg-fg-1)", letterSpacing: 1, textTransform: "uppercase" }}>lights</span>
        <button onClick={onClose}
          style={{ background: "transparent", border: "none", color: "var(--hg-fg-2)",
                   fontFamily: FONT_MONO, fontSize: 11, cursor: "pointer",
                   minHeight: mobile ? 38 : "auto", whiteSpace: "nowrap" }}>
          {mobile ? "close" : "close · esc"}
        </button>
      </div>

      {error && (
        <div role="status" style={{ padding: "8px 18px", borderBottom: "1px solid var(--hg-border-soft)", background: "var(--hg-bg-1)",
                      fontFamily: FONT_MONO, fontSize: 11, color: "var(--hg-warn)" }}>
          {error} <button onClick={() => setError(null)} style={{ background: "transparent", border: "none", color: "var(--hg-fg-2)", cursor: "pointer", fontFamily: FONT_MONO, fontSize: 11 }}>dismiss</button>
        </div>
      )}

      {/* Travel Mode is a safety control, so keep it outside the scroll
          body where it cannot be hidden by restored scroll position. */}
      <div style={{ borderBottom: "1px solid var(--hg-border-soft)", background: "var(--hg-bg-0)" }}>
        <TravelModeCard
          active={travelModeActive}
          available={travelModeAvailable}
          cached={travelModeCached}
          busy={travelModeBusy}
          disabled={!haOnline}
          onToggle={setTravelMode}
          compact={mobile}
        />
      </div>

      {/* Scrollable body — `hg-scroll` class applies the app's themed
          thin (6px) scrollbar from home-tokens.css instead of the
          default chunky Windows/WebView one. */}
      <div className="hg-scroll" style={{ flex: 1, overflowY: "auto", overflowX: "hidden" }}>

        <AdaptiveLightingDiagnosticsCard states={states} haOnline={haOnline} />

        {/* ── Right Now ─────────────────────────────────────────── */}
        <div style={{ margin: "12px 16px", padding: "12px 16px",
                      border: "1px solid var(--hg-border)", borderRadius: 8,
                      background: "var(--hg-bg-1)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
            <span style={{ fontFamily: FONT_MONO, fontSize: 10, color: "var(--hg-fg-2)", textTransform: "uppercase", letterSpacing: 0.8 }}>right now</span>
            <select aria-label="Zone" value={zone} onChange={(e) => setZone(e.target.value)}
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
              <button disabled={!haOnline} onClick={() => setText("input_text.living_lights_bias_zone_scope", "all")}
                style={{ background: scopeIsAll ? "var(--hg-accent)" : "var(--hg-bg-2)",
                         color: scopeIsAll ? "var(--hg-bg-0)" : "var(--hg-fg-1)",
                         border: "1px solid " + (scopeIsAll ? "transparent" : "var(--hg-border)"),
                          borderRadius: 4, padding: "2px 8px",
                          fontFamily: FONT_MONO, fontSize: 10, cursor: haOnline ? "pointer" : "not-allowed",
                          opacity: haOnline ? 1 : 0.45 }}>
                all zones
              </button>
              <button disabled={!haOnline} onClick={() => setText("input_text.living_lights_bias_zone_scope", zone)}
                style={{ background: scopeIsThisZone ? "var(--hg-accent)" : "var(--hg-bg-2)",
                         color: scopeIsThisZone ? "var(--hg-bg-0)" : "var(--hg-fg-1)",
                         border: "1px solid " + (scopeIsThisZone ? "transparent" : "var(--hg-border)"),
                          borderRadius: 4, padding: "2px 8px",
                          fontFamily: FONT_MONO, fontSize: 10, cursor: haOnline ? "pointer" : "not-allowed",
                          opacity: haOnline ? 1 : 0.45 }}>
                {zone} only
              </button>
            </div>
          </div>
          <Slider label="Warmth bias" unit=" K" min={-500} max={500} step={50} defaultValue={0}
                  value={n("input_number.living_lights_warmth_bias_k", 0)}
                  disabled={!haOnline}
                  onChange={(v) => setCTAndPush("input_number.living_lights_warmth_bias_k", v)}
                  hint={(scopeIsAll || scopeIsThisZone) ?
                        `effective ${predCT ?? "—"} K (clamped at ${HA_MIN_CT}–${HA_MAX_CT})` :
                        `not applied — scope is '${scope}'`}
                  fmt={(v) => (v >= 0 ? `+${v} K` : `${v} K`)} />
          <Slider label="Brightness bias" unit=" pp" min={-30} max={30} step={5} defaultValue={0}
                  value={n("input_number.living_lights_brightness_bias_pp", 0)}
                  disabled={!haOnline}
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
          subtitle="Wins over everything when the house is quiet overnight"
          intro="When the house has been quiet for 15 minutes overnight, the system latches into asleep mode and dims to 0 % in vacant zones. Bumping into a room shows a low ceiling (the slider below). Resetting comes from sustained presence, midday backstop, or arrival home."
          winning={b("input_boolean.living_lights_asleep") === "on"}>
          <ToggleRow label="Auto-asleep latch" state={b("input_boolean.living_lights_asleep")}
                     onToggle={() => toggleBool("input_boolean.living_lights_asleep")}
                     disabled={!haOnline}
                     hint="Set by 15-min quiet overnight; cleared by sustained presence." />
          <ToggleRow label="Night-safe mode (homeai_sleep)" state={b("input_boolean.homeai_sleep")}
                     onToggle={() => toggleBool("input_boolean.homeai_sleep")}
                     disabled={!haOnline}
                     hint="Caps brightness at 8% house-wide." />
          <Slider label="Occupied ceiling while asleep" unit="%" min={0} max={100} step={5} defaultValue={30}
                  value={n("input_number.living_lights_asleep_cap_pct", 30)}
                  disabled={!haOnline}
                  onChange={(v) => setNum("input_number.living_lights_asleep_cap_pct", v)}
                  hint="Maximum brightness in occupied zones while asleep." />
          <CTInheritanceNote onJumpToToD={jumpToToD} />
        </CascadeCard>

        {/* Gaming */}
        <CascadeCard title="2. Gaming (Steam)"
          subtitle="Office goes off, LR dims to 3% when an actual game is running"
          intro="When the HA Steam integration sees an actual game running in your Steam status (not Big Picture / Wallet UI), the cascade routes the office to 0 % and the LR watch zones to the slider below. Asleep, away, manual override, and presence override still win over gaming. Steam offline mode hides the signal."
          winning={(b("input_boolean.living_lights_gaming_enabled") === "on") &&
                   getAttr(states, "sensor.steam_steam_76561198136331341", "game")}>
          <ToggleRow label="Master toggle" state={b("input_boolean.living_lights_gaming_enabled")}
                     onToggle={() => toggleBool("input_boolean.living_lights_gaming_enabled")}
                     disabled={!haOnline}
                     hint="Off = gaming detection ignored (suppresses dim during work)." />
          <Slider label="LR dim target (gaming)" unit="%" min={0} max={100} step={1} defaultValue={3}
                  value={n("input_number.living_lights_gaming_dim_pct", 3)}
                  disabled={!haOnline}
                  onChange={(v) => setNum("input_number.living_lights_gaming_dim_pct", v)}
                  hint="Sofa, front_left, weights, front_door dim to this when gaming." />
          <CTInheritanceNote onJumpToToD={jumpToToD} />
        </CascadeCard>

        {/* Working hours */}
        <CascadeCard title="3. Working hours"
          subtitle="Weekday 08-18 + woke_up latched — boosts the workday surface"
          intro="Mon-Fri between 08:00 and 18:00, after the morning wake-up latch fires, this raises the floor and present target everywhere so the desk stays bright even when Frigate momentarily misses you. The latch auto-arms on 2 min of sustained morning presence, resets at 04:00, and resets again after 10 min of sustained asleep."
          winning={b("binary_sensor.living_lights_working_hours_active") === "on"}>
          <ToggleRow label="Master toggle" state={b("input_boolean.living_lights_working_hours_enabled")}
                     onToggle={() => toggleBool("input_boolean.living_lights_working_hours_enabled")}
                     disabled={!haOnline}
                     hint="Off for vacation days. Default on." />
          <ToggleRow label="Woke up today (latch)" state={b("input_boolean.living_lights_woke_up_today")}
                     onToggle={() => toggleBool("input_boolean.living_lights_woke_up_today")}
                     disabled={!haOnline}
                     hint="Auto-set by sustained presence 05-12; reset 04:00 + on sustained asleep." />
          <Slider label="Vacant floor" unit="%" min={0} max={100} step={5} defaultValue={80}
                  value={n("input_number.living_lights_working_hours_floor_pct", 80)}
                  disabled={!haOnline}
                  onChange={(v) => setNum("input_number.living_lights_working_hours_floor_pct", v)}
                  hint="When Frigate misses you at the desk, this is the floor everywhere." />
          <Slider label="Present target" unit="%" min={0} max={100} step={5} defaultValue={95}
                  value={n("input_number.living_lights_working_hours_present_pct", 95)}
                  disabled={!haOnline}
                  onChange={(v) => setNum("input_number.living_lights_working_hours_present_pct", v)}
                  hint="When Frigate sees you, this is the ramp destination (cap-clamped)." />
          <CTInheritanceNote onJumpToToD={jumpToToD} />
        </CascadeCard>

        {/* Movie */}
        <CascadeCard title="4. Movie mode (TV playing)"
          subtitle="LG TV on → living-room watch zones dim to floor"
          intro={"When the LG TV reports on / playing / paused / buffering, every living-room camera zone is treated as a watch zone and dims to the floor below — regardless of who is where. Working hours wins over this so background news during work still gets the bright workday surface."}
          winning={["on", "playing", "paused", "buffering"].includes(b("media_player.lg_tv") || "")}>
          <Slider label="TV-on dim target" unit="%" min={0} max={100} step={1} defaultValue={8}
                  value={n("input_number.living_lights_movie_dim_pct", 8)}
                  disabled={!haOnline}
                  onChange={(v) => setNum("input_number.living_lights_movie_dim_pct", v)}
                  hint="Sofa + LR zones dim to this while the LG TV is on (working_hours wins)." />
          <CTInheritanceNote onJumpToToD={jumpToToD} />
        </CascadeCard>

        {/* Time of day — wrapped in a div with todSectionRef so the
            "tune ToD ↑" buttons on the per-modifier cards above can
            scroll the user here. */}
        <div ref={todSectionRef} />
        <CascadeCard title="5. Time of day"
          subtitle={`Current bucket: ${b("sensor.living_lights_profile") || "—"} · tod_factor ${toNum(getAttr(states, "sensor.living_lights_profile", "tod_factor"), 0).toFixed(2)} · tod_color ${toNum(getAttr(states, "sensor.living_lights_profile", "tod_color_warm"), 0)} K`}
          intro={"The day is split into six buckets (overnight, morning, midday, afternoon, evening, late evening). Each bucket has its own color-temperature anchor and a brightness multiplier. Drag a bucket’s CT slider warmer (lower K) or cooler (higher K). The brightness multipliers scale the existing ToD curve up or down for morning + midday — 1.0 means use the default curve."}>
          <div style={{ padding: "4px 16px 6px", fontFamily: FONT_MONO, fontSize: 10, color: "var(--hg-fg-2)", textTransform: "uppercase", letterSpacing: 0.5 }}>color temperature by bucket</div>
          <Slider label="Overnight (22:30–06:00)" unit=" K" min={1800} max={4500} step={50} defaultValue={2000}
                  value={n("input_number.living_lights_ct_overnight_k", 2000)}
                  disabled={!haOnline}
                  onChange={(v) => setCTAndPush("input_number.living_lights_ct_overnight_k", v)} />
          <Slider label="Morning (06:00–09:00)" unit=" K" min={1800} max={4500} step={50} defaultValue={2200}
                  value={n("input_number.living_lights_ct_morning_k", 2200)}
                  disabled={!haOnline}
                  onChange={(v) => setCTAndPush("input_number.living_lights_ct_morning_k", v)} />
          <Slider label="Midday (09:00–13:00)" unit=" K" min={1800} max={4500} step={50} defaultValue={2700}
                  value={n("input_number.living_lights_ct_midday_k", 2700)}
                  disabled={!haOnline}
                  onChange={(v) => setCTAndPush("input_number.living_lights_ct_midday_k", v)} />
          <Slider label="Afternoon (13:00–17:00)" unit=" K" min={1800} max={4500} step={50} defaultValue={3000}
                  value={n("input_number.living_lights_ct_afternoon_k", 3000)}
                  disabled={!haOnline}
                  onChange={(v) => setCTAndPush("input_number.living_lights_ct_afternoon_k", v)} />
          <Slider label="Evening (17:00–20:00)" unit=" K" min={1800} max={4500} step={50} defaultValue={2500}
                  value={n("input_number.living_lights_ct_evening_k", 2500)}
                  disabled={!haOnline}
                  onChange={(v) => setCTAndPush("input_number.living_lights_ct_evening_k", v)} />
          <Slider label="Late evening (20:00–22:30)" unit=" K" min={1800} max={4500} step={50} defaultValue={2200}
                  value={n("input_number.living_lights_ct_late_evening_k", 2200)}
                  disabled={!haOnline}
                  onChange={(v) => setCTAndPush("input_number.living_lights_ct_late_evening_k", v)} />
          <div style={{ padding: "4px 16px 6px", fontFamily: FONT_MONO, fontSize: 10, color: "var(--hg-fg-2)", textTransform: "uppercase", letterSpacing: 0.5 }}>brightness factor multipliers</div>
          <Slider label="Morning multiplier" min={0.5} max={1.5} step={0.05} defaultValue={1.0}
                  value={n("input_number.living_lights_tod_factor_morning_mult", 1.0)}
                  disabled={!haOnline}
                  onChange={(v) => setNum("input_number.living_lights_tod_factor_morning_mult", v)}
                  fmt={(v) => `×${Number(v).toFixed(2)}`}
                  hint=">1.0 = brighter morning, <1.0 = dimmer." />
          <Slider label="Midday multiplier" min={0.5} max={1.5} step={0.05} defaultValue={1.0}
                  value={n("input_number.living_lights_tod_factor_midday_mult", 1.0)}
                  disabled={!haOnline}
                  onChange={(v) => setNum("input_number.living_lights_tod_factor_midday_mult", v)}
                  fmt={(v) => `×${Number(v).toFixed(2)}`} />
        </CascadeCard>

        {/* Defaults */}
        <CascadeCard title="6. Defaults (vacant / present)"
          subtitle="Fallbacks when no modifier wins"
          intro="When none of the modifiers above are active (no working-hours boost, no TV, no gaming, no asleep), the cascade falls back to these baselines. Vacant brightness is the floor for empty rooms; present brightness is the target the confidence ramp climbs to when Frigate sees you.">
          <Slider label="Vacant baseline (day)" unit="%" min={0} max={100} step={5} defaultValue={50}
                  value={n("input_number.living_lights_vacant_day_pct", 50)}
                  disabled={!haOnline}
                  onChange={(v) => setNum("input_number.living_lights_vacant_day_pct", v)}
                  hint="Brightness in empty zones during morning–evening." />
          <Slider label="Vacant baseline (night)" unit="%" min={0} max={100} step={5} defaultValue={20}
                  value={n("input_number.living_lights_vacant_night_pct", 20)}
                  disabled={!haOnline}
                  onChange={(v) => setNum("input_number.living_lights_vacant_night_pct", v)}
                  hint="Brightness in empty zones late evening + overnight." />
          <Slider label="Present ramp target" unit="%" min={0} max={100} step={5} defaultValue={80}
                  value={n("input_number.living_lights_ramp_target_pct", 80)}
                  disabled={!haOnline}
                  onChange={(v) => setNum("input_number.living_lights_ramp_target_pct", v)}
                  hint="Default destination when no modifier overrides." />
          <Slider label="Present ramp initial" unit="%" min={0} max={100} step={5} defaultValue={50}
                  value={n("input_number.living_lights_ramp_initial_pct", 50)}
                  disabled={!haOnline}
                  onChange={(v) => setNum("input_number.living_lights_ramp_initial_pct", v)}
                  hint="Fast-reaction step before the slow ramp." />
        </CascadeCard>

        {/* ── Frozen layers — code-only knobs ─────────────────────
            These layers can't be tweaked from the UI because their knobs
            live in Python source or in the AL integration's YAML. The
            FrozenCard wraps each with a real explanation, a list of
            tunable constants, and an "Ask ChatGPT to change this" button
            that pre-fills a /ask-prefixed chat message containing full
            context + your intent. You review + send; that turn routes
            to the external provider, which can propose specific changes.
        */}

        <FrozenCard
          title="7. Anticipator"
          subtitle="Kinematic chain-of-zones predictor"
          intro={"Watches your motion across cameras and pre-warms the next zone you are likely to walk into so lights are already on by the time you arrive. Driven by speed, hysteresis (to avoid flicker), and chain-depth (how far ahead it predicts). Sticky decay keeps pre-warmed lights on for a few seconds after the prediction expires."}
          knobs={[
            { name: "SPEED_THRESHOLD",       current: "0.025 norm/s", desc: "below this = standing still" },
            { name: "VELOCITY_WINDOW_S",     current: "3.0 s",        desc: "history window for speed" },
            { name: "HYSTERESIS_BUFFER_N",   current: "5",            desc: "events in the voting window" },
            { name: "HYSTERESIS_MAJORITY",   current: "3",            desc: "votes needed to flip state" },
            { name: "MIN_HOLD_S",            current: "2.5 s",        desc: "minimum stickiness" },
            { name: "CHAIN_DEPTH",           current: "2",            desc: "zones ahead to predict (1 = next; 2 = next-after-next)" },
            { name: "ANTICIPATED_DECAY_S",   current: "12 s",         desc: "how long pre-warmed lights stay on after prediction ends" },
          ]}
          fileLocation="addons/predictive-lighting/anticipate.py"
          whyFrozen="Python container — rebuild required"
          placeholder={"e.g. make pre-warm trigger sooner when I am walking fast"}
          topicKey="anticipator"
          onAsk={ask}
        />

        <FrozenCard
          title="8. Pilot transitions"
          subtitle="Per-light ramp timings"
          intro={"Each light’s brightness ramp is two-stage: a fast initial reaction (RAMP_FAST_S) gets to a safe brightness right away, then a slow confidence ramp (RAMP_SLOW_S) climbs to the target while you settle in. Different state transitions use different transition seconds — going to vacant is a gentle ease-down, going to away is a quicker turn-off."}
          knobs={[
            { name: "RAMP_FAST_S",            current: "2.0 s",  desc: "floor → ramp_initial (fast reaction)" },
            { name: "RAMP_SLOW_S",            current: "10.0 s", desc: "ramp_initial → target (confidence ramp)" },
            { name: "VACANT_TRANSITION_S",    current: "6.0 s",  desc: "ease-down when a zone goes vacant" },
            { name: "AWAY_TRANSITION_S",      current: "2.0 s",  desc: "turn-off when nobody's home" },
            { name: "PASS_TRANSITION_S",      current: "0.3 s",  desc: "quick path-light for pass-through" },
            { name: "ANTICIPATED_TRANSITION_S", current: "1.5 s", desc: "anticipation ramp-in" },
          ]}
          fileLocation="tools/build-living-lights-actuators.py"
          whyFrozen="Generator constant — regen + SCP required"
          placeholder="e.g. slower ramp at night so lights ease in gently"
          topicKey="pilot_transitions"
          onAsk={ask}
        />

        <FrozenCard
          title="9. Adaptive Lighting"
          subtitle="House-wide CT curve (circadian)"
          intro={"Adaptive Lighting drives a circadian color-temperature curve across every bulb on a 90 s tick. We have disabled its brightness adaptation (Living Lights owns that) but kept the CT curve. The min/max bounds clamp the curve; the interval controls how often AL refreshes."}
          knobs={[
            { name: "min_color_temp", current: `${HA_MIN_CT} K`, desc: "warmest the curve can drive (overnight low)" },
            { name: "max_color_temp", current: `${HA_MAX_CT} K`, desc: "coolest the curve can drive (midday high)" },
            { name: "interval",       current: "90 s",           desc: "how often AL recomputes + pushes the curve" },
          ]}
          fileLocation="ha-config/packages/adaptive_lighting.yaml"
          whyFrozen={`HA restart required · AL currently targets ${toNum(getAttr(states, "switch.home_adaptive_lighting_home", "color_temp_kelvin"), null) ?? "—"} K`}
          placeholder="e.g. allow cooler max during workdays"
          topicKey="adaptive_lighting"
          onAsk={ask}
        />

        <div style={{ height: 24 }} />
      </div>

      {/* Footer */}
      <div style={{ padding: "10px 18px", borderTop: "1px solid var(--hg-border-soft)",
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                    background: "var(--hg-bg-0)", gap: 8 }}>
        <div style={{ display: "flex", gap: 6 }}>
        <button disabled={!haOnline} onClick={clearAllCooldowns}
          title="Clear the 15-min manual-override cooldown on every zone so pilots can fire immediately"
          style={{ background: "transparent", border: "1px solid var(--hg-border)", borderRadius: 4,
                   color: "var(--hg-fg-1)", padding: "5px 10px",
                   fontFamily: FONT_MONO, fontSize: 11, cursor: haOnline ? "pointer" : "not-allowed",
                   opacity: haOnline ? 1 : 0.45 }}>
          ✕ clear cooldowns
        </button>
        <button disabled={!haOnline} onClick={() => confirmOrRun("reset_all", () => {
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
            // Per-modifier CT overrides — UI was removed in this pass
            // (they fought AL on its 90-s cycle without proper detach
            // support, which is V2 work). Still reset them to 0 here so
            // any leftover values from earlier experiments are cleared.
            ["input_number.living_lights_asleep_ct_k", 0],
            ["input_number.living_lights_gaming_ct_k", 0],
            ["input_number.living_lights_working_hours_ct_k", 0],
            ["input_number.living_lights_movie_ct_k", 0],
          ];
          for (const [eid, v] of resets) setNum(eid, v);
          setText("input_text.living_lights_bias_zone_scope", "all");
        })}
          title="Reset every promoted constant + bias knob to defaults (~24 HA writes). First click arms; click again within 3 s to confirm."
          style={{ background: "transparent", border: "1px solid var(--hg-border)", borderRadius: 4,
                   color: "var(--hg-fg-1)", padding: "5px 10px",
                   fontFamily: FONT_MONO, fontSize: 11, cursor: haOnline ? "pointer" : "not-allowed",
                   opacity: haOnline ? 1 : 0.45 }}>
          {_lightsConfirmButtonLabel(confirmVerb, "reset_all", "✓ confirm reset", "↺ reset all…")}
        </button>
        </div>
        <span style={{ fontFamily: FONT_MONO, fontSize: 10, color: "var(--hg-fg-2)" }}>
          scope: {scope}
        </span>
      </div>
    </div>
  );
}

window.HomeLightsDrawer = HomeLightsDrawer;
window.LIGHTS_ZONES = LIGHTS_ZONES;
