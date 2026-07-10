#!/usr/bin/env node
/* Browser-rendered chat transcript regression.
 *
 * Default mode is deterministic/static and requires no browser dependency.
 * Live mode is opt-in:
 *
 *   HOME_APP_URL=https://home-app.taild52a15.ts.net \
 *   HOME_LLM_UI_PROMPT="Whats in my driveway" \
 *   node tools/run-chat-ui-regression.js
 *
 * Live mode uses Playwright when installed. It sends one prompt through the
 * rendered app and checks the DOM for the regressions that are hard to catch
 * from backend traces alone: duplicated user text, repeated adjacent response
 * fragments, connection spam, and a stuck Stop button.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const REPO = path.resolve(__dirname, "..");
const APP_SOURCE = path.join(REPO, "app", "src", "home-app.jsx");
const REPORT_DIR = process.env.QA_REPORT_DIR || "";
const HOME_APP_URL = process.env.HOME_APP_URL || "";
const HOME_LLM_UI_PROMPT = process.env.HOME_LLM_UI_PROMPT || "";
const HOME_UI_HA_TOKEN = process.env.HOME_UI_HA_TOKEN || "";
const HOME_UI_HA_URL = process.env.HOME_UI_HA_URL || "/proxy/ha";
const HOME_UI_RESET_EVENTS = process.env.HOME_UI_RESET_EVENTS !== "0";
const HOME_UI_REQUIRE_BROWSER = process.env.HOME_UI_REQUIRE_BROWSER === "1";

function check(name, ok, detail = "") {
  return { name, status: ok ? "PASS" : "FAIL", detail };
}

function countOccurrences(haystack, needle) {
  if (!needle) return 0;
  let count = 0;
  let idx = 0;
  while ((idx = haystack.indexOf(needle, idx)) !== -1) {
    count++;
    idx += needle.length;
  }
  return count;
}

function canonicalText(text) {
  return String(text || "").toLowerCase().replace(/\s+/g, " ").trim();
}

function normalizedLines(text) {
  return String(text || "")
    .split(/\n+/)
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter((line) => line.length >= 18);
}

function adjacentDuplicateLines(text) {
  const lines = normalizedLines(text);
  const dupes = [];
  for (let i = 1; i < lines.length; i += 1) {
    if (lines[i] === lines[i - 1]) dupes.push(lines[i]);
  }
  return dupes;
}

function transcriptBlocks(text) {
  const eventKinds = new Set(["you", "home", "system", "perception"]);
  const lines = String(text || "")
    .split(/\n+/)
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter(Boolean);
  const blocks = [];
  for (let i = 0; i < lines.length; i += 1) {
    const kind = lines[i].toLowerCase();
    if (!eventKinds.has(kind)) continue;
    if (!/^\d{2}:\d{2}:\d{2}$/.test(lines[i + 1] || "")) continue;
    const body = [];
    let j = i + 2;
    while (j < lines.length) {
      const nextKind = lines[j].toLowerCase();
      if (eventKinds.has(nextKind) && /^\d{2}:\d{2}:\d{2}$/.test(lines[j + 1] || "")) break;
      if (nextKind === "idle") break;
      body.push(lines[j]);
      j += 1;
    }
    blocks.push({ kind, time: lines[i + 1], text: body.join("\n") });
    i = j - 1;
  }
  return blocks;
}

function duplicateFragments(text) {
  const lines = normalizedLines(text);
  const dupes = adjacentDuplicateLines(text);
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    for (let j = 0; j < lines.length; j += 1) {
      if (i === j) continue;
      const other = lines[j];
      if (other.length < line.length + 6) continue;
      if (other.includes(line)) dupes.push(line);
    }
  }
  return Array.from(new Set(dupes));
}

function perceptionAnswerText(text) {
  const lines = normalizedLines(text);
  if (lines.length >= 2 && /^[a-z0-9_ -]{2,32}$/i.test(lines[0])) {
    return lines.slice(1).join(" ");
  }
  return String(text || "").replace(/^[a-z0-9_ -]{2,32}:\s*/i, "").trim();
}

function redundantPerceptionCaptions(blocks) {
  const dupes = [];
  for (let i = 0; i < blocks.length; i += 1) {
    const block = blocks[i];
    if (block.kind !== "perception") continue;
    const answer = canonicalText(perceptionAnswerText(block.text));
    if (!answer) continue;
    for (let j = Math.max(0, i - 6); j < i; j += 1) {
      if (blocks[j].kind !== "home") continue;
      const home = canonicalText(blocks[j].text);
      if (home && (home.includes(answer) || answer.includes(home))) {
        dupes.push(perceptionAnswerText(block.text));
        break;
      }
    }
  }
  return dupes;
}

function firstExisting(paths) {
  for (const candidate of paths) {
    if (candidate && fs.existsSync(candidate)) return candidate;
  }
  return "";
}

function browserLaunchOptions() {
  const executablePath = firstExisting([
    process.env.HOME_PLAYWRIGHT_EXECUTABLE_PATH || "",
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ]);
  return executablePath
    ? { headless: true, executablePath }
    : { headless: true };
}

async function maybeLoadPlaywright() {
  try {
    return await import("playwright");
  } catch (_) {
    return null;
  }
}

