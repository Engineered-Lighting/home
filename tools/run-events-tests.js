#!/usr/bin/env node
/* Pure local tests for app/src/home-events.jsx feed helper contracts. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-events.jsx");
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
  if (start < 0) throw new Error("missing start marker: " + startNeedle);
  const end = source.indexOf(endNeedle, start);
  if (end < 0) throw new Error("missing end marker after " + startNeedle + ": " + endNeedle);
  return source.slice(start, end);
}

function loadHelpers() {
  const script = [
    sliceBetween("const INVERSE_SERVICE = {", "function ActionContent"),
    sliceBetween("function speakerOf", "/* \u2500\u2500 Turn block"),
    "Object.assign(window, { INVERSE_SERVICE, speakerOf, groupEventsBySpeaker });",
  ].join("\n\n");
  const sandbox = { window: {} };
  vm.runInNewContext(script, sandbox, { filename: "home-events.helpers.js" });
  return sandbox.window;
}

const H = loadHelpers();

process.stdout.write("\nevents_exports_and_static_contract_test\n");
assert("speakerOf exported", typeof H.speakerOf === "function");
assert("groupEventsBySpeaker exported", typeof H.groupEventsBySpeaker === "function");
assert("INVERSE_SERVICE exported", H.INVERSE_SERVICE && typeof H.INVERSE_SERVICE === "object");
assert("source exports EventContent", source.includes("EventContent, StatusText"));
assert("source exports HelpContent and HelpRow", source.includes("HelpContent, HelpRow"));
assert("source exports grouping helpers", source.includes("groupEventsBySpeaker, speakerOf"));
assert("help rows dispatch input fill event", source.includes('CustomEvent("hg-fill-input"'));
assert("why-click gate class is present", source.includes("hg-why-clickable"));
assert("controllable actions prefer LightControlCard", source.includes("window.LightControlCard"));
assert("controllable actions prefer MediaControlCard", source.includes("window.MediaControlCard"));

process.stdout.write("\nevents_speaker_mapping_test\n");
const cases = [
  [{ kind: "user" }, "you"],
  [{ kind: "voice" }, "you"],
  [{ kind: "system" }, "system"],
  [{ kind: "help" }, "system"],
  [{ kind: "perception" }, "perception"],
  [{ kind: "proactive" }, "home"],
  [{ kind: "home" }, "home"],
  [{ kind: "external" }, "home"],
  [{ kind: "tool" }, "home"],
  [{ kind: "action" }, "home"],
  [{ kind: "diag" }, "home"],
  [{ kind: "unknown" }, "home"],
];
for (const [event, expected] of cases) {
  assert(`${event.kind} maps to ${expected}`, H.speakerOf(event) === expected);
}

process.stdout.write("\nevents_grouping_test\n");
assert("empty event list groups to empty", H.groupEventsBySpeaker([]).length === 0);
const events = [
  { id: "u1", kind: "user", time: "09:00" },
  { id: "v1", kind: "voice", time: "09:01" },
  { id: "h1", kind: "home", time: "09:02" },
  { id: "t1", kind: "tool", time: "09:03" },
  { id: "a1", kind: "action", time: "09:04" },
  { id: "p1", kind: "perception", time: "09:05" },
  { id: "s1", kind: "system", time: "09:06" },
  { id: "help1", kind: "help", time: "09:07" },
  { id: "u2", kind: "user", time: "09:08" },
  { id: "h2", kind: "external", time: "09:09" },
  { id: "pa1", kind: "proactive", time: "09:10" },
];
const groups = H.groupEventsBySpeaker(events);
assert("groups are consecutive speaker runs", groups.map((g) => g.speaker).join("|") === "you|home|perception|system|you|home", groups);
assert("user voice and typed events share one group", groups[0].events.map((e) => e.id).join(",") === "u1,v1", groups[0]);
assert("tool and action stay in assistant group", groups[1].events.map((e) => e.id).join(",") === "h1,t1,a1", groups[1]);
assert("help groups with system event", groups[3].events.map((e) => e.id).join(",") === "s1,help1", groups[3]);
assert("non-consecutive same speaker starts new group", groups[4].events[0].id === "u2" && groups[5].events[0].id === "h2", groups);
assert("group time tracks latest event in group", groups[1].time === "09:04" && groups[5].time === "09:10", groups.map((g) => g.time));
assert("grouping preserves event object identity", groups[1].events[2] === events[4]);

process.stdout.write("\nevents_action_inverse_test\n");
const inv = H.INVERSE_SERVICE;
assert("light turn_on undo is turn_off", inv["light.turn_on"] === "light.turn_off");
assert("light turn_off undo is turn_on", inv["light.turn_off"] === "light.turn_on");
assert("switch turn_on undo is turn_off", inv["switch.turn_on"] === "switch.turn_off");
assert("fan turn_off undo is turn_on", inv["fan.turn_off"] === "fan.turn_on");
assert("cover open undo closes", inv["cover.open_cover"] === "cover.close_cover");
assert("cover close undo opens", inv["cover.close_cover"] === "cover.open_cover");
assert("media play undo pauses", inv["media_player.media_play"] === "media_player.media_pause");
assert("media pause undo plays", inv["media_player.media_pause"] === "media_player.media_play");
assert("media turn_on undo turns off", inv["media_player.turn_on"] === "media_player.turn_off");
assert("media turn_off undo turns on", inv["media_player.turn_off"] === "media_player.turn_on");
assert("unsafe climate operations do not expose undo", !("climate.set_temperature" in inv));
assert("script calls do not expose undo", !("script.turn_on" in inv));
assert("scene calls do not expose undo", !("scene.turn_on" in inv));

if (fails) {
  console.log("\nFailures:");
  for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
}
console.log(`\n${passes} pass . ${fails} fail`);
process.exit(fails ? 1 : 0);
