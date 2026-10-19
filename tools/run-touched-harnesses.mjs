#!/usr/bin/env node
/* ============================================================================
 * run-touched-harnesses.mjs — run the test harnesses that guard the files
 * touched by a diff.
 *
 * The tools/run-*.js harnesses and explicitly mapped Node test suites guard
 * specific app/src surfaces. This script maps changed files to guarding
 * harnesses and runs exactly that set (plus
 * check-jsx for any app/src change), so a PR that touches a surface runs the
 * surface's guards.
 *
 * Usage:
 *   node tools/run-touched-harnesses.mjs --base origin/main
 *   node tools/run-touched-harnesses.mjs --files app/src/home-app.jsx,...
 *   node tools/run-touched-harnesses.mjs --all
 *   node tools/run-touched-harnesses.mjs --all --list
 *   node tools/run-touched-harnesses.mjs --base origin/main --shard 2/3 \
 *     --receipt .tmp/touched-harnesses/shard-2-of-3.json
 *   node tools/run-touched-harnesses.mjs --base origin/main \
 *     --verify-receipts .tmp/touched-harnesses --shards 3
 *
 * --shard i/n runs only the plan items a deterministic partition assigns to
 * worker i. Every worker computes the same full plan first, so the n shards
 * are disjoint and their union is exactly the unsharded plan (proved by
 * tools/test-touched-shards.mjs). --receipt records the full plan, the
 * shard's plan and each result; --verify-receipts recomputes the plan for the
 * same base and fails unless the n receipts cover it exactly once, all green.
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
  [/^app\/src\/home-3d\//, ["test-apartment-picking.mjs", "test-apartment-performance.mjs"]],
  [/^tools\/test-apartment-(?:picking|performance)\.mjs$/, ["test-apartment-picking.mjs", "test-apartment-performance.mjs"]],
  [/^app\/src\/home-3d\/(?:engine\.js|picking\.js|apartment-ui-overlay-bridge\.mjs)$/, ["test-apartment-ui-overlay-bridge.mjs", "test-apartment-overlay-bridge-browser.mjs"]],
  [/^tools\/test-apartment-(?:ui-overlay-bridge|overlay-bridge-browser)\.mjs$/, ["test-apartment-ui-overlay-bridge.mjs", "test-apartment-overlay-bridge-browser.mjs"]],
  [/^tools\/(?:lib\/home-frontend-inventory|test-home-frontend-inventory)\.cjs$/, ["run-bootstrap-tests.js", "run-lab-tests.js", "test-home-frontend-inventory.cjs"]],

  [/^app\/src\/home-app\.jsx$/, [
    "run-chat-dedupe-tests", "run-slash-command-tests", "run-recovery-scenarios-tests",
    "run-bootstrap-tests", "run-lights-drawer-tests", "run-chat-ui-regression",
    "run-lab-tests", "run-capabilities-tests", "run-simulation-command-tests",
    "run-app-frigate-label-tests",
    "run-apartment-shell-tests.js", "test-home-app-source-contracts.cjs", "test-apartment-chat-presenter.cjs",
    "test-apartment-unread-state.cjs", "test-apartment-unread-integration.mjs",
    "test-apartment-chat-header-actions.cjs", "test-apartment-chat-header-browser.mjs",
  ]],
  [/^app\/src\/home-events\.jsx$/, ["run-events-tests", "run-chat-ui-regression", "run-slash-command-tests"]],
  [/^app\/src\/home-chat-header-actions\.jsx$/, ["test-apartment-chat-header-actions.cjs", "test-apartment-chat-header-browser.mjs"]],
  [/^tools\/test-apartment-chat-header-(?:actions\.cjs|browser\.mjs)$/, ["test-apartment-chat-header-actions.cjs", "test-apartment-chat-header-browser.mjs"]],
  [/^app\/src\/home-lights\.jsx$/, ["run-lights-drawer-tests", "run-recovery-scenarios-tests"]],
  [/^app\/src\/home-people(\.jsx|-helpers\.js|-prewarm\.js)$/, ["run-people-tests"]],
  [/^app\/src\/home-intelligence\.jsx$/, ["run-intelligence-tests"]],
  [/^app\/src\/home-apartment/, ["run-apartment-3d-contract-tests", "run-apartment-aiming-tests", "run-apartment-data-tests", "test-apartment-travel-guard.cjs"]],
  [/^app\/src\/home-apartment(?:-ui|-aim|-cards)?\.jsx$/, ["run-apartment-ui-tests.js", "test-apartment-ui-browser.mjs"]],
  [/^tools\/(?:run-apartment-ui-tests\.js|test-apartment-ui-browser\.mjs)$/, ["run-apartment-ui-tests.js", "test-apartment-ui-browser.mjs"]],
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
  [/^app\/src\/home-lighting-intent\.js$/, ["run-lighting-intent-tests"]],
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
  [/^app\/src\/home-tokens\.css$/, ["run-focus-visibility-tests", "run-apartment-ui-tests.js", "test-apartment-ui-browser.mjs", "test-apartment-chat-header-browser.mjs"]],
  [/^app\/src\/home-overlay\.js$/, ["run-overlay-stack-tests", "test-apartment-escape-stack.cjs", "test-apartment-escape-browser.mjs"]],
  [/^app\/src\/home-apartment(?:-edit)?\.jsx$/, ["test-apartment-escape-stack.cjs", "test-apartment-escape-browser.mjs"]],
  [/^app\/src\/home-apartment\.jsx$/, ["test-apartment-room-selection.mjs", "test-apartment-room-selection-browser.mjs"]],
  [/^tools\/test-apartment-room-selection(?:-browser)?\.mjs$/, ["test-apartment-room-selection.mjs", "test-apartment-room-selection-browser.mjs"]],
  [/^app\/src\/home-apartment(?:-cards|-edit)?\.jsx$/, ["test-apartment-room-inspector.mjs", "test-apartment-room-inspector-browser.mjs"]],
  [/^tools\/test-apartment-room-inspector(?:-browser)?\.mjs$/, ["test-apartment-room-inspector.mjs", "test-apartment-room-inspector-browser.mjs"]],
  [/^app\/src\/home-apartment(?:-directional\.js|-cards\.jsx|\.jsx)$/, ["test-apartment-directional.mjs", "test-apartment-directional-integration.mjs", "test-apartment-directional-browser.mjs"]],
  [/^tools\/test-apartment-directional(?:-integration|-browser)?\.mjs$/, ["test-apartment-directional.mjs", "test-apartment-directional-integration.mjs", "test-apartment-directional-browser.mjs"]],
  [/^tools\/(?:fixtures\/apartment-directional-native-mouse|test-apartment-directional-native-mouse)\.mjs$/, ["test-apartment-directional-native-mouse.mjs", "test-apartment-directional-browser.mjs"]],
  [/^tools\/(?:fixtures\/apartment-directional-readiness|test-apartment-directional-readiness)\.mjs$/, ["test-apartment-directional-readiness.mjs", "test-apartment-directional-browser.mjs"]],
  [/^(?:app\/src\/home-3d\/apartment-directional-presentation\.mjs|tools\/test-apartment-directional-presentation\.mjs)$/, ["test-apartment-directional-presentation.mjs", "test-apartment-directional-browser.mjs"]],
  [/^(?:app\/src\/home-3d\/(?:equipment-virtual-fixtures\.mjs|markers\.js)|tools\/test-apartment-radial-visual\.mjs)$/, ["test-apartment-radial-visual.mjs"]],
  [/^tools\/(?:run-overlay-stack-tests\.js|test-apartment-escape-(?:stack\.cjs|browser\.mjs))$/, ["run-overlay-stack-tests", "test-apartment-escape-stack.cjs", "test-apartment-escape-browser.mjs"]],
  [/^app\/src\/.*\.(jsx|js)$/, ["run-focus-visibility-tests"]],
  [/^web-gateway\//, [
    "run-resilience-tests", "run-web-gateway-grounded-vision-tests",
    "run-web-gateway-stack-token-tests", "run-native-agent-gateway-tests",
    "run-native-agent-security-tests",
  ]],
  [/^tools\/lib\/home-app-source-contracts\.cjs$/, ["run-bootstrap-tests.js", "run-simulation-command-tests.js", "test-home-app-source-contracts.cjs"]],
  [/^tools\/(?:run-bootstrap-tests|run-simulation-command-tests)\.js$/, ["test-home-app-source-contracts.cjs"]],
  [/^tools\/test-home-app-source-contracts\.cjs$/, ["test-home-app-source-contracts.cjs"]],
  [/^tools\/test-apartment-chat-presenter\.cjs$/, ["test-apartment-chat-presenter.cjs"]],
  [/^tools\/test-apartment-unread-(?:state\.cjs|integration\.mjs)$/, ["test-apartment-unread-state.cjs", "test-apartment-unread-integration.mjs"]],
  [/^tools\/test-apartment-travel-guard\.cjs$/, ["test-apartment-travel-guard.cjs"]],
  [/^tools\/run-touched-harnesses\.mjs$/, ["run-apartment-shell-tests.js", "run-apartment-data-tests.js", "test-apartment-travel-guard.cjs", "test-home-app-source-contracts.cjs"]],
];

const harnessFileName = name => /\.(?:cjs|mjs|js)$/.test(name) ? name : `${name}.js`;

/* Hosted-CI seconds per plan item, used only to balance --shard partitions.
 * Measured on run 39214584656 (#293 touched job, 7m22s of harnesses) and the
 * #287 sibling run 39174968832, rounded up. An unmeasured browser suite
 * counts as its 145 s deadline and
 * any other unmeasured item as 1 s. Weights never add, drop or reorder a
 * harness; they only decide which worker runs it. */
