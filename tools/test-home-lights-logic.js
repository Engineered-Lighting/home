#!/usr/bin/env node
// test-home-lights-logic.js — node-based unit tests for pure-function logic in
// app/src/home-lights.jsx (the /lights drawer). Lets us self-validate the
// slider math + cascade-rendering bits WITHOUT needing the running Tauri app.
//
// Approach: home-lights.jsx is babel-compiled JSX that gets loaded into the
// app's WebView. It exposes a few pure functions on `window` (e.g.
// `computeCTSliderEffective`). For Node-side testing, we extract the function
// bodies and define them locally, then assert. Each test guards the logic that
// drove a previous user-visible bug — keep them as regression coverage.
//
// USAGE
//   node tools/test-home-lights-logic.js          # all tests
//   node tools/test-home-lights-logic.js --verbose # show each test
//
// EXIT CODE
//   0 — all pass
//   1 — at least one failure

const verbose = process.argv.includes("--verbose");
let ok = 0;
let fail = 0;
const failures = [];

function t(name, condition, detail = "") {
  if (condition) {
    ok++;
    if (verbose) console.log(`  ✓  ${name}` + (detail ? ` — ${detail}` : ""));
  } else {
    fail++;
    failures.push(`${name}` + (detail ? ` — ${detail}` : ""));
    console.log(`  ✗  ${name}` + (detail ? ` — ${detail}` : ""));
  }
}

// ── computeCTSliderEffective — extracted from home-lights.jsx ─────
// Keep this implementation byte-identical to the one in home-lights.jsx.
// If the live function changes, update here too — the test catches drift
// only if both are kept in sync.
function computeCTSliderEffective(override, todValue, min) {
  const minK = Number.isFinite(min) && min > 0 ? min : 1800;
  const ovr = Number(override);
  if (Number.isFinite(ovr) && ovr > 0) return ovr;
  const tod = Number(todValue);
  if (Number.isFinite(tod) && tod >= minK) return tod;
  return 2700;
}

// ── Tests ───────────────────────────────────────────────────────────

console.log("# computeCTSliderEffective");

// Baseline: inherit (override=0) → returns ToD value, NOT slider min
t("override=0 + todValue=2700 → 2700 (inherit shows ToD)",
  computeCTSliderEffective(0, 2700, 1800) === 2700);

t("override=0 + todValue=2200 → 2200 (morning ToD)",
  computeCTSliderEffective(0, 2200, 1800) === 2200);

t("override=0 + todValue=4000 → 4000 (afternoon ToD)",
  computeCTSliderEffective(0, 4000, 1800) === 4000);

// Override engaged: returns the override value
t("override=3500 + todValue=2700 → 3500 (override wins)",
  computeCTSliderEffective(3500, 2700, 1800) === 3500);

t("override=2000 + todValue=4000 → 2000 (override wins)",
  computeCTSliderEffective(2000, 4000, 1800) === 2000);

// Defensive: falsy todValue (the actual user bug — slider stuck at 1800
// because todValue resolved to 0 / undefined during initial render before
// the profile sensor state was fetched).
t("override=0 + todValue=undefined → 2700 (safe fallback, NOT min=1800)",
  computeCTSliderEffective(0, undefined, 1800) === 2700,
  "regression for user-reported 'slider stuck at 1800 K inheriting ToD' bug");

t("override=0 + todValue=null → 2700 (safe fallback)",
  computeCTSliderEffective(0, null, 1800) === 2700);

t("override=0 + todValue=0 → 2700 (safe fallback, 0 is invalid as a real CT)",
  computeCTSliderEffective(0, 0, 1800) === 2700);

t("override=0 + todValue=NaN → 2700 (safe fallback)",
  computeCTSliderEffective(0, NaN, 1800) === 2700);

t("override=0 + todValue='2700' (string) → 2700 (Number-coerce)",
  computeCTSliderEffective(0, "2700", 1800) === 2700);

// Defensive: todValue below the slider min (impossible in practice but
// the function should still return a sane CT, not the min).
t("override=0 + todValue=1500 (below min=1800) → 2700 (fallback)",
  computeCTSliderEffective(0, 1500, 1800) === 2700);

// Defensive: min argument fallback
t("override=0 + todValue=2700 + min=undefined → 2700 (min fallback)",
  computeCTSliderEffective(0, 2700, undefined) === 2700);

// Override is negative or NaN (shouldn't happen via input_number with min=0,
// but be defensive against any future config drift)
t("override=-50 + todValue=2700 → 2700 (negative override ignored)",
  computeCTSliderEffective(-50, 2700, 1800) === 2700);

t("override=NaN + todValue=2700 → 2700 (NaN override ignored)",
  computeCTSliderEffective(NaN, 2700, 1800) === 2700);

// String inputs (the input_number REST call returns strings)
t("override='3500' + todValue='2700' → 3500 (Number-coerce string override)",
  computeCTSliderEffective("3500", "2700", 1800) === 3500);

// ── Summary ─────────────────────────────────────────────────────────

console.log();
console.log(`# summary: ok=${ok} fail=${fail}`);
if (fail > 0) {
  console.log();
  console.log("FAILURES:");
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
}
process.exit(0);
