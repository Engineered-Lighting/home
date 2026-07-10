#!/usr/bin/env node
/*
 * Redact and normalize captured Frigate MQTT payloads.
 *
 * Usage examples:
 *   mosquitto_sub -t frigate/reviews -t frigate/events -v | node tools/capture-frigate-perception-fixtures.mjs
 *   Get-Content captured.txt | node tools/capture-frigate-perception-fixtures.mjs
 *
 * Input may be either:
 *   frigate/reviews {"camera":"living_room",...}
 * or one JSON object per line with {topic,payload|payload_json}.
 */
import readline from "node:readline";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const normalizer = require("../app/src/home-frigate-perception.js");

function redactUrl(value) {
  if (typeof value !== "string") return value;
  return value
    .replace(/rtsp:\/\/[^@\s]+@/gi, "rtsp://REDACTED@")
    .replace(/https?:\/\/([^/:\s]+):([^/@\s]+)@/gi, "http://REDACTED@")
    .replace(/([?&](?:token|auth|password|key)=)[^&\s]+/gi, "$1REDACTED");
}

function redactDeep(value) {
  if (Array.isArray(value)) return value.map(redactDeep);
  if (!value || typeof value !== "object") return redactUrl(value);
  const out = {};
  for (const [key, child] of Object.entries(value)) {
    if (/password|token|secret|authorization|cookie/i.test(key)) out[key] = "REDACTED";
    else out[key] = redactDeep(child);
  }
  return out;
}

function parseLine(line) {
  const trimmed = line.trim();
  if (!trimmed) return null;
  try {
    const parsed = JSON.parse(trimmed);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch (_) {
    const match = /^(frigate\/(?:reviews|events))\s+(.+)$/.exec(trimmed);
    if (!match) return null;
    return { topic: match[1], payload: match[2] };
  }
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of rl) {
  const envelope = parseLine(line);
  if (!envelope) continue;
  const redacted = redactDeep(envelope);
  const normalized = normalizer.normalizeHomePerceptionEvent(redacted);
  process.stdout.write(JSON.stringify({ raw: redacted, normalized }, null, 2) + "\n");
}
