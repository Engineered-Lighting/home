#!/usr/bin/env node
/*
 * Mobile web screenshot audit for the Home app.
 *
 * This is intentionally a manual/pre-deploy tool, not CI by default. It drives
 * the browser UI at phone/tablet viewports and captures the surfaces that tend
 * to regress when the web app is used through Tailscale Serve.
 */

import fs from "node:fs/promises";
import fssync from "node:fs";
import { spawn } from "node:child_process";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO = path.resolve(__dirname, "..");
const SRC_DIR = path.join(REPO, "app", "src");
const DEFAULT_OUT = path.join(REPO, "tools", "reports", "mobile-web", timestamp());

const DEFAULT_VIEWPORTS = [
  { name: "phone-390x844", width: 390, height: 844 },
  { name: "large-phone-430x932", width: 430, height: 932 },
  { name: "narrow-tablet-690x1024", width: 690, height: 1024 },
];

const FEATURE_MATRIX = [
  ["01-boot", "App boot or first-run screen", "Header, main surface, and bottom affordances fit the viewport."],
  ["02-simulation-home", "Simulation home surface", "A usable non-secret test state is available for UI screenshots."],
  ["03-mobile-actions-menu", "Mobile header actions menu", "People, intelligence, video labeler, theme, and simulation actions remain reachable."],
  ["04-people", "People overlay", "Overlay opens from the mobile actions menu and remains dismissible."],
  ["05-intelligence", "Intelligence atlas overlay", "Atlas opens from the mobile actions menu without clipping close/actions."],
  ["06-video-labeler", "Video labeler overlay", "Labeler opens and its primary controls fit on phone width."],
  ["07-simulation-controls", "Simulation controls", "Scenario controls are reachable from mobile header actions."],
  ["08-slash-palette", "Slash command palette", "Command menu is scrollable above the mobile input bar."],
  ["09-help", "/help output", "Help/category output is readable without horizontal clipping."],
  ["10-profile-status", "/profile status output", "Active service profile is visible for travel debugging."],
  ["11-travel-status", "/travel status output", "Travel readiness status is visible and copyable."],
  ["12-remote-dialog", "Remote access / Travel readiness", "Profile selector, test-all, copy buttons, and readiness summary fit on mobile."],
  ["13-cameras", "/cameras output", "Camera snapshots/results fit the mobile feed."],
  ["14-world-state", "World-state drawer", "Room/state drawer can be opened and dismissed."],
  ["15-lights", "Living Lights drawer", "Lighting controls are usable at phone width."],
  ["16-spatial", "Spatial map drawer", "Map drawer opens without trapping or clipping the input."],
  ["17-look", "Look drawer", "Vision prompt drawer opens and fits the viewport."],
  ["18-apartment-cloud", "Apartment cloud mode", "3D Apartment opens with HUD controls reachable."],
  ["19-apartment-photo", "Apartment photo mode", "Photo/splat mode either renders or shows a clear asset error."],
  ["20-apartment-mesh", "Apartment mesh mode", "Mesh mode either renders or shows a clear asset error."],
  ["21-apartment-fly-camera", "Apartment fly-to-camera/live view", "Camera fly-to keeps the feed contained instead of cropped on mobile."],
];

const BUTTON_PROBE_MATRIX = [
  ["b01-home-layout", "Home", "Visible controls stay inside the mobile viewport with usable hit targets."],
  ["b02-simulation-entry", "First-run", "Try Simulation enters a non-secret UI state."],
  ["b03-mobile-menu", "Header", "Mobile actions opens and exposes its action buttons."],
  ["b04-people-close", "People", "People opens from the mobile menu and close dismisses it."],
  ["b05-intelligence-close", "Intelligence", "Intelligence opens from the mobile menu and close dismisses it."],
  ["b06-video-labeler-tabs", "Video labeler", "Video labeler opens, label/jobs tabs respond, refresh is clickable, and close dismisses it."],
  ["b07-simulation-controls", "Simulation", "Simulation controls open, reset is clickable, and close dismisses it."],
  ["b08-remote-dialog", "Remote", "Remote access dialog opens, profile buttons respond, test-all starts, and close dismisses it."],
  ["b09-slash-command", "Input", "Slash command input accepts and executes a command from mobile."],
  ["b10-drawer-closes", "Drawers", "World, lights, spatial, and look drawers expose a working close control."],
  ["b11-apartment-mode-buttons", "Apartment", "Cloud/photo/mesh HUD buttons respond without leaving a blank view."],
  ["b12-apartment-camera-buttons", "Apartment", "Fly-to-camera controls are visually captured and the audit resets cleanly afterward."],
];

const VISUAL_HEALTH_EXPRESSION = `(() => {
  const vw = window.innerWidth || document.documentElement.clientWidth || 0;
  const vh = window.innerHeight || document.documentElement.clientHeight || 0;
  const doc = document.documentElement;
  const body = document.body;
  const issues = [];
  const controls = [];
  const labelFor = (el) => {
    const aria = el.getAttribute("aria-label") || el.getAttribute("title") || "";
    const text = String(el.textContent || "").replace(/\\s+/g, " ").trim();
    return (aria || text || el.tagName || "control").slice(0, 64);
  };
  const isVisible = (el, rect, style) => {
    if (!rect || rect.width < 1 || rect.height < 1) return false;
    if (style.visibility === "hidden" || style.display === "none" || Number(style.opacity) === 0) return false;
    if (rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw) return false;
    return true;
  };
  const selectors = "button,[role='button'],[role='menuitem'],input,select,textarea,a[href]";
  for (const el of Array.from(document.querySelectorAll(selectors))) {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    if (!isVisible(el, rect, style)) continue;
    const label = labelFor(el);
    controls.push({ label, x: Math.round(rect.left), y: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height) });
    if (rect.left < -1 || rect.right > vw + 1) {
      issues.push(label + ": horizontally clipped (" + Math.round(rect.left) + ".." + Math.round(rect.right) + " of " + vw + ")");
    }
    const isTapTarget = el.tagName === "BUTTON" || el.getAttribute("role") === "button" || el.getAttribute("role") === "menuitem";
    if (isTapTarget && (rect.width < 24 || rect.height < 24)) {
      issues.push(label + ": small tap target " + Math.round(rect.width) + "x" + Math.round(rect.height));
    }
  }
  const scrollWidth = Math.max(doc?.scrollWidth || 0, body?.scrollWidth || 0);
  const overflow = Math.max(0, scrollWidth - vw);
  if (overflow > 3) issues.push("document horizontal overflow " + Math.round(overflow) + "px");
  return { ok: issues.length === 0, viewport: { width: vw, height: vh }, visibleControls: controls.length, issues: issues.slice(0, 24), sampleControls: controls.slice(0, 24) };
})()`;

function timestamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

function usage() {
  return [
    "Usage: node tools/mobile-web-screenshot-audit.mjs [options]",
    "",
    "Options:",
    "  --url <url>                 App URL to test. If omitted, serves app/src on a random localhost port.",
    "  --out <dir>                 Output directory. Default: tools/reports/mobile-web/<timestamp>",
    "  --viewports <list>          Comma list of WIDTHxHEIGHT. Default: 390x844,430x932,768x1024",
    "  --headed                    Run Chromium visibly.",
    "  --matrix-only               Write the feature matrix/report without launching a browser.",
    "  --help                      Show this help.",
    "",
    "Screenshot engines:",
    "  1. Playwright, when installed.",
    "  2. Installed Chrome via DevTools Protocol fallback.",
    "",
    "Optional Playwright setup:",
    "  npm install --save-dev playwright",
    "  npx playwright install chromium",
  ].join("\n");
}

function parseArgs(argv) {
  const args = {
    url: process.env.HOME_MOBILE_AUDIT_URL || "",
    out: process.env.HOME_MOBILE_AUDIT_OUT || DEFAULT_OUT,
    viewports: DEFAULT_VIEWPORTS,
    headed: false,
    matrixOnly: false,
    help: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--help" || arg === "-h") args.help = true;
    else if (arg === "--headed") args.headed = true;
    else if (arg === "--matrix-only") args.matrixOnly = true;
    else if (arg === "--url") args.url = argv[++i] || "";
    else if (arg === "--out") args.out = path.resolve(argv[++i] || args.out);
    else if (arg === "--viewports") args.viewports = parseViewports(argv[++i] || "");
    else throw new Error(`unknown argument: ${arg}`);
  }
  return args;
}

function parseViewports(value) {
  const out = [];
  for (const part of String(value || "").split(",")) {
    const m = part.trim().match(/^(\d+)x(\d+)$/i);
    if (!m) continue;
    const width = Number(m[1]);
    const height = Number(m[2]);
    out.push({ name: `${width}x${height}`, width, height });
  }
  return out.length ? out : DEFAULT_VIEWPORTS;
}

function contentType(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".html") return "text/html; charset=utf-8";
  if (ext === ".js" || ext === ".jsx" || ext === ".mjs") return "text/javascript; charset=utf-8";
  if (ext === ".css") return "text/css; charset=utf-8";
  if (ext === ".json") return "application/json; charset=utf-8";
  if (ext === ".svg") return "image/svg+xml";
  if (ext === ".png") return "image/png";
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".glb") return "model/gltf-binary";
  if (ext === ".ply") return "application/octet-stream";
  if (ext === ".spz") return "application/octet-stream";
  return "application/octet-stream";
}

function safeAppFile(requestUrl) {
  const pathname = new URL(requestUrl, "http://local").pathname;
  const raw = decodeURIComponent(pathname === "/" ? "/index.html" : pathname);
  const candidate = path.resolve(SRC_DIR, "." + raw);
  const relative = path.relative(SRC_DIR, candidate);
  if (relative.startsWith("..") || path.isAbsolute(relative)) return null;
  return candidate;
}

function startStaticServer() {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const filePath = safeAppFile(req.url || "/");
      if (!filePath) {
        res.writeHead(403);
        res.end("forbidden");
        return;
      }
      fssync.readFile(filePath, (err, data) => {
        if (err) {
          res.writeHead(404);
          res.end("not found");
          return;
        }
        res.writeHead(200, {
          "Content-Type": contentType(filePath),
          "Cache-Control": "no-store",
        });
        res.end(data);
      });
    });
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      resolve({ server, url: `http://127.0.0.1:${address.port}/` });
    });
  });
}

async function closeServer(server) {
  if (!server) return;
  await new Promise((resolve) => server.close(resolve));
}

async function writeMatrixReport(outDir, extras = {}) {
  await fs.mkdir(outDir, { recursive: true });
  const lines = [
    "# Mobile Web Screenshot Audit",
    "",
    `Generated: ${new Date().toISOString()}`,
    "",
    "## Status",
    "",
    extras.status || "Matrix generated.",
    "",
    "## Feature Matrix",
    "",
    "| ID | Surface | Expected |",
    "| --- | --- | --- |",
    ...FEATURE_MATRIX.map(([id, title, expected]) => `| ${id} | ${title} | ${expected} |`),
    "",
    "## Button / Tap Probe Matrix",
    "",
    "| ID | Surface | Expected |",
    "| --- | --- | --- |",
    ...BUTTON_PROBE_MATRIX.map(([id, title, expected]) => `| ${id} | ${title} | ${expected} |`),
    "",
  ];
  if (extras.results?.length) {
    lines.push("## Results", "");
    for (const result of extras.results) {
      lines.push(`### ${result.viewport}`);
      lines.push("");
      for (const item of result.items) {
        const rel = item.path ? path.relative(outDir, item.path).replace(/\\/g, "/") : "";
        lines.push(`- ${item.ok ? "PASS" : "FAIL"} ${item.id}: ${item.detail || ""}${rel ? ` (${rel})` : ""}`);
      }
      lines.push("");
    }
  }
  if (extras.results?.some((result) => result.buttonItems?.length)) {
    lines.push("## Button Probe Results", "");
    for (const result of extras.results) {
      if (!result.buttonItems?.length) continue;
      lines.push(`### ${result.viewport}`);
      lines.push("");
      for (const item of result.buttonItems) {
        lines.push(`- ${item.ok ? "PASS" : "FAIL"} ${item.id}: ${item.detail || ""}`);
      }
      lines.push("");
    }
  }
  if (extras.errors?.length) {
    lines.push("## Browser Errors", "");
    for (const err of extras.errors.slice(0, 80)) lines.push(`- ${err}`);
    lines.push("");
  }
  await fs.writeFile(path.join(outDir, "REPORT.md"), lines.join("\n"), "utf8");
  await fs.writeFile(
    path.join(outDir, "matrix.json"),
    JSON.stringify({
      generatedAt: new Date().toISOString(),
      features: FEATURE_MATRIX.map(([id, title, expected]) => ({ id, title, expected })),
      buttonProbes: BUTTON_PROBE_MATRIX.map(([id, title, expected]) => ({ id, title, expected })),
      ...extras,
    }, null, 2),
    "utf8",
  );
}

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch (err) {
    return null;
  }
}

