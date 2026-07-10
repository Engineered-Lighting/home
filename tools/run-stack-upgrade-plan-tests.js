#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const repo = path.resolve(__dirname, "..");
const matrixPath = path.join(repo, "tools", "stack-upgrade-candidates.json");

let passes = 0;
let fails = 0;

function assert(name, condition, detail) {
  if (condition) {
    passes++;
    process.stdout.write(`  PASS  ${name}\n`);
  } else {
    fails++;
    process.stdout.write(`  FAIL  ${name}`);
    if (detail !== undefined) {
      process.stdout.write(`\n        ${typeof detail === "string" ? detail : JSON.stringify(detail, null, 2).replace(/\n/g, "\n        ")}`);
    }
    process.stdout.write("\n");
  }
}

function loadJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

process.stdout.write("\nstack_upgrade_candidate_matrix\n");
const matrix = loadJson(matrixPath);
assert("schema is current", matrix.schema === 1);
assert("production rule forbids broad travel upgrades", /traveling/i.test(matrix.productionRule) && /blocker/i.test(matrix.productionRule));
assert("candidate tracks exist", Array.isArray(matrix.candidateTracks) && matrix.candidateTracks.length >= 6);
assert("track ids are unique", new Set(matrix.candidateTracks.map((t) => t.id)).size === matrix.candidateTracks.length);
assert("each track has gates and rollback", matrix.candidateTracks.every((t) =>
  t.id && t.label && t.priority && Array.isArray(t.promotionGates) && t.promotionGates.length > 0 && t.rollback
));
assert("runtime is maintenance-window-only", matrix.candidateTracks.some((t) =>
  t.id === "runtime" && !t.productionSafe && /maintenance/i.test(t.goal)
));
assert("frontend is lower priority than AI tracks", matrix.candidateTracks.some((t) => t.id === "frontend" && t.priority === "p4"));

process.stdout.write("\nstack_upgrade_inventory_contract\n");
let inventory = null;
try {
  const raw = execFileSync("node", ["tools/stack-upgrade-inventory.mjs", "--json"], {
    cwd: repo,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 20000,
  });
  inventory = JSON.parse(raw);
  assert("inventory emits parseable json", true);
} catch (err) {
  assert("inventory emits parseable json", false, err.stderr?.toString() || err.message);
}

if (inventory) {
  assert("inventory captures vllm model", inventory.vllm && typeof inventory.vllm.model === "string" && inventory.vllm.model.length > 0, inventory.vllm);
  assert("inventory captures served model name", inventory.vllm && inventory.vllm.servedModelName === "qwen3-vl-30b", inventory.vllm);
  assert("inventory flags compose model mismatch or unpinned image", Array.isArray(inventory.warnings) && inventory.warnings.length > 0, inventory.warnings);
  assert("inventory captures torch pins", inventory.runtimePins && inventory.runtimePins.parakeetTorch && inventory.runtimePins.kokoroTorch, inventory.runtimePins);
  assert("inventory includes production rule", /traveling/i.test(inventory.productionRule || ""));
}

process.stdout.write(`\nstack upgrade plan tests: ${passes} passed, ${fails} failed\n`);
if (fails) process.exit(1);