const SHARD_WEIGHT_S = {
  "test-apartment-directional-browser.mjs": 141,
  "test-apartment-room-selection-browser.mjs": 111,
  "test-apartment-overlay-bridge-browser.mjs": 100,
  "test-apartment-room-inspector-browser.mjs": 62,
  "test-apartment-ui-browser.mjs": 6,
  "test-apartment-chat-header-browser.mjs": 5,
  "test-apartment-unread-integration.mjs": 5,
  "test-apartment-escape-browser.mjs": 4,
  "test-apartment-directional-integration.mjs": 4,
  "check-jsx.js": 4,
  "run-resilience-tests.js": 3,
  "test-apartment-escape-stack.cjs": 3,
};
const MAX_SHARDS = 32;
const RECEIPT_SCHEMA = "touched-harness-shard-receipt/v1";

function shardWeight(name) {
  if (Object.hasOwn(SHARD_WEIGHT_S, name)) return SHARD_WEIGHT_S[name];
  return /-browser\.mjs$/.test(name) ? 145 : 1;
}

/* Longest-first greedy partition with total, input-independent tie-breaks
 * (weight, then name; then the lowest-loaded, lowest-index worker). Each
 * worker's items keep the unsharded plan order. */
function partitionPlan(plan, count) {
  const loads = Array(count).fill(0), owner = new Map();
  const order = [...plan].sort((a, b) => shardWeight(b) - shardWeight(a) || (a < b ? -1 : a > b ? 1 : 0));
  for (const name of order) {
    let target = 0;
    for (let i = 1; i < count; i++) if (loads[i] < loads[target]) target = i;
    owner.set(name, target); loads[target] += shardWeight(name);
  }
  return loads.map((_, index) => plan.filter(name => owner.get(name) === index));
}