async function runLiveBrowserChecks() {
  const checks = [];
  const playwright = await maybeLoadPlaywright();
  if (!playwright) {
    return [
      check("live browser regression skipped", !HOME_UI_REQUIRE_BROWSER, "Playwright is not installed"),
    ];
  }

  const browser = await playwright.chromium.launch(browserLaunchOptions());
  try {
    const page = await browser.newPage({
      viewport: { width: 390, height: 844, isMobile: true },
      userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
    });
    await page.addInitScript(({ prefs, resetEvents }) => {
      try {
        const existing = JSON.parse(localStorage.getItem("hg-prefs") || "{}") || {};
        localStorage.setItem("hg-prefs", JSON.stringify({ ...existing, ...prefs }));
        if (resetEvents) {
          localStorage.removeItem("hg-events");
          localStorage.removeItem("hg-conv-id");
        }
      } catch (_) {}
    }, {
      prefs: HOME_UI_HA_TOKEN ? { endpoint: HOME_UI_HA_URL, token: HOME_UI_HA_TOKEN } : {},
      resetEvents: HOME_UI_RESET_EVENTS,
    });
    await page.goto(HOME_APP_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
    const input = page.getByLabel("Command input");
    await input.waitFor({ timeout: 30000 });
    await input.fill(HOME_LLM_UI_PROMPT);
    const sendButton = page.getByLabel("Send");
    if (await sendButton.count().catch(() => 0)) {
      await sendButton.click({ timeout: 5000 });
    } else {
      await input.press("Enter");
    }

    const promptKey = canonicalText(HOME_LLM_UI_PROMPT);
    await page.waitForFunction((needle) => {
      const body = document.body?.innerText || "";
      return body.toLowerCase().replace(/\s+/g, " ").includes(needle);
    }, promptKey, { timeout: Number(process.env.HOME_LLM_UI_PROMPT_TIMEOUT_MS || 10000) })
      .catch(() => {});

    await page.waitForTimeout(Number(process.env.HOME_LLM_UI_WAIT_MS || 45000));
    const bodyText = await page.locator("body").innerText({ timeout: 5000 });
    const canonicalBody = canonicalText(bodyText);
    const promptOccurrences = countOccurrences(canonicalBody, promptKey);
    const blocks = transcriptBlocks(bodyText);
    const assistantDupes = blocks
      .filter((block) => block.kind === "home")
      .flatMap((block) => duplicateFragments(block.text));
    const perceptionDupes = redundantPerceptionCaptions(blocks);
    const connectionSpam = (bodyText.match(/system connecting to \/proxy\/ha/g) || []).length;
    const stopVisible = await page.getByText(/stop/i).count().catch(() => 0);

    checks.push(check("prompt rendered once", promptOccurrences === 1, `${promptOccurrences} occurrences`));
    checks.push(check("assistant bubble has no repeated fragments", assistantDupes.length === 0, assistantDupes.slice(0, 3).join(" | ")));
    checks.push(check("perception cards do not repeat assistant answer", perceptionDupes.length === 0, perceptionDupes.slice(0, 3).join(" | ")));
    checks.push(check("connection status is not spammed", connectionSpam <= 1, `${connectionSpam} connection lines`));
    checks.push(check("stop control not lingering after response window", stopVisible === 0, `${stopVisible} stop labels`));
    if (REPORT_DIR) {
      fs.writeFileSync(path.join(REPORT_DIR, "chat-ui-body.txt"), bodyText, "utf8");
    }
    return checks;
  } finally {
    await browser.close();
  }
}

function runStaticChecks() {
  const source = fs.readFileSync(APP_SOURCE, "utf8");
  return [
    check("canonical chat normalization helper exists", source.includes("function normalizeChatEventText"), "home-app.jsx"),
    check("streaming merge helper exists", source.includes("function mergeStreamingText"), "home-app.jsx"),
    check("recent duplicate guard exists", source.includes("function isRecentDuplicateEvent"), "home-app.jsx"),
    check("assistant-like replacement guard exists", source.includes("findRecentAssistantLikeIdx"), "home-app.jsx"),
    check("final answers settle active assistant streaming bubble", source.includes("function findActiveAssistantStreamingIdx") && source.includes("streamingAsstIdx"), "home-app.jsx"),
    check("input remains single command source", source.includes('aria-label="Command input"'), "home-app.jsx"),
    check("stop streaming control is state-backed", source.includes("stopStreaming") && source.includes("streamingIds.current") && source.includes("activeRunRef"), "home-app.jsx"),
  ];
}

async function main() {
  let checks = runStaticChecks();
  if (HOME_APP_URL && HOME_LLM_UI_PROMPT) {
    checks = checks.concat(await runLiveBrowserChecks());
  } else {
    checks.push(check(
      "live browser regression skipped",
      true,
      "set HOME_APP_URL and HOME_LLM_UI_PROMPT to drive the rendered app",
    ));
  }

  const failed = checks.filter((item) => item.status !== "PASS");
  const report = {
    app_url: HOME_APP_URL || null,
    prompt: HOME_LLM_UI_PROMPT || null,
    checks,
    summary: `${checks.length - failed.length} PASS, ${failed.length} FAIL`,
  };
  if (REPORT_DIR) {
    fs.writeFileSync(path.join(REPORT_DIR, "chat-ui-regression.json"), JSON.stringify(report, null, 2));
  }
  for (const item of checks) {
    console.log(`${item.status.padEnd(4)} ${item.name}${item.detail ? ` - ${item.detail}` : ""}`);
  }
  console.log("");
  console.log(`${checks.length - failed.length} pass . ${failed.length} fail`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((err) => {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
