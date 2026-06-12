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
 */
(function () {
  "use strict";

  const BASE_KEY = "videoLabeler.base";
  const DEFAULT_BASE = "http://192.168.0.100:8099";

  /* Service base URL. Returns null while Simulation Mode is active:
     media URLs bypass tauriFetch's sim guard, so a null base is what
     actually keeps the sim hermetic. */
  function vlBase() {
    if (typeof window !== "undefined" && window.__SIM_ACTIVE === true) return null;
    try {
      const v = localStorage.getItem(BASE_KEY);
      return (v || DEFAULT_BASE).replace(/\/+$/, "");
    } catch (e) {
      return DEFAULT_BASE;
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
   * inputs are never mutated; per-lane NON-OVERLAP is the invariant
   * (adapted from the recovered gesture editor's normalize, hardened). */

  /* Clamp into [0, duration], sort by start, then enforce non-overlap by
     clipping each segment against the previously placed neighbor (left
     wins). Segments shorter than minDur after clipping are dropped. */
  function normalizeSegments(segs, duration, minDur) {
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

  let vlIdCounter = 0;
  /* Local (unsaved) segment ids — the server re-keys on save. */
  function newSegId() {
    vlIdCounter += 1;
    return "seg_local_" + Date.now().toString(36) + "_" + vlIdCounter;
  }

  /* Split the segment containing t into [start,t) + [t,end). The left
     half keeps the original identity; the right half gets a fresh local
     id (value inherited). No-op — same array back — when t isn't
     strictly inside any segment. */
  function splitAt(segs, t) {
    const arr = segs || [];
    const i = arr.findIndex((s) => t > s.start_s && t < s.end_s);
    if (i < 0) return arr;
    const s = arr[i];
    const left = Object.assign({}, s, { end_s: t });
    const right = Object.assign({}, s, { id: newSegId(), start_s: t });
    return arr.slice(0, i).concat([left, right], arr.slice(i + 1));
  }

  /* Merge the identified segment with its immediate right neighbor (in
     start order). The merged segment spans both, keeping the left
     segment's identity + value. No-op when id is missing or last. */
  function mergeAdjacent(segs, id) {
    const arr = (segs || []).slice().sort((a, b) => a.start_s - b.start_s);
    const i = arr.findIndex((s) => s.id === id);
    if (i < 0 || i + 1 >= arr.length) return segs || [];
    const merged = Object.assign({}, arr[i], { end_s: Math.max(arr[i].end_s, arr[i + 1].end_s) });
    return arr.slice(0, i).concat([merged], arr.slice(i + 2));
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
    // media url builders (plain strings; null under sim)
    streamUrl, spriteManifestUrl, spriteSheetUrl,
    // taxonomies + colors
    VL_ACTIVITY, VL_POSTURE, VL_QUALITY, VL_REVIEW_STATES,
    VL_VALUE_COLORS, colorFor,
    // formatting
    fmtTimecode, fmtDuration,
    // pure segment math (M1 editor foundation)
    normalizeSegments, splitAt, mergeAdjacent, findSnap, newSegId,
  });
})();