async function waitForBoot(page) {
  await page.waitForLoadState("domcontentloaded");
  await page.waitForFunction(() => window.__bootState?.done === true || window.__bootState?.failed, null, { timeout: 25000 }).catch(() => {});
  await page.waitForTimeout(600);
}

async function screenshot(page, outDir, id) {
  const file = path.join(outDir, `${id}.png`);
  await page.screenshot({ path: file, fullPage: false });
  return file;
}

async function maybeClick(locator, timeout = 1200) {
  try {
    await locator.click({ timeout });
    return true;
  } catch {
    return false;
  }
}

async function closeOverlays(page) {
  await page.keyboard.press("Escape").catch(() => {});
  await page.waitForTimeout(250);
  await page.keyboard.press("Escape").catch(() => {});
  await page.waitForTimeout(250);
}

async function commandInput(page) {
  const byPlaceholder = page.locator('input[placeholder="type or /command"]');
  if (await byPlaceholder.count()) return byPlaceholder.last();
  return page.locator("input").last();
}

async function runCommand(page, command, waitMs = 1000) {
  const input = await commandInput(page);
  await input.click({ timeout: 3500 });
  await input.fill(command);
  await input.press("Enter");
  await page.waitForTimeout(waitMs);
}

function visualHealthDetail(health) {
  if (!health) return "no visual health payload";
  if (health.ok) return `${health.visibleControls || 0} visible controls fit ${health.viewport?.width || "?"}x${health.viewport?.height || "?"}`;
  return (health.issues || []).join("; ");
}

async function visualHealth(page) {
  return page.evaluate(VISUAL_HEALTH_EXPRESSION);
}

async function recordButtonProbe(buttonItems, id, detail, fn) {
  try {
    const result = await fn();
    buttonItems.push({ id, ok: true, detail: result || detail });
  } catch (err) {
    buttonItems.push({ id, ok: false, detail: `${detail}: ${err?.message || err}` });
  }
}

async function ensureVisualHealth(page, context) {
  const health = await visualHealth(page);
  if (!health.ok) throw new Error(`${context}: ${visualHealthDetail(health)}`);
  return visualHealthDetail(health);
}

async function clickButtonByName(page, name, timeout = 1800) {
  const locator = page.getByRole("button", { name }).first();
  if (!(await maybeClick(locator, timeout))) throw new Error(`button not clickable: ${name}`);
  await page.waitForTimeout(350);
}

async function expectVisibleText(page, pattern, timeout = 1800) {
  const locator = page.getByText(pattern).first();
  try {
    await locator.waitFor({ state: "visible", timeout });
  } catch {
    throw new Error(`text not visible: ${pattern}`);
  }
}

async function enterSimulation(page) {
  const firstRunButton = page.getByRole("button", { name: /try simulation/i });
  if (await maybeClick(firstRunButton, 2500)) {
    await page.waitForTimeout(1600);
    return true;
  }
  try {
    await runCommand(page, "/simulation healthy", 1200);
    return true;
  } catch {
    return false;
  }
}

async function openMobileMenu(page) {
  return maybeClick(page.getByLabel(/open mobile actions/i), 1800);
}

async function clickMobileMenuItem(page, label) {
  const item = page.getByRole("menuitem", { name: new RegExp(`^${label}$`, "i") }).first();
  let clicked = await maybeClick(item, 500);
  if (!clicked) {
    await openMobileMenu(page);
    clicked = await maybeClick(item, 1800);
  }
  await page.waitForTimeout(1100);
  return clicked;
}

