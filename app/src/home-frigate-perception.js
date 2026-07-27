/*
 * Frigate review/event perception normalizer.
 *
 * This module is intentionally dependency-free and works in both the browser
 * boot chain and Node tests. Frigate payloads vary by version and topic; keep
 * parsing tolerant and emit one conservative HomePerceptionEvent shape.
 */
(function (root, factory) {
  const api = factory();
  root.HomeFrigatePerception = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  const CAMERA_ROOM = {
    living_room: "living room",
    kitchen: "kitchen",
    dining_room: "dining room",
    workshop: "workshop",
    driveway: "driveway",
  };

  const OUTDOOR_CAMERAS = new Set(["driveway"]);
  const DEFAULT_FRESH_MS = 90 * 1000;
  const DISPLAYABLE_SOURCES = new Set(["frigate_review", "semantic_trigger"]);

  function asObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function parseJsonMaybe(value) {
    if (!value) return null;
    if (typeof value === "object") return value;
    if (typeof value !== "string") return null;
    try { return JSON.parse(value); } catch (_) { return null; }
  }

  function compactString(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function safeCamera(value) {
    return compactString(value).toLowerCase().replace(/[^a-z0-9_ -]+/g, "").replace(/[\s-]+/g, "_");
  }

  function coalesce(...values) {
    for (const value of values) {
      if (value !== undefined && value !== null && value !== "") return value;
    }
    return undefined;
  }

  function firstArray(...values) {
    for (const value of values) {
      if (Array.isArray(value)) return value.filter(Boolean);
    }
    return [];
  }

  function normalizeUrl(value) {
    const s = compactString(value);
    return s || undefined;
  }

  function eventTimeMs(value, fallback = Date.now()) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return value > 1e12 ? value : value * 1000;
    }
    const parsed = Date.parse(value || "");
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function summarizeEvent({ camera, label, subLabel, semanticHint, zones }) {
    const room = CAMERA_ROOM[camera] || camera.replace(/_/g, " ");
    const parts = [];
    if (semanticHint) parts.push(`possible ${semanticHint}`);
    else if (subLabel) parts.push(String(subLabel));
    else if (label) parts.push(String(label));
    else parts.push("activity");
    if (zones && zones.length) parts.push(`in ${zones.map((z) => String(z).replace(/_/g, " ")).join(", ")}`);
    return `${room}: ${parts.join(" ")}`;
  }

  function unwrapEnvelope(rawEnvelope) {
    const envelope = asObject(rawEnvelope);
    const topic = compactString(envelope.topic || envelope.mqtt_topic || "");
    const payload = parseJsonMaybe(envelope.payload_json || envelope.payload || envelope.raw || envelope.data) || envelope;
    return { topic, payload: asObject(payload), envelope };
  }

  function normalizeHomePerceptionEvent(rawEnvelope, options = {}) {
    const nowMs = Number.isFinite(options.nowMs) ? options.nowMs : Date.now();
    const { topic, payload, envelope } = unwrapEnvelope(rawEnvelope);
    const before = asObject(payload.before);
    const after = asObject(payload.after);
    const review = asObject(payload.review || payload.data);
    const eventData = Object.keys(after).length ? after : review;

    const camera = safeCamera(coalesce(
      payload.camera,
      eventData.camera,
      review.camera,
      after.camera,
      before.camera,
      envelope.camera,
    ));
    if (!camera) return null;

    const id = compactString(coalesce(
      payload.id,
      payload.review_id,
      review.id,
      eventData.id,
      after.id,
      envelope.id,
      `${topic || "frigate"}:${camera}:${coalesce(eventData.start_time, payload.start_time, nowMs)}`,
    ));

    const label = compactString(coalesce(
      eventData.label,
      eventData.data?.label,
      payload.label,
      review.label,
    ));
    const subLabelRaw = coalesce(eventData.sub_label, eventData.subLabel, payload.sub_label, review.sub_label);
    const subLabel = Array.isArray(subLabelRaw) ? compactString(subLabelRaw[0]) : compactString(subLabelRaw);
    const attributes = asObject(coalesce(eventData.attributes, payload.attributes, review.attributes, {}));
    const semanticHint = compactString(coalesce(
      attributes.semantic_trigger,
      attributes.trigger,
      attributes.description,
      eventData.semantic_trigger,
      payload.semantic_hint,
      payload.trigger,
    ));
    const zones = firstArray(eventData.current_zones, eventData.entered_zones, eventData.zones, payload.zones, review.zones);
    const source = semanticHint ? "semantic_trigger"
      : topic.includes("reviews") || payload.type === "review" ? "frigate_review"
      : "frigate_event";

    const occurredMs = eventTimeMs(coalesce(
      eventData.start_time,
      review.start_time,
      payload.start_time,
      payload.created_at,
      envelope.received_at,
      envelope.ts,
    ), nowMs);
    const freshMs = Number.isFinite(options.freshMs) ? options.freshMs : DEFAULT_FRESH_MS;
    const freshUntil = new Date(occurredMs + freshMs).toISOString();

    const urls = {
      thumbnail: normalizeUrl(coalesce(payload.thumbnail_url, review.thumbnail_url, eventData.thumbnail_url)),
      snapshot: normalizeUrl(coalesce(payload.snapshot_url, review.snapshot_url, eventData.snapshot_url)),
      clip: normalizeUrl(coalesce(payload.clip_url, review.clip_url, eventData.clip_url)),
    };
    for (const key of Object.keys(urls)) if (!urls[key]) delete urls[key];

    const normalized = {
      version: 1,
      id,
      source,
      camera,
      room: CAMERA_ROOM[camera] || camera.replace(/_/g, " "),
      occurred_at: new Date(occurredMs).toISOString(),
      fresh_until: freshUntil,
      summary: compactString(payload.summary || review.summary || summarizeEvent({ camera, label, subLabel, semanticHint, zones })),
      identity_allowed: !OUTDOOR_CAMERAS.has(camera),
      raw_topic: topic || undefined,
    };
    if (label) normalized.label = label;
    if (subLabel) normalized.sub_label = subLabel;
    if (zones.length) normalized.zones = zones;
    if (Object.keys(attributes).length) normalized.attributes = attributes;
    if (semanticHint) normalized.semantic_hint = semanticHint;
    const confidence = eventData.score ?? eventData.top_score ?? eventData.data?.score ?? eventData.data?.top_score ?? payload.score ?? review.score;
    if (Number.isFinite(Number(confidence))) {
      normalized.confidence = Number(confidence);
    }
    if (Object.keys(urls).length) normalized.urls = urls;
    normalized.event_ids = firstArray(payload.event_ids, review.event_ids);
    if (!normalized.event_ids.length && eventData.id) normalized.event_ids = [String(eventData.id)];
    if (!normalized.event_ids.length) delete normalized.event_ids;
    return normalized;
  }

  function perceptionEventKey(event) {
    if (!event) return "";
    const semantic = event.semantic_hint || event.sub_label || event.label || "";
    return [event.id, event.source, event.camera, semantic].filter(Boolean).join("|");
  }

  function isDuplicatePerceptionEvent(prevEvents, event, options = {}) {
    if (!event) return true;
    const lookback = Number.isFinite(options.lookback) ? options.lookback : 60;
    const nowMs = Number.isFinite(options.nowMs) ? options.nowMs : Date.now();
    const windowMs = Number.isFinite(options.windowMs) ? options.windowMs : 2 * 60 * 1000;
    const key = perceptionEventKey(event);
    const semantic = compactString(event.semantic_hint || event.sub_label || event.label || event.summary);
    for (let i = prevEvents.length - 1; i >= Math.max(0, prevEvents.length - lookback); i--) {
      const candidate = prevEvents[i];
      const data = candidate?.perception || candidate?.homePerception;
      if (data && perceptionEventKey(data) === key) return true;
      if (candidate?.kind !== "perception") continue;
      const sameCamera = safeCamera(candidate.camera || data?.camera || "") === event.camera;
      const sameText = compactString(candidate.text) === compactString(event.summary);
      const sameSemantic = semantic && compactString(data?.semantic_hint || data?.sub_label || data?.label || data?.summary) === semantic;
      const candidateMs = eventTimeMs(candidate.createdAt || candidate.time, nowMs);
      if (sameCamera && (sameText || sameSemantic) && Math.abs(nowMs - candidateMs) <= windowMs) return true;
    }
    return false;
  }

  function toPerceptionFeedEvent(event, deps = {}) {
    if (!event) return null;
    if (!DISPLAYABLE_SOURCES.has(event.source)) return null;
    let snapshotUrl = event.urls?.snapshot || event.urls?.thumbnail || "";
    if (!snapshotUrl && Array.isArray(event.event_ids) && event.event_ids[0]) {
      const serviceBase = deps.frigateBase ||
        (typeof window !== "undefined" && window.HomeServices?.get ? window.HomeServices.get("frigate") : "") ||
        "/proxy/frigate";
      snapshotUrl = `${String(serviceBase).replace(/\/+$/, "")}/api/events/${encodeURIComponent(event.event_ids[0])}/thumbnail.jpg`;
    }
    return {
      id: deps.nextId ? deps.nextId() : event.id,
      kind: "perception",
      channel: "perception",
      time: deps.fmtTime ? deps.fmtTime() : new Date().toTimeString().slice(0, 8),
      text: event.summary,
      camera: event.camera,
      snapshotUrl,
      imageMode: event.semantic_hint ? "semantic" : undefined,
      perception: event,
      createdAt: Date.now(),
    };
  }

  function freshPerceptionHints(events, nowMs = Date.now()) {
    return (events || [])
      .map((e) => e?.perception || e?.homePerception || e)
      .filter((e) => e && Date.parse(e.fresh_until || "") >= nowMs);
  }

  return {
    normalizeHomePerceptionEvent,
    isDuplicatePerceptionEvent,
    perceptionEventKey,
    toPerceptionFeedEvent,
    freshPerceptionHints,
  };
});
