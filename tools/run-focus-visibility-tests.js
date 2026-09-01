#!/usr/bin/env node
/* ============================================================================
 * run-focus-visibility-tests.js — keyboard-focus visibility contracts.
 *
 * Campaign Phase 1 (design-audit criticals): the token focus ring must be
 * global, and no inline style may defeat it. Inline `outline: "none"` /
 * `all: "unset"` beat ANY stylesheet selector at inline priority — that bug
 * once made keyboard focus invisible on the header, onboarding form, People
 * panel, and every labeler control.
 * ========================================================================== */
"use strict";

const fs = require("fs");
const path = require("path");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src");

let fails = 0;
const failures = [];
function assert(name, ok, detail) {
  if (ok) {
    process.stdout.write("  PASS  " + name + "\n");
  } else {
    fails += 1;
    failures.push({ name, detail });
    process.stdout.write("  FAIL  " + name + "\n");
    if (detail !== undefined) {
      process.stdout.write("        " + JSON.stringify(detail, null, 2).split("\n").join("\n        ") + "\n");
    }
  }
}

function listSources(dir) {
  const out = [];
  for (const f of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, f.name);
    if (f.isDirectory()) {
      if (f.name === "vendor" || f.name === "assets" || f.name === "simulation") continue;
      out.push(...listSources(p));
    } else if (/\.(jsx|js)$/.test(f.name) && !/\.bak\./.test(f.name) && f.name !== "home-service-worker.js") {
      out.push(p);
    }
  }
  return out;
}

process.stdout.write("\nfocus_ring_suppression_test\n");
const offenders = [];
for (const file of listSources(SRC)) {
  const src = fs.readFileSync(file, "utf8");
  const rel = path.relative(REPO, file).replace(/\\/g, "/");
  const lines = src.split(/\r?\n/);
  lines.forEach((line, i) => {
    if (/outline:\s*["'](none|unset)["']/.test(line)) offenders.push(rel + ":" + (i + 1) + " outline suppressed");
    if (/\ball:\s*["']unset["']/.test(line)) offenders.push(rel + ":" + (i + 1) + " all:unset inline reset");
  });
}
assert("no inline outline suppression or all:unset resets in app/src", offenders.length === 0, offenders);

process.stdout.write("\nfocus_ring_tokens_test\n");
const tokens = fs.readFileSync(path.join(SRC, "home-tokens.css"), "utf8");
assert("global keyboard focus ring exists (button:focus-visible)",
  tokens.includes("button:focus-visible") && tokens.includes('[role="button"]:focus-visible'));
assert("legacy .hg-focusable ring is preserved", tokens.includes(".hg-focusable:focus-visible"));
assert("engine-neutral slider focus ring exists (Firefox has no thumb glow)",
  /\.hg-slider:focus-visible\s*\{[^}]*outline:/.test(tokens));

process.stdout.write("\nfocus_shortcut_test\n");
const appSource = fs.readFileSync(path.join(SRC, "home-app.jsx"), "utf8");
assert("Ctrl+K targets the command input by accessible name, not class",
  appSource.includes('input[aria-label="Command input"]'));

if (fails) {
  console.log("\nFailures:");
  for (const f of failures) console.log("- " + f.name);
}
console.log("\n" + (offenders.length === 0 && fails === 0 ? "all green" : fails + " fail(s)"));
process.exit(fails ? 1 : 0);