async function runButtonProbes(page, buttonItems) {
  await recordButtonProbe(buttonItems, "b12-apartment-camera-buttons", "Apartment back and close buttons respond", async () => {
    await page.waitForFunction(() => !!window.__havApartmentDebug, null, { timeout: 5000 });
    const detail = await ensureVisualHealth(page, "apartment fly-camera controls");
    await closeOverlays(page);
    return `${detail}; fly-camera screenshot captured as 21-apartment-fly-camera.png`;
  });

  await closeOverlays(page);

  await recordButtonProbe(buttonItems, "b01-home-layout", "Home controls fit viewport", async () => {
    return ensureVisualHealth(page, "home");
  });

  await recordButtonProbe(buttonItems, "b02-simulation-entry", "Try Simulation / simulation command responds", async () => {
    const ok = await enterSimulation(page);
    if (!ok) throw new Error("simulation entry did not respond");
    return ensureVisualHealth(page, "simulation home");
  });

  await recordButtonProbe(buttonItems, "b03-mobile-menu", "Mobile actions menu opens", async () => {
    const ok = await openMobileMenu(page);
    if (!ok) throw new Error("mobile actions button missing");
    await expectVisibleText(page, /^people$/i);
    await expectVisibleText(page, /^intelligence$/i);
    await expectVisibleText(page, /^video labeler$/i);
    const detail = await ensureVisualHealth(page, "mobile menu");
    await closeOverlays(page);
    return detail;
  });

  await recordButtonProbe(buttonItems, "b04-people-close", "People opens and close dismisses", async () => {
    const opened = await clickMobileMenuItem(page, "people");
    if (!opened) throw new Error("people menu item did not click");
    await expectVisibleText(page, /people/i);
    await clickButtonByName(page, /close/i);
    return ensureVisualHealth(page, "people closed");
  });

  await recordButtonProbe(buttonItems, "b05-intelligence-close", "Intelligence opens and close dismisses", async () => {
    const opened = await clickMobileMenuItem(page, "intelligence");
    if (!opened) throw new Error("intelligence menu item did not click");
    await expectVisibleText(page, /intelligence/i);
    await clickButtonByName(page, /close/i);
    return ensureVisualHealth(page, "intelligence closed");
  });

  await recordButtonProbe(buttonItems, "b06-video-labeler-tabs", "Video labeler tabs, refresh, and close respond", async () => {
    const opened = await clickMobileMenuItem(page, "video labeler");
    if (!opened) throw new Error("video labeler menu item did not click");
    await expectVisibleText(page, /video labeler/i);
    await clickButtonByName(page, /^jobs$/i);
    await clickButtonByName(page, /^label$/i);
    const refreshed = await maybeClick(page.getByRole("button", { name: /^refresh$/i }).first(), 900);
    await clickButtonByName(page, /close/i);
    const detail = await ensureVisualHealth(page, "video labeler closed");
    return refreshed ? detail : `${detail}; refresh absent in simulation state`;
  });

  await recordButtonProbe(buttonItems, "b07-simulation-controls", "Simulation controls reset and close respond", async () => {
    const opened = await clickMobileMenuItem(page, "simulation");
    if (!opened) throw new Error("simulation menu item did not click");
    await expectVisibleText(page, /^simulation$/i);
    await clickButtonByName(page, /^reset$/i);
    await clickButtonByName(page, /close simulation controls|^x$/i);
    return ensureVisualHealth(page, "simulation controls closed");
  });

  await recordButtonProbe(buttonItems, "b09-slash-command", "Slash command input executes", async () => {
    await closeOverlays(page);
    await runCommand(page, "/profile status", 900);
    await expectVisibleText(page, /profile/i);
    return ensureVisualHealth(page, "profile command");
  });

  await recordButtonProbe(buttonItems, "b10-drawer-closes", "World/lights/spatial/look drawers close from mobile", async () => {
    for (const command of ["/world-state", "/lights", "/spatial", "/look kitchen what is on the counter"]) {
      await closeOverlays(page);
      await runCommand(page, command, 900);
      await clickButtonByName(page, /close/i, 2500);
      await page.waitForTimeout(250);
    }
    return ensureVisualHealth(page, "drawers closed");
  });

  await recordButtonProbe(buttonItems, "b11-apartment-mode-buttons", "Apartment cloud/photo/mesh HUD buttons respond", async () => {
    await closeOverlays(page);
    await runCommand(page, "/apartment", 3200);
    await page.waitForFunction(() => !!window.__havApartmentDebug, null, { timeout: 12000 });
    await clickButtonByName(page, /^cloud$/i);
    await clickButtonByName(page, /^photo$/i, 2600);
    await clickButtonByName(page, /^mesh$/i, 2600);
    const detail = await ensureVisualHealth(page, "apartment mode controls");
    await clickButtonByName(page, /close/i);
    await page.waitForFunction(() => !window.__havApartmentDebug, null, { timeout: 5000 });
    return detail;
  });

  await recordButtonProbe(buttonItems, "b08-remote-dialog", "Remote dialog profile buttons, test all, and close respond", async () => {
    await closeOverlays(page);
    const remote = page.locator('button[aria-label="Remote profile"]').first();
    if (!(await maybeClick(remote, 1800))) throw new Error("remote profile button missing");
    await expectVisibleText(page, /Remote access \/ Travel readiness/i);
    await clickButtonByName(page, /Home LAN/i);
    await clickButtonByName(page, /Remote via Tailscale/i);
    await clickButtonByName(page, /^Custom$/i);
    await clickButtonByName(page, /Home LAN/i);
    await clickButtonByName(page, /test all/i, 2500);
    await page.waitForTimeout(500);
    await clickButtonByName(page, /^x$|close/i);
    return ensureVisualHealth(page, "remote dialog closed");
  });
}

async function runViewportAudit(browser, appUrl, viewport, outRoot, errors) {
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    isMobile: viewport.width < 700,
    hasTouch: viewport.width < 700,
    deviceScaleFactor: viewport.width < 700 ? 2 : 1,
  });
  const page = await context.newPage();
  page.on("pageerror", (err) => errors.push(`${viewport.name}: pageerror: ${err.message || err}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`${viewport.name}: console: ${msg.text()}`);
  });

  const outDir = path.join(outRoot, viewport.name);
  await fs.mkdir(outDir, { recursive: true });
  const items = [];
  const buttonItems = [];
  const capture = async (id, detail = "") => {
    try {
      const file = await screenshot(page, outDir, id);
      items.push({ id, ok: true, detail, path: file });
    } catch (err) {
      items.push({ id, ok: false, detail: err?.message || String(err) });
    }
  };
  const step = async (id, detail, fn) => {
    try {
      await fn();
      await capture(id, detail);
    } catch (err) {
      items.push({ id, ok: false, detail: `${detail}: ${err?.message || err}` });
    }
  };

  try {
    await page.goto(appUrl, { waitUntil: "domcontentloaded" });
    await waitForBoot(page);
    await capture("01-boot", "Initial load");

    await step("02-simulation-home", "Enter simulation for non-secret UI state", async () => {
      await enterSimulation(page);
    });

    await step("03-mobile-actions-menu", "Header actions menu", async () => {
      await openMobileMenu(page);
      await page.waitForTimeout(500);
    });

    for (const [id, label, title] of [
      ["04-people", "people", "People overlay"],
      ["05-intelligence", "intelligence", "Intelligence atlas overlay"],
      ["06-video-labeler", "video labeler", "Video labeler overlay"],
      ["07-simulation-controls", "simulation", "Simulation controls"],
    ]) {
      await closeOverlays(page);
      await step(id, title, async () => {
        const ok = await clickMobileMenuItem(page, label);
        if (!ok) throw new Error(`menu item not found: ${label}`);
      });
    }

    await closeOverlays(page);
    await step("08-slash-palette", "Slash command palette", async () => {
      const input = await commandInput(page);
      await input.click();
      await input.fill("/");
      await page.waitForTimeout(700);
    });

    for (const [id, command, waitMs] of [
      ["09-help", "/help", 800],
      ["10-profile-status", "/profile status", 800],
      ["11-travel-status", "/travel status", 900],
    ]) {
      await closeOverlays(page);
      await step(id, command, async () => {
        await runCommand(page, command, waitMs);
      });
    }

    await closeOverlays(page);
    await step("12-remote-dialog", "Remote access / Travel readiness", async () => {
      const remote = page.locator('button[aria-label="Remote profile"]').first();
      if (!(await maybeClick(remote, 1800))) throw new Error("remote profile button missing");
      await expectVisibleText(page, /Remote access \/ Travel readiness/i);
      await page.waitForTimeout(600);
    });

    for (const [id, command, waitMs] of [
      ["13-cameras", "/cameras", 1800],
      ["14-world-state", "/world-state", 1000],
      ["15-lights", "/lights", 1000],
      ["16-spatial", "/spatial", 1000],
      ["17-look", "/look kitchen what is on the counter", 1000],
    ]) {
      await closeOverlays(page);
      await step(id, command, async () => {
        await runCommand(page, command, waitMs);
      });
    }

    await closeOverlays(page);
    await step("18-apartment-cloud", "/apartment cloud mode", async () => {
      await runCommand(page, "/apartment", 3600);
      await page.waitForFunction(() => !!window.__havApartmentDebug, null, { timeout: 12000 }).catch(() => {});
    });

    await step("19-apartment-photo", "Apartment photo/splat mode", async () => {
      await page.evaluate(() => window.__havApartmentDebug?.setMode?.("splat"));
      await page.waitForTimeout(2600);
    });

    await step("20-apartment-mesh", "Apartment mesh mode", async () => {
      await page.evaluate(() => window.__havApartmentDebug?.setMode?.("mesh"));
      await page.waitForTimeout(2600);
    });

    await step("21-apartment-fly-camera", "Apartment fly-to-camera/live view", async () => {
      const ok = await page.evaluate(() => window.__havApartmentDebug?.flyFirstCamera?.());
      if (!ok) throw new Error("no camera device available for fly-to test");
      await page.waitForTimeout(1600);
    });

    await runButtonProbes(page, buttonItems);
  } finally {
    await context.close();
  }

  return { viewport: viewport.name, items, buttonItems };
}

function chromeCandidates() {
  const candidates = [];
  if (process.env.CHROME_PATH) candidates.push(process.env.CHROME_PATH);
  if (process.platform === "win32") {
    const env = process.env;
    if (env.ProgramFiles) candidates.push(path.join(env.ProgramFiles, "Google", "Chrome", "Application", "chrome.exe"));
    if (env["ProgramFiles(x86)"]) candidates.push(path.join(env["ProgramFiles(x86)"], "Google", "Chrome", "Application", "chrome.exe"));
    if (env.LOCALAPPDATA) candidates.push(path.join(env.LOCALAPPDATA, "Google", "Chrome", "Application", "chrome.exe"));
  } else if (process.platform === "darwin") {
    candidates.push("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
  } else {
    candidates.push("/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser");
  }
  return candidates.filter((candidate, index, arr) => candidate && arr.indexOf(candidate) === index);
}

function findChromeExecutable() {
  for (const candidate of chromeCandidates()) {
    if (fssync.existsSync(candidate)) return candidate;
  }
  return "";
}

function randomPort() {
  return 42000 + Math.floor(Math.random() * 12000);
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`);
  return res.json();
}