function usage(message) {
  console.error(`[touched-harnesses] ${message}`);
  process.exit(2);
}

function parseCount(value, flag) {
  if (!/^[1-9]\d*$/.test(value || "") || Number(value) > MAX_SHARDS) usage(`${flag} needs a worker count from 1 to ${MAX_SHARDS}`);
  return Number(value);
}

function parseArgs(argv) {
  const out = { base: null, files: null, all: false, list: false, shard: null, receipt: null, verifyReceipts: null, shards: null };
  const value = (i, flag) => {
    if (i >= argv.length || /^--/.test(argv[i])) usage(`${flag} needs a value`);
    return argv[i];
  };
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === "--base") out.base = value(++i, "--base");
    else if (argv[i] === "--files") out.files = value(++i, "--files").split(",").map((s) => s.trim()).filter(Boolean);
    else if (argv[i] === "--all") out.all = true;
    else if (argv[i] === "--list") out.list = true;
    else if (argv[i] === "--shard") {
      const match = /^([1-9]\d*)\/([1-9]\d*)$/.exec(value(++i, "--shard"));
      if (!match) usage("--shard needs i/n, for example 2/3");
      const count = parseCount(match[2], "--shard"), index = Number(match[1]);
      if (index > count) usage(`--shard ${index}/${count} names a worker outside 1..${count}`);
      out.shard = { index, count };
    } else if (argv[i] === "--receipt") out.receipt = value(++i, "--receipt");
    else if (argv[i] === "--verify-receipts") out.verifyReceipts = value(++i, "--verify-receipts");
    else if (argv[i] === "--shards") out.shards = parseCount(value(++i, "--shards"), "--shards");
    else usage(`unknown argument ${JSON.stringify(argv[i])}`);
  }
  if (out.all && (out.files || out.base)) usage("--all cannot be combined with --base or --files");
  if (out.files && out.base) usage("--files cannot be combined with --base");
  if (out.verifyReceipts && (out.shard || out.receipt || out.list || !out.shards)) usage("--verify-receipts needs --shards n and no --shard, --receipt or --list");
  if (out.shards && !out.verifyReceipts) usage("--shards is only for --verify-receipts");
  if (out.receipt && out.list) usage("--receipt records executed results and cannot be combined with --list");
  return out;
}

