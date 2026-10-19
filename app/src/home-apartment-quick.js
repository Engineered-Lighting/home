/* Echo Home Assistant light presentation, Quick plans and exact Undo.
 * Pure data: callers own dispatch, currentness and Travel Mode through the
 * existing guarded service path. Nothing here reads a client or sends. */
(function (global) {
  "use strict";

  const LIGHT_DEVICE_TYPES = new Set(["light", "plug", "lamp"]);
  const COLOR_KEYS = { color_temp: "color_temp_kelvin", hs: "hs_color", xy: "xy_color",
    rgb: "rgb_color", rgbw: "rgbw_color", rgbww: "rgbww_color" };
  const QUICK_OFFLINE_REASON = "Home Assistant is offline. Quick controls return when it reconnects.";
  const QUICK_TRAVEL_REASON = "Travel Mode is on. Controls that may turn lights on are refused.";
  const DEFAULT_CCT = [2000, 6500];

  const domainOf = entityId => typeof entityId === "string" && entityId.includes(".") ? entityId.split(".")[0] : "";
  const copy = value => value == null ? value : JSON.parse(JSON.stringify(value));
  const deepFreeze = value => {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
      Object.values(value).forEach(deepFreeze); Object.freeze(value);
    }
    return value;
  };
  const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

  // Ceiling lights are light.*; plugs are switch.* devices registered as lights.
  function isEchoLightDevice(device) {
    const entityId = device?.ha_entity_id;
    if (typeof entityId !== "string") return false;
    if (entityId.startsWith("light.")) return true;
    return entityId.startsWith("switch.") && LIGHT_DEVICE_TYPES.has(String(device.type || "").toLowerCase());
  }

  function echoLightEntityIds(devices = []) {
    return [...new Set((devices || []).filter(isEchoLightDevice).map(device => device.ha_entity_id))];
  }

  // One status vocabulary for the Echo inspector, chips and room switches.
  function haStatus({ connection, sim = false } = {}) {
    if (sim) return "simulation";
    if (connection === "online") return "connected";
    if (connection === "connecting" || connection === "picking-model") return "connecting";
    return "offline";
  }

  // Only a time Home Assistant reported is shown; nothing is invented.
  function lastReportedAt(state) {
    const value = state?.last_reported || state?.last_updated || null;
    return typeof value === "string" && Number.isFinite(Date.parse(value)) ? value : null;
  }

  function formatClock(iso) {
    const date = new Date(iso);
    if (!Number.isFinite(date.getTime())) return null;
    return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  }

  function lastReportedLine(state) {
    const at = lastReportedAt(state);
    return at ? `Last reported ${formatClock(at)}` : "Last reported time unavailable";
  }

  function controlBlock({ connection, sim = false, state, entityId } = {}) {
    const status = haStatus({ connection, sim });
    if (!entityId) return { disabled: true, status, reason: "Not linked to a Home Assistant light. Match it in Setup › Home Assistant." };
    if (status === "simulation") return { disabled: true, status, reason: "Simulation · controls disabled" };
    if (status === "connecting") return { disabled: true, status, reason: "Connecting to Home Assistant" };
    if (status === "offline") return { disabled: true, status, reason: "Home Assistant is offline. Nothing is queued.", detail: lastReportedLine(state) };
    if (!state || ["unavailable", "unknown"].includes(state.state)) {
      return { disabled: true, status, reason: "This light is unavailable in Home Assistant.", detail: lastReportedLine(state) };
    }
    return { disabled: false, status, reason: "" };
  }

  function lightCapabilities(entityId, state) {
    const domain = domainOf(entityId), attributes = state?.attributes || {};
    if (domain !== "light") return { power: domain === "switch", brightness: false, colorTemp: null };
    const modes = Array.isArray(attributes.supported_color_modes) ? attributes.supported_color_modes : null;
    const brightness = modes ? modes.some(mode => mode !== "onoff") : attributes.brightness != null;
    const colorTemp = modes?.includes("color_temp") ? {
      min: Math.round(attributes.min_color_temp_kelvin ?? DEFAULT_CCT[0]),
      max: Math.round(attributes.max_color_temp_kelvin ?? DEFAULT_CCT[1]) } : null;
    return { power: true, brightness, colorTemp };
  }

  function brightnessPct(state) {
    const raw = state?.attributes?.brightness;
    return Number.isFinite(raw) ? Math.max(1, Math.round(raw / 255 * 100)) : null;
  }

  function lightSummary(entityId, state) {
    if (!state || ["unavailable", "unknown"].includes(state.state)) return "Unavailable";
    if (state.state !== "on") return "Off";
    const parts = [], pct = brightnessPct(state), kelvin = state.attributes?.color_temp_kelvin;
    if (domainOf(entityId) === "light" && pct != null) parts.push(`${pct}%`);
    if (state.attributes?.color_mode === "color_temp" && Number.isFinite(kelvin)) parts.push(`${Math.round(kelvin)} K`);
    return parts.length ? parts.join(" · ") : "On";
  }

  // Modeled Den channels: 'Spot 55% · Down 18%' style summaries.
  function virtualSummary(state) {
    if (!state) return "Unavailable";
    const parts = [];
    const level = channel => channel?.on && channel.brightness > 0 ? Math.round(channel.brightness * 100) : 0;
    if (level(state.spotlight)) parts.push(`Spot ${level(state.spotlight)}%`);
    if (level(state.downward)) parts.push(`Down ${level(state.downward)}%`);
    const lit = (state.radials || []).filter(zone => level(zone));
    if (lit.length) parts.push(`Radial ${Math.round(lit.reduce((sum, zone) => sum + level(zone), 0) / lit.length)}%`);
    return parts.length ? parts.join(" · ") : "Off";
  }

  // Chips are the home's own HA scenes and scripts; scripts run (▶).
  function quickChips(states = {}) {
    const chips = [];
    for (const [entityId, state] of Object.entries(states || {})) {
      const domain = domainOf(entityId);
      if (domain !== "scene" && domain !== "script") continue;
      const members = domain === "scene" && Array.isArray(state?.attributes?.entity_id)
        ? state.attributes.entity_id.filter(id => typeof id === "string") : [];
      chips.push({ entity_id: entityId, kind: domain, runs: domain === "script",
        name: state?.attributes?.friendly_name || entityId.slice(domain.length + 1).replace(/_/g, " "),
        active: domain === "script" && state?.state === "on",
        // A scene that never ran reports "unknown"; only "unavailable" blocks it.
        unavailable: !state || state.state === "unavailable", members });
    }
    return chips.sort((a, b) => a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === "scene" ? -1 : 1);
  }

  function travelModeOn() {
    if (global?.__HOME_TRAVEL_MODE_STATE === "on") return true;
    // Same cache key as the guarded dispatch; this only explains a refusal early.
    try { return global?.localStorage?.getItem("home.lights.travelMode.state.v1") === "on"; } catch (_) { return false; }
  }

  // Disabled reasons are shown beside the controls; dispatch stays guarded anyway.
  function quickBlock({ connection, sim = false, travel = travelModeOn() } = {}) {
    const status = haStatus({ connection, sim });
    if (status === "simulation") return { all: "Simulation · controls disabled", energizing: "Simulation · controls disabled" };
    if (status !== "connected") return { all: QUICK_OFFLINE_REASON, energizing: QUICK_OFFLINE_REASON };
    return { all: "", energizing: travel ? QUICK_TRAVEL_REASON : "" };
  }

  function snapshotEntity(entityId, state) {
    const domain = domainOf(entityId), attributes = state?.attributes || {};
    const entry = { entity_id: entityId, domain, state: state?.state ?? "unknown" };
    if (domain === "light" && entry.state === "on") {
      if (Number.isFinite(attributes.brightness)) entry.brightness = attributes.brightness;
      const mode = attributes.color_mode, key = COLOR_KEYS[mode];
      if (mode) entry.color_mode = mode;
      if (key && attributes[key] != null) entry[key] = copy(attributes[key]);
    }
    return entry;
  }

  // An exact snapshot of the lights a Quick action may change.
  function snapshot(states = {}, entityIds = []) {
    return deepFreeze([...new Set(entityIds)].filter(id => ["light", "switch"].includes(domainOf(id)))
      .map(id => snapshotEntity(id, states[id])));
  }

  function restoreCalls(entries = []) {
    const calls = [], skipped = [];
    for (const entry of entries) {
      if (!["on", "off"].includes(entry.state)) { skipped.push(entry.entity_id); continue; }
      if (entry.state === "off" || entry.domain === "switch") {
        calls.push({ domain: entry.domain, service: entry.state === "on" ? "turn_on" : "turn_off", data: { entity_id: entry.entity_id } });
        continue;
      }
      const data = { entity_id: entry.entity_id };
      if (Number.isFinite(entry.brightness)) data.brightness = entry.brightness;
      const key = COLOR_KEYS[entry.color_mode];
      if (key && entry[key] != null) data[key] = copy(entry[key]);
      calls.push({ domain: "light", service: "turn_on", data });
    }
    return { calls, skipped, exact: skipped.length === 0 };
  }

  function matchesSnapshot(entry, state) {
    return same(snapshotEntity(entry.entity_id, state), entry);
  }

  /* Plans name the exact calls and what Undo can restore exactly.
   * all: every Echo light and plug · room: the given entities · chip: one scene/script. */
  function planQuick(action, { devices = [], states = {} } = {}) {
    if (action?.kind === "all" || action?.kind === "room") {
      if (typeof action.on !== "boolean") return null;
      const ids = action.kind === "all" ? echoLightEntityIds(devices)
        : [...new Set((action.entityIds || []).filter(id => ["light", "switch"].includes(domainOf(id))))];
      if (!ids.length) return null;
      const verb = action.on ? "on" : "off";
      return { kind: action.kind, label: action.kind === "all" ? `All ${verb}` : `${action.label || "Room"} ${verb}`,
        energizing: action.on, targets: ids, exactUndo: true, note: "",
        calls: ids.map(id => ({ domain: domainOf(id), service: action.on ? "turn_on" : "turn_off", data: { entity_id: id } })) };
    }
    if (action?.kind === "chip") {
      const chip = quickChips(states).find(item => item.entity_id === action.entity_id);
      if (!chip || chip.unavailable) return null;
      const lights = chip.members.filter(id => ["light", "switch"].includes(domainOf(id)));
      const scene = chip.kind === "scene";
      const exactUndo = scene && lights.length > 0 && lights.length === chip.members.length;
      return { kind: "chip", label: chip.name, energizing: true,
        targets: scene ? lights : echoLightEntityIds(devices), exactUndo,
        note: exactUndo ? "" : scene ? "Undo restores this scene's lights; its other effects stay."
          : "Undo restores Echo's lights; the script's other effects can't be undone exactly.",
        calls: [{ domain: chip.kind, service: "turn_on", data: { entity_id: chip.entity_id } }] };
    }
    return null;
  }

  /* Sequential, current-checked dispatch. dispatch(domain, service, data)
   * is the caller's guarded path and resolves true only when it was sent. */
  async function runCalls(calls, { dispatch, isCurrent }) {
    let sent = 0;
    for (const call of calls) {
      if (isCurrent() !== true) return { ok: false, sent, reason: "stale" };
      let ok = false;
      try { ok = await dispatch(call.domain, call.service, copy(call.data)) === true; } catch (_) { ok = false; }
      if (!ok) return { ok: false, sent, reason: "refused" };
      sent++;
    }
    return { ok: true, sent, reason: "" };
  }

  async function applyQuick(plan, { states = {}, dispatch, isCurrent }) {
    if (!plan || typeof dispatch !== "function" || typeof isCurrent !== "function") return { ok: false, sent: 0, reason: "invalid" };
    const before = snapshot(states, plan.targets);
    const result = await runCalls(plan.calls, { dispatch, isCurrent });
    return { ...result, before, exactUndo: plan.exactUndo && before.every(entry => ["on", "off"].includes(entry.state)) };
  }

  async function undoQuick(before, { dispatch, isCurrent }) {
    const { calls, skipped, exact } = restoreCalls(before);
    const result = await runCalls(calls, { dispatch, isCurrent });
    return { ...result, skipped, exact };
  }

  // Modeled Den batches: power-only patches keep every other channel value.
  function virtualSnapshot(fixtures = []) {
    return deepFreeze(fixtures.filter(fixture => fixture?.fixture_id && fixture.state).map(fixture => ({
      fixture_id: fixture.fixture_id,
      spotlight: { ...pickChannel(fixture.state.spotlight) },
      downward: { ...pickChannel(fixture.state.downward) },
      radials: (fixture.state.radials || []).map(zone => ({ number: zone.number, ...pickChannel(zone) })),
    })));
  }
  function pickChannel(channel = {}) {
    return { on: channel.on, brightness: channel.brightness, color_temp_kelvin: channel.color_temp_kelvin };
  }
  function virtualRestorePatch(entry) {
    return { spotlight: { ...entry.spotlight }, downward: { ...entry.downward }, radials: entry.radials.map(zone => ({ ...zone })) };
  }
  function virtualMatches(entry, state) {
    return !!state && same(virtualSnapshot([{ fixture_id: entry.fixture_id, state }])[0], entry);
  }

  const api = Object.freeze({ QUICK_OFFLINE_REASON, QUICK_TRAVEL_REASON, isEchoLightDevice, echoLightEntityIds,
    haStatus, lastReportedAt, formatClock, lastReportedLine, controlBlock, lightCapabilities, brightnessPct,
    lightSummary, virtualSummary, quickChips, quickBlock, travelModeOn, snapshot, restoreCalls, matchesSnapshot,
    planQuick, applyQuick, undoQuick, virtualSnapshot, virtualRestorePatch, virtualMatches });
  if (global) global.HomeApartmentQuick = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
