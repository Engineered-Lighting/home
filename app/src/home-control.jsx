/* Home — inline post-action control cards.
 *
 * After a successful light/media action, the chat shows an interactive card
 * bound to the EXACT entities the command resolved — brightness/color-temp
 * sliders for lights, transport + per-zone volume for media. See the plan in
 * keen-doodling-parasol.md.
 *
 * Loaded via index.html as a babel-standalone <script type="text/babel">,
 * after home-metrics.jsx. No imports — uses globals from earlier scripts
 * (React, the live HA client at window.__hav_haClient, window.SimControlStore).
 *
 * Render-stability discipline (this feed re-renders on every HomeApp render):
 *   - onControlAction is a stable useCallback in HomeApp.
 *   - controlLifecycles is useMemo'd.
 *   - every timer below lives in a ref, cleared on unmount.
 *   - effects key only on stable values — never a freshly-created prop.
 */

/* ── Local design tokens (self-contained) ─────────────────────────────── */
const HC_MONO = "'Geist Mono', ui-monospace, monospace";
const HC_SANS = "'Geist', system-ui, sans-serif";

/* ── Constants ────────────────────────────────────────────────────────── */
// A control card stays "active" (interactive) for this long, then "expired".
const CONTROL_EXPIRY_MS = 8 * 60 * 1000;
const HC_TRAVEL_MODE_CACHE_KEY = "home.lights.travelMode.state.v1";
const HC_TRAVEL_MODE_EVENT = "home-travel-mode-change";

// Only these (domain, service) pairs get a rich control card. Everything
// else — light.turn_off, media_player.turn_off, scene.*, etc. — renders as
// the plain ActionContent row.
const LIGHT_CARD_SERVICES = new Set(["turn_on", "toggle"]);
const MEDIA_CARD_SERVICES = new Set([
  "play_media", "media_play", "media_pause", "media_play_pause",
  "media_next_track", "media_previous_track",
  "volume_set", "volume_mute", "select_source", "shuffle_set", "join",
]);

// Volume-routing: when a media_player's volume is physically driven by a
// different entity, map playbackEntity -> volumeControlEntity here. Transport
// + metadata stay on the playback entity; only the volume slider + mute follow
// the route. The living-room Sonos feeds an Onkyo TX-RZ30 AV receiver, so its
// volume is driven by the Onkyo, not the Sonos. Entries for unrelated entities
// are simply inert, so the real-HA and Simulation-Mode mappings coexist.
const VOLUME_ROUTING = {
  // Real Home Assistant entities.
  "media_player.living_room": "media_player.tx_rz30",
  // Simulation Mode mock entities (simulation-controls.jsx) — keeps the
  // media-group-control scenario exercising the same routing path.
  "media_player.living_room_sonos": "media_player.living_room_onkyo",
};

