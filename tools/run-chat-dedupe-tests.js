#!/usr/bin/env node
/* Regression tests for chat duplicate/snapshot text handling. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-app.jsx");
const source = fs.readFileSync(SRC, "utf8");

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, cond, detail) {
  if (cond) {
    passes++;
    process.stdout.write("  PASS  " + name + "\n");
  } else {
    fails++;
    failures.push({ name, detail });
    process.stdout.write("  FAIL  " + name);
    if (detail !== undefined) {
      const dumped = typeof detail === "string" ? detail : JSON.stringify(detail, null, 2);
      process.stdout.write("\n        " + dumped.replace(/\n/g, "\n        "));
    }
    process.stdout.write("\n");
  }
}

function sliceBetween(startNeedle, endNeedle) {
  const start = source.indexOf(startNeedle);
  const end = source.indexOf(endNeedle, start);
  if (start < 0 || end < 0) throw new Error(`slice not found: ${startNeedle} -> ${endNeedle}`);
  return source.slice(start, end);
}

const helperSource = [
  sliceBetween("const ASR_ALIASES", "function readViewportProfile"),
  sliceBetween("function _withinWindow", "function isDirectLightStateQuestion"),
  `
  Object.assign(window, {
    canonicalChatText,
    collapseRepeatedAdjacentText,
    normalizeChatEventText,
    mergeStreamingText,
    isRecentDuplicateEvent,
    findRecentUserIdx,
    findRecentAssistantIdx,
  });
  `,
].join("\n");

const sandbox = { window: {}, Date, RegExp, String, Math };
vm.runInNewContext(helperSource, sandbox, { filename: "home-app.chat-dedupe-helpers.js" });
const H = sandbox.window;

function nowTime() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

process.stdout.write("\nchat_dedupe_tests\n");

assert("snapshot stream replaces the previous prefix",
  H.mergeStreamingText("A car", "A car covered with a tarp") === "A car covered with a tarp");

assert("true token delta still appends",
  H.mergeStreamingText("A car", " covered with a tarp") === "A car covered with a tarp");

assert("duplicate snapshot does not append twice",
  H.mergeStreamingText("A car covered with a tarp", "A car covered with a tarp") === "A car covered with a tarp");

assert("adjacent duplicate paragraphs collapse",
  H.normalizeChatEventText("Whats in my driveway\n\nWhats in my driveway") === "Whats in my driveway");

assert("adjacent duplicate word-runs collapse when long enough",
  H.normalizeChatEventText("A car is parked outside A car is parked outside") === "A car is parked outside");

assert("short repeated phrases are preserved",
  H.normalizeChatEventText("no no no no no no") === "no no no no no no");

const prev = [
  { kind: "user", time: nowTime(), text: "Whats in my driveway" },
  { kind: "home", time: nowTime(), text: "A car is parked outside" },
];

assert("recent duplicate user/voice text is detected across kind names",
  H.isRecentDuplicateEvent(prev, { kind: "voice", text: "Whats in my driveway" }));

assert("recent duplicate assistant text is detected",
  H.isRecentDuplicateEvent(prev, { kind: "home", text: "A car is parked outside" }));

assert("distinct assistant text is not treated as duplicate",
  !H.isRecentDuplicateEvent(prev, { kind: "home", text: "A person is outside" }));

process.stdout.write("\n");
process.stdout.write(`${passes} pass . ${fails} fail\n`);
if (failures.length) process.exit(1);
