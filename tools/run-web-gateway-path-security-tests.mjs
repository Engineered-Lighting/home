#!/usr/bin/env node
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import { safeFile } from "../web-gateway/path-security.mjs";

const root = path.join(os.tmpdir(), "home-gateway-safe-root", "app", "src");
const nested = safeFile(root, "/home-agent/index.html");
assert.equal(nested, path.join(root, "home-agent", "index.html"));
assert.equal(safeFile(root, "index.html?build=1"), path.join(root, "index.html"));

const denied = [
  "../src-evil/private.txt",
  "..\\src-evil\\private.txt",
  "%2e%2e%2fsrc-evil%2fprivate.txt",
  "../../outside.txt",
  "C:\\Windows\\System32\\drivers\\etc\\hosts",
  "C:/Windows/System32/drivers/etc/hosts",
  "\\\\attacker\\share\\private.txt",
  "%E0%A4%A",
];
for (const candidate of denied) {
  assert.equal(safeFile(root, candidate), null, candidate);
}

process.stdout.write(`web_gateway_path_security_test: ${denied.length + 2} pass · 0 fail\n`);