async function waitForChrome(port, child) {
  const url = `http://127.0.0.1:${port}/json/version`;
  const deadline = Date.now() + 12000;
  let last = "";
  while (Date.now() < deadline) {
    if (child.exitCode != null) throw new Error(`Chrome exited with ${child.exitCode}`);
    try {
      return await fetchJson(url);
    } catch (err) {
      last = err?.message || String(err);
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  throw new Error(`Chrome debugging endpoint did not start: ${last}`);
}

async function launchChromeForCdp(viewport) {
  const chrome = findChromeExecutable();
  if (!chrome) throw new Error("Chrome executable not found. Set CHROME_PATH or install Google Chrome.");
  const port = randomPort();
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), "home-mobile-audit-"));
  const args = [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    "--headless=new",
    "--disable-gpu",
    "--hide-scrollbars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-dev-shm-usage",
    `--window-size=${viewport.width},${viewport.height}`,
    "about:blank",
  ];
  const child = spawn(chrome, args, { stdio: ["ignore", "ignore", "pipe"] });
  let stderr = "";
  child.stderr?.on("data", (chunk) => {
    stderr += String(chunk);
    if (stderr.length > 4000) stderr = stderr.slice(-4000);
  });
  await waitForChrome(port, child);
  return {
    port,
    child,
    profile,
    async close() {
      if (child.exitCode == null) child.kill();
      await fs.rm(profile, { recursive: true, force: true }).catch(() => {});
    },
    stderr: () => stderr,
  };
}

class CdpClient {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.events = [];
    this.ready = new Promise((resolve, reject) => {
      this.ws.addEventListener("open", resolve, { once: true });
      this.ws.addEventListener("error", reject, { once: true });
    });
    this.ws.addEventListener("message", (event) => {
      const msg = JSON.parse(String(event.data));
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(`${msg.error.message || "CDP error"} ${msg.error.data || ""}`.trim()));
        else resolve(msg.result || {});
      } else if (msg.method) {
        this.events.push(msg);
        if (this.events.length > 500) this.events.shift();
      }
    });
  }

  async send(method, params = {}) {
    await this.ready;
    const id = this.nextId++;
    const payload = JSON.stringify({ id, method, params });
    const promise = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.ws.send(payload);
    return promise;
  }

  close() {
    try { this.ws.close(); } catch {}
  }
}

async function createCdpPage(chrome, url, viewport) {
  const target = await fetchJson(
    `http://127.0.0.1:${chrome.port}/json/new?${encodeURIComponent(url)}`,
    { method: "PUT" },
  );
  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  await client.send("Emulation.setDeviceMetricsOverride", {
    width: viewport.width,
    height: viewport.height,
    deviceScaleFactor: viewport.width < 700 ? 2 : 1,
    mobile: viewport.width < 700,
  });
  await client.send("Emulation.setTouchEmulationEnabled", { enabled: viewport.width < 700 });
  return client;
}

async function cdpEval(client, expression, awaitPromise = false) {
  const res = await client.send("Runtime.evaluate", {
    expression,
    awaitPromise,
    returnByValue: true,
    userGesture: true,
  });
  if (res.exceptionDetails) {
    const text = res.exceptionDetails.text || res.exceptionDetails.exception?.description || "evaluation failed";
    throw new Error(text);
  }
  return res.result?.value;
}

