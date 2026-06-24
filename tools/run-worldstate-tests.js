#!/usr/bin/env node
/* ============================================================================
 * tools/run-worldstate-tests.js
 *
 * Pure-function tests for home-worldstate-helpers.js (Addendum 27 F-32).
 *
 *   node tools/run-worldstate-tests.js
 *
 * Exit 0 if all pass, 1 if any fail.
 * ============================================================================ */

"use strict";

const path = require("path");
const H = require(path.join(__dirname, "..", "app", "src", "home-worldstate-helpers.js"));

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, cond, detail) {
  if (cond) {
    passes++;
    process.stdout.write("  \x1b[32mPASS\x1b[0m  " + name + "\n");
  } else {
    fails++;
    failures.push({ name, detail });
    process.stdout.write("  \x1b[31mFAIL\x1b[0m  " + name);
    if (detail !== undefined) {
      const dumped = typeof detail === "string"
        ? detail : JSON.stringify(detail, null, 2).replace(/\n/g, "\n        ");
      process.stdout.write("\n        " + dumped);
    }
    process.stdout.write("\n");
  }
}

/* Suite 1 — ageOf */
process.stdout.write("\nageOf_test\n");

const now = 1779000000000;  // fixed unix ms for deterministic math
assert("ageOf: null returns null", H.ageOf(null, now) === null);
assert("ageOf: undefined returns null", H.ageOf(undefined, now) === null);
assert("ageOf: empty string returns null", H.ageOf("", now) === null);
assert("ageOf: ISO 30s ago returns 30",
  H.ageOf(new Date(now - 30000).toISOString(), now) === 30);
assert("ageOf: ISO 5min ago returns 300",
  H.ageOf(new Date(now - 5 * 60 * 1000).toISOString(), now) === 300);
assert("ageOf: unix-seconds number 45s ago returns 45",
  H.ageOf((now / 1000) - 45, now) === 45);
assert("ageOf: future ts clamps to 0",
  H.ageOf(now / 1000 + 60, now) === 0);
assert("ageOf: invalid string returns null",
  H.ageOf("garbage", now) === null);
assert("ageOf: invalid type (object) returns null",
  H.ageOf({}, now) === null);

/* Suite 2 — fmtAge */
process.stdout.write("\nfmtAge_test\n");

assert("fmtAge: null → dash", H.fmtAge(null) === "—");
assert("fmtAge: 0 → 0s", H.fmtAge(0) === "0s");
assert("fmtAge: 45 → 45s", H.fmtAge(45) === "45s");
assert("fmtAge: 60 → 1m", H.fmtAge(60) === "1m");
assert("fmtAge: 120 → 2m", H.fmtAge(120) === "2m");
assert("fmtAge: 3599 → 59m", H.fmtAge(3599) === "59m");
assert("fmtAge: 3600 → 1h", H.fmtAge(3600) === "1h");
assert("fmtAge: 7200 → 2h", H.fmtAge(7200) === "2h");

/* Suite 3 — freshnessBand */
process.stdout.write("\nfreshnessBand_test\n");

assert("band: null → dash label", H.freshnessBand(null).label === "—");
assert("band: 0s → fresh", H.freshnessBand(0).label === "fresh");
assert("band: 59s → fresh", H.freshnessBand(59).label === "fresh");
assert("band: 60s → recent", H.freshnessBand(60).label === "recent");
assert("band: 179s → recent", H.freshnessBand(179).label === "recent");
assert("band: 180s → stale", H.freshnessBand(180).label === "stale");
assert("band: 599s → stale", H.freshnessBand(599).label === "stale");
assert("band: 600s → old", H.freshnessBand(600).label === "old");
assert("band: fresh band uses ice color", H.freshnessBand(30).color === "var(--hg-ice)");
assert("band: stale band uses warn color", H.freshnessBand(300).color === "var(--hg-warn)");

/* Suite 4 — countIdentifiedPersons */
process.stdout.write("\ncountIdentifiedPersons_test\n");

assert("count: null returns 0", H.countIdentifiedPersons(null) === 0);
assert("count: empty object returns 0", H.countIdentifiedPersons({}) === 0);

const rooms1 = {
  kitchen: { persons: [{ identity: { name: "Marcelo" } }] },
  living_room: { persons: [{ identity: { name: "Amelia" } }, { identity: null }] },
  office: { persons: [] },
  hall: { /* no persons key */ },
};
assert("count: ignores null-identity person + missing-persons rooms",
  H.countIdentifiedPersons(rooms1) === 2);

const rooms2 = {
  kitchen: { persons: [
    { identity: { name: "Marcelo" } },
    { identity: { name: "Amelia" } },
    { identity: { name: "" } },          // empty name should NOT count
  ] },
};
assert("count: empty-name identity doesn't count",
  H.countIdentifiedPersons(rooms2) === 2);

/* Suite 5 — sortRoomKeys */
process.stdout.write("\nsortRoomKeys_test\n");

