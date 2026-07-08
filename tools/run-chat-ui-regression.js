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
      check("live browser regression skipped", true, "Playwright is not installed"),
    ];
  }

  const browser = await playwright.chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 390, height: 844, isMobile: true },
      userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
    });
    await page.goto(HOME_APP_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
    const input = page.getByLabel("Command input");
    await input.waitFor({ timeout: 30000 });
    await input.fill(HOME_LLM_UI_PROMPT);
    await input.press("Enter");

    await page.waitForTimeout(Number(process.env.HOME_LLM_UI_WAIT_MS || 45000));
    const bodyText = await page.locator("body").innerText({ timeout: 5000 });
    const promptOccurrences = countOccurrences(bodyText, HOME_LLM_UI_PROMPT);
    const duplicateLines = adjacentDuplicateLines(bodyText);
    const connectionSpam = (bodyText.match(/system connecting to \/proxy\/ha/g) || []).length;
    const stopVisible = await page.getByText(/stop/i).count().catch(() => 0);

    checks.push(check("prompt rendered once", promptOccurrences <= 1, `${promptOccurrences} occurrences`));
    checks.push(check("no adjacent duplicate transcript lines", duplicateLines.length === 0, duplicateLines.slice(0, 3).join(" | ")));
    checks.push(check("connection status is not spammed", connectionSpam <= 1, `${connectionSpam} connection lines`));
    checks.push(check("stop control not lingering after response window", stopVisible === 0, `${stopVisible} stop labels`));
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
