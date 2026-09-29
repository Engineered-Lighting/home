#!/usr/bin/env node
/* Deterministic parsing of explicit cross-home lighting commands. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(REPO, "app", "src", "home-lighting-intent.js"), "utf8");
const window = {};
vm.runInNewContext(source, {window});
const {parse} = window.HomeLightingIntent;

let passes = 0;
const failures = [];
function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (ok) passes++;
  else failures.push({name, actual, expected});
  process.stdout.write(`  ${ok ? "PASS" : "FAIL"}  ${name}\n`);
}
const request = (sites, targets, operation, brightness = null) => ({request: {sites, targets, operation, brightness}});
const clarify = reason => value => value && value.clarify === reason;

const cases = [
  ["Turn off the kitchen light in Victoria.", "echo", request(["victoria"], ["kitchen light"], "off")],
  ["please switch on the porch and the hall in LA", "victoria", request(["echo"], ["porch", "hall"], "on")],
  ["turn the lights off in both homes", "echo", request(["echo", "victoria"], "all", "off")],
  ["turn off all the lights in victoria", "echo", request(["victoria"], "all", "off")],
  ["Dim the living room lamp to 30% in Victoria", "echo", request(["victoria"], ["living room lamp"], "brightness", 30)],
  ["set the Victoria kitchen light to 100 percent", "echo", request(["victoria"], ["kitchen light"], "brightness", 100)],
  ["turn off Victoria's lights", "echo", request(["victoria"], "all", "off")],
  ["turn on the lights in los angeles", "victoria", request(["echo"], "all", "on")],
  ["Turn off the kitchen light", "echo", null],                   // LA view: existing path
  ["what lighting do I prefer in the evening", "victoria", null], // not a command
  ["turn off the tv in the den", "echo", null],                   // no home named, LA view
];
for (const [text, viewed, expected] of cases) check(`${text} [${viewed}]`, parse(text, viewed), expected);

const clarifications = [
  ["turn off the kitchen light", "victoria", "which_home"],
  ["turn them off in victoria", "echo", "which_lights"],
  ["turn it on in both homes", "echo", "which_lights"],
  ["set the kitchen light to 0% in victoria", "echo", "brightness_range"],
  ["set the kitchen light to 150% in victoria", "echo", "brightness_range"],
  ["turn off the victoria kitchen light in LA", "echo", "which_home"],
];
for (const [text, viewed, reason] of clarifications) {
  const value = parse(text, viewed);
  check(`${text} [${viewed}] asks ${reason}`, clarify(reason)(value) && typeof value.message === "string", true);
}

// Nothing that is not on/off/brightness of named or all lights becomes a request.
for (const text of ["activate the movie scene in victoria", "set the kitchen light to blue in victoria",
  "unlock the front door in victoria", "run the bedtime script in both homes", "x".repeat(200) + " in victoria"]) {
  check(`no request: ${text.slice(0, 50)}`, parse(text, "victoria")?.request === undefined, true);
}
check("eight names at most", parse("turn off a, b, c, d, e, f, g, h and i in victoria", "echo").clarify, "which_lights");
check("the module is frozen", Object.isFrozen(window.HomeLightingIntent), true);

process.stdout.write(`\n${passes} passed, ${failures.length} failed\n`);
if (failures.length) {
  for (const failure of failures) process.stdout.write(JSON.stringify(failure) + "\n");
  process.exit(1);
}
