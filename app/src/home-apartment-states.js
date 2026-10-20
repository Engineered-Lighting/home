/* APT-F06: the one place raw producer data becomes display states.
 *
 * Surfaces (the status popover, inspectors, the camera dock) read these
 * states; none computes its own. Everything here is pure presentation:
 * - Producer times are never restamped, and a missing time stays missing.
 * - Frame age is the producer's own `age` block (the perception view computes
 *   it). Nothing here reads a clock, so no age band can replace expiry.
 * - "Holding · last seen Ns ago" is passed through from the producer only.
 * - Reason codes render through Den's vocabulary (den-reason-vocabulary-1):
 *   exact code, then pattern, then "Unavailable" with the raw code in Details.
 * - Capability values say supported / available / reason only. Permission
 *   stays with its own authority and is never read or shown here.
 * - Every value belongs to one home. Another home's value is not applicable,
 *   never a fallback, so Victoria never inherits Echo's status.
 * Nothing here reads a client, dispatches, saves or starts a session. */
(function (global) {
  "use strict";

  // The app's Home Assistant client serves Echo Park only. Any other home
  // reports its own connection through its own producer.
  const HA_CLIENT_SITE = "echo";
  const CAPABILITIES_SCHEMA = "home-den-capabilities-1";
  // The Apartment's save states that still hold work not saved to the home.
  const UNSAVED_SAVE_STATES = Object.freeze(["unsaved", "saving", "conflict", "offline", "reviewed", "validation"]);

  const own = (value, key) => !!value && Object.prototype.hasOwnProperty.call(value, key);
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

  /* ---- Reason vocabulary ---------------------------------------------- */

  const escapeRegExp = text => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  function patternRegExp(pattern) {
    const source = escapeRegExp(pattern)
      .replace(/\\\{n\\\}/g, "\\d+")
      .replace(/\\\{state\\\}/g, "[a-z0-9]+(?:_[a-z0-9]+)*");
    return new RegExp(`^${source}$`);
  }

  function unknownReason(group, code) {
    return Object.freeze({ group, code, known: false, text: null, shown: "Unavailable", severity: null, legacy: false, detail: code });
  }

  /* Accepts a den-reason-vocabulary-1 document. Without one, every code reads
   * "Unavailable" with the raw code in Details: honest, never invented. */
  function createVocabulary(doc) {
    const groups = new Map();
    for (const group of (doc && Array.isArray(doc.groups) ? doc.groups : [])) {
      if (!group || typeof group.id !== "string") continue;
      const exact = new Map(), patterns = [];
      for (const entry of Array.isArray(group.codes) ? group.codes : []) {
        if (!entry || typeof entry.text !== "string") continue;
        if (typeof entry.code === "string") {
          exact.set(entry.code, entry);
          for (const alias of Array.isArray(entry.aliases) ? entry.aliases : []) if (typeof alias === "string") exact.set(alias, entry);
        } else if (typeof entry.pattern === "string") patterns.push([patternRegExp(entry.pattern), entry]);
      }
      groups.set(group.id, { legacy: group.legacy === true, exact, patterns });
    }
    function describe(group, code) {
      if (code == null) return null;
      const raw = String(code), table = groups.get(group);
      if (!table) return unknownReason(group, raw);
      const entry = table.exact.get(raw) || (table.patterns.find(([regexp]) => regexp.test(raw)) || [])[1];
      if (!entry) return unknownReason(group, raw);
      const legacy = table.legacy || entry.legacy === true;
      return Object.freeze({ group, code: raw, known: true, text: entry.text,
        shown: legacy ? `${entry.text} (retired preview)` : entry.text,
        severity: entry.severity === "warn" || entry.severity === "alert" ? entry.severity : null, legacy, detail: raw });
    }
    // The first listed group that knows the code; otherwise Unavailable.
    function describeAny(code, groupIds) {
      if (code == null) return null;
      for (const group of groupIds) {
        const found = describe(group, code);
        if (found?.known) return found;
      }
      return unknownReason(groupIds[0] || null, String(code));
    }
    return Object.freeze({ schema: doc?.schema || null, size: groups.size, describe, describeAny });
  }
  const EMPTY_VOCABULARY = createVocabulary(null);

  // A row's badge tone from a reason's severity hint. Severity never decides
  // whether a control is enabled; the producer's own check does that.
  const severityBadge = (reason, fallback) => reason?.severity === "alert" ? "blocked" : reason?.severity === "warn" ? "stale" : fallback;

  /* ---- Home Assistant ------------------------------------------------- */

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

  // ISO strings from Home Assistant, or Unix seconds from a producer.
  function reportTimeMs(value) {
    if (typeof value === "string") { const ms = Date.parse(value); return Number.isFinite(ms) ? ms : null; }
    return finite(value) && value > 0 ? value * 1000 : null;
  }

  function formatClock(value) {
    const ms = reportTimeMs(value);
    if (ms == null) return null;
    const date = new Date(ms);
    return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  }

  const reportedLine = value => reportTimeMs(value) == null ? "Last reported time unavailable" : `Last reported ${formatClock(value)}`;
  const lastReportedLine = state => reportedLine(lastReportedAt(state));

  // The newest time Home Assistant reported for these entities, or null.
  function latestReport(states, entityIds) {
    let best = null, bestMs = -Infinity;
    for (const entityId of Array.isArray(entityIds) ? entityIds : []) {
      const at = lastReportedAt(states?.[entityId]), ms = at ? Date.parse(at) : NaN;
      if (ms > bestMs) { best = at; bestMs = ms; }
    }
    return best;
  }

  function haConnectionState(status, lastReported) {
    if (status === "connected") return { status, badge: "connected", title: "Home Assistant connected", detail: "" };
    if (status === "connecting") return { status, badge: "loading", title: "Connecting to Home Assistant", detail: "" };
    if (status === "simulation") return { status, badge: "simulated", title: "Simulation · Home Assistant not used", detail: "" };
    if (status === "offline") return { status, badge: "disconnected", title: "Home Assistant offline", detail: reportedLine(lastReported) };
    return { status: "unknown", badge: "unavailable", title: "Home Assistant state unknown", detail: "" };
  }

  /* Echo reads the app's own client. Any other home reads only its own
   * report (the capabilities `ha` section); without one it is not reported. */
  function haState({ siteId, connection, sim = false, lastReportedAt: reported = null, report = null } = {}) {
    if (siteId === HA_CLIENT_SITE) return haConnectionState(haStatus({ connection, sim }), reported);
    if (report && report.supported !== false) {
      const status = { connected: "connected", offline: "offline" }[report.connection] || "unknown";
      return haConnectionState(status, report.last_report_at ?? null);
    }
    return { status: "not-reported", badge: "unavailable", title: "Home Assistant not reported for this home",
      detail: "Each home reports its own connection." };
  }

  /* ---- Saves and the room --------------------------------------------- */

  const SAVE_STATES = {
    saved: ["saved", "Saved"], unsaved: ["unsaved", "Unsaved changes"], saving: ["loading", "Saving"],
    conflict: ["conflict", "Save conflict"], offline: ["draft", "Local draft kept"], reviewed: ["draft", "Draft ready to publish"],
    validation: ["invalid", "Can't save yet"], loading: ["loading", "Loading saved work"], failed: ["blocked", "Save failed"],
    error: ["blocked", "Save failed"],
  };
  function saveState(state, detail = "") {
    if (state == null || state === "") return { state: null, badge: "unavailable", title: "No changes", detail: detail || "", unsaved: false };
    const known = own(SAVE_STATES, state) ? SAVE_STATES[state] : null;
    return { state, badge: known ? known[0] : "unavailable", title: known ? known[1] : "Unavailable",
      detail: known ? detail || "" : [detail, String(state)].filter(Boolean).join(" · "), unsaved: UNSAVED_SAVE_STATES.includes(state) };
  }
  const isUnsaved = state => UNSAVED_SAVE_STATES.includes(state);

  // The site session phase, and the capabilities `assets.scene` values.
  const SITE_PHASES = {
    ready: ["current", "Room ready"], loading: ["loading", "Loading room"], idle: ["loading", "Loading room"],
    error: ["blocked", "Room failed to load"], unavailable: ["unavailable", "Room not available here"],
    invalidated: ["stale", "Room changed · reload needed"],
  };
  function siteState(phase) {
    const key = phase == null || phase === "" ? "loading" : phase;
    const known = own(SITE_PHASES, key) ? SITE_PHASES[key] : ["unavailable", "Unavailable"];
    return { phase: key, badge: known[0], title: known[1], detail: own(SITE_PHASES, key) ? "" : String(key) };
  }

  /* ---- Frame timing --------------------------------------------------- */

  const AGE_STATUSES = new Set(["current", "stale", "unavailable", "invalid", "future"]);
  const nullableTime = value => finite(value) ? value : null;

  /* Reads Den's frame-timing block. The age is the producer's: a number is
   * shown only when the age is current and its clock uncertainty is known.
   * Expiry is the producer's `valid_until`; nothing here extends it. */
  function frameAge(frame, vocabulary = EMPTY_VOCABULARY) {
    if (!frame || typeof frame !== "object") return { status: "none", badge: "unavailable", title: "No frame", detail: "", seconds: null, times: null };
    const times = { captured_at: nullableTime(frame.captured_at), observed_at: nullableTime(frame.observed_at),
      available_at: nullableTime(frame.available_at), valid_until: nullableTime(frame.valid_until) };
    const age = frame.age && typeof frame.age === "object" ? frame.age : null;
    const status = age && AGE_STATUSES.has(age.status) ? age.status : "unavailable";
    const reason = age?.reason == null ? null : vocabulary.describe("perception.age", age.reason);
    const qualified = status === "current" && finite(age.seconds) && age.seconds >= 0 && finite(age.uncertainty_s) && age.uncertainty_s >= 0;
    const seconds = qualified ? age.seconds : null;
    if (status === "current") {
      return { status, badge: "current", title: qualified ? `Current · ${Math.round(seconds)} s old` : "Current",
        detail: qualified ? `± ${age.uncertainty_s} s clock uncertainty` : "", seconds, times, reason };
    }
    if (status === "stale") {
      return { status, badge: "stale", title: age.reason === "expired" ? "Expired" : reason?.shown || "Stale",
        detail: reason?.known ? "" : reason?.detail || "", seconds: null, times, reason };
    }
    const badge = status === "unavailable" ? "unavailable" : "invalid";
    return { status, badge, title: reason?.shown || "Age unavailable",
      detail: reason && !reason.known ? reason.detail : "", seconds: null, times, reason };
  }

  /* Den's preview reports Holding with its own last-seen age. This passes it
   * through; it never runs a hold timer. Anything else is not Holding. */
  function holdingLabel(lastSeenSeconds) {
    const age = Number(lastSeenSeconds);
    return lastSeenSeconds != null && Number.isFinite(age) && age >= 0 ? `Holding · last seen ${Math.round(age)}s ago` : "Holding";
  }
  function holding(report) {
    if (!report || report.state !== "holding") return null;
    return { badge: "holding", title: holdingLabel(report.last_seen_s) };
  }

  /* ---- Perception session --------------------------------------------- */

  const ANALYSIS_STATES = {
    requested: ["loading", "Analysis requested"], starting: ["loading", "Analysis starting"],
    recording: ["live", "Analysis recording"], stopping: ["loading", "Analysis stopping"],
    failed: ["blocked", "Analysis failed"], interrupted: ["blocked", "Analysis interrupted"],
    blocked: ["blocked", "Analysis blocked"], indeterminate: ["stale", "Analysis state unknown"],
  };
  const NO_SESSION_DETAIL = "No analysis session is running for this home. Start and Stop are in Camera · JEV.";

  /* No session is a normal state: an empty dock, no body layer, and the
   * explicit Start kept in the dock. Missing pose never means vacant. */
  function perceptionState(section, vocabulary = EMPTY_VOCABULARY) {
    if (!section || section.supported !== true) return { state: "unsupported", badge: "unavailable", title: "No analyzed-frame camera here", detail: "", bodyLayer: false };
    const camera = section.reason == null ? null : vocabulary.describeAny(section.reason, ["activity_loop.health"]);
    const analysis = section.analysis == null || section.analysis === "idle" || section.analysis === "stopped" ? null : section.analysis;
    if (!analysis) {
      return { state: "no-session", badge: "no-session", title: "No session",
        detail: [camera?.shown, NO_SESSION_DETAIL].filter(Boolean).join(" · "), bodyLayer: false, reason: camera };
    }
    const known = own(ANALYSIS_STATES, analysis) ? ANALYSIS_STATES[analysis] : ["unavailable", "Unavailable"];
    const age = frameAge(section.frame, vocabulary);
    return { state: analysis, badge: known[0], title: known[1],
      detail: [own(ANALYSIS_STATES, analysis) ? "" : String(analysis), section.frame ? `Frame: ${age.title}` : "", camera?.shown].filter(Boolean).join(" · "),
      bodyLayer: analysis === "recording", frame: age, reason: camera };
  }

  /* ---- Capabilities adapter (proposed home-den-capabilities-1) -------- */

  const SECTIONS = ["ha", "engineered", "perception", "body", "assets", "bench", "lighting_brain", "den_ops"];
  // Which vocabulary groups a section's `reason` is looked up in, in order.
  const SECTION_REASON_GROUPS = {
    ha: [], engineered: [], perception: ["activity_loop.health", "perception.age"], body: ["perception.placement", "perception.age"],
    assets: [], bench: [], lighting_brain: ["brain.status", "brain.refusal"], den_ops: ["den_ops.level"],
  };
  const ENGINEERED_VALUES = new Set(["none", "simulated", "modeled", "commissioned"]);

  /* Reads one home's capabilities document for the home being shown.
   * Another home's document is not applicable, never a fallback. A
   * `permitted` field is dropped: capability values never grant permission.
   * A fixture that claims physical output breaks the contract (always false
   * in the MVP), so the engineered section reads invalid. */
  function readCapabilities(doc, { siteId } = {}) {
    if (!doc || typeof doc !== "object") return null;
    if (doc.schema !== CAPABILITIES_SCHEMA) return { applicable: false, reason: "schema_unsupported", siteId };
    if (typeof siteId !== "string" || doc.site_id !== siteId) return { applicable: false, reason: "other_home", siteId };
    const sections = {};
    for (const name of SECTIONS) {
      const raw = doc[name];
      if (!raw || typeof raw !== "object") continue;
      const section = {};
      for (const [key, value] of Object.entries(raw)) if (key !== "permitted") section[key] = value;
      section.supported = raw.supported === true;
      section.available = raw.available === true ? true : raw.available === false ? false : null;
      section.reason = raw.reason == null ? null : String(raw.reason);
      if (name === "engineered") {
        const fixtures = Array.isArray(raw.fixtures) ? raw.fixtures : [];
        section.invalid = fixtures.some(fixture => fixture?.physical_output !== false)
          || (raw.value != null && !ENGINEERED_VALUES.has(raw.value));
        section.fixtures = fixtures.map(fixture => ({ fixture_id: String(fixture?.fixture_id || ""), value: fixture?.value ?? null, physical_output: false }));
        section.physical_output = false;
      }
      sections[name] = Object.freeze(section);
    }
    return Object.freeze({ applicable: true, siteId, generatedAt: finite(doc.generated_at) ? doc.generated_at : null, sections: Object.freeze(sections) });
  }

  const sectionReason = (name, section, vocabulary) => section?.reason == null ? null
    : vocabulary.describeAny(section.reason, SECTION_REASON_GROUPS[name] || []);

  const CAPABILITY_LABELS = { engineered: "Engineered fixtures", perception: "Perception", body: "Body", assets: "Scene assets", bench: "Bench" };
  const ENGINEERED_TEXT = { none: "No engineered fixtures", simulated: "Simulated", modeled: "Modeled", commissioned: "Commissioned" };
  const PLACEMENT_TEXT = { registered: "Registered room placement", diagnostic: "Diagnostic room placement",
    approximate: "Approximate room placement", none: "No room placement" };

  // supported / available / reason, kept apart. No wording here grants or
  // implies permission.
  function supportLine(section) {
    if (!section || section.supported !== true) return "Not supported here";
    if (section.available === true) return "Supported · available";
    if (section.available === false) return "Supported · not available now";
    return "Supported · availability unknown";
  }

  function capabilityRow(name, section, vocabulary) {
    const reason = sectionReason(name, section, vocabulary);
    const row = { key: `capability-${name}`, system: CAPABILITY_LABELS[name], group: "capabilities",
      supported: section.supported, available: section.available,
      state: section.supported !== true ? "unavailable" : section.available === true ? "current" : section.available === false ? "unavailable" : "unavailable",
      title: supportLine(section), detail: "", details: reason ? [reason] : [] };
    if (name === "engineered") {
      if (section.invalid) return { ...row, state: "invalid", title: "Engineered state invalid", detail: "The report claims physical output, which is always off here." };
      const value = section.value;
      row.state = value === "modeled" ? "modeled" : value === "simulated" ? "simulated" : row.state;
      row.title = `${ENGINEERED_TEXT[value] || "Unavailable"} · ${supportLine(section)}`;
      row.detail = `${plural(section.fixtures.length, "fixture")} · physical output off`;
    } else if (name === "body" && section.supported === true) {
      const placement = PLACEMENT_TEXT[section.placement] || "Placement unavailable";
      row.detail = [placement, section.profile == null ? "Body profile not supplied" : ""].filter(Boolean).join(" · ");
    } else if (name === "assets" && section.supported === true) {
      row.detail = siteState(section.scene).title;
    }
    if (reason) row.detail = [row.detail, reason.shown].filter(Boolean).join(" · ");
    return row;
  }

  /* ---- Den systems ---------------------------------------------------- */

  const BRAIN_BADGES = { online: "connected", connecting: "loading", offline: "disconnected", stopped: "disconnected" };
  function brainRow(section, vocabulary) {
    const status = vocabulary.describe("brain.status", section.status);
    const reason = sectionReason("lighting_brain", section, vocabulary);
    const mode = section.mode == null ? null : vocabulary.describe("brain.mode", section.mode);
    const badge = !status ? "unavailable" : status.known ? BRAIN_BADGES[section.status] || severityBadge(status, "stale") : "unavailable";
    return { key: "lighting-brain", system: "Lighting brain", state: badge,
      title: status ? status.shown : "Not reported",
      // `why` is the brain's own sentence, shown as written.
      detail: [mode?.shown, typeof section.why === "string" ? section.why : ""].filter(Boolean).join(" · "),
      details: [status, reason, mode].filter(Boolean) };
  }

  /* den-ops-banner-1 (or the capabilities `den_ops` section, same fields).
   * A banner is shown with its own text; "monitor silent" has no level. */
  function denOpsRow(ops, vocabulary) {
    if (!ops || ops.supported === false) return { key: "den-ops", system: "Den ops", state: "unavailable", title: "Not reported", detail: "", details: [] };
    const step = vocabulary.describe("den_ops.level", ops.step ?? null);
    const banner = ops.banner && typeof ops.banner.text === "string" ? ops.banner : null;
    if (banner) {
      const silent = /^Ops monitor silent\b/.test(banner.text) ? vocabulary.describe("den_ops.level", "monitor_silent") : null;
      return { key: "den-ops", system: "Den ops", state: banner.severity === "alert" ? "blocked" : banner.severity === "warn" ? "stale" : "current",
        title: banner.text, detail: step?.shown || "", details: [silent, step].filter(Boolean) };
    }
    if (!step) return { key: "den-ops", system: "Den ops", state: "unavailable", title: "Not reported", detail: "", details: [] };
    return { key: "den-ops", system: "Den ops", state: step.known ? severityBadge(step, "current") : "unavailable",
      title: step.shown, detail: "", details: [step] };
  }

  const HEALTH_COMPONENTS = ["jev", "deep_stack", "camera"];
  /* The newest health event per component for this home, as reported.
   * A health event's ttl is the producer's; nothing here ages it out. */
  function activityHealth(events, siteId, perception, vocabulary) {
    const latest = new Map();
    for (const event of Array.isArray(events) ? events : []) {
      if (!event || event.type !== "health" || event.home !== siteId || !HEALTH_COMPONENTS.includes(event.component)) continue;
      const before = latest.get(event.component);
      if (!before || (finite(event.observed_at) && event.observed_at >= (before.observed_at ?? -Infinity))) latest.set(event.component, event);
    }
    if (!latest.has("jev") && perception?.jev && typeof perception.jev.ok === "boolean") latest.set("jev", { component: "jev", ok: perception.jev.ok, detail: perception.jev.detail });
    if (!latest.size) {
      const none = vocabulary.describe("activity_loop.health", "not_reported");
      return { key: "activity-loop", system: "Activity loop", state: "unavailable", title: none?.known ? none.shown : "Not reported yet", detail: "", details: [] };
    }
    const components = HEALTH_COMPONENTS.filter(name => latest.has(name)).map(name => {
      const event = latest.get(name);
      return { component: name, ok: event.ok === true,
        reason: event.detail == null ? null : vocabulary.describe("activity_loop.health", event.detail) };
    });
    const failing = components.filter(item => !item.ok);
    const worst = failing.find(item => item.reason?.severity === "alert") || failing.find(item => item.reason?.severity === "warn") || failing[0];
    const label = item => `${item.component === "deep_stack" ? "Deep sensing" : item.component === "jev" ? "JEV" : "Camera"}: ${item.ok ? "OK" : item.reason?.shown || "Not OK"}`;
    return { key: "activity-loop", system: "Activity loop",
      state: worst ? worst.reason?.known ? severityBadge(worst.reason, "unavailable") : "unavailable" : "current",
      title: worst ? worst.reason?.shown || "Not OK" : "Activity loop healthy",
      detail: components.map(label).join(" · "), details: components.map(item => item.reason).filter(Boolean), components };
  }

  /* ---- Status rows ---------------------------------------------------- */

  function lightsRow(shell, ha, caps) {
    const siteId = shell?.siteId;
    const engineered = caps?.sections?.engineered || null;
    // Lights driven through Home Assistant name its connection. Another home's
    // connection is never used: haState reads only this home's own report.
    if (siteId === HA_CLIENT_SITE || shell?.lightEntities > 0) {
      if (ha.status === "connected") {
        return { key: "lights", system: "Lights", state: "connected", title: "Home Assistant connected",
          detail: shell?.physicalControls ? "Output guards apply." : "Physical controls are unavailable here." };
      }
      const why = ha.status === "connecting" ? "Connecting. " : "";
      return { key: "lights", system: "Lights", state: ha.status === "connecting" ? "loading" : "disconnected", title: "Home Assistant not connected",
        detail: `${why}Light controls are disabled and nothing is queued.${ha.detail ? ` ${ha.detail}` : ""}` };
    }
    const modeled = engineered ? engineered.fixtures.length : finite(shell?.modeledFixtures) ? shell.modeledFixtures : 0;
    if (engineered?.invalid) return { key: "lights", system: "Lights", state: "invalid", title: "Engineered state invalid", detail: "The report claims physical output, which is always off here." };
    if (modeled > 0 || engineered?.value === "modeled") {
      // Capabilities documents are Den's, so their fixtures are the Den's.
      const room = engineered ? "Den" : shell?.lightsRoom || "these";
      return { key: "lights", system: "Lights", state: "modeled", title: `No light entities for ${room} fixtures`,
        detail: `${plural(modeled, "modeled fixture")} · nothing is sent to hardware.` };
    }
    return { key: "lights", system: "Lights", state: "unavailable", title: "No lights in this home's model", detail: "" };
  }

  /* One row per system for the home being shown, from that home's facts:
   * - `shell`: the Apartment's shell state (siteId, phase, sourceLabel,
   *   saveState, saveDetail, physicalControls, hasPerception, haConnection,
   *   haLastReportedAt; optionally lightEntities, modeledFixtures, lightsRoom).
   * - `report`: { capabilities, denOps, activityHealth } from that home's
   *   producers; by default the report store's entry for shell.siteId.
   * A report for another home is ignored, never used as a fallback. */
  function statusRows(shell, { report, vocabulary } = {}) {
    const siteId = shell?.siteId || null;
    const vocab = vocabulary || reports.vocabulary();
    const homeReport = report === undefined ? reports.get(siteId) : report;
    const caps = readCapabilities(homeReport?.capabilities, { siteId });
    const sections = caps?.applicable ? caps.sections : {};
    const room = siteState(shell?.phase);
    const ha = haState({ siteId, connection: shell?.haConnection, lastReportedAt: shell?.haLastReportedAt, report: sections.ha || null });
    const save = saveState(shell?.saveState, shell?.saveDetail);
    const rows = [
      { key: "room", system: "Room", state: room.badge, title: room.title, detail: shell?.sourceLabel || room.detail },
      lightsRow(shell, ha, caps?.applicable ? caps : null),
      { key: "save", system: "Saved work", state: save.badge, title: save.title, detail: save.detail },
    ];
    // Echo's lights row names its connection. Any other home shows Home
    // Assistant only from its own report, so it never shows Echo's.
    if (siteId !== HA_CLIENT_SITE && sections.ha) rows.splice(1, 0, { key: "home-assistant", system: "Home Assistant", state: ha.badge, title: ha.title, detail: ha.detail });
    if (sections.perception) {
      const perception = perceptionState(sections.perception, vocab);
      rows.push({ key: "camera", system: "Camera", state: perception.badge, title: perception.title, detail: perception.detail });
    } else {
      rows.push(shell?.hasPerception
        ? { key: "camera", system: "Camera", state: null, title: "Camera · JEV",
          detail: "Video, analyzed frames, pose and inference have separate states there. Missing pose does not mean vacant." }
        : { key: "camera", system: "Camera", state: "unavailable", title: "No analyzed-frame camera here", detail: "" });
    }
    if (sections.lighting_brain?.supported) rows.push(brainRow(sections.lighting_brain, vocab));
    const denOps = homeReport?.denOps || sections.den_ops || null;
    if (caps?.applicable && (denOps || sections.den_ops)) rows.push(denOpsRow(denOps, vocab));
    if (caps?.applicable && (sections.perception?.supported || Array.isArray(homeReport?.activityHealth))) {
      rows.push(activityHealth(homeReport?.activityHealth, siteId, sections.perception, vocab));
    }
    if (caps?.applicable) {
      for (const name of ["engineered", "perception", "body", "assets", "bench"]) {
        if (sections[name]) rows.push(capabilityRow(name, sections[name], vocab));
      }
    } else {
      rows.push({ key: "capabilities", system: "Capabilities", state: "unavailable", title: "Not reported",
        detail: "This home has not reported its fixture, perception, body or bench capabilities." });
    }
    return rows;
  }

  /* ---- Report store: the adapter seam --------------------------------- */

  /* Per-home producer reports for display. Nothing in Home fills this yet:
   * the producer wiring lands with Den's contracts. A report is kept only
   * under the home its capabilities name, so one home's report can never be
   * read for another. Display only; it grants and activates nothing. */
  function createReportStore() {
    const byHome = new Map(), listeners = new Set();
    let vocabulary = EMPTY_VOCABULARY, version = 0;
    const notify = () => { version += 1; for (const listener of [...listeners]) { try { listener(version); } catch (_) { /* A listener never breaks another. */ } } };
    return Object.freeze({
      set(siteId, report) {
        if (typeof siteId !== "string" || !report || typeof report !== "object") return false;
        const caps = report.capabilities;
        if (caps != null && (caps.schema !== CAPABILITIES_SCHEMA || caps.site_id !== siteId)) return false;
        byHome.set(siteId, Object.freeze({ capabilities: caps ?? null, denOps: report.denOps ?? null,
          activityHealth: Array.isArray(report.activityHealth) ? report.activityHealth.slice() : null }));
        notify(); return true;
      },
      get: siteId => byHome.get(siteId) || null,
      clear(siteId) { if (siteId == null) byHome.clear(); else byHome.delete(siteId); notify(); },
      setVocabulary(doc) { vocabulary = createVocabulary(doc); notify(); return vocabulary.size; },
      vocabulary: () => vocabulary,
      version: () => version,
      subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    });
  }
  const reports = createReportStore();

  const api = Object.freeze({
    HA_CLIENT_SITE, CAPABILITIES_SCHEMA, UNSAVED_SAVE_STATES,
    createVocabulary, haStatus, lastReportedAt, formatClock, reportedLine, lastReportedLine, latestReport, haState,
    saveState, isUnsaved, siteState, frameAge, holdingLabel, holding, perceptionState, readCapabilities, supportLine,
    statusRows, createReportStore, reports,
  });
  if (global) global.HomeApartmentStates = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
