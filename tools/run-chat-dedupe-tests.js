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
    settleAssistantFinalText,
    isNearDuplicateChatText,
    isRecentDuplicateEvent,
    sanitizeChatEventForStorage,
    findRecentUserIdx,
    findRecentAssistantIdx,
    findRecentAssistantLikeIdx,
    findAssistantDuplicateIdx,
    findActiveAssistantStreamingIdx,
    upsertAssistantTurnEvent,
    coalesceAssistantTurnEvents,
    serviceCallMayEnergizeLights,
    shouldSuppressActionCardsForToolCall,
  });
  `,
].join("\n");

const sandbox = {
  window: {},
  localStorage: {
    _data: new Map(),
    getItem(key) { return this._data.has(key) ? this._data.get(key) : null; },
    setItem(key, value) { this._data.set(key, String(value)); },
    removeItem(key) { this._data.delete(key); },
  },
  Date,
  RegExp,
  String,
  Math,
};
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

const groundedLookAnswer = "Books, a cup, and other small objects are on the glass-topped coffee table in the living room.";

assert("grounded-look cumulative snapshots settle to one sentence",
  [
    "Books, a cup, and other small objects",
    groundedLookAnswer,
    groundedLookAnswer,
  ].reduce((current, chunk) => H.mergeStreamingText(current, chunk), "") === groundedLookAnswer);

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

assert("travel-mode blocked response keeps one final sentence",
  H.normalizeChatEventText(
    "Travel Mode is on, so I did not turn on any lights.\n\nTravel Mode is on so did not\n\nTravel Mode is on, so I did not turn on any lights.",
  ) === "Travel Mode is on, so I did not turn on any lights.");

assert("travel-mode blocked streaming merge keeps one final sentence",
  H.mergeStreamingText(
    "Travel Mode is on, so I did not turn on any lights.",
    "Travel Mode is on so did not\n\nTravel Mode is on, so I did not turn on any lights.",
  ) === "Travel Mode is on, so I did not turn on any lights.");

assert("camera answer drops orphan fragment between duplicate finals",
  H.normalizeChatEventText(
    "A motorcycle covered with a tarp is parked in the driveway.\n\nmotorcycle covered with\n\nA motorcycle covered with a tarp is parked in the driveway.",
  ) === "A motorcycle covered with a tarp is parked in the driveway.");

assert("camera answer final transcript replaces partial duplicate draft",
  H.settleAssistantFinalText(
    "A motorcycle covered with a tarp is parked in the driveway.\n\nmotorcycle covered with",
    "A motorcycle covered with a tarp is parked in the driveway.",
  ) === "A motorcycle covered with a tarp is parked in the driveway.");

assert("conversation finished final transcript replaces stale streamed draft",
  H.settleAssistantFinalText(
    "I don't have a full picture right now.\n\nI don't have a full picture right now. I can check specific rooms if you'd like.",
    "I don't have a full picture right now. I can check specific rooms if you'd like.",
  ) === "I don't have a full picture right now. I can check specific rooms if you'd like.");

assert("intent-end final answer replaces streamed duplicate draft",
  H.settleAssistantFinalText(
    "Travel Mode is on, so I did not turn on any lights.\n\nTravel Mode is on so did not",
    "Travel Mode is on, so I did not turn on any lights.",
  ) === "Travel Mode is on, so I did not turn on any lights.");

assert("persisted duplicated streaming event is normalized and settled",
  (() => {
    const ev = H.sanitizeChatEventForStorage({
      kind: "home",
      text: "Travel Mode is on, so I did not turn on any lights.\n\nTravel Mode is on so did not\n\nTravel Mode is on, so I did not turn on any lights.",
      streaming: true,
    });
    return ev.text === "Travel Mode is on, so I did not turn on any lights." && !ev.streaming;
  })());

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

assert("intent-end final answer replaces abstract visual duplicate draft",
  H.settleAssistantFinalText(
    "I don't currently see anyone in any room. The driveway camera shows a person in a red shirt standing near a covered car, while another person walks a dog across the street.",
    apartmentStatusAnswer,
  ) === apartmentStatusAnswer);

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

assert("active streaming assistant is found across interleaved system/perception events",
  H.findActiveAssistantStreamingIdx([
    { kind: "user", time: nowTime(), text: "What do you see in my apartment" },
    { kind: "home", time: nowTime(), text: "I'll check current status", streaming: true },
    { kind: "system", time: nowTime(), text: "system get_all_rooms_state..." },
    { kind: "perception", time: nowTime(), text: "driveway: A person walks by." },
  ], "home", 80) === 1);

assert("active streaming assistant search stops at a newer user turn",
  H.findActiveAssistantStreamingIdx([
    { kind: "user", time: nowTime(), text: "First question" },
    { kind: "home", time: nowTime(), text: "Old partial", streaming: true },
    { kind: "user", time: nowTime(), text: "Second question" },
    { kind: "system", time: nowTime(), text: "system connected" },
  ], "home", 80) === -1);

assert("short repeated phrases are preserved",
  H.normalizeChatEventText("no no no no no no") === "no no no no no no");

const prev = [
  { kind: "user", time: nowTime(), text: "Whats in my driveway" },
  { kind: "home", time: nowTime(), text: "A car is parked outside" },
];

assert("recent duplicate user/voice text is detected across kind names",
  H.isRecentDuplicateEvent(prev, { kind: "voice", text: "Whats in my driveway" }));

assert("same user text in a distinct keyed turn is preserved",
  !H.isRecentDuplicateEvent([
    { kind: "user", time: nowTime(), text: "Whats in my driveway", turnKey: "turn-1" },
  ], { kind: "user", time: nowTime(), text: "Whats in my driveway", turnKey: "turn-2" }));

assert("recent duplicate assistant text is detected",
  H.isRecentDuplicateEvent(prev, { kind: "home", text: "A car is parked outside" }));

assert("perception card repeating recent assistant answer is suppressed",
  H.isRecentDuplicateEvent([
    ...prev,
    { kind: "home", time: nowTime(), text: "A covered vehicle is parked in the driveway." },
  ], { kind: "perception", text: "driveway: A covered vehicle is parked in the driveway." }));

assert("grounded-look image survives duplicate-caption suppression",
  !H.isRecentDuplicateEvent([
    { kind: "user", time: nowTime(), text: "What's on my coffee table?" },
    { kind: "home", time: nowTime(), text: groundedLookAnswer },
  ], {
    kind: "perception",
    time: nowTime(),
    text: `living_room: ${groundedLookAnswer}`,
    snapshotUrl: "/proxy/vision/reason/living_room/annotated.jpg?cb=1",
    imageMode: "annotated",
  }));

assert("explicit unavailable-image state survives duplicate-caption suppression",
  !H.isRecentDuplicateEvent([
    { kind: "home", time: nowTime(), text: groundedLookAnswer },
  ], {
    kind: "perception",
    time: nowTime(),
    text: `living_room: ${groundedLookAnswer}`,
    imageUnavailable: true,
  }));

assert("three assistant sources coalesce into one grounded-look bubble",
  (() => {
    const turnKey = "ha-turn:e-42";
    const events = H.coalesceAssistantTurnEvents([
      { id: "u1", kind: "user", time: nowTime(), text: "What's on my coffee table?", turnKey },
      { id: "a1", kind: "home", time: nowTime(), text: groundedLookAnswer, turnKey, streaming: false },
      { id: "a2", kind: "home", time: nowTime(), text: groundedLookAnswer, convId: "conversation-1" },
      { id: "a3", kind: "home", time: nowTime(), text: groundedLookAnswer, sourceTurnId: "sse:99" },
    ]);
    const answers = events.filter((event) => event.kind === "home");
    return answers.length === 1 && answers[0].text === groundedLookAnswer;
  })());

assert("turn identity replaces a different streamed draft with its final",
  (() => {
    const turnKey = "ha-turn:e-43";
    const events = H.upsertAssistantTurnEvent([
      { id: "u1", kind: "user", time: nowTime(), text: "Look in the living room", turnKey },
      { id: "a1", kind: "home", time: nowTime(), text: "I'll take a closer look.", turnKey, streaming: true },
      { id: "s1", kind: "system", time: nowTime(), text: "looking closely (living_room)…" },
    ], {
      id: "a2", kind: "home", time: nowTime(), text: groundedLookAnswer,
      turnKey, convId: "conversation-1", streaming: false,
    });
    const answers = events.filter((event) => event.kind === "home");
    return answers.length === 1
      && answers[0].id === "a1"
      && answers[0].text === groundedLookAnswer
      && answers[0].convId === "conversation-1"
      && answers[0].streaming === false;
  })());

assert("identical answers across separate user turns remain separate",
  H.coalesceAssistantTurnEvents([
    { kind: "user", time: nowTime(), text: "First look" },
    { kind: "home", time: nowTime(), text: groundedLookAnswer },
    { kind: "user", time: nowTime(), text: "Look again" },
    { kind: "home", time: nowTime(), text: groundedLookAnswer },
  ]).filter((event) => event.kind === "home").length === 2);

assert("unscoped identical assistant events far apart remain separate",
  H.coalesceAssistantTurnEvents([
    { kind: "home", time: "08:00:00", text: groundedLookAnswer },
    { kind: "system", time: "08:30:00", text: "reconnected" },
    { kind: "home", time: "09:00:00", text: groundedLookAnswer },
  ]).filter((event) => event.kind === "home").length === 2);

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

assert("travel mode suppresses execute_services light action cards",
  H.shouldSuppressActionCardsForToolCall("execute_services", {
    list: [{
      domain: "light",
      service: "turn_on",
      service_data: { entity_id: ["light.office"] },
    }],
  }, true));

assert("travel mode does not suppress harmless turn_off cards",
  !H.shouldSuppressActionCardsForToolCall("execute_services", {
    list: [{
      domain: "light",
      service: "turn_off",
      service_data: { entity_id: ["light.office"] },
    }],
  }, true));

assert("travel mode does not suppress non-light action cards",
  !H.shouldSuppressActionCardsForToolCall("execute_services", {
    list: [{
      domain: "media_player",
      service: "media_pause",
      service_data: { entity_id: ["media_player.living_room"] },
    }],
  }, true));

process.stdout.write("\n");
process.stdout.write(`${passes} pass . ${fails} fail\n`);
if (failures.length) process.exit(1);