function clampNum(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

function readTravelModeLocked() {
  try {
    if (typeof window !== "undefined" && window.__HOME_TRAVEL_MODE_STATE === "on") return true;
    if (typeof localStorage !== "undefined") {
      return localStorage.getItem(HC_TRAVEL_MODE_CACHE_KEY) === "on";
    }
  } catch (_) {}
  return false;
}

function useTravelModeLock() {
  const [locked, setLocked] = React.useState(() => readTravelModeLocked());
  React.useEffect(() => {
    const refresh = () => setLocked(readTravelModeLocked());
    window.addEventListener?.(HC_TRAVEL_MODE_EVENT, refresh);
    window.addEventListener?.("storage", refresh);
    const id = setInterval(refresh, 1500);
    refresh();
    return () => {
      window.removeEventListener?.(HC_TRAVEL_MODE_EVENT, refresh);
      window.removeEventListener?.("storage", refresh);
      clearInterval(id);
    };
  }, []);
  return locked;
}

/* ── humanize: entity_id / area slug → "Living Room" ─────────────────── */
function humanize(s) {
  if (!s) return "";
  const tail = String(s).includes(".") ? String(s).split(".").pop() : String(s);
  return tail.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/* ── distillSetValues: normalize a light action's service_data into the
 *    initial slider positions (used until live get_states resolves) ────── */
function distillSetValues(domain, attrs) {
  const out = {};
  attrs = attrs || {};
  if (domain === "light") {
    if (attrs.brightness_pct != null) out.brightness_pct = clampNum(Math.round(attrs.brightness_pct), 0, 100);
    else if (attrs.brightness != null) out.brightness_pct = clampNum(Math.round(attrs.brightness / 255 * 100), 0, 100);
    if (attrs.color_temp_kelvin != null) out.color_temp_kelvin = Math.round(attrs.color_temp_kelvin);
    else if (attrs.kelvin != null) out.color_temp_kelvin = Math.round(attrs.kelvin);
    else if (attrs.color_temp != null) out.color_temp_kelvin = Math.round(1e6 / attrs.color_temp);
  }
  return out;
}

/* ── buildActionContext ───────────────────────────────────────────────────
 * Derive the structured control context from a completed action. Called by
 * the SSE handler in home-app.jsx at action-event creation. The per-kind
 * target arrays come pre-separated from the handler (it no longer flattens
 * entity/area/device into one set — see plan Adversarial #2). */
function buildActionContext(args) {
  const { id, service, attrs, status } = args;
  const entityTargets = args.entityTargets || [];
  const areaTargets = args.areaTargets || [];
  const deviceTargets = args.deviceTargets || [];
  const parts = String(service || "").split(".");
  const domain = parts[0] || "";
  const svc = parts[1] || "";

  const kinds = [
    entityTargets.length ? "entity" : null,
    areaTargets.length ? "area" : null,
    deviceTargets.length ? "device" : null,
  ].filter(Boolean);
  const targetKind = kinds.length === 0 ? "none" : (kinds.length > 1 ? "mixed" : kinds[0]);

  const controllable =
    (domain === "light" && LIGHT_CARD_SERVICES.has(svc)) ||
    (domain === "media_player" && MEDIA_CARD_SERVICES.has(svc));

  const nTargets = entityTargets.length + areaTargets.length + deviceTargets.length;
  const firstTarget = entityTargets[0] || areaTargets[0] || deviceTargets[0] || "";
  const now = Date.now();

  return {
    id,
    domain,
    service: service || "",
    targetEntities: entityTargets,
    targetAreas: areaTargets,
    targetDevices: deviceTargets,
    targetKind,
    targetLabel: null,                 // filled lazily by the card
    actionSummary: `${service || "action"}${firstTarget ? " · " + humanize(firstTarget) : ""}${nTargets > 1 ? ` +${nTargets - 1}` : ""}`,
    status: status || "success",
    error: null,
    startedAt: now,
    completedAt: now,
    setValues: distillSetValues(domain, attrs),
    capabilities: null,                // filled lazily by the card
    controllable,
  };
}

/* ── ctxToHaTarget ────────────────────────────────────────────────────────
 * Build an HA service-call `target` object for a WHOLE-context operation
 * (light sliders → all light entities; media transport → all media
 * entities). Per-zone volume builds its own single-entity target instead. */
function ctxToHaTarget(ctx) {
  if (!ctx) return null;
  const t = {};
  if (ctx.targetEntities && ctx.targetEntities.length) t.entity_id = ctx.targetEntities;
  if (ctx.targetAreas && ctx.targetAreas.length) t.area_id = ctx.targetAreas;
  if (ctx.targetDevices && ctx.targetDevices.length) t.device_id = ctx.targetDevices;
  return Object.keys(t).length ? t : null;
}

/* ── deriveControlLifecycles ──────────────────────────────────────────────
 * Pure: derive each controllable action event's lifecycle state from the
 * events list + the clock. The caller MUST useMemo this (render-stability).
 *   active      newest of its domain, age <= CONTROL_EXPIRY_MS
 *   superseded  a newer same-domain card exists
 *   expired     newest of its domain but older than CONTROL_EXPIRY_MS
 *   failed      the underlying action failed
 * "unavailable" is decided by the card at runtime from live state. */
function deriveControlLifecycles(events, now) {
  now = now || Date.now();
  const out = new Map();
  const cards = (events || []).filter((e) => e && e.kind === "action" && e.controllable && e.actionCtx);
  const newestByDomain = new Map();
  for (const e of cards) newestByDomain.set(e.actionCtx.domain, e.id); // last wins
  for (const e of cards) {
    const ctx = e.actionCtx;
    let lc;
    if (ctx.status === "failed") {
      lc = "failed";
    } else if (newestByDomain.get(ctx.domain) !== e.id) {
      lc = "superseded";
    } else {
      const age = now - (ctx.completedAt || ctx.startedAt || now);
      lc = age > CONTROL_EXPIRY_MS ? "expired" : "active";
    }
    out.set(e.id, lc);
  }
  return out;
}

/* ── Shared get_states cache ──────────────────────────────────────────────
 * Multiple cards mounting in one conversation shouldn't each fetch the whole
 * HA state list. ~2.5s TTL. REAL-HA ONLY — sim-mode cards read
 * window.SimControlStore directly. */
let _statesCache = { at: 0, promise: null };
function fetchStatesCached(maxAgeMs) {
  maxAgeMs = maxAgeMs || 2500;
  const t = Date.now();
  if (_statesCache.promise && (t - _statesCache.at) < maxAgeMs) return _statesCache.promise;
  const client = window.__hav_haClient;
  if (!client || typeof client.call !== "function") return Promise.resolve([]);
  const p = client.call({ type: "get_states" }).then(
    (r) => (Array.isArray(r) ? r : []),
    () => []
  );
  _statesCache = { at: t, promise: p };
  return p;
}
function invalidateStatesCache() { _statesCache = { at: 0, promise: null }; }

/* ── readEntityStates ─────────────────────────────────────────────────────
 * Resolve a list of entity_ids to their HA state objects, from either the
 * sim store or the real-HA get_states cache. Returns a Map<entity_id,state>. */
async function readEntityStates(entityIds) {
  const ids = entityIds || [];
  if (window.__SIM_ACTIVE && window.SimControlStore) {
    const all = window.SimControlStore.getAll();
    const m = new Map();
    for (const id of ids) if (all[id]) m.set(id, all[id]);
    return m;
  }
  const states = await fetchStatesCached();
  const m = new Map();
  for (const s of states) if (s && ids.includes(s.entity_id)) m.set(s.entity_id, s);
  return m;
}

/* ──────────────────────────────────────────────────────────────────────────
 * Components
 * ────────────────────────────────────────────────────────────────────────── */

/* ── ControlSlider ────────────────────────────────────────────────────────
 * A styled native <input type="range"> shared by both cards. The parent owns
 * `value` (optimistic). Behaviour:
 *   - onDrag(v)  — fires on every continuous change → parent updates `value`
 *                  optimistically; a ~120ms ref-held debounce commits during
 *                  a long drag-with-pauses.
 *   - onCommit(v)— the debounce, OR an immediate flush on pointer-up / key-up /
 *                  blur (so the final value always lands).
 * The debounce timer lives in a ref and is cleared on unmount (render-stability). */
function ControlSlider({ kind, label, sub, value, min, max, step, unit, disabled, mixed, applying, onDrag, onCommit }) {
  const debRef = React.useRef(null);
  React.useEffect(() => () => { if (debRef.current) { clearTimeout(debRef.current); debRef.current = null; } }, []);
  React.useEffect(() => {
    if (!disabled || !debRef.current) return;
    clearTimeout(debRef.current);
    debRef.current = null;
  }, [disabled]);

  const handleChange = (e) => {
    if (disabled) return;
    const v = Number(e.target.value);
    if (onDrag) onDrag(v);
    if (debRef.current) clearTimeout(debRef.current);
    debRef.current = setTimeout(() => {
      debRef.current = null;
      if (onCommit) onCommit(v);
    }, 120);
  };
  const flush = (e) => {
    if (disabled) return;
    if (debRef.current) { clearTimeout(debRef.current); debRef.current = null; }
    if (onCommit) onCommit(Number(e.target.value));
  };

  const cls = "hg-slider"
    + (kind === "colorTemp" ? " hg-slider--temp" : "")
    + (applying ? " hg-slider--applying" : "");
  const readout = disabled
    ? "unsupported"
    : (mixed ? "~" : "") + (value == null ? "—" : Math.round(value)) + (unit || "");

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(56px, 88px) 1fr auto", columnGap: 10, alignItems: "center" }}>
      <span style={{ minWidth: 0 }}>
        <span style={{ fontFamily: HC_MONO, fontSize: 10, color: "var(--hg-fg-3)", whiteSpace: "nowrap" }}>{label}</span>
        {sub && <span style={{ display: "block", fontFamily: HC_MONO, fontSize: 8.5, color: "var(--hg-fg-5)", letterSpacing: "0.04em", whiteSpace: "nowrap" }}>{sub}</span>}
      </span>
      <input
        type="range"
        className={cls}
        min={min} max={max} step={step || 1}
        value={value == null ? min : value}
        disabled={disabled}
        onChange={handleChange}
        onPointerUp={flush}
        onKeyUp={flush}
        onBlur={flush}
        aria-label={label}
      />
      <span style={{
        fontFamily: HC_MONO, fontSize: 10.5,
        color: disabled ? "var(--hg-fg-4)" : "var(--hg-fg-1)",
        fontVariantNumeric: "tabular-nums", minWidth: 48, textAlign: "right", whiteSpace: "nowrap",
      }}>{readout}</span>
    </div>
  );
}

/* ── fireServiceCall ──────────────────────────────────────────────────────
 * The single sim-vs-real branch point. In Simulation Mode the card writes to
 * SimControlStore (synchronous mock); otherwise it calls onControlAction —
 * HomeApp's thin HA executor. `target` is the real-HA target object; the
 * `simTargets` entity_id array is what the mock store applies to. */
function fireServiceCall(opts) {
  const { domain, service, service_data, target, simTargets, onControlAction } = opts;
  if (window.__SIM_ACTIVE && window.SimControlStore) {
    return window.SimControlStore.callService({
      domain, service, service_data: service_data || {}, targets: simTargets || [],
    });
  }
  if (typeof onControlAction !== "function") return Promise.reject(new Error("no control handler"));
  return onControlAction({ domain, service, service_data: service_data || {}, target });
}

/* ── ControlCardShell ─────────────────────────────────────────────────────
 * The glassy card surface shared by both control cards. `muted` dims a
 * read-only (superseded/expired/failed/unavailable) card. */
function ControlCardShell({ children, muted, accent, locked }) {
  return (
    <div className="hg-fade" aria-disabled={locked ? "true" : undefined} style={{
      position: "relative",
      margin: "7px 0",
      background: "var(--hg-bg-1)",
      border: `1px solid ${accent && !muted ? "var(--hg-border)" : "var(--hg-border-soft)"}`,
      borderRadius: 8,
      padding: "11px 13px",
      opacity: muted ? 0.55 : 1,
    }}>
      {children}
      {locked && (
        <div
          aria-hidden="true"
          style={{
            position: "absolute",
            inset: 0,
            borderRadius: 8,
            pointerEvents: "none",
            background: "linear-gradient(180deg, rgba(245,158,11,0.035), rgba(0,0,0,0.035))",
          }}
        />
      )}
    </div>
  );
}

/* ── StatusChip — dot + word, top-right of an active card header ───────── */
function StatusChip({ status }) {
  const map = {
    idle:     { tone: "success", word: "ready" },
    locked:   { tone: "warn",    word: "travel mode" },
    applying: { tone: "pending", word: "applying" },
    updated:  { tone: "success", word: "updated" },
    error:    { tone: "error",   word: "failed" },
  };
  const s = map[status] || map.idle;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
      <window.StatusDot tone={s.tone} size={5} />
      <span style={{ fontFamily: HC_MONO, fontSize: 8.5, color: "var(--hg-fg-4)", letterSpacing: "0.1em", textTransform: "uppercase" }}>{s.word}</span>
    </span>
  );
}