assert("sortRooms: null returns []", H.sortRoomKeys(null).length === 0);
assert("sortRooms: empty returns []", H.sortRoomKeys({}).length === 0);

// Occupied rooms first
const r = {
  zebra:  { occupied: false, perception_age_seconds: 30 },
  alpha:  { occupied: true,  perception_age_seconds: 100 },
  charlie:{ occupied: true,  perception_age_seconds: 20 },
};
assert("sortRooms: occupied wins over unoccupied (even if newer perception)",
  H.sortRoomKeys(r)[0] === "charlie" && H.sortRoomKeys(r)[1] === "alpha",
  H.sortRoomKeys(r));

// Within unoccupied: still goes after occupied
const r2 = { a: { occupied: false }, b: { occupied: false } };
assert("sortRooms: alphabetical fallback for all-unoccupied",
  H.sortRoomKeys(r2).join(",") === "a,b");

// Fresher perception wins among occupied
const r3 = {
  b: { occupied: true, perception_age_seconds: 100 },
  a: { occupied: true, perception_age_seconds: 50 },
};
assert("sortRooms: fresher perception sorts earlier",
  H.sortRoomKeys(r3)[0] === "a");

// Missing perception_age sorts after rooms with one
const r4 = {
  c: { occupied: true },     // no perception
  a: { occupied: true, perception_age_seconds: 200 },
};
assert("sortRooms: room with perception_age sorts before one without",
  H.sortRoomKeys(r4)[0] === "a");

/* Suite 6 — sortPersonKeys */
process.stdout.write("\nsortPersonKeys_test\n");

assert("sortPeople: null returns []", H.sortPersonKeys(null).length === 0);

const p = {
  zebra:  { currently_seen: false, last_visual_at: new Date(now - 100000).toISOString() },
  alpha:  { currently_seen: true,  last_visual_at: new Date(now - 60000).toISOString() },
  beta:   { currently_seen: true,  last_visual_at: new Date(now - 5000).toISOString() },
};
const order = H.sortPersonKeys(p, now);
assert("sortPeople: currently_seen wins over not-seen", order[0] !== "zebra" && order[1] !== "zebra");
assert("sortPeople: fresher among currently_seen sorts first",
  order[0] === "beta", order);

// All-unseen → falls back to age order
const p2 = {
  a: { currently_seen: false, last_visual_at: new Date(now - 1000000).toISOString() },
  b: { currently_seen: false, last_visual_at: new Date(now - 100).toISOString() },
};
assert("sortPeople: among unseen, recent visual wins",
  H.sortPersonKeys(p2, now)[0] === "b");

// People with no visual_at fall to bottom
const p3 = {
  a: { currently_seen: false, last_visual_at: new Date(now - 100).toISOString() },
  noVisual: { currently_seen: false },
};
assert("sortPeople: missing last_visual_at sorts after present",
  H.sortPersonKeys(p3, now)[0] === "a");

/* Suite 7 — formatRefreshCountdown (F-32+) */
process.stdout.write("\nformatRefreshCountdown_test\n");

assert("countdown: refreshing wins all other modes",
  H.formatRefreshCountdown(3, false, true) === "refreshing…");
assert("countdown: refreshing wins even when paused",
  H.formatRefreshCountdown(3, true, true) === "refreshing…");
assert("countdown: paused when not refreshing",
  H.formatRefreshCountdown(3, true, false) === "paused (tab hidden)");
assert("countdown: 3s remaining → 'next in 3s'",
  H.formatRefreshCountdown(3, false, false) === "next in 3s");
assert("countdown: 0.4s remaining → 'next in 1s' (ceiling)",
  H.formatRefreshCountdown(0.4, false, false) === "next in 1s");
assert("countdown: exactly 0 → 'next in soon' (not 'next in 0s')",
  H.formatRefreshCountdown(0, false, false) === "next in soon");
assert("countdown: negative (clock skew) → 'next in soon'",
  H.formatRefreshCountdown(-1.5, false, false) === "next in soon");
assert("countdown: null → '—' (no countdown info yet)",
  H.formatRefreshCountdown(null, false, false) === "—");
assert("countdown: undefined → '—'",
  H.formatRefreshCountdown(undefined, false, false) === "—");
assert("countdown: NaN → '—'",
  H.formatRefreshCountdown(NaN, false, false) === "—");
assert("countdown: 4.1s → ceiling to 'next in 5s'",
  H.formatRefreshCountdown(4.1, false, false) === "next in 5s");
assert("countdown: precisely 5 → 'next in 5s'",
  H.formatRefreshCountdown(5, false, false) === "next in 5s");

/* ── Footer ───────────────────────────────────────────────────────── */
process.stdout.write("\n");
if (fails === 0) {
  process.stdout.write("\x1b[32m" + passes + " pass · 0 fail\x1b[0m\n");
  process.exit(0);
} else {
  process.stdout.write("\x1b[31m" + passes + " pass · " + fails + " fail\x1b[0m\n");
  process.exit(1);
}
