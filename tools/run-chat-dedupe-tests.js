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
    isNearDuplicateChatText,
    isRecentDuplicateEvent,
    findRecentUserIdx,
    findRecentAssistantIdx,
    findRecentAssistantLikeIdx,
    findAssistantDuplicateIdx,
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

assert("stream overlap keeps one full camera answer",
  H.mergeStreamingText(
    "A car covered with a silver protective",
    "A car covered with a silver protective cover is in the driveway.",
  ) === "A car covered with a silver protective cover is in the driveway.");

assert("suffix after full camera answer is ignored",
  H.mergeStreamingText(
    "A car covered with a silver protective cover is in the driveway.",
    "cover is in the driveway.",
  ) === "A car covered with a silver protective cover is in the driveway.");

assert("near-duplicate partial is replaced by canonical final answer",
  H.mergeStreamingText(
    "I don't have full picture right now.",
    "I don't have a full picture right now. I can check specific areas if you'd like.",
  ) === "I don't have a full picture right now. I can check specific areas if you'd like.");

assert("prefix full suffix fragments collapse across lines",
  H.normalizeChatEventText(
    "A car covered with a silver protective\nA car covered with a silver protective cover is in the driveway.\ncover is in the driveway.",
  ) === "A car covered with a silver protective cover is in the driveway.");

assert("prefix full suffix fragments collapse without newlines",
  H.normalizeChatEventText(
    "A car covered with a silver protective A car covered with a silver protective cover is in the driveway. cover is in the driveway.",
  ) === "A car covered with a silver protective cover is in the driveway.");

assert("near-duplicate answer fragments collapse into one final answer",
  H.normalizeChatEventText(
    "I don't have full picture right now. I don't have a full picture right now. I can check specific areas if you'd like. can check specific areas you'd like.",
  ) === "I don't have a full picture right now. I can check specific areas if you'd like.");

assert("similar final answer matches active partial bubble",
  H.isNearDuplicateChatText(
    "I don't have full picture right now.",
    "I don't have a full picture right now. I can check specific areas if you'd like.",
  ));

assert("adjacent duplicate paragraphs collapse",
  H.normalizeChatEventText("Whats in my driveway\n\nWhats in my driveway") === "Whats in my driveway");

assert("adjacent duplicate word-runs collapse when long enough",
  H.normalizeChatEventText("A car is parked outside A car is parked outside") === "A car is parked outside");

const apartmentStatusAnswer = "I don't currently see anyone in any room. The driveway camera shows a person in a red shirt standing near a covered car, while another person walks a dog across the street. The kitchen appears to be empty with the stove, sink, and open cabinets visible. All other rooms seem unoccupied at the moment.";

assert("screenshot apartment status duplicate paragraph collapses",
  H.normalizeChatEventText(`${apartmentStatusAnswer}\n\n${apartmentStatusAnswer}`) === apartmentStatusAnswer);

assert("screenshot apartment status final merge stays single",
  H.mergeStreamingText(apartmentStatusAnswer, apartmentStatusAnswer) === apartmentStatusAnswer);

assert("screenshot apartment status duplicated final merge collapses",
  H.mergeStreamingText(apartmentStatusAnswer, `${apartmentStatusAnswer}\n\n${apartmentStatusAnswer}`) === apartmentStatusAnswer);

const fullPictureAnswer = "I don't have a full picture right now. I can describe specific rooms if you'd like\u2014just say which one.";

assert("screenshot full-picture duplicate paragraph collapses",
  H.normalizeChatEventText(`${fullPictureAnswer}\n\n${fullPictureAnswer}`) === fullPictureAnswer);

assert("delayed duplicate assistant answer is found within same turn",
  H.findAssistantDuplicateIdx([
    { kind: "user", time: nowTime(), text: "What do you see in my home" },
    { kind: "home", time: nowTime(), text: fullPictureAnswer },
    { kind: "perception", time: nowTime(), text: "driveway: A person walks by." },
  ], fullPictureAnswer, "home", 60) === 1);

assert("same assistant answer after a new user turn is preserved",
  H.findAssistantDuplicateIdx([
    { kind: "user", time: nowTime(), text: "What do you see in my home" },
    { kind: "home", time: nowTime(), text: fullPictureAnswer },
    { kind: "user", time: nowTime(), text: "Ask again" },
  ], fullPictureAnswer, "home", 60) === -1);

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

assert("perception card repeating recent assistant answer is suppressed",
  H.isRecentDuplicateEvent([
    ...prev,
    { kind: "home", time: nowTime(), text: "A covered vehicle is parked in the driveway." },
  ], { kind: "perception", text: "driveway: A covered vehicle is parked in the driveway." }));

assert("distinct perception card after assistant answer is preserved",
  !H.isRecentDuplicateEvent([
    ...prev,
    { kind: "home", time: nowTime(), text: "A covered vehicle is parked in the driveway." },
  ], { kind: "perception", text: "driveway: A person is taking out the trash." }));

assert("recent similar assistant text is detected",
  H.findRecentAssistantLikeIdx([
    ...prev,
    { kind: "home", time: nowTime(), text: "I don't have full picture right now.", streaming: true },
  ], "I don't have a full picture right now. I can check specific areas if you'd like.", "home", 20) === 2);

assert("distinct assistant text is not treated as duplicate",
  !H.isRecentDuplicateEvent(prev, { kind: "home", text: "A person is outside" }));

process.stdout.write("\n");
process.stdout.write(`${passes} pass . ${fails} fail\n`);
if (failures.length) process.exit(1);