function changedFiles(base) {
  const range = `${base}...HEAD`;
  const stdout = execFileSync("git", ["diff", "--name-only", range], { cwd: REPO, encoding: "utf8" });
  return stdout.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
}

const git = argv => execFileSync("git", argv, { cwd: REPO, encoding: "utf8" }).trim();
const args = parseArgs(process.argv);
let files = [];
let harnesses = new Set();
const addHarness = name => harnesses.add(harnessFileName(name));

if (args.all) {
  for (const f of fs.readdirSync(TOOLS)) {
    if (/^run-.*\.js$/.test(f)) addHarness(f);
  }
  // Include mapped .cjs/.mjs suites without sweeping optional browser, GPU or
  // service workloads from every test-* file in the repository.
  for (const [, names] of MAP) names.forEach(addHarness);
} else {
  files = args.files || changedFiles(args.base || "origin/main");
  for (const file of files) {
    const posix = file.replace(/\\/g, "/");
    for (const [pattern, names] of MAP) {
      if (pattern.test(posix)) names.forEach(addHarness);
    }
    // An edit to a harness exercises that harness even without an app edit.
    // The dispatcher itself is mapped above instead of recursively running it.
    const ownHarness = /^tools\/(run-[^/]+\.(?:js|cjs|mjs))$/.exec(posix);
    if (ownHarness && ownHarness[1] !== "run-touched-harnesses.mjs") addHarness(ownHarness[1]);
  }
}

