/* Camera dock model (APT-P01..P06): pure functions from the perception view,
 * the player state and Den's capability block to what the dock says. No I/O,
 * no timers and no authority: every value here is presentation of supplied
 * evidence, and anything not supplied reads unavailable with its reason.
 *
 * Contracts: docs/contracts/den/home-capabilities.md (frame timing),
 * reason-vocabulary.md (wording) and body.md (the coordinator's reduced pose,
 * separate from the fuller viewing-only approximate room pose). */
(function (root, factory) {
  "use strict";
  var api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.HomeApartmentPerceptionDockModel = api;
})(typeof self !== "undefined" ? self : this, function (root) {
  "use strict";

  var DOCK_STATES = Object.freeze(["collapsed", "default", "expanded", "hidden"]);
  var DETAIL_LEVELS = Object.freeze(["simple", "detailed"]);
  var COCO = Object.freeze(["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
    "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle"]);
  var SHOULDERS = Object.freeze([5, 6]);
  // body.md: head is the nose, else the ears, else the eyes. Never a guessed point.
  var HEAD_FALLBACK = Object.freeze([{ part: "nose", ids: [0] }, { part: "ears", ids: [3, 4] }, { part: "eyes", ids: [1, 2] }]);
  // Interim capability: no registry or session supplies home-den-capabilities-1
  // yet, so a home without a supplied block reads its documented defaults.
  var INTERIM_CAPABILITIES = Object.freeze({
    victoria: Object.freeze({ perception: Object.freeze({ supported: true, available: null, variant: "exact-frame", camera_id: "den", reason: null }),
      body: Object.freeze({ supported: true, placement: "approximate", tracking: null }) }),
  });

  function finite(value) { return typeof value === "number" && Number.isFinite(value); }
  function object(value) { return !!value && typeof value === "object" && !Array.isArray(value); }
  function words(value) { return String(value == null || value === "" ? "unavailable" : value).replace(/_/g, " "); }
  function vocabulary() {
    if (root && root.HomeDenReasonVocabulary) return root.HomeDenReasonVocabulary;
    try { return typeof require === "function" ? require("./home-den-reason-vocabulary.js") : null; } catch (_) { return null; }
  }
  /* Den codes read through Den's vocabulary; an unlisted code reads
   * "Unavailable" and keeps its raw code for Details. */
  function reason(group, code) {
    var vocab = vocabulary();
    if (code == null || code === "") return null;
    var row = vocab ? vocab.describe(group, code) : { group: group, code: String(code), text: "Unavailable", severity: null, legacy: false, known: false };
    return { group: row.group, code: row.code, text: row.text, severity: row.severity, legacy: row.legacy, known: row.known };
  }

  function normalizeDockState(value, fallback) {
    return DOCK_STATES.indexOf(value) >= 0 ? value : (fallback || "default");
  }
  function normalizeDetail(value) { return value === "detailed" ? "detailed" : "simple"; }

  /* The dock mounts from the home's perception capability, never its site id.
   * A supplied home-den-capabilities-1 block wins; otherwise the interim
   * default for that home applies and says so. */
  function perceptionCapability(input) {
    input = input || {};
    var supplied = object(input.capabilities) && object(input.capabilities.perception) ? input.capabilities : null;
    var base = supplied || INTERIM_CAPABILITIES[input.siteId] || null;
    var perception = base && base.perception, body = base && base.body;
    if (!perception || perception.supported !== true) {
      return { variant: "none", supported: false, available: false, cameraId: null, reason: perception ? perception.reason || "perception_unsupported" : "perception_unsupported",
        source: supplied ? "capability" : "interim_default", body: { supported: false, placement: "none", tracking: null } };
    }
    return { variant: perception.variant || (object(perception.frame) || perception.camera ? "exact-frame" : "none"),
      supported: true, available: perception.available === undefined ? null : perception.available,
      cameraId: perception.camera_id || "den", reason: perception.reason || null, camera: perception.camera || null,
      analysis: perception.analysis || null, jev: object(perception.jev) ? perception.jev : null,
      frame: object(perception.frame) ? perception.frame : null, source: supplied ? "capability" : "interim_default",
      body: { supported: !!(body && body.supported === true), placement: (body && body.placement) || "none",
        // Follow needs continuous registered tracking; no producer supplies it.
        tracking: body && body.tracking === "continuous_registered" ? "continuous_registered" : null } };
  }

  /* Frame timing block (home-capabilities.md): producer times plus the view's
   * computed age. An age is shown as a number only when the view called it
   * current with a known clock uncertainty; otherwise its reason is shown. */
  function frameTiming(view) {
    var frame = view && view.frame, descriptor = frame && frame.descriptor, pose = view && view.pose2d;
    var times = (pose && pose.originalTimes) || {};
    var age = (pose && pose.renderable && pose.age) || (frame && frame.age) || { status: "unavailable", seconds: null, uncertainty_s: null, reason: "original_time_unavailable" };
    var qualified = age.status === "current" && finite(age.uncertainty_s) && finite(age.seconds);
    var why = age.status === "stale" && age.reason === "expired" ? reason("perception.age", "expired") : age.reason ? reason("perception.age", age.reason) : null;
    return {
      captured_at: descriptor && finite(descriptor.captured_at) ? descriptor.captured_at : null,
      observed_at: finite(times.observed_at) ? times.observed_at : null,
      available_at: finite(times.available_at) ? times.available_at : null,
      valid_until: finite(times.valid_until) ? times.valid_until : null,
      age: { status: age.status || "unavailable", seconds: qualified ? age.seconds : null, uncertainty_s: finite(age.uncertainty_s) ? age.uncertainty_s : null, reason: age.reason || null },
      qualified: qualified,
      text: qualified ? "Analyzed " + Math.max(0, age.seconds).toFixed(age.seconds < 10 ? 1 : 0) + " s ago" : why ? why.text : "Age unavailable",
      reason: why,
    };
  }

  function sessionDate(session) {
    var raw = session && (session.started_at || session.created_at || session.recorded_at);
    var ms = typeof raw === "number" ? raw * 1000 : typeof raw === "string" ? Date.parse(raw) : NaN;
    return finite(ms) ? new Date(ms) : null;
  }
  function dateLabel(date) {
    if (!date) return null;
    try { return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }); }
    catch (_) { return date.toISOString().slice(0, 10); }
  }
  function replayLabel(session) {
    var label = dateLabel(sessionDate(session));
    return label ? "Replay · recorded " + label : "Replay · recording date unavailable";
  }
  function clockLabel(seconds) {
    if (!finite(seconds)) return null;
    var iso = new Date(seconds * 1000).toISOString();
    return iso.slice(0, 10) + " " + iso.slice(11, 19) + " UTC";
  }

  /* How honest the shown frame is: none, replay, stale (expired, invalid or
   * future by the view's own age), current (qualified age) or unqualified
   * (shown, but its age can't be known). A predecessor held for a few
   * milliseconds while the next frame decodes is noted, not dimmed. Nothing
   * the dock shows drives lighting. */
  function frameState(view, player, timing) {
    if (!player || !player.frame || !view || !view.frame) return "none";
    if (view.frame.status === "recorded") return "replay";
    if (["stale", "invalid", "future"].indexOf(timing.age.status) >= 0) return "stale";
    return timing.qualified ? "current" : "unqualified";
  }

  function badge(state, timing, session, connection) {
    if (state === "none") return connection === "connecting" ? { state: "loading", label: "Connecting" } : { state: "no-session", label: "No session" };
    if (state === "replay") return { state: "replay", label: replayLabel(session).replace(" · recorded ", " · ") };
    if (state === "stale") return { state: "stale", label: timing.age.status === "stale" ? "Stale · expired" : "Stale" };
    if (state === "current") return { state: "current", label: timing.text };
    return { state: "unavailable", label: "Age unavailable" };
  }

  /* Detector, pose and JEV stay separate values: three rows, three sources. */
  function detector(view) {
    var pose = view && view.pose2d;
    if (!pose || !pose.renderable) return { status: "unavailable", count: null, scores: [], reason: pose ? pose.reason || pose.status : "pose_unavailable", text: "Detector: no result on this frame" };
    var boxes = pose.boxes || [], count = pose.persons && pose.persons.length ? pose.persons.length : boxes.length;
    var scores = boxes.map(function (box) { return finite(box.confidence) ? box.confidence : null; }).filter(function (value) { return value !== null; });
    if (pose.boxesReason && !boxes.length) return { status: "unavailable", count: null, scores: [], reason: pose.boxesReason, text: "Detector: person boxes unavailable" };
    return { status: "available", count: count, scores: scores, reason: null,
      text: count === 0 ? "Detector found nobody in this frame" : "Detector found " + count + (count === 1 ? " person" : " people") };
  }
  function poseSummary(view) {
    var pose = view && view.pose2d;
    if (!pose || !pose.renderable) return { status: "unavailable", supported: null, total: null, model: pose && pose.model_id || null, reason: pose ? pose.reason || pose.status : "pose_unavailable", text: "Pose: not on this frame" };
    var persons = pose.persons || [];
    if (!persons.length) {
      var supplied = (pose.keypoints || []).length;
      return { status: "available", supported: supplied, total: null, model: pose.model_id || null, reason: null,
        text: supplied ? "Pose: " + supplied + " supplied joints" : "Pose: no supported joints" };
    }
    var supported = 0, total = 0;
    persons.forEach(function (person) { (person.joints || []).forEach(function (joint) { total++; if (joint.status === "supported") supported++; }); });
    return { status: "available", supported: supported, total: total, model: pose.model_id || null, reason: null,
      text: "Pose: " + supported + " of " + total + " joints supported" + (total > supported ? ", the rest missing" : "") };
  }

  function jevModelRows(view) {
    return ((view && view.models) || []).filter(function (row) { return /jev/i.test([row.family, row.model_id, row.source_id].join(" ")); });
  }
  function resultObject(row) { return row && object(row.result) ? row.result : null; }
  /* JEV: a judged distribution, an explicit abstention with its reason, or
   * unavailable. Probabilities are JEV's judgment, not detector or pose scores. */
  function jev(view, capability) {
    var persons = (view && view.pose2d && view.pose2d.persons) || [];
    var judged = persons.map(function (person) { return person.judgment; }).filter(Boolean);
    var available = judged.find(function (row) { return row.status === "available"; });
    var health = capability && capability.jev && capability.jev.ok === false && capability.jev.detail ? reason("activity_loop.health", capability.jev.detail) : null;
    if (available) {
      var probabilities = available.judgment.probabilities || {};
      var distribution = Object.keys(probabilities).map(function (key) { return { activity: key, label: (reason("activity.label", key) || {}).text || words(key), p: probabilities[key] }; })
        .sort(function (a, b) { return b.p - a.p; });
      return { status: "judged", activity: available.judgment.activity, label: (reason("activity.label", available.judgment.activity) || {}).text || words(available.judgment.activity),
        probability: probabilities[available.judgment.activity], confidence: available.judgment.confidence, model: available.judgment.model_version || null,
        distribution: distribution, reasons: [], window: null, health: health, source: "person_judgment" };
    }
    var abstained = judged.find(function (row) { return row.reason === "insufficient_evidence"; });
    var rows = jevModelRows(view), rowAbstained = rows.find(function (row) {
      var result = resultObject(row); return result && (result.abstained === true || result.activity === "insufficient_evidence");
    });
    if (abstained || rowAbstained) {
      var result = resultObject(rowAbstained) || {};
      var reasons = [reason("activity.label", "insufficient_evidence").text];
      if (typeof result.reason === "string") reasons.push(words(result.reason));
      if (rowAbstained && rowAbstained.reason) reasons.push(words(rowAbstained.reason));
      return { status: "abstained", activity: null, label: "Abstained", probability: null, confidence: null, model: rowAbstained ? rowAbstained.model_id || null : null,
        distribution: [], reasons: reasons, window: windowFacts(result), health: health, source: abstained ? "person_judgment" : "model_row" };
    }
    var rowJudged = rows.find(function (row) { var r = resultObject(row); return r && typeof r.activity === "string"; });
    if (rowJudged) {
      var value = resultObject(rowJudged), dist = object(value.distribution) ? value.distribution : null;
      return { status: "judged", activity: value.activity, label: (reason("activity.label", value.activity) || {}).text || words(value.activity),
        probability: finite(value.probability) ? value.probability : dist && finite(dist[value.activity]) ? dist[value.activity] : null, confidence: null,
        model: rowJudged.model_id || null, source: "model_row", health: health, window: windowFacts(value), reasons: Array.isArray(value.reasons) ? value.reasons.map(String).slice(0, 6) : [],
        distribution: dist ? Object.keys(dist).filter(function (key) { return finite(dist[key]); }).map(function (key) { return { activity: key, label: (reason("activity.label", key) || {}).text || words(key), p: dist[key] }; }).sort(function (a, b) { return b.p - a.p; }) : [] };
    }
    var why = judged.length ? judged[0].reason : rows.length ? rows[0].reason || rows[0].status : null;
    return { status: "unavailable", activity: null, label: "Not available", probability: null, confidence: null, model: null, distribution: [],
      reasons: [why ? words(why) : "No JEV result supplied"], window: null, health: health, source: null };
  }
  function windowFacts(result) {
    if (!object(result)) return null;
    var out = {};
    [["window_s", "window_s"], ["frames", "frames"], ["usable_frames", "usable"], ["min_usable", "min_usable"]].forEach(function (pair) {
      if (finite(result[pair[0]])) out[pair[1]] = result[pair[0]];
    });
    return Object.keys(out).length ? out : null;
  }

  /* Seen → Interpreted → Proposed → Did. Proposed and Did never invent a
   * record: no perception proposal or execution record exists yet. */
  function trace(input) {
    var state = input.frameState, det = input.detector, judgment = input.jev, none = state === "none";
    var unusable = state === "stale" || state === "replay";
    var seen = none ? { value: "Nothing", tone: "muted", reason: input.connected ? "No analyzed frame yet" : "No session connected" }
      : { value: (det.status === "available" ? (det.count === 0 ? "Nobody" : det.count === 1 ? "Person" : det.count + " people") : "Frame") + (input.frameId ? " · " + input.frameId : ""), tone: unusable ? "muted" : "", reason: null };
    var interpreted = none ? { value: "Nothing", tone: "muted", reason: "Nothing analyzed" }
      : judgment.status === "abstained" ? { value: "Abstained", tone: "muted", reason: judgment.reasons.join(" · ") }
      : judgment.status === "judged" ? { value: judgment.label + (input.detail === "detailed" && finite(judgment.probability) ? " · " + judgment.probability.toFixed(2) : ""), tone: unusable ? "muted" : "", reason: null }
      : { value: "Not available", tone: "muted", reason: judgment.reasons.join(" · ") };
    var proposedReason = none ? "Nothing analyzed" : judgment.status === "abstained" ? "JEV abstained, so nothing is proposed"
      : unusable ? "This frame is not used for lighting" : "Home has no perception proposal record yet";
    return [
      { key: "seen", label: "Seen", value: seen.value, tone: seen.tone, reason: seen.reason, disabled: false },
      { key: "interpreted", label: "Interpreted", value: interpreted.value, tone: interpreted.tone, reason: interpreted.reason, disabled: false },
      { key: "proposed", label: "Proposed", value: "Nothing", tone: "muted", reason: proposedReason, disabled: false },
      { key: "did", label: "Did", value: none ? "Nothing" : "Not available", tone: "muted",
        reason: "No execution record. Nothing runs from perception without you, and these fixtures aren't commissioned.", disabled: false },
    ];
  }

  function summary(input) {
    var state = input.frameState, judgment = input.jev;
    if (!input.discovered && !input.connected) return "Camera view closed · nothing is analyzed here until you connect";
    if (state === "none") return input.connected ? "Connected · waiting for an analyzed frame" : input.sessions ? "Not viewing a Den session" : "No Den camera session";
    var head = judgment.status === "abstained" ? "JEV abstained · " + (judgment.reasons[0] || "no reason supplied")
      : judgment.status === "judged" ? "JEV reads " + judgment.label.toLowerCase() : input.detector.text;
    if (state === "replay") return input.replayLabel + " · " + head + " · nothing here is live";
    if (state === "stale") return "Last frame stale · " + head + " · not used for lighting";
    return head + " · proposes nothing";
  }

  /* The viewing-only approximate_room_pose can supply the full joint set.
   * The coordinator's separate person_pose still uses shoulders and head.
   * Keep only actual, visible positions; never synthesize missing joints. */
  function bodyInRoom(placement) {
    if (!placement || placement.renderable !== true || !Array.isArray(placement.joints)) return null;
    var byId = new Map(placement.joints.filter(function (joint) { return joint && joint.visible !== false
      && Number.isInteger(joint.id) && joint.id >= 0 && joint.id < COCO.length
      && Array.isArray(joint.position_m) && joint.position_m.length === 3 && joint.position_m.every(finite);
    }).map(function (joint) { return [joint.id, joint]; }));
    var shoulders = SHOULDERS.filter(function (id) { return byId.has(id); });
    var head = HEAD_FALLBACK.find(function (row) { return row.ids.every(function (id) { return byId.has(id); }); }) || null;
    var joints = Array.from(byId.values());
    var edges = (placement.edges || []).filter(function (edge) { return Array.isArray(edge) && edge.length === 2
      && edge[0] !== edge[1] && byId.has(edge[0]) && byId.has(edge[1]); });
    return { joints: joints, edges: edges, shoulders: shoulders.length, head: head ? head.part : null,
      text: joints.length + " of 17 supplied joints · shoulders " + shoulders.length + " of 2 · head " + (head ? "from " + head.part : "missing"),
      label: "One analyzed frame, not tracked" };
  }
  /* The room layer draws all supplied viewing joints. All provenance,
   * freshness, approximation and authority fields pass through unchanged. */
  function roomLayerView(view) {
    if (!view || !view.approximateRoomPose || view.approximateRoomPose.renderable !== true) return view;
    var body = bodyInRoom(view.approximateRoomPose);
    if (!body) return view;
    var copy = Object.assign({}, view);
    copy.approximateRoomPose = Object.assign({}, view.approximateRoomPose, { joints: body.joints, edges: body.edges });
    return copy;
  }

  // App-lifetime display history only. Source times and expiry stay original;
  // the history never grants current-evidence, geometry or lighting authority.
  function modelUpdates(previous, view) {
    if (!view || view.status !== 'available' || !view.scope) return previous || [];
    var context = JSON.stringify([view.scope, view.sourceEpoch]);
    var rows = (previous || []).filter(function (entry) { return entry.context === context; });
    var changed = rows.length !== (previous || []).length;
    (view.models || []).forEach(function (row) {
      var snapshot = Object.assign({}, row); delete snapshot.age;
      var key = JSON.stringify(snapshot);
      if (rows.some(function (entry) { return entry.key === key; })) return;
      rows.unshift({ context: context, key: key, scope: Object.assign({}, view.scope), model: snapshot });
      changed = true;
    });
    return changed ? rows.slice(0, 20) : previous || [];
  }

  /* Open in Lab: session always; time only from the frame's capture time
   * inside the session's own span; Lab has no person selection to carry. */
  function labIntent(input) {
    input = input || {};
    var session = input.session, frame = input.frame;
    if (!session || typeof session.id !== "string") return { available: false, reason: "Select or connect a Den session to open it in Lab", session_id: null, t_s: null };
    var started = sessionDate(session), captured = frame && finite(frame.captured_at) ? frame.captured_at : null, t = null, timeReason = null;
    if (captured === null) timeReason = "No analyzed frame, so Lab opens at the session start";
    else if (!started) timeReason = "Session start time unavailable, so Lab opens at the session start";
    else {
      t = captured - started.getTime() / 1000;
      var duration = finite(session.duration_s) ? session.duration_s : null;
      if (!(t >= 0) || duration !== null && t > duration) { t = null; timeReason = "Frame time is outside this session, so Lab opens at the session start"; }
    }
    return { available: true, reason: null, session_id: session.id, camera_id: session.camera_id || null, frame_id: frame && frame.frame_id || null,
      t_s: t === null ? null : Math.round(t * 10) / 10, time_reason: timeReason, selection: null, selection_reason: "Lab has no person selection to carry" };
  }

  return Object.freeze({ DOCK_STATES: DOCK_STATES, DETAIL_LEVELS: DETAIL_LEVELS, COCO: COCO, INTERIM_CAPABILITIES: INTERIM_CAPABILITIES,
    reason: reason, words: words, normalizeDockState: normalizeDockState, normalizeDetail: normalizeDetail,
    perceptionCapability: perceptionCapability, frameTiming: frameTiming, frameState: frameState, badge: badge,
    replayLabel: replayLabel, clockLabel: clockLabel, detector: detector, pose: poseSummary, jev: jev, trace: trace,
    summary: summary, bodyInRoom: bodyInRoom, roomLayerView: roomLayerView, modelUpdates: modelUpdates, labIntent: labIntent });
});
