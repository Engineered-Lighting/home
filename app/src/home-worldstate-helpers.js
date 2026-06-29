/* ============================================================================
 * home-worldstate-helpers.js — pure helpers for the world-state drawer.
 *
 * Same dual-export pattern as home-explain-helpers.js — loaded as plain
 * JS before the JSX so Node tests can require directly.
 * ============================================================================ */

"use strict";

/** Seconds since a timestamp (ISO string OR unix seconds). null on
 *  bad/missing input. Clamps negative (future) to 0. */
function ageOf(tsIso, nowMs) {
  if (!tsIso && tsIso !== 0) return null;
  let ms;
  if (typeof tsIso === "number") ms = Math.floor(tsIso * 1000);
  else if (typeof tsIso === "string") ms = Date.parse(tsIso);
  else return null;
  if (!Number.isFinite(ms)) return null;
  const now = nowMs == null ? Date.now() : nowMs;
  return Math.max(0, Math.floor((now - ms) / 1000));
}

/** Compact "12s" / "3m" / "2h" string. */
function fmtAge(sec) {
  if (sec == null) return "—";
  if (sec < 60) return sec + "s";
  if (sec < 3600) return Math.floor(sec / 60) + "m";
  return Math.floor(sec / 3600) + "h";
}

/** Bucket an age into a band with a label + a color CSS var. Aligns
 *  with world_state.py's _freshness thresholds (60s / 180s / 600s). */
function freshnessBand(sec) {
  if (sec == null) return { label: "—", color: "var(--hg-fg-4)" };
  if (sec < 60) return { label: "fresh", color: "var(--hg-ice)" };
  if (sec < 180) return { label: "recent", color: "var(--hg-fg-2)" };
  if (sec < 600) return { label: "stale", color: "var(--hg-warn)" };
  return { label: "old", color: "var(--hg-fg-4)" };
}

/** Count identified persons across all rooms. Useful for the
 *  header chip + sim fixtures. */
function countIdentifiedPersons(rooms) {
  if (!rooms || typeof rooms !== "object") return 0;
  let total = 0;
  for (const room of Object.values(rooms)) {
    const persons = Array.isArray(room?.persons) ? room.persons : [];
    for (const p of persons) {
      if (p?.identity && p.identity.name) total++;
    }
  }
  return total;
}

/** Sort rooms by activity for stable display ordering: occupied first,
 *  then by perception_age (freshest first), then alphabetical. */
function sortRoomKeys(rooms) {
  if (!rooms || typeof rooms !== "object") return [];
  return Object.keys(rooms).sort((a, b) => {
    const ra = rooms[a] || {};
    const rb = rooms[b] || {};
    if (!!ra.occupied !== !!rb.occupied) return ra.occupied ? -1 : 1;
    const aa = ra.perception_age_seconds;
    const bb = rb.perception_age_seconds;
    if (aa != null && bb != null) return aa - bb;
    if (aa != null) return -1;
    if (bb != null) return 1;
    return a.localeCompare(b);
  });
}

/** Format the auto-refresh countdown badge text shown in the drawer
 *  header. Three modes:
 *    refreshing → "refreshing…"
 *    paused     → "paused (tab hidden)"
 *    countdown  → "next in Ns" / "next in soon"
 *  The N=0 case shows "next in soon" rather than "next in 0s" — the
 *  refresh is about to fire so the precise "0" reads as a glitch
 *  rather than an imminent action. */
function formatRefreshCountdown(secsLeft, paused, refreshing) {
  if (refreshing) return "refreshing…";
  if (paused) return "paused (tab hidden)";
  if (secsLeft == null || !Number.isFinite(secsLeft)) return "—";
  if (secsLeft <= 0) return "next in soon";
  return "next in " + Math.ceil(secsLeft) + "s";
}

/** Same idea for people: currently_seen first, then by recency of
 *  last_visual_at, then alphabetical. */
function sortPersonKeys(people, nowMs) {
  if (!people || typeof people !== "object") return [];
  return Object.keys(people).sort((a, b) => {
    const pa = people[a] || {};
    const pb = people[b] || {};
    if (!!pa.currently_seen !== !!pb.currently_seen) return pa.currently_seen ? -1 : 1;
    const ageA = ageOf(pa.last_visual_at, nowMs);
    const ageB = ageOf(pb.last_visual_at, nowMs);
    if (ageA != null && ageB != null) return ageA - ageB;
    if (ageA != null) return -1;
    if (ageB != null) return 1;
    return a.localeCompare(b);
  });
}

// Dual export
if (typeof window !== "undefined") {
  window.wsAgeOf = ageOf;
  window.wsFmtAge = fmtAge;
  window.wsFreshnessBand = freshnessBand;
  window.wsCountIdentifiedPersons = countIdentifiedPersons;
  window.wsSortRoomKeys = sortRoomKeys;
  window.wsSortPersonKeys = sortPersonKeys;
  window.wsFormatRefreshCountdown = formatRefreshCountdown;
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    ageOf, fmtAge, freshnessBand,
    countIdentifiedPersons, sortRoomKeys, sortPersonKeys,
    formatRefreshCountdown,
  };
}