const touchedAppSrc = args.all || files.some((f) => f.replace(/\\/g, "/").startsWith("app/src/"));
const list = [...harnesses].sort();
// The full plan: every harness, then check-jsx (which also runs the ESM checks).
const plan = touchedAppSrc ? [...list, "check-jsx.js"] : list;
const viaBase = !args.all && !args.files;
const source = {
  mode: args.all ? "all" : args.files ? "files" : "base",
  base: viaBase ? args.base || "origin/main" : null,
  merge_base: viaBase ? git(["merge-base", args.base || "origin/main", "HEAD"]) : null,
  head: git(["rev-parse", "HEAD"]),
  changed_files: files.map((f) => f.replace(/\\/g, "/")).sort(),
};

console.log(`[touched-harnesses] ${args.all ? "(--all)" : files.length + " changed files"} → ${list.length} harnesses${touchedAppSrc ? " + check-jsx" : ""}`);

if (args.verifyReceipts) process.exit(verifyReceipts(args.verifyReceipts, args.shards));

const shardPlan = args.shard ? partitionPlan(plan, args.shard.count)[args.shard.index - 1] : plan;
if (args.shard) {
  const weight = items => items.reduce((sum, name) => sum + shardWeight(name), 0);
  console.log(`[touched-harnesses] shard ${args.shard.index}/${args.shard.count} → ${shardPlan.length} of ${plan.length} plan items (~${weight(shardPlan)}s of ~${weight(plan)}s measured)`);
}
const results = [];
function finish(code) {
  if (args.receipt) {
    const receipt = { schema: RECEIPT_SCHEMA, ...source, plan,
      shard: args.shard || { index: 1, count: 1 }, shard_plan: shardPlan, results };
    fs.mkdirSync(path.dirname(path.resolve(REPO, args.receipt)), { recursive: true });
    fs.writeFileSync(path.resolve(REPO, args.receipt), `${JSON.stringify(receipt, null, 2)}\n`);
    console.log(`[touched-harnesses] receipt ${args.receipt}`);
  }
  process.exit(code);
}

if (!plan.length) {
  console.log("[touched-harnesses] nothing mapped — done");
  finish(0);
}
if (!shardPlan.length) {
  console.log("[touched-harnesses] this shard has no plan items — done");
  finish(0);
}

let failed = [];
function record(name, status, started) {
  results.push({ name, status, duration_ms: Date.now() - started });
  if (status !== "ok") failed.push(name);
}
for (const name of shardPlan.filter(item => item !== "check-jsx.js")) {
  const file = path.join(TOOLS, name), started = Date.now();
  if (!fs.existsSync(file)) {
    console.log(`  FAIL ${name} (mapped harness file missing)`);
    record(name, "missing", started);
    continue;
  }
  if (args.list) { console.log(`  PLAN ${name}`); continue; }
  process.stdout.write(`  RUN  ${name} ... `);
  const nodeArgs = /^test-.*\.(?:cjs|mjs|js)$/.test(name) ? ["--test", file] : [file];
  const res = spawnSync(process.execPath, nodeArgs, { cwd: REPO, encoding: "utf8", timeout: 180000 });
  if (res.status === 0) {
    console.log("ok");
    record(name, "ok", started);
  } else {
    console.log("FAIL");
    const tail = `${res.stdout || ""}\n${res.stderr || ""}`.trim().split(/\r?\n/).slice(-15).join("\n");
    console.log(tail.replace(/^/gm, "       "));
    record(name, "fail", started);
  }
}

const runCheckJsx = shardPlan.includes("check-jsx.js");
if (runCheckJsx && args.list) console.log("  PLAN check-jsx.js");
if (runCheckJsx && !args.list) {
  const started = Date.now();
  let status = "ok";
  process.stdout.write("  RUN  check-jsx ... ");
  const res = spawnSync(process.execPath, [path.join(TOOLS, "check-jsx.js")], { cwd: REPO, encoding: "utf8", timeout: 180000 });
  if (res.status === 0) console.log("ok");
  else {
    console.log("FAIL");
    console.log(`${res.stdout || ""}\n${res.stderr || ""}`.trim().split(/\r?\n/).slice(-15).join("\n").replace(/^/gm, "       "));
    failed.push("check-jsx");
    status = "fail";
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
      status = "fail";
    }
  }
  results.push({ name: "check-jsx.js", status, duration_ms: Date.now() - started });
}

