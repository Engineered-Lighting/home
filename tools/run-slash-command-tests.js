#!/usr/bin/env node
/* Pure local tests for Home app slash-command catalog/handler coherence. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-app.jsx");
const EVENTS_SRC = path.join(REPO, "app", "src", "home-events.jsx");
const CONST_SRC = path.join(REPO, "ha-config", "extended_openai_conversation", "const.py");
const source = fs.readFileSync(SRC, "utf8");
const eventsSource = fs.readFileSync(EVENTS_SRC, "utf8");
const constSource = fs.readFileSync(CONST_SRC, "utf8");

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
      const dumped = typeof detail === "string"
        ? detail
        : JSON.stringify(detail, null, 2);
      process.stdout.write("\n        " + dumped.replace(/\n/g, "\n        "));
    }
    process.stdout.write("\n");
  }
}

function extractCatalog() {
  const start = source.indexOf("const SLASH_CMD_CATEGORIES");
  const end = source.indexOf("function InputRow", start);
  if (start < 0 || end < 0) throw new Error("slash command catalog block not found");
  const script = source.slice(start, end) + "\nObject.assign(window, { SLASH_CMD_CATEGORIES, SLASH_CMDS });";
  const sandbox = { window: {} };
  vm.runInNewContext(script, sandbox, { filename: "home-app.slash-catalog.js" });
  return sandbox.window;
}

function extractHandleBlock() {
  const start = source.indexOf("const handleCommand = useCallback");
  const end = source.indexOf("default:", start);
  if (start < 0 || end < 0) throw new Error("slash command handler block not found");
  return source.slice(start, end);
}

function extractCaseBlock(caseName) {
  const start = source.indexOf(`case "${caseName}"`);
  if (start < 0) throw new Error(`case ${caseName} not found`);
  const rest = source.slice(start);
  const branchEnd = rest.match(/\n\s*return true;\s*\n\s*}\s*\n\s*case\s+"[^"]+"\s*:/);
  if (branchEnd) {
    const nextCaseInMatch = branchEnd[0].lastIndexOf("\n");
    return source.slice(start, start + branchEnd.index + nextCaseInMatch);
  }
  const next = rest.slice(1).search(/\n\s*case\s+"[^"]+"\s*:/);
  const end = next >= 0 ? start + 1 + next : source.indexOf("default:", start);
  return source.slice(start, end >= 0 ? end : source.length);
}

function extractBackendToolNames() {
  return Array.from(
    constSource.matchAll(/"spec"\s*:\s*\{[\s\S]*?"name"\s*:\s*"([^"]+)"/g),
    (m) => m[1]
  );
}

function extractAgentToolNames() {
  const block = extractCaseBlock("agent-tools");
  return Array.from(block.matchAll(/\["([^"]+)"\s*,/g), (m) => m[1]);
}

function commandName(cmd) {
  return String(cmd || "").replace(/^\//, "");
}

function completionValue(cmd, commands) {
  const entry = commands.find((c) => c.cmd === cmd);
  return cmd + (entry && entry.hint ? " " : "");
}

(function main() {
  process.stdout.write("\nslash_command_catalog_test\n");
  const api = extractCatalog();
  const commands = api.SLASH_CMDS;
  const categories = api.SLASH_CMD_CATEGORIES;
  assert("SLASH_CMDS exported", Array.isArray(commands) && commands.length >= 38, commands && commands.length);
  assert("SLASH_CMD_CATEGORIES exported", Array.isArray(categories) && categories.length >= 8, categories && categories.length);

  const commandNames = commands.map((c) => c.cmd);
  const categoryIds = new Set(categories.map((c) => c.id));
  assert("catalog commands are unique", new Set(commandNames).size === commandNames.length, commandNames);
  assert("category ids are unique", categoryIds.size === categories.length, categories.map((c) => c.id));
  assert("every command starts with slash", commands.every((c) => /^\/[a-z0-9-]+$/.test(c.cmd)), commands.map((c) => c.cmd));
  assert("every command has known category", commands.every((c) => categoryIds.has(c.category)), commands.filter((c) => !categoryIds.has(c.category)));
  assert("every category has at least one command", categories.every((cat) => commands.some((c) => c.category === cat.id)), categories);

  const byCmd = Object.fromEntries(commands.map((c) => [c.cmd, c]));
  assert("/route-log is visible in help/autocomplete", !!byCmd["/route-log"], commandNames);
  assert("/route-log lives in debug category", byCmd["/route-log"] && byCmd["/route-log"].category === "debug", byCmd["/route-log"]);
  assert("/route-log advertises /routes alias", /\/routes/.test(byCmd["/route-log"] && byCmd["/route-log"].desc || ""), byCmd["/route-log"]);
  assert("/s2s catalog describes configuration, not retired on/off voice mode", byCmd["/s2s"] && !/\bon\|off\b/i.test(byCmd["/s2s"].hint + " " + byCmd["/s2s"].desc), byCmd["/s2s"]);

  process.stdout.write("\nslash_command_handler_coherence_test\n");
  const handleBlock = extractHandleBlock();
  const handlerCases = Array.from(handleBlock.matchAll(/case\s+"([^"]+)"\s*:/g), (m) => m[1]);
  const handlerSet = new Set(handlerCases);
  assert("handler cases are unique", handlerSet.size === handlerCases.length, handlerCases);

  const visibleNames = new Set(commands.map((c) => commandName(c.cmd)));
  const missingHandlers = [...visibleNames].filter((name) => !handlerSet.has(name));
  assert("every visible catalog command has a handler case", missingHandlers.length === 0, missingHandlers);

  const hiddenAliases = {
    "stacktoken": "stack-token",
    "cams": "cameras",
    "3d": "apartment",
    "vl": "labeler",
    "world": "world-state",
    "clip": "describe-clip",
    "clips": "find-clips",
    "tools": "agent-tools",
    "version": "about",
    "routes": "route-log",
    "spatial-home": "apartment",
  };
  const unexpectedHidden = [...handlerSet].filter((name) => !visibleNames.has(name) && !hiddenAliases[name]);
  assert("hidden handler aliases are explicitly allowlisted", unexpectedHidden.length === 0, unexpectedHidden);
  const brokenAliasTargets = Object.entries(hiddenAliases)
    .filter(([alias, primary]) => !handlerSet.has(alias) || !visibleNames.has(primary));
  assert("hidden aliases point at visible primary commands", brokenAliasTargets.length === 0, brokenAliasTargets);

  process.stdout.write("\nslash_command_completion_test\n");
  assert("commands with no hint complete without trailing space", completionValue("/lights", commands) === "/lights", completionValue("/lights", commands));
  assert("commands with hints complete with trailing space", completionValue("/route-log", commands) === "/route-log ", completionValue("/route-log", commands));
  assert("meta help completes without trailing space", completionValue("/help", commands) === "/help", completionValue("/help", commands));
  assert("s2s config command completes with trailing space", completionValue("/s2s", commands) === "/s2s ", completionValue("/s2s", commands));

  process.stdout.write("\ninput_row_button_sweep_contract_test\n");
  const inputStart = source.indexOf("function InputRow");
  const inputEnd = source.indexOf("/* VoiceModeButton", inputStart);
  const inputBlock = inputStart >= 0 && inputEnd >= 0 ? source.slice(inputStart, inputEnd) : "";
  const stopStart = source.indexOf("const stopStreaming = useCallback");
  const stopEnd = source.indexOf("/*", stopStart + 1);
  const stopBlock = stopStart >= 0 && stopEnd >= 0 ? source.slice(stopStart, stopEnd) : "";
  const renderStart = source.indexOf("<InputRow");
  const renderEnd = source.indexOf("</div>", renderStart);
  const renderBlock = renderStart >= 0 && renderEnd >= 0 ? source.slice(renderStart, renderEnd) : "";
  assert("InputRow accepts the S80 control props",
    /function InputRow\(\{\s*value,\s*onChange,\s*onSend,\s*voice,\s*onMicToggle,\s*isStreaming,\s*onStop,\s*focusToken,\s*mobile\s*=\s*false\s*\}\)/.test(inputBlock),
    inputBlock.slice(0, 300));
  assert("InputRow focus guard does not steal initial FirstRun focus",
    /useEffect\(\(\) => \{ if \(focusToken\) inputRef\.current\?\.focus\(\); \}, \[focusToken\]\)/.test(inputBlock),
    inputBlock.slice(0, 600));
  assert("slash menu filters only command prefixes before first argument",
    /SLASH_CMDS\.filter\(\(c\) => c\.cmd\.startsWith\(firstTok\)\)/.test(inputBlock) &&
    /showMenu = isSlash && matches\.length > 0 && !value\.includes\(" "\)/.test(inputBlock),
    inputBlock.slice(0, 900));
  assert("slash menu keyboard navigation handles up down tab and Enter completion",
    /e\.key === "ArrowDown"[\s\S]*setSel\(\(s\) => \(s \+ 1\) % matches\.length\)/.test(inputBlock) &&
    /e\.key === "ArrowUp"[\s\S]*setSel\(\(s\) => \(s - 1 \+ matches\.length\) % matches\.length\)/.test(inputBlock) &&
    /e\.key === "Tab"[\s\S]*complete\(matches\[sel\]\.cmd\)/.test(inputBlock) &&
    /e\.key === "Enter" && matches\[sel\]\.cmd !== firstTok[\s\S]*complete\(matches\[sel\]\.cmd\)/.test(inputBlock),
    inputBlock.slice(0, 1600));
  assert("plain Enter sends only non-empty input and Shift+Enter is preserved",
    /e\.key === "Enter" && !e\.shiftKey[\s\S]*if \(value\.trim\(\)\.length > 0\) onSend\(\)/.test(inputBlock),
    inputBlock.slice(0, 1900));
  assert("slash suggestions support mouse completion without blurring input",
    /onMouseEnter=\{\(\) => setSel\(i\)\}/.test(inputBlock) &&
    /onMouseDown=\{\(e\) => \{ e\.preventDefault\(\); complete\(m\.cmd\); \}\}/.test(inputBlock),
    inputBlock.slice(0, 2500));
  assert("streaming state swaps send for stop and calls onStop",
    /isStreaming \? \([\s\S]*aria-label="Stop generation"[\s\S]*onClick=\{onStop\}/.test(inputBlock),
    inputBlock.slice(0, 3300));
  assert("non-empty idle input renders send/run button wired to onSend",
    /value\.trim\(\)\.length > 0 && \([\s\S]*aria-label="Send"[\s\S]*onClick=\{onSend\}[\s\S]*\{isSlash \? "run" : "send"\}/.test(inputBlock),
    inputBlock.slice(0, 4000));
  assert("MicButton receives InputRow mic toggle and button invokes onClick",
    /<MicButton state=\{voice\.state\} onClick=\{onMicToggle\} mobile=\{mobile\} \/>/.test(inputBlock) &&
    /function MicButton\(\{ state, onClick, mobile = false \}\)[\s\S]*onClick=\{onClick\}/.test(source),
    inputBlock.slice(-700));
  assert("HomeApp renders InputRow with stopStreaming and visible streaming state",
    /isStreaming=\{events\.some\(\(e\) => e\.streaming\)\}/.test(renderBlock) &&
    /onStop=\{stopStreaming\}/.test(renderBlock) &&
    /focusToken=\{focusToken\}/.test(renderBlock),
    renderBlock.slice(0, 900));
  const sendToHaStart = source.indexOf("const sendToHA = useCallback");
  const sendToHaEnd = source.indexOf("const stopStreaming = useCallback", sendToHaStart);
  const sendToHaBlock = sendToHaStart >= 0 && sendToHaEnd >= 0 ? source.slice(sendToHaStart, sendToHaEnd) : "";
  assert("sendToHA settles streaming bubble on intent-end and run completion",
    /const finalizeStreamingBubble = \(\) =>/.test(sendToHaBlock) &&
    /finalizeStreamingBubble\(\);/.test(sendToHaBlock) &&
    /const pendingStreamingId = streamingBubbleId/.test(sendToHaBlock) &&
    /streamingIds\.current\.delete\(pendingStreamingId\)/.test(sendToHaBlock) &&
    /e\.id === pendingStreamingId[\s\S]*streaming: false/.test(sendToHaBlock),
    sendToHaBlock.slice(-1600));
  assert("stopStreaming cancels active run, timers, and streaming ids",
    /activeRunRef\.current\.cancel\?\.\(\)/.test(stopBlock) &&
    /activeRunRef\.current = null/.test(stopBlock) &&
    /timers\.current\.forEach\(clearTimeout\)/.test(stopBlock) &&
    /streamingIds\.current\.forEach/.test(stopBlock) &&
    /streamingIds\.current\.clear\(\)/.test(stopBlock),
    stopBlock);

  process.stdout.write("\nslash_command_regression_test\n");
  const labStart = source.indexOf('case "lab-dump-watch"');
  const labEnd = source.indexOf('case "debug"', labStart);
  const labBlock = labStart >= 0 && labEnd >= 0 ? source.slice(labStart, labEnd) : "";
  assert("lab-dump-watch block found", labBlock.length > 0);
  assert("lab-dump-watch parses from arg, not an undefined parts array", /const\s+sub\s*=\s*\(arg\s*\|\|\s*""\)\.trim\(\)\.toLowerCase\(\)/.test(labBlock), labBlock.slice(0, 500));
  assert("lab-dump-watch no longer references parts[]", !/parts\s*\[/.test(labBlock), labBlock.slice(0, 500));
  assert("s2s handler explicitly warns for retired on/off mode", /sub === "on"[\s\S]*sub === "off"[\s\S]*retired/.test(handleBlock), "missing retired /s2s on|off guard");

  process.stdout.write("\nslash_command_surface_contract_test\n");
  const helpBlock = extractCaseBlock("help");
  assert("/help emits structured help event", /kind:\s*"help"[\s\S]*groups[\s\S]*totalCount:\s*SLASH_CMDS\.length/.test(helpBlock), helpBlock.slice(0, 600));
  assert("/help includes Simulation Mode help when sim is active", /sim\.active[\s\S]*window\.SimulationCommand[\s\S]*formatSimHelp/.test(helpBlock), helpBlock.slice(0, 600));
  assert("HelpContent renders clickable command rows", /function\s+HelpContent/.test(eventsSource) && /function\s+HelpRow/.test(eventsSource), "HelpContent/HelpRow missing");
  assert("HelpContent dispatches hg-fill-input events", /CustomEvent\("hg-fill-input"[\s\S]*detail:\s*cmd/.test(eventsSource), "missing hg-fill-input dispatch");
  assert("HomeApp listens for hg-fill-input and focuses input", /setInput\(cmd\)[\s\S]*setFocusToken[\s\S]*addEventListener\("hg-fill-input"/.test(source), "missing fill-input listener");

  const worldBlock = extractCaseBlock("world-state");
  assert("/world-state bare opens drawer without fetch", /setWorldStateInitialRoom\(null\)[\s\S]*setWorldStateDrawerOpen\(true\)[\s\S]*return true/.test(worldBlock), worldBlock.slice(0, 900));
  assert("/world-state room opens drawer pre-filtered", /setWorldStateInitialRoom\(argSansFlags\)[\s\S]*setWorldStateDrawerOpen\(true\)[\s\S]*return true/.test(worldBlock), worldBlock.slice(0, 1000));
  assert("/world-state --raw uses encoded room query", /encodeURIComponent\(room\)/.test(worldBlock) && /tauriFetch\(url/.test(worldBlock), worldBlock.slice(0, 1200));

  const camerasBlock = extractCaseBlock("cameras");
  assert("/cameras reads configured HG_CAMERAS", /window\.HG_CAMERAS\s*\|\|\s*\[\]/.test(camerasBlock), camerasBlock.slice(0, 600));
  assert("/cameras is inert without vision URL unless sim active", /if\s*\(!visionUrl\s*&&\s*!isSim\)/.test(camerasBlock), camerasBlock.slice(0, 900));
  assert("/cameras uses sim camera fixture without network URLs", /__SIM_CAMERAS_FIXTURE/.test(camerasBlock) && /snapshotUrl\s*=\s*simFixture\[cam\.id\]/.test(camerasBlock), camerasBlock.slice(0, 1200));
  assert("/cameras emits perception events with snapshot URLs", /kind:\s*"perception"[\s\S]*snapshotUrl/.test(camerasBlock), camerasBlock.slice(0, 1200));

  const describeBlock = extractCaseBlock("describe-clip");
  assert("/describe-clip uses tolerant parser helper", /parseDescribeClipArg/.test(describeBlock), describeBlock.slice(0, 900));
  assert("/describe-clip reports usage on missing camera", /missing camera[\s\S]*usage: \/describe-clip/.test(describeBlock), describeBlock.slice(0, 1000));
  assert("/describe-clip is inert without vision URL unless sim active", /if\s*\(!visionUrl\s*&&\s*!isSim\)/.test(describeBlock), describeBlock.slice(0, 1200));
  assert("/describe-clip uses sim fixture instead of live fetch", /__SIM_DESCRIBE_CLIP_FIXTURE/.test(describeBlock) && /Sim path: skip the network/.test(describeBlock), describeBlock.slice(0, 1400));
  assert("/describe-clip posts to vision sidecar only in live path", /tauriFetch\(`\$\{visionUrl\}\/describe_clip`[\s\S]*method:\s*"POST"/.test(describeBlock), describeBlock.slice(0, 1800));

  const backendTools = extractBackendToolNames().sort();
  const frontendTools = extractAgentToolNames().sort();
  const missingTools = backendTools.filter((name) => !frontendTools.includes(name));
  const extraTools = frontendTools.filter((name) => !backendTools.includes(name));
  assert("/agent-tools mirrors backend tool registry names", missingTools.length === 0 && extraTools.length === 0, { missingTools, extraTools, backendTools, frontendTools });
  assert("/agent-tools includes Living Lights override tools", ["set_presence_override", "clear_presence_override", "get_recent_overrides"].every((name) => frontendTools.includes(name)), frontendTools);
  assert("/agent-tools includes gesture training capture", frontendTools.includes("start_gesture_training_capture"), frontendTools);

  if (fails) {
    console.log("\nFailures:");
    for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
  }
  console.log(`\n${passes} pass . ${fails} fail`);
  process.exit(fails ? 1 : 0);
})()