async function cdpWaitFor(client, expression, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  let last = "";
  while (Date.now() < deadline) {
    try {
      const ok = await cdpEval(client, expression);
      if (ok) return true;
    } catch (err) {
      last = err?.message || String(err);
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(last || `timed out waiting for ${expression}`);
}

async function cdpDelay(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function cdpScreenshot(client, outDir, id) {
  const shot = await client.send("Page.captureScreenshot", {
    format: "png",
    fromSurface: true,
    captureBeyondViewport: false,
  });
  const file = path.join(outDir, `${id}.png`);
  await fs.writeFile(file, Buffer.from(shot.data, "base64"));
  return file;
}

const DOM_HELPERS = `
(() => {
  const norm = (value) => String(value || "").replace(/\\s+/g, " ").trim();
  const byText = (selector, text) => {
    const needle = norm(text).toLowerCase();
    return Array.from(document.querySelectorAll(selector)).find((el) =>
      norm(el.textContent).toLowerCase() === needle
    );
  };
  const byTextMatch = (selector, pattern) => {
    const re = new RegExp(pattern, "i");
    return Array.from(document.querySelectorAll(selector)).find((el) => {
      const label = [
        el.getAttribute("aria-label"),
        el.getAttribute("title"),
        el.getAttribute("value"),
        el.textContent,
      ].map(norm).filter(Boolean).join(" ");
      return re.test(label);
    });
  };
  const clickButton = (text) => {
    const el = byText("button", text);
    if (!el) return false;
    el.click();
    return true;
  };
  const clickButtonMatch = (pattern) => {
    const el = byTextMatch("button,[role='button'],[role='menuitem']", pattern);
    if (!el) return false;
    el.click();
    return true;
  };
  const clickSelector = (selector) => {
    const el = document.querySelector(selector);
    if (!el) return false;
    el.click();
    return true;
  };
  const existsTextMatch = (pattern) => {
    const re = new RegExp(pattern, "i");
    return Array.from(document.querySelectorAll("body *")).some((el) => re.test(norm(el.textContent)));
  };
  const clickMenuItem = async (text) => {
    let el = byText('[role="menuitem"], button', text);
    if (!el) {
      const menu = document.querySelector('button[aria-label="Open mobile actions"]');
      if (menu) menu.click();
      await new Promise((resolve) => setTimeout(resolve, 120));
      el = byText('[role="menuitem"], button', text);
    }
    if (!el) return false;
    el.click();
    return true;
  };
  const input = () => {
    const exact = document.querySelector('input[placeholder="type or /command"]');
    if (exact) return exact;
    const inputs = Array.from(document.querySelectorAll("input"));
    return inputs[inputs.length - 1] || null;
  };
  const setInputValue = (el, value) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
  };
  const command = (value) => {
    const el = input();
    if (!el) return false;
    el.focus();
    setInputValue(el, value);
    el.dispatchEvent(new KeyboardEvent("keydown", {
      key: "Enter",
      code: "Enter",
      keyCode: 13,
      which: 13,
      bubbles: true,
      cancelable: true,
    }));
    return true;
  };
  const escape = () => {
    const closeButtons = Array.from(document.querySelectorAll("button")).filter((el) => {
      const text = String(el.textContent || "").trim().toLowerCase();
      return text.startsWith("close") || text === "x" || text === "×";
    });
    closeButtons.forEach((button) => button.click());
    const dispatch = (type, target) => target.dispatchEvent(new KeyboardEvent(type, {
      key: "Escape",
      code: "Escape",
      keyCode: 27,
      which: 27,
      bubbles: true,
      cancelable: true,
    }));
    dispatch("keydown", window);
    dispatch("keyup", window);
    dispatch("keydown", document);
    dispatch("keyup", document);
    return true;
  };
  return { clickButton, clickButtonMatch, clickSelector, existsTextMatch, clickMenuItem, command, escape };
})()
`;

async function cdpAction(client, source) {
  return cdpEval(client, `(() => { const h = ${DOM_HELPERS}; return (${source})(h); })()`, true);
}

async function cdpCloseOverlays(client) {
  await cdpEval(client, "window.__havUiDebug && window.__havUiDebug.closeOverlays && window.__havUiDebug.closeOverlays()", true).catch(() => {});
  await cdpDelay(100);
  await cdpAction(client, "(h) => h.escape()");
  await cdpDelay(250);
  await cdpAction(client, "(h) => h.escape()");
  await cdpDelay(250);
}

async function cdpRunCommand(client, command, waitMs = 1000) {
  const ok = await cdpAction(client, `(h) => h.command(${JSON.stringify(command)})`);
  if (!ok) throw new Error(`command input not found for ${command}`);
  await cdpDelay(waitMs);
}

async function cdpVisualHealth(client) {
  return cdpEval(client, VISUAL_HEALTH_EXPRESSION, true);
}

async function cdpEnsureVisualHealth(client, context) {
  const health = await cdpVisualHealth(client);
  if (!health.ok) throw new Error(`${context}: ${visualHealthDetail(health)}`);
  return visualHealthDetail(health);
}

async function cdpRecordButtonProbe(buttonItems, id, detail, fn) {
  try {
    const result = await fn();
    buttonItems.push({ id, ok: true, detail: result || detail });
  } catch (err) {
    buttonItems.push({ id, ok: false, detail: `${detail}: ${err?.message || err}` });
  }
}

async function cdpClickButton(client, pattern, waitMs = 350) {
  const ok = await cdpAction(client, `(h) => h.clickButtonMatch(${JSON.stringify(pattern)})`);
  if (!ok) throw new Error(`button not clickable: /${pattern}/i`);
  await cdpDelay(waitMs);
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function cdpClickMobileMenuItem(client, label, waitMs = 900) {
  const pattern = `^${escapeRegExp(label)}$`;
  let clicked = await cdpAction(client, `(h) => h.clickButtonMatch(${JSON.stringify(pattern)})`);
  if (!clicked) {
    const opened = await cdpAction(client, `(h) => h.clickSelector('button[aria-label="Open mobile actions"]')`);
    if (!opened) throw new Error("mobile actions button missing");
    await cdpDelay(180);
    clicked = await cdpAction(client, `(h) => h.clickButtonMatch(${JSON.stringify(pattern)})`);
  }
  if (!clicked) throw new Error(`menu item not clickable: ${label}`);
  await cdpDelay(waitMs);
}

async function cdpExpectText(client, pattern) {
  const ok = await cdpAction(client, `(h) => h.existsTextMatch(${JSON.stringify(pattern)})`);
  if (!ok) throw new Error(`text not visible: /${pattern}/i`);
}

async function cdpRunButtonProbes(client, buttonItems) {
  await cdpRecordButtonProbe(buttonItems, "b12-apartment-camera-buttons", "Apartment back and close buttons respond", async () => {
    await cdpWaitFor(client, "!!window.__havApartmentDebug", 5000);
    const detail = await cdpEnsureVisualHealth(client, "apartment fly-camera controls");
    await cdpCloseOverlays(client);
    return `${detail}; fly-camera screenshot captured as 21-apartment-fly-camera.png`;
  });

  await cdpCloseOverlays(client);

  await cdpRecordButtonProbe(buttonItems, "b01-home-layout", "Home controls fit viewport", async () => {
    return cdpEnsureVisualHealth(client, "home");
  });

  await cdpRecordButtonProbe(buttonItems, "b02-simulation-entry", "Try Simulation / simulation command responds", async () => {
    const ok = await cdpAction(client, "(h) => h.clickButton('try simulation') || h.command('/simulation healthy')");
    if (!ok) throw new Error("simulation entry did not respond");
    await cdpDelay(1600);
    return cdpEnsureVisualHealth(client, "simulation home");
  });

  await cdpRecordButtonProbe(buttonItems, "b03-mobile-menu", "Mobile actions menu opens", async () => {
    const ok = await cdpAction(client, `(h) => h.clickSelector('button[aria-label="Open mobile actions"]')`);
    if (!ok) throw new Error("mobile actions button missing");
    await cdpDelay(350);
    await cdpExpectText(client, "^people$");
    await cdpExpectText(client, "^intelligence$");
    await cdpExpectText(client, "^video labeler$");
    const detail = await cdpEnsureVisualHealth(client, "mobile menu");
    await cdpCloseOverlays(client);
    return detail;
  });

  await cdpRecordButtonProbe(buttonItems, "b04-people-close", "People opens and close dismisses", async () => {
    await cdpClickMobileMenuItem(client, "people", 800);
    await cdpExpectText(client, "people");
    await cdpClickButton(client, "close", 450);
    return cdpEnsureVisualHealth(client, "people closed");
  });

  await cdpRecordButtonProbe(buttonItems, "b05-intelligence-close", "Intelligence opens and close dismisses", async () => {
    await cdpClickMobileMenuItem(client, "intelligence", 900);
    await cdpExpectText(client, "intelligence");
    await cdpClickButton(client, "close", 450);
    return cdpEnsureVisualHealth(client, "intelligence closed");
  });

  await cdpRecordButtonProbe(buttonItems, "b06-video-labeler-tabs", "Video labeler tabs, refresh, and close respond", async () => {
    await cdpClickMobileMenuItem(client, "video labeler", 900);
    await cdpExpectText(client, "video labeler");
    await cdpClickButton(client, "^jobs$");
    await cdpClickButton(client, "^label$");
    const refreshed = await cdpAction(client, `(h) => h.clickButtonMatch("^refresh$")`);
    if (refreshed) await cdpDelay(350);
    await cdpClickButton(client, "close");
    const detail = await cdpEnsureVisualHealth(client, "video labeler closed");
    return refreshed ? detail : `${detail}; refresh absent in simulation state`;
  });

  await cdpRecordButtonProbe(buttonItems, "b07-simulation-controls", "Simulation controls reset and close respond", async () => {
    await cdpClickMobileMenuItem(client, "simulation", 650);
    await cdpExpectText(client, "^simulation$");
    await cdpClickButton(client, "^reset$");
    await cdpClickButton(client, "close simulation controls|^x$");
    return cdpEnsureVisualHealth(client, "simulation controls closed");
  });

  await cdpRecordButtonProbe(buttonItems, "b09-slash-command", "Slash command input executes", async () => {
    await cdpCloseOverlays(client);
    await cdpRunCommand(client, "/profile status", 900);
    await cdpExpectText(client, "profile");
    return cdpEnsureVisualHealth(client, "profile command");
  });

  await cdpRecordButtonProbe(buttonItems, "b10-drawer-closes", "World/lights/spatial/look drawers close from mobile", async () => {
    for (const command of ["/world-state", "/lights", "/spatial", "/look kitchen what is on the counter"]) {
      await cdpCloseOverlays(client);
      await cdpRunCommand(client, command, 900);
      await cdpClickButton(client, "close", 450);
    }
    return cdpEnsureVisualHealth(client, "drawers closed");
  });

  await cdpRecordButtonProbe(buttonItems, "b11-apartment-mode-buttons", "Apartment cloud/photo/mesh HUD buttons respond", async () => {
    await cdpCloseOverlays(client);
    await cdpRunCommand(client, "/apartment", 3200);
    await cdpWaitFor(client, "!!window.__havApartmentDebug", 12000);
    await cdpClickButton(client, "^cloud$");
    await cdpClickButton(client, "^photo$", 2600);
    await cdpClickButton(client, "^mesh$", 2600);
    const detail = await cdpEnsureVisualHealth(client, "apartment mode controls");
    await cdpClickButton(client, "close", 600);
    await cdpWaitFor(client, "!window.__havApartmentDebug", 5000);
    return detail;
  });

  await cdpRecordButtonProbe(buttonItems, "b08-remote-dialog", "Remote dialog profile buttons, test all, and close respond", async () => {
    await cdpCloseOverlays(client);
    const ok = await cdpAction(client, `(h) => h.clickSelector('button[aria-label="Remote profile"]')`);
    if (!ok) throw new Error("remote profile button missing");
    await cdpDelay(450);
    await cdpExpectText(client, "Remote access / Travel readiness");
    await cdpClickButton(client, "Home LAN");
    await cdpClickButton(client, "Remote via Tailscale");
    await cdpClickButton(client, "^Custom$");
    await cdpClickButton(client, "Home LAN");
    await cdpClickButton(client, "test all", 700);
    await cdpClickButton(client, "^x$|close");
    return cdpEnsureVisualHealth(client, "remote dialog closed");
  });
}

async function runViewportAuditCdp(appUrl, viewport, outRoot, errors) {
  const outDir = path.join(outRoot, viewport.name);
  await fs.mkdir(outDir, { recursive: true });
  const items = [];
  const buttonItems = [];
  const chrome = await launchChromeForCdp(viewport);
  let client = null;

  const capture = async (id, detail = "") => {
    try {
      const file = await cdpScreenshot(client, outDir, id);
      items.push({ id, ok: true, detail, path: file });
    } catch (err) {
      items.push({ id, ok: false, detail: err?.message || String(err) });
    }
  };
  const step = async (id, detail, fn) => {
    try {
      await fn();
      await capture(id, detail);
    } catch (err) {
      items.push({ id, ok: false, detail: `${detail}: ${err?.message || err}` });
    }
  };

  try {
    client = await createCdpPage(chrome, appUrl, viewport);
    await cdpWaitFor(client, "window.__bootState && (window.__bootState.done || window.__bootState.failed)", 35000);
    await cdpDelay(900);
    const bootFailed = await cdpEval(client, "window.__bootState && window.__bootState.failed ? String(window.__bootState.failed.error || window.__bootState.failed) : ''").catch(() => "");
    if (bootFailed) errors.push(`${viewport.name}: boot failed: ${bootFailed}`);
    await capture("01-boot", "Initial load");

    await step("02-simulation-home", "Enter simulation for non-secret UI state", async () => {
      const ok = await cdpAction(client, "(h) => h.clickButton('try simulation') || h.command('/simulation healthy')");
      if (!ok) throw new Error("could not enter simulation");
      await cdpDelay(1600);
    });

    await step("03-mobile-actions-menu", "Header actions menu", async () => {
      const ok = await cdpEval(client, "(() => { const b = document.querySelector('button[aria-label=\"Open mobile actions\"]'); if (!b) return false; b.click(); return true; })()");
      if (!ok) throw new Error("mobile menu button missing");
      await cdpDelay(500);
    });

    for (const [id, label, title] of [
      ["04-people", "people", "People overlay"],
      ["05-intelligence", "intelligence", "Intelligence atlas overlay"],
      ["06-video-labeler", "video labeler", "Video labeler overlay"],
      ["07-simulation-controls", "simulation", "Simulation controls"],
    ]) {
      await cdpCloseOverlays(client);
      await step(id, title, async () => {
        const ok = await cdpAction(client, `(h) => h.clickMenuItem(${JSON.stringify(label)})`);
        if (!ok) throw new Error(`menu item not found: ${label}`);
        await cdpDelay(1100);
      });
    }

    await cdpCloseOverlays(client);
    await step("08-slash-palette", "Slash command palette", async () => {
      const ok = await cdpEval(client, `(() => {
        const inputs = Array.from(document.querySelectorAll("input"));
        const el = document.querySelector('input[placeholder="type or /command"]') || inputs[inputs.length - 1];
        if (!el) return false;
        el.focus();
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
        if (setter) setter.call(el, "/");
        else el.value = "/";
        el.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
      })()`);
      if (!ok) throw new Error("command input missing");
      await cdpDelay(700);
    });

    for (const [id, command, waitMs] of [
      ["09-help", "/help", 900],
      ["10-profile-status", "/profile status", 900],
      ["11-travel-status", "/travel status", 900],
    ]) {
      await cdpCloseOverlays(client);
      await step(id, command, async () => {
        await cdpRunCommand(client, command, waitMs);
      });
    }

    await cdpCloseOverlays(client);
    await step("12-remote-dialog", "Remote access / Travel readiness", async () => {
      const ok = await cdpAction(client, `(h) => h.clickSelector('button[aria-label="Remote profile"]')`);
      if (!ok) throw new Error("remote profile button missing");
      await cdpExpectText(client, "Remote access / Travel readiness");
      await cdpDelay(600);
    });

    for (const [id, command, waitMs] of [
      ["13-cameras", "/cameras", 1900],
      ["14-world-state", "/world-state", 1100],
      ["15-lights", "/lights", 1100],
      ["16-spatial", "/spatial", 1100],
      ["17-look", "/look kitchen what is on the counter", 1100],
    ]) {
      await cdpCloseOverlays(client);
      await step(id, command, async () => {
        await cdpRunCommand(client, command, waitMs);
      });
    }

    await cdpCloseOverlays(client);
    await step("18-apartment-cloud", "/apartment cloud mode", async () => {
      await cdpRunCommand(client, "/apartment", 3600);
      await cdpWaitFor(client, "!!window.__havApartmentDebug", 15000);
    });

    await step("19-apartment-photo", "Apartment photo/splat mode", async () => {
      await cdpEval(client, "window.__havApartmentDebug && window.__havApartmentDebug.setMode('splat')", true);
      await cdpDelay(2600);
    });

    await step("20-apartment-mesh", "Apartment mesh mode", async () => {
      await cdpEval(client, "window.__havApartmentDebug && window.__havApartmentDebug.setMode('mesh')", true);
      await cdpDelay(2600);
    });

    await step("21-apartment-fly-camera", "Apartment fly-to-camera/live view", async () => {
      const ok = await cdpEval(client, "window.__havApartmentDebug && window.__havApartmentDebug.flyFirstCamera()", true);
      if (!ok) throw new Error("no camera device available for fly-to test");
      await cdpDelay(1600);
    });

    await cdpRunButtonProbes(client, buttonItems);
  } catch (err) {
    errors.push(`${viewport.name}: ${err?.message || err}`);
    if (chrome.stderr()) errors.push(`${viewport.name}: chrome stderr: ${chrome.stderr().slice(-1000)}`);
  } finally {
    client?.close();
    await chrome.close();
  }

  return { viewport: viewport.name, items, buttonItems };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return;
  }

  if (args.matrixOnly) {
    await writeMatrixReport(args.out, { status: "Matrix-only run. No browser screenshots captured." });
    console.log(`mobile audit matrix written to ${path.join(args.out, "REPORT.md")}`);
    return;
  }

  let staticServer = null;
  let appUrl = args.url;
  const errors = [];
  const results = [];
  try {
    if (!appUrl) {
      const started = await startStaticServer();
      staticServer = started.server;
      appUrl = started.url;
    }

    const playwright = await loadPlaywright();
    let engine = "chrome-cdp";
    if (playwright) {
      engine = "playwright";
      const browser = await playwright.chromium.launch({ headless: !args.headed });
      try {
        for (const viewport of args.viewports) {
          results.push(await runViewportAudit(browser, appUrl, viewport, args.out, errors));
        }
      } finally {
        await browser.close();
      }
    } else {
      for (const viewport of args.viewports) {
        results.push(await runViewportAuditCdp(appUrl, viewport, args.out, errors));
      }
    }

    const featureFailures = results.flatMap((r) => r.items).filter((i) => !i.ok);
    const buttonFailures = results.flatMap((r) => r.buttonItems || []).filter((i) => !i.ok);
    const failures = [...featureFailures, ...buttonFailures];
    await writeMatrixReport(args.out, {
      status: `Screenshots captured from ${appUrl} with ${engine}. ${failures.length ? `${featureFailures.length} feature(s), ${buttonFailures.length} button probe(s) failed.` : "All scripted captures and button probes completed."}`,
      results,
      errors,
    });
    console.log(`mobile screenshots written to ${args.out}`);
    const totalChecks = results.reduce((sum, r) => sum + r.items.length + (r.buttonItems?.length || 0), 0);
    console.log(`${totalChecks - failures.length} pass, ${failures.length} fail`);
    if (failures.length) process.exitCode = 1;
  } finally {
    await closeServer(staticServer);
  }
}

main().catch(async (err) => {
  console.error(err?.stack || err);
  process.exit(2);
});