if (failed.length) {
  console.log(`\n[touched-harnesses] FAILED: ${failed.join(", ")}`);
  finish(1);
}
console.log(args.list ? "\n[touched-harnesses] plan complete (no harnesses executed)" : "\n[touched-harnesses] all green");
finish(0);

/* The aggregate gate: recompute the plan for the same base, then require the
 * workers' receipts to be exactly shards 1..n of that plan, each item run once
 * and green, so the shards are disjoint and their union is the unsharded plan.
 * Returns the exit code. */
function verifyReceipts(dir, count) {
  const problems = [], expected = partitionPlan(plan, count), ranIn = new Map(), present = new Set();
  const absolute = path.resolve(REPO, dir);
  const entries = fs.existsSync(absolute) ? fs.readdirSync(absolute).filter(name => /^shard-.*\.json$/.test(name)).sort() : [];
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  for (const name of entries) {
    const match = /^shard-([1-9]\d*)-of-([1-9]\d*)\.json$/.exec(name);
    if (!match || Number(match[2]) !== count || Number(match[1]) > count) { problems.push(`${name}: not a receipt for shards 1..${count}`); continue; }
    let receipt;
    try { receipt = JSON.parse(fs.readFileSync(path.join(absolute, name), "utf8")); } catch (error) { problems.push(`${name}: unreadable (${error.message})`); continue; }
    const index = Number(match[1]), label = `shard ${index}/${count}`;
    present.add(index);
    if (receipt?.schema !== RECEIPT_SCHEMA) problems.push(`${label}: schema ${JSON.stringify(receipt?.schema)}`);
    if (!same(receipt?.shard, { index, count })) problems.push(`${label}: receipt names shard ${JSON.stringify(receipt?.shard)}`);
    for (const key of ["mode", "base", "merge_base", "head", "changed_files"]) {
      if (!same(receipt?.[key], source[key])) problems.push(`${label}: ${key} ${JSON.stringify(receipt?.[key])} differs from ${JSON.stringify(source[key])}`);
    }
    if (!same(receipt?.plan, plan)) problems.push(`${label}: full plan differs from the unsharded plan`);
    if (!same(receipt?.shard_plan, expected[index - 1])) problems.push(`${label}: shard plan differs from the partition`);
    const ran = Array.isArray(receipt?.results) ? receipt.results : [];
    if (!same(ran.map(result => result?.name), expected[index - 1])) problems.push(`${label}: results do not match its shard plan one to one`);
    for (const result of ran) {
      if (result?.status !== "ok") problems.push(`${label}: ${result?.name} ${result?.status}`);
      if (ranIn.has(result?.name)) problems.push(`${label}: ${result?.name} also ran in shard ${ranIn.get(result?.name)}`);
      else ranIn.set(result?.name, index);
    }
  }
  for (let index = 1; index <= count; index++) if (!present.has(index)) problems.push(`shard ${index}/${count}: receipt missing`);
  const missing = plan.filter(name => !ranIn.has(name)), extra = [...ranIn.keys()].filter(name => !plan.includes(name));
  if (missing.length || extra.length) problems.push(`union differs from the unsharded plan: missing ${JSON.stringify(missing)}, extra ${JSON.stringify(extra)}`);
  for (const problem of problems) console.log(`  FAIL ${problem}`);
  if (problems.length) {
    console.log(`\n[touched-harnesses] receipts REJECTED: ${problems.length} problem(s)`);
    return 1;
  }
  console.log(`  OK   ${count} receipts, disjoint, union = unsharded plan (${plan.length} items), all green`);
  console.log("\n[touched-harnesses] receipts verified");
  return 0;
}
