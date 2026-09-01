#!/usr/bin/env node
/* ============================================================================
 * run-touched-harnesses.mjs — run the test harnesses that guard the files
 * touched by a diff.
 *
 * The 46 tools/run-*.js harnesses are literal-pin and behavioral guards over
 * specific app/src surfaces, but none of them run in CI on pull requests —
 * which is how stale pins have merged silently in the past. This script maps
 * changed files → guarding harnesses and runs exactly that set (plus
 * check-jsx for any app/src change), so a PR that touches a surface runs the
 * surface's guards.
 *
 * Usage:
 *   node tools/run-touched-harnesses.mjs --base origin/main
 *   node tools/run-touched-harnesses.mjs --files app/src/home-app.jsx,...
 *   node tools/run-touched-harnesses.mjs --all
 * ========================================================================= */
"use strict";

import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const TOOLS = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(TOOLS, "..");

/* file-pattern → harnesses that pin or exercise it. Patterns are tested with
 * RegExp.test against repo-relative POSIX paths. Keep this map in sync with
 * new harnesses (a harness with no row here simply never auto-runs). */
const MAP = [
  [/^app\/src\/home-app\.jsx$/, [
    "run-chat-dedupe-tests", "run-slash-command-tests", "run-recovery-scenarios-tests",
    "run-bootstrap-tests", "run-lights-drawer-tests", "run-chat-ui-regression",
    "run-lab-tests", "run-capabilities-tests", "run-simulation-command-tests",
    "run-app-frigate-label-tests",
  ]],
  [/^app\/src\/home-events\.jsx$/, ["run-events-tests", "run-chat-ui-regression", "run-slash-command-tests"]],
  [/^app\/src\/home-lights\.jsx$/, ["run-lights-drawer-tests", "run-recovery-scenarios-tests"]],
  [/^app\/src\/home-people(\.jsx|-helpers\.js|-prewarm\.js)$/, ["run-people-tests"]],
  [/^app\/src\/home-intelligence\.jsx$/, ["run-intelligence-tests"]],
  [/^app\/src\/home-apartment/, ["run-apartment-3d-contract-tests", "run-apartment-aiming-tests", "run-apartment-data-tests"]],
  [/^app\/src\/home-3d\//, ["run-apartment-3d-contract-tests"]],
  [/^app\/src\/home-metrics-lab/, ["run-lab-tests"]],
  [/^app\/src\/home-metrics\.jsx$/, ["run-metrics-tests"]],
  [/^app\/src\/home-ai-stack\.jsx$/, ["run-ai-stack-card-tests"]],
  [/^app\/src\/home-stack-actions\.jsx$/, ["run-stack-actions-tests"]],
  [/^app\/src\/home-control\.jsx$/, ["run-control-card-tests"]],
  [/^app\/src\/home-vision\.jsx$/, ["run-vision-tests"]],
  [/^app\/src\/home-proactive\.jsx$/, ["run-proactive-tests"]],
  [/^app\/src\/home-tauri\.jsx$/, ["run-tauri-glue-tests"]],
  [/^app\/src\/home-security\.js$/, ["run-home-security-tests"]],
  [/^app\/src\/home-services\.js$/, ["run-home-services-tests"]],
  [/^app\/src\/home-lighting-events\.jsx$/, ["run-lighting-events-tests"]],
  [/^app\/src\/home-explain/, ["run-explain-tests"]],
  [/^app\/src\/home-external\.jsx$/, ["run-external-tests"]],
  [/^app\/src\/home-look\.jsx$/, ["run-look-tests"]],
  [/^app\/src\/home-natural-look\.js$/, ["run-natural-look-routing-tests"]],
  [/^app\/src\/home-worldstate/, ["run-worldstate-tests"]],
  [/^app\/src\/home-spatial/, ["run-spatial-tests"]],
  [/^app\/src\/home-s2s\.jsx$/, ["run-s2s-tests"]],
  [/^app\/src\/home-sse-fetch\.js$/, ["run-sse-fetch-tests"]],
  [/^app\/src\/home-ha\.jsx$/, ["run-ha-client-tests"]],
  [/^app\/src\/home-frigate-perception\.js$/, ["run-frigate-perception-tests"]],
  [/^app\/src\/home-video-labeler-data\.js$/, ["run-video-labeler-data-tests"]],
  [/^app\/src\/(home-fetch-with-retry\.js|home-service-worker\.js)$/, ["run-resilience-tests"]],
  [/^app\/src\/simulation-cameras\.jsx$/, ["run-simulation-camera-tests"]],
  [/^app\/src\/simulation-controls\.jsx$/, ["run-simulation-control-tests"]],
  [/^app\/src\/simulation(-data)?\.jsx$/, ["run-simulation-scenario-tests"]],
  [/^app\/src\/index\.html$/, ["run-lab-tests", "run-bootstrap-tests", "run-resilience-tests"]],
  [/^app\/src\/home-tokens\.css$/, ["run-focus-visibility-tests"]],
  [/^app\/src\/.*\.(jsx|js)$/, ["run-focus-visibility-tests"]],
  [/^web-gateway\//, [
    "run-resilience-tests", "run-web-gateway-grounded-vision-tests",
    "run-web-gateway-stack-token-tests", "run-native-agent-gateway-tests",
    "run-native-agent-security-tests",
  ]],
];

