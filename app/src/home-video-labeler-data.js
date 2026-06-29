/* home-video-labeler-data.js — data layer for the /labeler view.
 *
 * Plain JS, JSX-free, IIFE-wrapped (loads as a raw script, not through
 * Babel — same contract as home-apartment-data.js). Three concerns:
 *   1. video-labeler service client — tauriFetch wrapper that NEVER
 *      throws; every call resolves to {ok, status, data, error} so the
 *      overlay renders offline chips instead of crashing the app.
 *   2. URL builders for media elements (<video src>, sprite sheets).
 *      These return plain strings because media elements bypass
 *      tauriFetch entirely — which is also why they must return null
 *      under Simulation Mode (sim containment lives HERE, not only in
 *      the component gate).
 *   3. Frozen label taxonomies + colors, timecode formatting, and the
 *      pure segment math the M1 timeline editor builds on (clamp /
 *      non-overlap / split / merge / snap) — unit-testable, no DOM.
 *      Multi-person: non-overlap is per-(lane, person_slot) — clamping
 *      only sees neighbors with the same slot ((seg.person_slot||null));
 *      slot-less lanes (quality/custom) form one group = M1 behavior.
 */
(function () {
  "use strict";

  const BASE_KEY = "videoLabeler.base";
  const DEFAULT_BASE = "http://192.168.0.100:8099";

  function defaultBase() {
    return (typeof window !== "undefined" && window.HG_DEFAULT_VIDEO_LABELER_BASE) || DEFAULT_BASE;
  }

  /* Service base URL. Returns null while Simulation Mode is active:
     media URLs bypass tauriFetch's sim guard, so a null base is what
     actually keeps the sim hermetic. */
  function vlBase() {
    if (typeof window !== "undefined" && window.__SIM_ACTIVE === true) return null;
    try {
      const v = localStorage.getItem(BASE_KEY);
      return (v || defaultBase()).replace(/\/+$/, "");
    } catch (e) {
      return defaultBase();
    }
  }

  /* ---------------- API client ---------------- */

  /* No-throw fetch. The app must still BOOT (and the overlay must still
     render) with the service down — callers branch on .ok, never catch. */
  async function vlApi(path, init) {
    const base = vlBase();
    if (!base) return { ok: false, status: 0, data: null, error: "simulation mode" };
    const fetcher = (typeof window !== "undefined" && window.tauriFetch) || fetch;
    try {
      const r = await fetcher(base + path, Object.assign({ cache: "no-store" }, init || {}));
      let data = null;
      try { data = await r.json(); } catch (e) { /* non-JSON body */ }
      if (!r.ok) {
        const detail = data && (data.detail || data.error);
        return { ok: false, status: r.status, data, error: detail ? String(detail) : ("HTTP " + r.status) };
      }
      return { ok: true, status: r.status, data, error: null };
    } catch (e) {
      return { ok: false, status: 0, data: null, error: String((e && e.message) || e) };
    }
  }

  const vlEnc = encodeURIComponent;

  function health() { return vlApi("/healthz"); }
  function listVideos() { return vlApi("/api/video-labeler/videos"); }
  function getVideo(id) { return vlApi("/api/video-labeler/videos/" + vlEnc(id)); }
  function importManual(batchName) {
    return vlApi("/api/video-labeler/import/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(batchName ? { batch_name: batchName } : {}),
    });
  }
  /* Sprite manifest: {tile_w, tile_h, cols, interval_s, count, sheets:[url,...]} */
  function getSprite(id) { return vlApi("/api/video-labeler/videos/" + vlEnc(id) + "/sprite"); }
  function listJobs(params) {
    const q = [];
    if (params && params.state) q.push("state=" + vlEnc(params.state));
    if (params && params.type) q.push("type=" + vlEnc(params.type));
    return vlApi("/api/video-labeler/jobs" + (q.length ? "?" + q.join("&") : ""));
  }
  function cancelJob(id) { return vlApi("/api/video-labeler/jobs/" + vlEnc(id) + "/cancel", { method: "POST" }); }
  function retryJob(id) { return vlApi("/api/video-labeler/jobs/" + vlEnc(id) + "/retry", { method: "POST" }); }

  /* ---------------- labels API (M1) ----------------
   * Contract (backend M1, built in parallel — coded exactly against it):
   *   GET  /labels/ontology               → {axes, aux_axes, review_states, custom}
   *   GET  /videos/{id}/labels            → {revision, axes:{activity_primary,posture,quality,custom}}
   *   PUT  /videos/{id}/labels            → 200 {revision, id_map?} | 409 {revision, axes} | 400 {detail}
   *   POST /videos/{id}/segments/{sid}/review {action} → {revision, segment}
   *   custom labels CRUD under /custom-labels (+/merge /hide /promote). */

  const vlJsonInit = (method, payload) => ({
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload == null ? {} : payload),
  });

  /* Lane-axis (client) vs db-axis (server) naming: the labels doc speaks
     activity_primary/quality while custom-label rows store activity/quality —
     normalize at this boundary so every consumer sees lane names only. */
  function vlServerAxis(axis) {
    if (axis === "activity_primary") return "activity";
    if (axis === "quality_flags") return "quality";
    return axis;
  }
  function vlClientAxis(axis) {
    return axis === "activity" ? "activity_primary" : axis;
  }
  async function getOntology() {
    const r = await vlApi("/api/video-labeler/labels/ontology");
    if (r.ok && r.data && Array.isArray(r.data.custom)) {
      r.data.custom = r.data.custom.map((c) =>
        Object.assign({}, c, { axis: vlClientAxis(c.axis) }));
    }
    return r;
  }
  function getLabels(videoId) {
    return vlApi("/api/video-labeler/videos/" + vlEnc(videoId) + "/labels");
  }
  /* body = {revision, axes}; resolves {ok,status,data,error} — 409 carries
     the server snapshot in data, 400 carries {detail}. */
  function putLabels(videoId, body) {
    return vlApi("/api/video-labeler/videos/" + vlEnc(videoId) + "/labels", vlJsonInit("PUT", body));
  }
  function reviewSegment(videoId, segId, action) {
    return vlApi(
      "/api/video-labeler/videos/" + vlEnc(videoId) + "/segments/" + vlEnc(segId) + "/review",
      vlJsonInit("POST", { action: action })
    );
  }

  /* ---------------- suggestions + calibration (M3) ----------------
   * Coded against videolabeler/api/labels.py + media.py + prelabel.py:
   *   GET /videos/{id}/suggestions → {axes:{activity_primary|posture|
   *       quality|custom:[SEG]}} — the prelabel layer; carries NO revision
   *       (suggestions never bump it). Accepting goes through the review
   *       endpoint, which graduates the row into the canonical lane.
   *   GET /videos/{id}/windows/{wid}/keyframes → {video_id, window_id,
   *       keyframes:[{t, frame, crop|null}]} — frame/crop are
   *       service-relative file paths (sibling endpoint serves the JPEGs).
   *   GET /calibration?axis= → {axes:{<axis>:{deciles, top_deciles:{n,
   *       accepted, acceptance, wilson_lower}, bulk_ok}}, wilson_min}. */
  function getSuggestions(videoId) {
    return vlApi("/api/video-labeler/videos/" + vlEnc(videoId) + "/suggestions");
  }
  function getWindowKeyframes(videoId, windowId) {
    return vlApi(
      "/api/video-labeler/videos/" + vlEnc(videoId) + "/windows/" + vlEnc(windowId) + "/keyframes"
    );
  }
  function getCalibration(axis) {
    return vlApi("/api/video-labeler/calibration" + (axis ? "?axis=" + vlEnc(axis) : ""));
  }

  /* ---------------- neighbors + cluster signals (M4) ----------------
   * Coded against videolabeler/api/embeddings.py:
   *   GET /segments/{sid}/neighbors?k= → {segment_id, model_id, k,
   *       query_windows, neighbors:[{window_id, video_id, start_s, end_s,
   *       t_center, distance, labels:{activity, posture}, keyframes}],
   *       note?} — nearest REVIEWED non-holdout windows by cosine
   *       distance over person-crop embeddings. distance ∈ [0, 2].
   *   GET /videos/{id}/window-signals → {video_id, model_id,
   *       cluster_run_id, windows:[{window_id, start_s, end_s, cluster_id,
   *       cluster_dist, is_outlier}]} from the LATEST ready cluster run
   *       (is_outlier null when the window isn't in any run).
   * Both 404 on older deploys — callers degrade silently. */
  function getNeighbors(segmentId, k) {
    return vlApi("/api/video-labeler/segments/" + vlEnc(segmentId) + "/neighbors"
      + (k ? "?k=" + vlEnc(k) : ""));
  }
  function getWindowSignals(videoId) {
    return vlApi("/api/video-labeler/videos/" + vlEnc(videoId) + "/window-signals");
  }

  /* Analysis-window id convention (videolabeler/jobs/windows.py):
     aw_<video>_<start_ms>. seg_to_api serializes no window linkage, but
     suggestions carry their window's EXACT geometry — derive the id from
     the suggestion's start_s to fetch the evidence keyframes. */
  function windowIdFor(videoId, startS) {
    return "aw_" + videoId + "_" + Math.round((Number(startS) || 0) * 1000);
  }

  /* SEG disagreement, tolerant: today's serializer drops evidence_json
     entirely; if it ever surfaces, accept seg.evidence.disagreement or
     seg.aux.disagreement. Absent/non-numeric → 0 (no badge). */
  function suggestionDisagreement(seg) {
    if (!seg) return 0;
    let d = null;
    if (seg.evidence && typeof seg.evidence === "object") d = seg.evidence.disagreement;
    if ((d == null || typeof d !== "number") && seg.aux && typeof seg.aux === "object") {
      d = seg.aux.disagreement;
    }
    return (typeof d === "number" && d > 0) ? d : null;
  }

  /* Human-readable evidence text for the inspector, tolerant of shape
     variants (aux.notes, evidence as string, evidence.{evidence,notes,
     other_hint}); null when nothing usable exists. */
  function suggestionEvidenceText(seg) {
    if (!seg) return null;
    if (seg.aux && typeof seg.aux === "object" &&
        typeof seg.aux.notes === "string" && seg.aux.notes) return seg.aux.notes;
    const ev = seg.evidence;
    if (typeof ev === "string" && ev) return ev;
    if (ev && typeof ev === "object") {
      if (typeof ev.evidence === "string" && ev.evidence) return ev.evidence;
      if (typeof ev.notes === "string" && ev.notes) return ev.notes;
      if (typeof ev.other_hint === "string" && ev.other_hint) return "hint: " + ev.other_hint;
    }
    return null;
  }
  function listCustomLabels(q, axis) {
    const p = [];
    if (q) p.push("q=" + vlEnc(q));
    if (axis) p.push("axis=" + vlEnc(vlServerAxis(axis)));
    return vlApi("/api/video-labeler/custom-labels" + (p.length ? "?" + p.join("&") : ""));
  }
  function createCustomLabel(payload) {
    const body = Object.assign({}, payload, { axis: vlServerAxis(payload.axis) });
    return vlApi("/api/video-labeler/custom-labels", vlJsonInit("POST", body));
  }
  function mergeCustomLabel(slug, into) {
    return vlApi("/api/video-labeler/custom-labels/" + vlEnc(slug) + "/merge", vlJsonInit("POST", { into_slug: into }));
  }
  function hideCustomLabel(slug) {
    return vlApi("/api/video-labeler/custom-labels/" + vlEnc(slug) + "/hide", vlJsonInit("POST", {}));
  }
  function promoteCustomLabel(slug, canonicalValue) {
    return vlApi(
      "/api/video-labeler/custom-labels/" + vlEnc(slug) + "/promote",
      vlJsonInit("POST", { canonical_value: canonicalValue })
    );
  }

  /* ---------------- draft persistence (localStorage) ----------------
   * Key videoLabeler.draft.<id>; record = {doc, baseRevision, rev, ts}.
   * localStorage is shared with hg-events/prefs, so drafts are pruned
   * aggressively (>14 days old or beyond the 10 newest) and every touch
   * is try/catch'd — a full or disabled store must never throw. */

  const DRAFT_PREFIX = "videoLabeler.draft.";
  const DRAFT_MAX_AGE_MS = 14 * 24 * 3600 * 1000;
  const DRAFT_MAX_COUNT = 10;

  function pruneDrafts(keepKey) {
    try {
      const now = Date.now();
      const kept = [];
      const drop = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (!k || k.indexOf(DRAFT_PREFIX) !== 0) continue;
        let ts = 0;
        try { ts = (JSON.parse(localStorage.getItem(k)) || {}).ts || 0; } catch (e) { /* corrupt */ }
        if (k !== keepKey && (!ts || now - ts > DRAFT_MAX_AGE_MS)) drop.push(k);
        else kept.push({ k: k, ts: ts });
      }
      kept.sort((a, b) => b.ts - a.ts);
      for (let j = DRAFT_MAX_COUNT; j < kept.length; j++) {
        if (kept[j].k !== keepKey) drop.push(kept[j].k);
      }
      for (const k of drop) localStorage.removeItem(k);
    } catch (e) { /* storage unavailable */ }
  }

  function saveDraft(videoId, draft) {
    try {
      const rec = Object.assign({ ts: Date.now() }, draft || {});
      localStorage.setItem(DRAFT_PREFIX + videoId, JSON.stringify(rec));
      pruneDrafts(DRAFT_PREFIX + videoId);
    } catch (e) { /* quota/disabled — drafts are best-effort */ }
  }

  function loadDraft(videoId) {
    try {
      const raw = localStorage.getItem(DRAFT_PREFIX + videoId);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function clearDraft(videoId) {
    try { localStorage.removeItem(DRAFT_PREFIX + videoId); } catch (e) { /* */ }
  }

  /* Validate a draft doc against the current server doc: segments whose
     ids are neither local temps nor present server-side were superseded
     by another session — drop them and report the count. */
  function validateDraftDoc(draftAxes, serverAxes) {
    const serverIds = {};
    for (const ax in (serverAxes || {})) {
      for (const s of (serverAxes[ax] || [])) serverIds[s.id] = true;
    }
    const axes = {};
    let dropped = 0;
    for (const ax in (draftAxes || {})) {
      axes[ax] = (draftAxes[ax] || []).filter((s) => {
        const id = s && s.id;
        const ok = typeof id === "string" &&
          (id.indexOf("tmp_") === 0 || id.indexOf("seg_local_") === 0 || serverIds[id] === true);
        if (!ok) dropped += 1;
        return ok;
      });
    }
    return { axes: axes, dropped: dropped };
  }

  /* ---------------- media URL builders (plain strings; null in sim) ---------------- */

  function streamUrl(id, opts) {
    const base = vlBase();
    if (!base) return null;
    const original = opts && opts.original ? 1 : 0;
    return base + "/api/video-labeler/videos/" + vlEnc(id) + "/stream?original=" + original;
  }

  function spriteManifestUrl(id) {
    const base = vlBase();
    if (!base) return null;
    return base + "/api/video-labeler/videos/" + vlEnc(id) + "/sprite";
  }

  /* Keyframe refs come from the keyframes listing (entry.frame / entry.crop)
     and are service-relative ("/api/video-labeler/videos/…/keyframes/
     frame_0.jpg") or absolute. Null under sim (media bypasses tauriFetch). */
  function keyframeUrl(ref) {
    const base = vlBase();
    if (!base || !ref) return null;
    const r = String(ref);
    if (/^https?:\/\//i.test(r)) return r;
    return base + (r.charAt(0) === "/" ? "" : "/") + r;
  }

  /* Sheet refs come out of the manifest's sheets[] and may be absolute or
     service-relative; pass the manifest entry as `ref`. A bare index is
     also accepted (conventional /sprite/<n> path) for callers without a
     manifest in hand. */
  function spriteSheetUrl(id, ref) {
    const base = vlBase();
    if (!base) return null;
    if (typeof ref === "string") {
      if (/^https?:\/\//i.test(ref)) return ref;
      return base + (ref.charAt(0) === "/" ? "" : "/") + ref;
    }
    return base + "/api/video-labeler/videos/" + vlEnc(id) + "/sprite/" + ref;
  }

  /* ---------------- taxonomies (canonical, frozen) ---------------- */

  const VL_ACTIVITY = Object.freeze([
    "cooking", "food_prep", "eating_drinking", "washing_dishes", "cleaning",
    "laundry", "organizing", "reading", "watching_tv", "working_computer",
    "phone_use", "conversation", "resting", "sleeping", "exercising",
    "stretching_yoga", "walking", "entering_leaving", "personal_care",
    "pet_care", "idle_present", "no_person", "unknown",
  ]);

  const VL_POSTURE = Object.freeze([
    "standing", "walking", "sitting_upright", "sitting_reclined",
    "lying_down", "bending", "crouching", "kneeling", "reaching", "leaning",
    "exercising_dynamic", "partially_visible", "no_person", "unknown",
  ]);

  const VL_QUALITY = Object.freeze([
    "clear", "occluded", "dark", "blurry", "backlit", "partial_body",
    "multiple_people", "ambiguous", "private_skip", "screen_sensitive",
  ]);

  const VL_REVIEW_STATES = Object.freeze([
    "prelabel", "needs_review", "reviewed", "accepted", "rejected",
    "excluded_from_export",
  ]);

  /* Per-value colors. Hand-assigned hues for the canonical sets (values
     shared across axes — walking, no_person, unknown — deliberately share
     one color); anything else falls through to a deterministic hash hue
     so custom:<slug> labels stay stable across sessions. */
  const VL_VALUE_COLORS = Object.freeze({
    // activity
    cooking: "hsl(18 60% 58%)",
    food_prep: "hsl(32 58% 56%)",
    eating_drinking: "hsl(46 55% 55%)",
    washing_dishes: "hsl(192 52% 56%)",
    cleaning: "hsl(172 45% 52%)",
    laundry: "hsl(206 50% 60%)",
    organizing: "hsl(228 45% 62%)",
    reading: "hsl(268 42% 62%)",
    watching_tv: "hsl(286 45% 60%)",
    working_computer: "hsl(214 55% 58%)",
    phone_use: "hsl(250 45% 64%)",
    conversation: "hsl(96 40% 54%)",
    resting: "hsl(140 38% 54%)",
    sleeping: "hsl(156 35% 48%)",
    exercising: "hsl(2 58% 58%)",
    stretching_yoga: "hsl(330 48% 60%)",
    walking: "hsl(62 45% 50%)",
    entering_leaving: "hsl(78 42% 50%)",
    personal_care: "hsl(312 42% 60%)",
    pet_care: "hsl(26 50% 55%)",
    idle_present: "hsl(0 0% 58%)",
    no_person: "hsl(0 0% 38%)",
    unknown: "hsl(0 0% 48%)",
    // posture (walking / no_person / unknown shared above)
    standing: "hsl(200 50% 58%)",
    sitting_upright: "hsl(216 48% 60%)",
    sitting_reclined: "hsl(236 44% 62%)",
    lying_down: "hsl(154 40% 52%)",
    bending: "hsl(30 52% 55%)",
    crouching: "hsl(16 50% 56%)",
    kneeling: "hsl(44 48% 54%)",
    reaching: "hsl(328 46% 60%)",
    leaning: "hsl(266 40% 60%)",
    exercising_dynamic: "hsl(2 58% 58%)",
    partially_visible: "hsl(0 0% 52%)",
    // quality
    clear: "hsl(146 42% 52%)",
    occluded: "hsl(30 55% 55%)",
    dark: "hsl(232 35% 48%)",
    blurry: "hsl(260 30% 55%)",
    backlit: "hsl(46 60% 52%)",
    partial_body: "hsl(18 45% 55%)",
    multiple_people: "hsl(300 45% 58%)",
    ambiguous: "hsl(0 0% 55%)",
    private_skip: "hsl(354 62% 56%)",
    screen_sensitive: "hsl(348 50% 58%)",
    // review states
    prelabel: "hsl(0 0% 50%)",
    needs_review: "hsl(40 80% 55%)",
    reviewed: "hsl(210 60% 62%)",
    accepted: "hsl(146 48% 50%)",
    rejected: "hsl(354 55% 55%)",
    excluded_from_export: "hsl(0 0% 38%)",
  });

  function vlHashHue(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
    return ((h % 360) + 360) % 360;
  }

  function colorFor(value) {
    if (!value) return "hsl(0 0% 55%)";
    const v = String(value);
    if (VL_VALUE_COLORS[v]) return VL_VALUE_COLORS[v];
    return "hsl(" + vlHashHue(v.replace(/^custom:/, "")) + " 48% 58%)";
  }

  /* ---------------- timecode formatting ---------------- */

  /* "mm:ss.ff" — ff is the frame index within the second at `fps`. */
  function fmtTimecode(s, fps) {
    const sec = Math.max(0, Number(s) || 0);
    const f = Math.max(1, Math.round(Number(fps) || 30));
    const m = Math.floor(sec / 60);
    const ss = Math.floor(sec % 60);
    const ff = Math.min(f - 1, Math.floor((sec - Math.floor(sec)) * f));
    const p2 = (n) => String(n).padStart(2, "0");
    return p2(m) + ":" + p2(ss) + "." + p2(ff);
  }

  /* "m:ss" (or "h:mm:ss" past the hour). */
  function fmtDuration(s) {
    const sec = Math.max(0, Math.round(Number(s) || 0));
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const ss = sec % 60;
    const p2 = (n) => String(n).padStart(2, "0");
    return h > 0 ? h + ":" + p2(m) + ":" + p2(ss) : m + ":" + p2(ss);
  }

  /* ---------------- pure segment math (M1 foundation) ----------------
   * Segments are {id, start_s, end_s, ...}. All functions are pure —
   * inputs are never mutated; NON-OVERLAP is the invariant, enforced
   * per-(lane, person_slot): a segment only clamps/clips against
   * neighbors with the SAME slot ((seg.person_slot||null)); segments of
   * other slots are transparent to it. Slot-less lanes (quality/custom)
   * collapse into a single group — exact M1 behavior preserved. */

  /* person slots — activity_primary/posture ONLY. null/absent = the
     primary occupant ("you"); "p2".."p9" = other people; "track:<id>"
     reserved (the UI never mints it, but a known slot is never
     stripped — display + pass-through only). */
  const VL_SLOT_AXES = Object.freeze({ activity_primary: true, posture: true });
  const VL_EXTRA_SLOTS = Object.freeze(["p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9"]);

  function slotOf(seg) { return (seg && seg.person_slot) || null; }

  function slotLabel(slot) {
    if (!slot) return "you";
    const m = /^p([2-9])$/.exec(String(slot));
    return m ? "person " + m[1] : String(slot);
  }

  /* ordered unique slots present in a lane (primary first, then p2..p9 /
     track:* lexicographically) — drives the timeline's stacked sub-rows */
  function laneSlots(segs) {
    let hasNull = false;
    const seen = {};
    const rest = [];
    for (const s of segs || []) {
      const k = slotOf(s);
      if (k === null) { hasNull = true; continue; }
      if (!seen[k]) { seen[k] = true; rest.push(k); }
    }
    rest.sort();
    return (hasNull || rest.length === 0) ? [null].concat(rest) : rest;
  }

  /* split a lane into the id-segment's slot group + the rest, run `fn`
     over the group only, recombine sorted. Identity when id is absent. */
  function vlWithSlotGroup(segs, id, fn) {
    const arr = segs || [];
    const target = arr.find((s) => s.id === id);
    if (!target) return arr;
    const slot = slotOf(target);
    const group = [];
    const others = [];
    for (const s of arr) (slotOf(s) === slot ? group : others).push(s);
    if (!others.length) return fn(group);
    return others.concat(fn(group))
      .sort((a, b) => (a.start_s - b.start_s) || (a.end_s - b.end_s));
  }

  /* single-slot-group normalize: clamp into [0, duration], sort by start,
     then enforce non-overlap by clipping each segment against the
     previously placed neighbor (left wins). Segments shorter than minDur
     after clipping are dropped. */
  function vlNormalizeGroup(segs, duration, minDur) {
    const dur = Math.max(0, Number(duration) || 0);
    const min = Math.max(1e-9, Number(minDur) || 0);
    const sorted = (segs || [])
      .map((s) => Object.assign({}, s, {
        start_s: Math.max(0, Math.min(dur, Number(s.start_s))),
        end_s: Math.max(0, Math.min(dur, Number(s.end_s))),
      }))
      .filter((s) => Number.isFinite(s.start_s) && Number.isFinite(s.end_s) && s.end_s > s.start_s)
      .sort((a, b) => (a.start_s - b.start_s) || (a.end_s - b.end_s));
    const out = [];
    for (const s of sorted) {
      const prev = out[out.length - 1];
      const start = prev ? Math.max(s.start_s, prev.end_s) : s.start_s;
      if (s.end_s - start >= min) out.push(Object.assign({}, s, { start_s: start }));
    }
    return out;
  }

  /* per-slot normalize: each slot group is normalized independently —
     cross-slot overlap is legal (two people, same time range). */
  function normalizeSegments(segs, duration, minDur) {
    const groups = [];
    const byKey = {};
    for (const s of segs || []) {
      const k = slotOf(s) == null ? " " : String(slotOf(s));
      if (!byKey[k]) { byKey[k] = []; groups.push(byKey[k]); }
      byKey[k].push(s);
    }
    let out = [];
    for (const g of groups) out = out.concat(vlNormalizeGroup(g, duration, minDur));
    return out.sort((a, b) => (a.start_s - b.start_s) || (a.end_s - b.end_s));
  }

  let vlIdCounter = 0;
  /* Local (unsaved) segment ids. The M1 PUT contract re-keys ids it does
     not own via id_map, recognizing the tmp_ prefix — so every locally
     minted id MUST carry it. */
  function newTempId() {
    vlIdCounter += 1;
    return "tmp_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8) + "_" + vlIdCounter;
  }
  /* M0 alias kept for callers; same tmp_ contract since M1. */
  function newSegId() { return newTempId(); }

  /* Split the segment containing t into [start,t) + [t,end). The left
     half keeps the original identity; the right half gets a fresh local
     id (value + person_slot inherited). No-op — same array back — when t
     isn't strictly inside any segment. opts.slot (null = primary) limits
     the candidates to one slot group; omit for slot-agnostic M1 behavior. */
  function splitAt(segs, t, opts) {
    const arr = segs || [];
    const hasSlot = !!(opts && opts.slot !== undefined);
    const want = hasSlot ? (opts.slot || null) : undefined;
    const i = arr.findIndex((s) =>
      (!hasSlot || slotOf(s) === want) && t > s.start_s && t < s.end_s);
    if (i < 0) return arr;
    const s = arr[i];
    const left = Object.assign({}, s, { end_s: t });
    const right = Object.assign({}, s, { id: newTempId(), start_s: t });
    return arr.slice(0, i).concat([left, right], arr.slice(i + 1));
  }

  /* Move segment `id` so it starts at newStart, clamped between its
     SAME-SLOT lane neighbors and [0, duration]. Move never clips
     neighbors — it stops against them (drag-move semantics); other
     slots' segments are transparent. */
  function moveSegment(segs, id, newStart, opts) {
    return vlWithSlotGroup(segs, id, (group) => {
      const dur = Math.max(0, Number(opts && opts.duration) || 0);
      const arr = group.slice().sort((a, b) => a.start_s - b.start_s);
      const i = arr.findIndex((s) => s.id === id);
      if (i < 0) return group;
      const s = arr[i];
      const len = s.end_s - s.start_s;
      const lo = i > 0 ? arr[i - 1].end_s : 0;
      const hi = Math.max(lo, (i + 1 < arr.length ? arr[i + 1].start_s : dur) - len);
      const ns = Math.max(lo, Math.min(hi, Number(newStart)));
      if (!Number.isFinite(ns)) return arr;
      const out = arr.slice();
      out[i] = Object.assign({}, s, { start_s: ns, end_s: ns + len });
      return out;
    });
  }

  /* Resize one edge ('start'|'end') of segment `id` to time t. With
     clipNeighbors (default, drag-resize semantics) the adjacent SAME-SLOT
     neighbor is clipped down to minDur and then the edge stops; without
     it the edge simply stops at the neighbor. Other slots are
     transparent. The segment itself always keeps minDur. Returns a new
     sorted array; same-slot neighbors stay non-overlapping. */
  function resizeSegment(segs, id, edge, t, opts) {
    return vlWithSlotGroup(segs, id, (group) => {
      const dur = Math.max(0, Number(opts && opts.duration) || 0);
      const min = Math.max(1e-9, Number(opts && opts.minDur) || 0.2);
      const clip = !(opts && opts.clipNeighbors === false);
      const arr = group.slice().sort((a, b) => a.start_s - b.start_s);
      const i = arr.findIndex((s) => s.id === id);
      if (i < 0) return group;
      const s = arr[i];
      let nt = Math.max(0, Math.min(dur, Number(t)));
      if (!Number.isFinite(nt)) return arr;
      const out = arr.slice();
      if (edge === "start") {
        nt = Math.min(nt, s.end_s - min);
        const left = i > 0 ? arr[i - 1] : null;
        if (left && nt < left.end_s) {
          if (clip) {
            nt = Math.max(nt, left.start_s + min);
            if (nt < left.end_s) out[i - 1] = Object.assign({}, left, { end_s: nt });
          } else {
            nt = left.end_s;
          }
        }
        nt = Math.max(0, Math.min(nt, s.end_s - min));
        out[i] = Object.assign({}, s, { start_s: nt });
      } else {
        nt = Math.max(nt, s.start_s + min);
        const right = i + 1 < arr.length ? arr[i + 1] : null;
        if (right && nt > right.start_s) {
          if (clip) {
            nt = Math.min(nt, right.end_s - min);
            if (nt > right.start_s) out[i + 1] = Object.assign({}, right, { start_s: nt });
          } else {
            nt = right.start_s;
          }
        }
        nt = Math.min(dur, Math.max(nt, s.start_s + min));
        out[i] = Object.assign({}, s, { end_s: nt });
      }
      return out;
    });
  }

  /* Clear [start, end) in a lane: overlapping segments are trimmed or
     split (the right part of a split gets a fresh temp id); remainders
     shorter than minDur are dropped. Used by the P/private flow before
     inserting a covering quality segment. opts.slot (null = primary)
     carves only that slot group — other slots pass through untouched. */
  function carveRange(segs, start, end, minDur, opts) {
    const min = Math.max(1e-9, Number(minDur) || 0.2);
    const hasSlot = !!(opts && opts.slot !== undefined);
    const want = hasSlot ? (opts.slot || null) : undefined;
    const out = [];
    for (const s of segs || []) {
      if (hasSlot && slotOf(s) !== want) { out.push(s); continue; }
      if (s.end_s <= start || s.start_s >= end) { out.push(s); continue; }
      const leftOk = start - s.start_s >= min;
      const rightOk = s.end_s - end >= min;
      if (leftOk) out.push(Object.assign({}, s, { end_s: start }));
      if (rightOk) {
        out.push(leftOk
          ? Object.assign({}, s, { id: newTempId(), start_s: end })
          : Object.assign({}, s, { start_s: end }));
      }
    }
    return out.sort((a, b) => a.start_s - b.start_s);
  }

  /* Insert a fresh human segment around time t: prefDur (default 4s)
     clamped into the gap containing t — or the nearest gap when t sits
     inside an existing segment. opts.slot (null = primary) places the
     segment in that slot group: gaps are computed against SAME-SLOT
     segments only, and the new segment carries person_slot = slot.
     Returns {segments, id} or null when no gap can host minDur. */
  function createSegmentAt(segs, t, value, opts) {
    const dur = Math.max(0, Number(opts && opts.duration) || 0);
    const min = Math.max(1e-9, Number(opts && opts.minDur) || 0.2);
    const pref = Math.max(min, Number(opts && opts.prefDur) || 4);
    const slot = (opts && opts.slot) || null;
    if (!(dur > 0)) return null;
    const all = (segs || []).slice();
    const sorted = all.filter((s) => slotOf(s) === slot)
      .sort((a, b) => a.start_s - b.start_s);
    const gaps = [];
    let cursor = 0;
    for (const s of sorted) {
      if (s.start_s - cursor >= min) gaps.push([cursor, s.start_s]);
      cursor = Math.max(cursor, s.end_s);
    }
    if (dur - cursor >= min) gaps.push([cursor, dur]);
    if (!gaps.length) return null;
    const tt = Math.max(0, Math.min(dur, Number(t) || 0));
    let gap = null;
    let bestD = Infinity;
    for (const g of gaps) {
      if (tt >= g[0] && tt <= g[1]) { gap = g; break; }
      const d = tt < g[0] ? g[0] - tt : tt - g[1];
      if (d < bestD) { bestD = d; gap = g; }
    }
    const maxStart = Math.max(gap[0], gap[1] - pref);
    const start = Math.max(gap[0], Math.min(tt, maxStart));
    const end = Math.min(gap[1], start + pref);
    if (end - start < min) return null;
    const id = newTempId();
    const seg = {
      id: id, start_s: start, end_s: end, value: value,
      review_state: "reviewed", source: "human",
      confidence: null, aux: null, person_slot: slot,
    };
    return { segments: all.concat([seg]).sort((a, b) => a.start_s - b.start_s), id: id };
  }

  /* Merge the identified segment with its immediate SAME-SLOT right
     neighbor (in start order); other slots' segments in between are
     skipped (and may end up covered by the merged span — legal, they are
     a different person). Keeps the left segment's identity + value.
     No-op when id is missing or has no same-slot right neighbor. */
  function mergeAdjacent(segs, id) {
    const arr = (segs || []).slice().sort((a, b) => a.start_s - b.start_s);
    const i = arr.findIndex((s) => s.id === id);
    if (i < 0) return segs || [];
    const slot = slotOf(arr[i]);
    let j = -1;
    for (let k = i + 1; k < arr.length; k++) {
      if (slotOf(arr[k]) === slot) { j = k; break; }
    }
    if (j < 0) return segs || [];
    const merged = Object.assign({}, arr[i], { end_s: Math.max(arr[i].end_s, arr[j].end_s) });
    return arr.slice(0, i).concat([merged], arr.slice(i + 1, j), arr.slice(j + 1));
  }

  /* Nearest snap candidate (plain seconds values) within tol of t, or
     null. Candidates come from all-lane boundaries + playhead + keyframes
     in M1; this is just the resolver. */
  function findSnap(candidates, t, tol) {
    let best = null;
    let bestD = Infinity;
    for (const c of candidates || []) {
      const d = Math.abs(c - t);
      if (d <= tol && d < bestD) { best = c; bestD = d; }
    }
    return best;
  }

  window.HomeVideoLabelerData = Object.freeze({
    BASE_KEY,
    DEFAULT_BASE,
    vlBase,
    // api client (no-throw, {ok,status,data,error})
    health, listVideos, getVideo, importManual, getSprite,
    listJobs, cancelJob, retryJob,
    // labels API (M1)
    getOntology, getLabels, putLabels, reviewSegment,
    listCustomLabels, createCustomLabel, mergeCustomLabel,
    hideCustomLabel, promoteCustomLabel,
    // suggestions + calibration (M3)
    getSuggestions, getWindowKeyframes, getCalibration,
    windowIdFor, suggestionDisagreement, suggestionEvidenceText,
    // neighbors + cluster signals (M4)
    getNeighbors, getWindowSignals,
    // draft persistence (best-effort localStorage)
    saveDraft, loadDraft, clearDraft, validateDraftDoc,
    // media url builders (plain strings; null under sim)
    streamUrl, spriteManifestUrl, spriteSheetUrl, keyframeUrl,
    // taxonomies + colors
    VL_ACTIVITY, VL_POSTURE, VL_QUALITY, VL_REVIEW_STATES,
    VL_VALUE_COLORS, colorFor,
    // formatting
    fmtTimecode, fmtDuration,
    // pure segment math (M1 editor; non-overlap per-(lane, person_slot))
    normalizeSegments, splitAt, mergeAdjacent, findSnap,
    moveSegment, resizeSegment, carveRange, createSegmentAt,
    newSegId, newTempId,
    // person slots (multi-person; activity/posture only)
    slotOf, slotLabel, laneSlots, VL_SLOT_AXES, VL_EXTRA_SLOTS,
  });
})();