/* ── QuickChip — a small preset button ──────────────────────────────────── */
function QuickChip({ label, onClick, disabled }) {
  return (
    <button
      disabled={disabled}
      onClick={disabled ? undefined : onClick}
      className="hg-focusable"
      style={{
        background: "transparent",
        border: "1px solid var(--hg-border)",
        color: disabled ? "var(--hg-fg-5)" : "var(--hg-fg-2)",
        padding: "3px 9px", borderRadius: 4,
        fontFamily: HC_MONO, fontSize: 9.5, letterSpacing: "0.06em",
        cursor: disabled ? "default" : "pointer",
      }}
    >{label}</button>
  );
}

/* ── MutedHeader — collapsed read-only variant header (both card types) ── */
function MutedHeader({ icon, label, word }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      {icon}
      <span style={{ fontFamily: HC_MONO, fontSize: 11, color: "var(--hg-fg-3)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
      <span style={{ fontFamily: HC_MONO, fontSize: 8.5, color: "var(--hg-fg-5)", letterSpacing: "0.1em", textTransform: "uppercase", whiteSpace: "nowrap" }}>{word}</span>
    </div>
  );
}

const LIFECYCLE_WORD = {
  superseded: "superseded",
  expired: "controls inactive",
  failed: "action failed",
  unavailable: "unavailable",
};

/* ── buildLightCaps: HA light states → normalized capabilities + rollups ── */
function buildLightCaps(statesMap, entityIds) {
  const BRIGHT_MODES = ["brightness", "color_temp", "hs", "rgb", "rgbw", "rgbww", "xy"];
  const entities = [];
  for (const id of entityIds || []) {
    const s = statesMap.get(id);
    if (!s) { entities.push({ entity_id: id, available: false, missing: true }); continue; }
    const a = s.attributes || {};
    const modes = a.supported_color_modes || [];
    entities.push({
      entity_id: id,
      available: !["unavailable", "unknown"].includes(s.state),
      on: s.state === "on",
      friendlyName: a.friendly_name || humanize(id),
      supportsBrightness: modes.some((m) => BRIGHT_MODES.includes(m)) || a.brightness != null,
      supportsColorTemp: modes.includes("color_temp") || a.color_temp_kelvin != null || a.min_color_temp_kelvin != null,
      minKelvin: a.min_color_temp_kelvin || 2000,
      maxKelvin: a.max_color_temp_kelvin || 6500,
      brightnessPct: a.brightness != null ? clampNum(Math.round(a.brightness / 255 * 100), 0, 100) : null,
      colorTempKelvin: a.color_temp_kelvin != null ? Math.round(a.color_temp_kelvin)
        : (a.color_temp ? Math.round(1e6 / a.color_temp) : null),
    });
  }
  const avail = entities.filter((e) => e.available);
  const briVals = avail.map((e) => e.brightnessPct).filter((v) => v != null);
  const kelVals = avail.map((e) => e.colorTempKelvin).filter((v) => v != null);
  const avg = (arr) => (arr.length ? Math.round(arr.reduce((a, b) => a + b, 0) / arr.length) : null);
  const ct = avail.filter((e) => e.supportsColorTemp);
  return {
    resolvedAt: Date.now(),
    entities,
    anyAvailable: avail.length > 0,
    anyBrightness: avail.some((e) => e.supportsBrightness),
    anyColorTemp: avail.some((e) => e.supportsColorTemp),
    minKelvin: ct.length ? Math.max.apply(null, ct.map((e) => e.minKelvin)) : 2000,
    maxKelvin: ct.length ? Math.min.apply(null, ct.map((e) => e.maxKelvin)) : 6500,
    avgBrightnessPct: avg(briVals),
    avgColorTempKelvin: avg(kelVals),
    mixedBrightness: briVals.length > 1 && (Math.max.apply(null, briVals) - Math.min.apply(null, briVals) > 5),
    mixedColorTemp: kelVals.length > 1 && (Math.max.apply(null, kelVals) - Math.min.apply(null, kelVals) > 100),
  };
}

// Synthetic optimistic caps for area/device targets (no entity_ids to read).
const AREA_CAPS = {
  entities: [], anyAvailable: true, anyBrightness: true, anyColorTemp: true,
  minKelvin: 2000, maxKelvin: 6500, avgBrightnessPct: null, avgColorTempKelvin: null,
  mixedBrightness: false, mixedColorTemp: false, synthetic: true,
};

/* ── LightControlCard ────────────────────────────────────────────────────
 * Inline card after a light.turn_on / light.toggle action — bound to the
 * exact entities the command resolved. */
function LightControlCard({ ctx, lifecycle, onControlAction }) {
  const hasEntities = ctx.targetEntities && ctx.targetEntities.length > 0;
  const travelModeLocked = useTravelModeLock();
  const [caps, setCaps] = React.useState(hasEntities ? (ctx.capabilities || null) : AREA_CAPS);
  const [bri, setBri] = React.useState(ctx.setValues ? (ctx.setValues.brightness_pct != null ? ctx.setValues.brightness_pct : null) : null);
  const [kel, setKel] = React.useState(ctx.setValues ? (ctx.setValues.color_temp_kelvin != null ? ctx.setValues.color_temp_kelvin : null) : null);
  const [status, setStatus] = React.useState("idle");
  const [statusMsg, setStatusMsg] = React.useState("");
  const reconcileRef = React.useRef(null);
  const fadeRef = React.useRef(null);
  const mountFetchedRef = React.useRef(false);

  const lifeActive = lifecycle === "active" && ctx.status !== "pending";

  // Clear all timers on unmount (render-stability).
  React.useEffect(() => () => {
    if (reconcileRef.current) clearTimeout(reconcileRef.current);
    if (fadeRef.current) clearTimeout(fadeRef.current);
  }, []);

  // Mount fetch — ALWAYS pull live state when a card with entity targets
  // becomes active, even if ctx.capabilities (pre-action rollup) was passed
  // in. The pre-passed caps reflect state AT THE TIME the agent decided to
  // act, not after the bulb reports back. Without a fresh fetch, the slider
  // sticks at ctx.setValues (the agent's TARGET) forever if state_changed
  // events race with the subscription attaching below.
  // Invalidate the shared get_states cache first so the slider doesn't
  // initialize from a pre-action snapshot (the 2.5 s cache otherwise
  // serves stale state when a card mounts right after the agent acts).
  React.useEffect(() => {
    if (!lifeActive || !hasEntities) return undefined;
    if (mountFetchedRef.current) return undefined;
    mountFetchedRef.current = true;
    let cancelled = false;
    (async () => {
      if (!window.__SIM_ACTIVE) invalidateStatesCache();
      const sm = await readEntityStates(ctx.targetEntities);
      if (cancelled) return;
      const c = buildLightCaps(sm, ctx.targetEntities);
      setCaps(c);
      if (c.avgBrightnessPct != null) setBri(c.avgBrightnessPct);
      if (c.avgColorTempKelvin != null) setKel(c.avgColorTempKelvin);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lifeActive]);

  // Subscribe to state_changed for the card's target entities so the
  // slider tracks live state after mount — agent-driven changes, manual
  // changes from elsewhere, and the card's own commits all flow through
  // the same reconcile() debounce. Without this, the card only refreshes
  // when the user drags a slider, and agent-set values from voice
  // commands are never reflected.
  React.useEffect(() => {
    if (!lifeActive || !hasEntities) return undefined;
    const client = window.__hav_haClient;
    if (!client || typeof client.subscribeEvents !== "function") return undefined;
    const targets = new Set(ctx.targetEntities || []);
    if (targets.size === 0) return undefined;
    let unsub;
    try {
      unsub = client.subscribeEvents("state_changed", (ev) => {
        const eid = ev?.data?.entity_id;
        if (eid && targets.has(eid)) reconcile();
      });
    } catch {
      return undefined;
    }
    return () => { try { unsub && unsub(); } catch {} };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lifeActive, hasEntities]);

  const fadeToReady = () => {
    if (fadeRef.current) clearTimeout(fadeRef.current);
    fadeRef.current = setTimeout(() => { setStatus("idle"); setStatusMsg(""); }, 2200);
  };
  const reconcile = () => {
    if (!hasEntities) return;
    if (reconcileRef.current) clearTimeout(reconcileRef.current);
    reconcileRef.current = setTimeout(async () => {
      invalidateStatesCache();
      const sm = await readEntityStates(ctx.targetEntities);
      const c = buildLightCaps(sm, ctx.targetEntities);
      setCaps(c);
      if (c.avgBrightnessPct != null) setBri(c.avgBrightnessPct);
      if (c.avgColorTempKelvin != null) setKel(c.avgColorTempKelvin);
    }, 700);
  };
  const commit = async (service_data, label) => {
    if (travelModeLocked) {
      setStatus("locked");
      setStatusMsg("travel mode enabled");
      return;
    }
    setStatus("applying"); setStatusMsg("applying…");
    try {
      await fireServiceCall({
        domain: "light", service: "turn_on", service_data,
        target: ctxToHaTarget(ctx), simTargets: ctx.targetEntities, onControlAction,
      });
      setStatus("updated"); setStatusMsg(label || "updated");
      reconcile(); fadeToReady();
    } catch (e) {
      setStatus("error"); setStatusMsg((e && e.message) || "call failed");
    }
  };

  // Target label — friendly names, never raw entity IDs.
  const label = (caps && caps.entities && caps.entities.length === 1 && caps.entities[0].friendlyName)
    ? caps.entities[0].friendlyName
    : ctx.targetEntities.length === 1 ? humanize(ctx.targetEntities[0])
    : ctx.targetEntities.length > 1 ? `${ctx.targetEntities.length} Lights`
    : ctx.targetAreas.length ? `${humanize(ctx.targetAreas[0])} Lights`
    : "Lights";

  // Effective lifecycle: downgrade to "unavailable" once caps prove it.
  const effLifecycle = (caps && !caps.synthetic && !caps.anyAvailable && hasEntities) ? "unavailable" : lifecycle;

  if (effLifecycle !== "active" || ctx.status === "pending") {
    return (
      <ControlCardShell muted>
        <MutedHeader
          icon={<window.IconBulb size={13} stroke="var(--hg-fg-4)" />}
          label={label}
          word={ctx.status === "pending" ? "applying…" : (LIFECYCLE_WORD[effLifecycle] || effLifecycle)}
        />
      </ControlCardShell>
    );
  }

  const briReady = !!caps;
  const ctMin = caps ? caps.minKelvin : 2000;
  const ctMax = caps ? caps.maxKelvin : 6500;
  const colorDisabled = travelModeLocked || !caps || !caps.anyColorTemp;
  const briDisabled = travelModeLocked || !briReady || (caps && !caps.anyBrightness);

  const setKelChip = (v) => {
    if (travelModeLocked) return;
    const cv = clampNum(v, ctMin, ctMax);
    setKel(cv); commit({ color_temp_kelvin: cv }, `${cv} K`);
  };
  const setBriChip = (v) => {
    if (travelModeLocked) return;
    setBri(v); commit({ brightness_pct: v }, `${v}%`);
  };

  return (
    <ControlCardShell accent muted={travelModeLocked} locked={travelModeLocked}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 9 }}>
        <window.IconBulb size={13} stroke="var(--hg-ice-bright)" />
        <span style={{ fontFamily: HC_SANS, fontSize: 12.5, fontWeight: 500, color: "var(--hg-fg-0)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
        <StatusChip status={travelModeLocked ? "locked" : status} />
      </div>

      {travelModeLocked && (
        <div style={{
          marginBottom: 9,
          padding: "6px 8px",
          border: "1px solid rgba(245, 158, 11, 0.35)",
          borderRadius: 5,
          background: "rgba(245, 158, 11, 0.08)",
          color: "var(--hg-warn)",
          fontFamily: HC_MONO,
          fontSize: 9.5,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
        }}>
          travel mode enabled - controls locked
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <ControlSlider
          kind="brightness" label="brightness"
          value={bri} min={1} max={100} step={1} unit="%"
          disabled={briDisabled}
          mixed={caps && caps.mixedBrightness}
          applying={status === "applying"}
          onDrag={setBri}
          onCommit={(v) => commit({ brightness_pct: v }, `${v}%`)}
        />
        <ControlSlider
          kind="colorTemp" label="color temp"
          sub={colorDisabled ? "no color temp" : (caps && caps.mixedColorTemp ? "mixed" : null)}
          value={kel} min={ctMin} max={ctMax} step={50} unit=" K"
          disabled={colorDisabled}
          mixed={caps && caps.mixedColorTemp}
          applying={status === "applying"}
          onDrag={setKel}
          onCommit={(v) => commit({ color_temp_kelvin: v }, `${v} K`)}
        />
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
        <QuickChip label="warm" disabled={colorDisabled} onClick={() => setKelChip(2700)} />
        <QuickChip label="neutral" disabled={colorDisabled} onClick={() => setKelChip(4000)} />
        <QuickChip label="cool" disabled={colorDisabled} onClick={() => setKelChip(5500)} />
        <span style={{ width: 1, alignSelf: "stretch", background: "var(--hg-border-soft)", margin: "0 2px" }} />
        <QuickChip label="dim" disabled={briDisabled} onClick={() => setBriChip(20)} />
        <QuickChip label="bright" disabled={briDisabled} onClick={() => setBriChip(100)} />
      </div>

      {statusMsg && (
        <div style={{ marginTop: 8, fontFamily: HC_MONO, fontSize: 9.5, color: status === "error" ? "var(--hg-warn)" : "var(--hg-fg-4)" }}>
          {statusMsg}
          {status === "error" && (
            <button
              onClick={() => { setStatus("idle"); setStatusMsg(""); }}
              className="hg-focusable"
              style={{ marginLeft: 8, background: "transparent", border: "1px solid var(--hg-border)", color: "var(--hg-fg-3)", padding: "1px 7px", borderRadius: 3, fontFamily: HC_MONO, fontSize: 9, cursor: "pointer", textTransform: "uppercase", letterSpacing: "0.08em" }}
            >dismiss</button>
          )}
        </div>
      )}
    </ControlCardShell>
  );
}

/* ── Media helpers ────────────────────────────────────────────────────── */
// media_player supported_features bitmask (HA constants).
const MEDIA_SF = { VOLUME_SET: 4, VOLUME_MUTE: 8, PREV: 16, NEXT: 32, GROUPING: 524288 };

function readMediaState(liveStates, entityId) {
  const s = liveStates && liveStates.get(entityId);
  if (!s) return { entity_id: entityId, available: false, missing: true, caps: {}, groupMembers: [] };
  const a = s.attributes || {};
  const sf = a.supported_features || 0;
  return {
    entity_id: entityId,
    available: !["unavailable", "unknown"].includes(s.state),
    state: s.state,
    playing: s.state === "playing",
    title: a.media_title || null,
    artist: a.media_artist || null,
    source: a.app_name || a.source || null,
    art: a.entity_picture || null,
    volume: a.volume_level != null ? a.volume_level : null,   // 0..1
    muted: !!a.is_volume_muted,
    groupMembers: Array.isArray(a.group_members) ? a.group_members : [],
    friendlyName: a.friendly_name || humanize(entityId),
    caps: {
      next: (sf & MEDIA_SF.NEXT) !== 0,
      prev: (sf & MEDIA_SF.PREV) !== 0,
      volume: (sf & MEDIA_SF.VOLUME_SET) !== 0,
      mute: (sf & MEDIA_SF.VOLUME_MUTE) !== 0,
      group: (sf & MEDIA_SF.GROUPING) !== 0,
    },
  };
}

function prettyRoom(entityId, liveStates) {
  const s = liveStates && liveStates.get(entityId);
  const fn = s && s.attributes && s.attributes.friendly_name;
  if (fn) return fn.replace(/\s*(sonos|speaker)\s*$/i, "").trim() || fn;
  const h = humanize(entityId);
  return h.replace(/\s*(Sonos|Speaker)$/i, "").trim() || h;
}

function mediaTitleOf(liveStates, entityIds) {
  const ids = entityIds || [];
  if (ids.length === 0) return "Media";
  if (ids.length === 1) {
    const s = liveStates && liveStates.get(ids[0]);
    return (s && s.attributes && s.attributes.friendly_name) || humanize(ids[0]);
  }
  const names = ids.slice(0, 3).map((id) => prettyRoom(id, liveStates));
  return names.join(" + ") + (ids.length > 3 ? ` +${ids.length - 3}` : "");
}

// Album art URL. Absolute → as-is. Relative (HA-signed) → prepend the HA
// endpoint. Sim mode / absent → null (placeholder tile).
function resolveArtUrl(art) {
  if (!art || window.__SIM_ACTIVE) return null;
  if (/^(https?:|data:)/i.test(art)) return art;
  const ep = window.__hav_haEndpoint;
  if (art[0] === "/" && ep) return ep.replace(/\/+$/, "") + art;
  return null;
}

/* ── Media sub-components ─────────────────────────────────────────────── */
function TransportBtn({ children, onClick, primary, disabled }) {
  const sz = primary ? 38 : 30;
  return (
    <button
      disabled={disabled}
      onClick={disabled ? undefined : onClick}
      className="hg-focusable"
      style={{
        width: sz, height: sz, borderRadius: "50%", padding: 0,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        background: primary ? "var(--hg-bg-2)" : "transparent",
        border: `1px solid ${primary ? "var(--hg-border)" : "var(--hg-border-soft)"}`,
        color: disabled ? "var(--hg-fg-5)" : (primary ? "var(--hg-fg-0)" : "var(--hg-fg-2)"),
        cursor: disabled ? "default" : "pointer",
      }}
    >{children}</button>
  );
}

function MuteBtn({ muted, onClick, disabled }) {
  return (
    <button
      disabled={disabled}
      onClick={disabled ? undefined : onClick}
      className="hg-focusable"
      title={muted ? "unmute" : "mute"}
      style={{
        background: "transparent",
        border: "1px solid " + (muted ? "var(--hg-warn)" : "var(--hg-border-soft)"),
        color: disabled ? "var(--hg-fg-5)" : (muted ? "var(--hg-warn)" : "var(--hg-fg-3)"),
        borderRadius: 4, padding: "3px 6px", lineHeight: 0,
        cursor: disabled ? "default" : "pointer",
        display: "inline-flex", alignItems: "center",
      }}
    ><window.IconVolume size={12} stroke="currentColor" /></button>
  );
}

function SpeakerChip({ label, on, disabled, onClick }) {
  return (
    <button
      disabled={disabled}
      onClick={disabled ? undefined : onClick}
      className="hg-focusable"
      style={{
        display: "inline-flex", alignItems: "center", gap: 5,
        background: "transparent",
        border: "1px solid " + (on ? "var(--hg-ice)" : "var(--hg-border)"),
        color: disabled ? "var(--hg-fg-5)" : (on ? "var(--hg-ice-bright)" : "var(--hg-fg-3)"),
        borderRadius: 4, padding: "3px 8px", cursor: disabled ? "default" : "pointer",
        fontFamily: HC_MONO, fontSize: 9.5,
      }}
    >
      <span style={{
        width: 5, height: 5, borderRadius: "50%",
        background: on ? "var(--hg-ice-bright)" : "transparent",
        border: on ? "none" : "1px solid var(--hg-fg-4)",
      }} />
      {label}
    </button>
  );
}

/* ── MediaControlCard ────────────────────────────────────────────────────
 * Inline card after a media_player playback/volume action. Transport acts on
 * all targeted speakers (Sonos groups play in sync); volume is PER ZONE — one
 * slider per speaker — and follows VOLUME_ROUTING (the living-room zone's
 * volume is driven by the Onkyo AV receiver, not the Sonos). */
function MediaControlCard({ ctx, lifecycle, onControlAction }) {
  const playbackEntities = ctx.targetEntities || [];
  const volEntityFor = (id) => VOLUME_ROUTING[id] || id;
  const allEntities = React.useMemo(() => {
    const set = new Set();
    for (const id of playbackEntities) { set.add(id); set.add(volEntityFor(id)); }
    return Array.from(set);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx.id]);

  const [liveStates, setLiveStates] = React.useState(() => new Map());
  const [volumes, setVolumes] = React.useState(() => ({}));        // {volEntity: pct}
  const [optimisticState, setOptimisticState] = React.useState(null);  // play/pause overlay
  const [status, setStatus] = React.useState("idle");
  const [statusMsg, setStatusMsg] = React.useState("");
  const reconcileRef = React.useRef(null);
  const fadeRef = React.useRef(null);

  const lifeActive = lifecycle === "active" && ctx.status !== "pending";

  React.useEffect(() => () => {
    if (reconcileRef.current) clearTimeout(reconcileRef.current);
    if (fadeRef.current) clearTimeout(fadeRef.current);
  }, []);

  // Re-read live state + re-seed the per-zone volume sliders from the routed
  // entities. Used on mount, on sim-store change, and after a commit.
  const refreshStates = React.useCallback(async () => {
    // In sim mode, read the WHOLE mock store so group-chip rosters (which can
    // include speakers outside `allEntities`) resolve correctly.
    let sm;
    if (window.__SIM_ACTIVE && window.SimControlStore) {
      sm = new Map(Object.entries(window.SimControlStore.getAll()));
    } else {
      sm = await readEntityStates(allEntities);
    }
    setLiveStates(sm);
    setVolumes((prev) => {
      const next = { ...prev };
      for (const id of playbackEntities) {
        const ve = volEntityFor(id);
        const vs = sm.get(ve);
        if (vs && vs.attributes && vs.attributes.volume_level != null) {
          next[ve] = Math.round(vs.attributes.volume_level * 100);
        } else if (next[ve] == null) {
          next[ve] = 0;
        }
      }
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx.id]);

  // Mount: only active cards fetch. Sim mode also subscribes for live updates.
  // Invalidate the shared get_states cache first so the slider doesn't
  // initialize from a pre-action snapshot (the 2.5 s cache otherwise
  // serves stale volume/state when a card mounts right after the agent acts).
  React.useEffect(() => {
    if (!lifeActive) return undefined;
    if (!window.__SIM_ACTIVE) invalidateStatesCache();
    refreshStates();
    if (window.__SIM_ACTIVE && window.SimControlStore) {
      return window.SimControlStore.subscribe(() => refreshStates());
    }
    return undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lifeActive]);

  // Subscribe to state_changed for the card's playback + routed-volume
  // entities so the volume slider tracks live state after mount — agent
  // changes, manual changes from the Sonos app, automations, and the
  // card's own commits all flow through the same reconcile() debounce.
  // No-op in sim mode (the SimControlStore subscription above handles it).
  React.useEffect(() => {
    if (!lifeActive) return undefined;
    if (window.__SIM_ACTIVE) return undefined;
    const client = window.__hav_haClient;
    if (!client || typeof client.subscribeEvents !== "function") return undefined;
    const targets = new Set(allEntities || []);
    if (targets.size === 0) return undefined;
    let unsub;
    try {
      unsub = client.subscribeEvents("state_changed", (ev) => {
        const eid = ev?.data?.entity_id;
        if (eid && targets.has(eid)) reconcile();
      });
    } catch {
      return undefined;
    }
    return () => { try { unsub && unsub(); } catch {} };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lifeActive, allEntities]);

  const fadeToReady = () => {
    if (fadeRef.current) clearTimeout(fadeRef.current);
    fadeRef.current = setTimeout(() => { setStatus("idle"); setStatusMsg(""); }, 2200);
  };
  const reconcile = () => {
    if (reconcileRef.current) clearTimeout(reconcileRef.current);
    reconcileRef.current = setTimeout(() => {
      if (!window.__SIM_ACTIVE) invalidateStatesCache();
      refreshStates();
      setOptimisticState(null);
    }, 700);
  };
  const fire = async (opts, label) => {
    setStatus("applying"); setStatusMsg("applying…");
    try {
      await fireServiceCall({ ...opts, onControlAction });
      setStatus("updated"); setStatusMsg(label || "updated");
      reconcile(); fadeToReady();
    } catch (e) {
      setStatus("error"); setStatusMsg((e && e.message) || "call failed");
      setOptimisticState(null);
    }
  };

  const coord = readMediaState(liveStates, playbackEntities[0] || "");
  const playing = optimisticState != null ? optimisticState === "playing" : coord.playing;
  const statesLoaded = liveStates.size > 0;
  const allUnavailable = statesLoaded && playbackEntities.length > 0 &&
    playbackEntities.every((id) => {
      const s = liveStates.get(id);
      return s && ["unavailable", "unknown"].includes(s.state);
    });
  const effLifecycle = allUnavailable ? "unavailable" : lifecycle;
  const title = mediaTitleOf(liveStates, playbackEntities);

  if (effLifecycle !== "active" || ctx.status === "pending") {
    return (
      <ControlCardShell muted>
        <MutedHeader
          icon={<window.IconSpeaker size={13} stroke="var(--hg-fg-4)" />}
          label={title}
          word={ctx.status === "pending" ? "applying…" : (LIFECYCLE_WORD[effLifecycle] || effLifecycle)}
        />
      </ControlCardShell>
    );
  }

  const togglePlay = () => {
    const willPlay = !playing;
    setOptimisticState(willPlay ? "playing" : "paused");
    fire({
      domain: "media_player", service: willPlay ? "media_play" : "media_pause",
      service_data: {}, target: { entity_id: playbackEntities }, simTargets: playbackEntities,
    }, willPlay ? "playing" : "paused");
  };
  const skip = (dir) => fire({
    domain: "media_player",
    service: dir === "next" ? "media_next_track" : "media_previous_track",
    service_data: {}, target: { entity_id: playbackEntities }, simTargets: playbackEntities,
  }, dir === "next" ? "skipped" : "back");

  // Group chips — roster = group members ∪ targeted speakers (∪ sim entities).
  const showGroup = coord.caps.group && coord.groupMembers.length > 0;
  const roster = new Set([].concat(coord.groupMembers, playbackEntities));
  if (showGroup && window.__SIM_ACTIVE && window.SimControlStore) {
    const allEnt = window.SimControlStore.getAll();
    for (const id of Object.keys(allEnt)) {
      if (id.indexOf("media_player.") !== 0) continue;
      const sf = (allEnt[id].attributes && allEnt[id].attributes.supported_features) || 0;
      if (sf & 524288) roster.add(id);   // groupable speakers only (excludes the AV receiver)
    }
  }
  const toggleSpeaker = (speakerId, isOn) => {
    if (isOn) {
      fire({ domain: "media_player", service: "unjoin", service_data: {},
        target: { entity_id: [speakerId] }, simTargets: [speakerId] }, "removed");
    } else {
      fire({ domain: "media_player", service: "join",
        service_data: { group_members: [speakerId] },
        target: { entity_id: [playbackEntities[0]] }, simTargets: [playbackEntities[0]] }, "added");
    }
  };

  const artUrl = resolveArtUrl(coord.art);

  return (
    <ControlCardShell accent>
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 9 }}>
        <window.IconSpeaker size={13} stroke="var(--hg-ice-bright)" />
        <span style={{ fontFamily: HC_SANS, fontSize: 12.5, fontWeight: 500, color: "var(--hg-fg-0)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</span>
        <span style={{ fontFamily: HC_MONO, fontSize: 9, color: "var(--hg-fg-4)", letterSpacing: "0.08em", textTransform: "uppercase" }}>{playing ? "playing" : (coord.state || "—")}</span>
        <StatusChip status={status} />
      </div>

      {/* now playing */}
      {coord.title && (
        <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 10 }}>
          <div style={{
            width: 34, height: 34, borderRadius: 4, flex: "0 0 auto",
            background: "var(--hg-bg-2)", border: "1px solid var(--hg-border-soft)",
            backgroundImage: artUrl ? `url("${artUrl}")` : "none",
            backgroundSize: "cover", backgroundPosition: "center",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>{!artUrl && <window.IconWaveStatic size={18} stroke="var(--hg-fg-5)" />}</div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontFamily: HC_SANS, fontSize: 11.5, color: "var(--hg-fg-1)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {coord.title}{coord.artist ? <span style={{ color: "var(--hg-fg-3)" }}> — {coord.artist}</span> : null}
            </div>
            {coord.source && <div style={{ fontFamily: HC_MONO, fontSize: 9, color: "var(--hg-fg-4)", letterSpacing: "0.06em" }}>{coord.source}</div>}
          </div>
        </div>
      )}

      {/* transport — acts on all targeted speakers */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 14, margin: "2px 0 11px" }}>
        {coord.caps.prev && (
          <TransportBtn onClick={() => skip("prev")}><window.IconSkipPrev size={13} fill="currentColor" /></TransportBtn>
        )}
        <TransportBtn primary onClick={togglePlay}>
          {playing ? <window.IconPause size={15} fill="currentColor" /> : <window.IconPlay size={15} fill="currentColor" />}
        </TransportBtn>
        {coord.caps.next && (
          <TransportBtn onClick={() => skip("next")}><window.IconSkipNext size={13} fill="currentColor" /></TransportBtn>
        )}
      </div>

      {/* per-zone volume rows */}
      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        {playbackEntities.map((id) => {
          const ve = volEntityFor(id);
          const routed = ve !== id;
          const vs = readMediaState(liveStates, ve);
          const routeName = humanize(String(ve).split(".").pop().split("_").pop());
          return (
            <div key={id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <ControlSlider
                  kind="volume"
                  label={prettyRoom(id, liveStates)}
                  sub={routed ? `via ${routeName}` : null}
                  value={volumes[ve] != null ? volumes[ve] : 0}
                  min={0} max={100} step={1} unit="%"
                  disabled={!vs.available || !vs.caps.volume}
                  applying={status === "applying"}
                  onDrag={(v) => setVolumes((p) => ({ ...p, [ve]: v }))}
                  onCommit={(v) => fire({
                    domain: "media_player", service: "volume_set",
                    service_data: { volume_level: clampNum(v, 0, 100) / 100 },
                    target: { entity_id: [ve] }, simTargets: [ve],
                  }, `${v}%`)}
                />
              </div>
              <MuteBtn
                muted={vs.muted}
                disabled={!vs.available || !vs.caps.mute}
                onClick={() => fire({
                  domain: "media_player", service: "volume_mute",
                  service_data: { is_volume_muted: !vs.muted },
                  target: { entity_id: [ve] }, simTargets: [ve],
                }, vs.muted ? "unmuted" : "muted")}
              />
            </div>
          );
        })}
      </div>

      {/* group chips */}
      {showGroup && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 11 }}>
          {Array.from(roster).map((sid) => {
            const isOn = coord.groupMembers.includes(sid);
            const sState = readMediaState(liveStates, sid);
            return (
              <SpeakerChip
                key={sid}
                label={prettyRoom(sid, liveStates)}
                on={isOn}
                disabled={!sState.available && !sState.missing}
                onClick={() => toggleSpeaker(sid, isOn)}
              />
            );
          })}
        </div>
      )}

      {/* footer status */}
      {statusMsg && (
        <div style={{ marginTop: 9, fontFamily: HC_MONO, fontSize: 9.5, color: status === "error" ? "var(--hg-warn)" : "var(--hg-fg-4)" }}>
          {statusMsg}
          {status === "error" && (
            <button
              onClick={() => { setStatus("idle"); setStatusMsg(""); }}
              className="hg-focusable"
              style={{ marginLeft: 8, background: "transparent", border: "1px solid var(--hg-border)", color: "var(--hg-fg-3)", padding: "1px 7px", borderRadius: 3, fontFamily: HC_MONO, fontSize: 9, cursor: "pointer", textTransform: "uppercase", letterSpacing: "0.08em" }}
            >dismiss</button>
          )}
        </div>
      )}
    </ControlCardShell>
  );
}

Object.assign(window, {
  CONTROL_EXPIRY_MS, LIGHT_CARD_SERVICES, MEDIA_CARD_SERVICES, VOLUME_ROUTING,
  humanize, distillSetValues, buildActionContext, ctxToHaTarget,
  deriveControlLifecycles, fetchStatesCached, invalidateStatesCache, readEntityStates,
  fireServiceCall, buildLightCaps, readMediaState, mediaTitleOf,
  ControlSlider, ControlCardShell, StatusChip, QuickChip,
  LightControlCard, MediaControlCard,
});