function parseArgs(argv) {
  const out = { base: null, files: null, all: false };
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === "--base") out.base = argv[++i];
    else if (argv[i] === "--files") out.files = argv[++i].split(",").map((s) => s.trim()).filter(Boolean);
    else if (argv[i] === "--all") out.all = true;
  }
  return out;
}

function changedFiles(base) {
  const range = `${base}...HEAD`;
  const stdout = execFileSync("git", ["diff", "--name-only", range], { cwd: REPO, encoding: "utf8" });
  return stdout.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
}

const args = parseArgs(process.argv);
let files = [];
let harnesses = new Set();

if (args.all) {
  for (const f of fs.readdirSync(TOOLS)) {
    if (/^run-.*\.js$/.test(f)) harnesses.add(f.replace(/\.js$/, ""));
  }
} else {
  files = args.files || changedFiles(args.base || "origin/main");
  for (const file of files) {
    const posix = file.replace(/\\/g, "/");
    for (const [pattern, names] of MAP) {
      if (pattern.test(posix)) names.forEach((n) => harnesses.add(n));
    }
  }
}

const touchedAppSrc = args.all || files.some((f) => f.replace(/\\/g, "/").startsWith("app/src/"));
const list = [...harnesses].sort();

console.log(`[touched-harnesses] ${args.all ? "(--all)" : files.length + " changed files"} → ${list.length} harnesses${touchedAppSrc ? " + check-jsx" : ""}`);
if (!list.length && !touchedAppSrc) {
  console.log("[touched-harnesses] nothing mapped — done");
  process.exit(0);
}

let failed = [];
for (const name of list) {
  const file = path.join(TOOLS, `${name}.js`);
  if (!fs.existsSync(file)) {
    console.log(`  SKIP ${name} (harness file missing)`);
    continue;
  }
  process.stdout.write(`  RUN  ${name} ... `);
  const res = spawnSync(process.execPath, [file], { cwd: REPO, encoding: "utf8", timeout: 180000 });
  if (res.status === 0) {
    console.log("ok");
  } else {
    console.log("FAIL");
    const tail = `${res.stdout || ""}\n${res.stderr || ""}`.trim().split(/\r?\n/).slice(-15).join("\n");
    console.log(tail.replace(/^/gm, "       "));
    failed.push(name);
  }
}

if (touchedAppSrc) {
  process.stdout.write("  RUN  check-jsx ... ");
  const res = spawnSync(process.execPath, [path.join(TOOLS, "check-jsx.js")], { cwd: REPO, encoding: "utf8", timeout: 180000 });
  if (res.status === 0) console.log("ok");
  else {
    console.log("FAIL");
    console.log(`${res.stdout || ""}\n${res.stderr || ""}`.trim().split(/\r?\n/).slice(-15).join("\n").replace(/^/gm, "       "));
    failed.push("check-jsx");
  }

  // home-3d/ and home-agent/ are real ES modules — check-jsx's Function-based
  // parse can't read them; node --check can.
  const esmFiles = files
    .map((f) => f.replace(/\\/g, "/"))
    .filter((f) => /^app\/src\/(home-3d|home-agent)\/.*\.js$/.test(f))
    .filter((f) => fs.existsSync(path.join(REPO, f)));
  for (const f of esmFiles) {
    process.stdout.write(`  RUN  esm-check ${f} ... `);
    const src = fs.readFileSync(path.join(REPO, f), "utf8");
    const r = spawnSync(process.execPath, ["--input-type=module", "--check"], { cwd: REPO, encoding: "utf8", timeout: 60000, input: src });
    if (r.status === 0) console.log("ok");
    else {
      console.log("FAIL");
      console.log(`${r.stderr || ""}`.trim().split(/\r?\n/).slice(-6).join("\n").replace(/^/gm, "       "));
      failed.push(`node-check:${f}`);
    }
  }
}

if (failed.length) {
  console.log(`\n[touched-harnesses] FAILED: ${failed.join(", ")}`);
  process.exit(1);
}
console.log("\n[touched-harnesses] all green");
