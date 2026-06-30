#!/usr/bin/env node
/*
 * Real apartment camera media audit.
 *
 * This is intentionally narrower than mobile-web-screenshot-audit.mjs. It
 * proves the live web app can snap to a calibrated apartment camera and render
 * an actual Frigate frame in the overlay. Simulation screenshots are not enough
 * for this path.
 */

import fs from "node:fs/promises";
import fssync from "node:fs";
import { spawn } from "node:child_process";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO = path.resolve(__dirname, "..");

const DEFAULT_URL = process.env.HOME_APARTMENT_CAMERA_AUDIT_URL || "https://home-app.taild52a15.ts.net/";
const DEFAULT_VIEWPORT = { width: 390, height: 844 };

function usage() {
  return [
    "Usage: node tools/audit-apartment-live-camera.mjs [options]",
    "",
    "Options:",
    "  --url <url>         App URL. Default: HOME_APARTMENT_CAMERA_AUDIT_URL or live Tailscale URL.",
    "  --viewport WxH      Mobile viewport. Default: 390x844.",
    "  --out <dir>         Output dir. Default: tools/reports/apartment-live-camera/<timestamp>.",
    "  --headed            Launch Chrome visibly.",
    "  --help              Show this help.",
  ].join("\n");
}

function timestamp() {
  return new Date().toISOString().replace(/[:.]/g, "-").replace("T", "_").replace("Z", "");
}

function defaultOutDir() {
  return path.join(REPO, "tools", "reports", "apartment-live-camera", timestamp());
}

function parseViewport(value) {
  const m = String(value || "").match(/^(\d+)x(\d+)$/i);
  if (!m) throw new Error(`invalid viewport: ${value}`);
  return { width: Number(m[1]), height: Number(m[2]) };
}

function parseArgs(argv) {
  const args = {
    url: DEFAULT_URL,
    viewport: DEFAULT_VIEWPORT,
    out: defaultOutDir(),
    headed: false,
    help: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--help" || arg === "-h") args.help = true;
    else if (arg === "--headed") args.headed = true;
    else if (arg === "--url") args.url = argv[++i] || "";
    else if (arg === "--viewport") args.viewport = parseViewport(argv[++i] || "");
    else if (arg === "--out") args.out = path.resolve(argv[++i] || args.out);
    else throw new Error(`unknown argument: ${arg}`);
  }
  if (!args.url) throw new Error("--url is required");
  return args;
}

function withPath(baseUrl, suffix) {
  return new URL(suffix, baseUrl).toString();
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`);
  return res.json();
}

async function probeRealCameraEndpoints(appUrl) {
  const modelUrl = withPath(appUrl, "/proxy/tracker/model");
  const model = await fetchJson(modelUrl, { cache: "no-store" });
  const cameras = Array.isArray(model?.devices)
    ? model.devices.filter((dev) => dev?.camera?.frigate_name)
    : [];
  const calibrated = cameras.find((dev) => dev?.camera?.intrinsics && dev?.camera?.extrinsics);
  if (!calibrated) throw new Error("tracker model has no calibrated Frigate camera");

  const frigateName = calibrated.camera.frigate_name;
  const imageUrl = withPath(appUrl, `/proxy/frigate/api/${encodeURIComponent(frigateName)}/latest.jpg?_=audit-${Date.now()}`);
  const res = await fetch(imageUrl, { cache: "no-store" });
  const type = res.headers.get("content-type") || "";
  const body = Buffer.from(await res.arrayBuffer());
  if (!res.ok || !type.toLowerCase().includes("image/") || body.length < 16 * 1024) {
    throw new Error(`Frigate latest.jpg did not return a usable image: HTTP ${res.status}, ${type || "no content-type"}, ${body.length} bytes`);
  }
  return {
    cameraId: calibrated.id || "",
    cameraName: calibrated.name || "",
    frigateName,
    rmsPx: calibrated.camera?.intrinsics?.rms_px ?? calibrated.camera?.extrinsics?.rms_px ?? null,
    imageBytes: body.length,
    imageType: type,
    imageUrl: imageUrl.replace(/\?.*$/, ""),
  };
}

function findChromeExecutable() {
  const candidates = [];
  if (process.env.CHROME_PATH) candidates.push(process.env.CHROME_PATH);
  const env = process.env;
  if (process.platform === "win32") {
    if (env.ProgramFiles) candidates.push(path.join(env.ProgramFiles, "Google", "Chrome", "Application", "chrome.exe"));
    if (env["ProgramFiles(x86)"]) candidates.push(path.join(env["ProgramFiles(x86)"], "Google", "Chrome", "Application", "chrome.exe"));
    if (env.LOCALAPPDATA) candidates.push(path.join(env.LOCALAPPDATA, "Google", "Chrome", "Application", "chrome.exe"));
  } else if (process.platform === "darwin") {
    candidates.push("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
    candidates.push("/Applications/Chromium.app/Contents/MacOS/Chromium");
  } else {
    candidates.push("/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser");
  }
  return candidates.find((candidate) => candidate && fssync.existsSync(candidate)) || "";
}

function randomPort() {
  return 43000 + Math.floor(Math.random() * 10000);
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

async function launchChrome(viewport, headed) {
  const chrome = findChromeExecutable();
  if (!chrome) throw new Error("Chrome executable not found. Set CHROME_PATH.");
  const port = randomPort();
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), "home-live-camera-audit-"));
  const args = [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    "--ignore-certificate-errors",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-dev-shm-usage",
    `--window-size=${viewport.width},${viewport.height}`,
    headed ? "" : "--headless=new",
    "about:blank",
  ].filter(Boolean);
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
    stderr: () => stderr,
    async close() {
      if (child.exitCode == null) child.kill();
      await fs.rm(profile, { recursive: true, force: true }).catch(() => {});
    },
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
    const promise = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.ws.send(JSON.stringify({ id, method, params }));
    return promise;
  }

  close() {
    try { this.ws.close(); } catch {}
  }
}

async function createPage(chrome, url, viewport) {
  const target = await fetchJson(
    `http://127.0.0.1:${chrome.port}/json/new?${encodeURIComponent(url)}`,
    { method: "PUT" },
  );
  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  await client.send("Log.enable").catch(() => {});
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
    const text = res.exceptionDetails.exception?.description || res.exceptionDetails.text || "evaluation failed";
    throw new Error(text);
  }
  return res.result?.value;
}

async function cdpWaitFor(client, expression, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  let last = "";
  while (Date.now() < deadline) {
    try {
      const value = await cdpEval(client, expression);
      if (value) return value;
    } catch (err) {
      last = err?.message || String(err);
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(last || `timed out waiting for ${expression}`);
}

async function cdpScreenshot(client, outFile) {
  const shot = await client.send("Page.captureScreenshot", {
    format: "png",
    fromSurface: true,
    captureBeyondViewport: false,
  });
  await fs.writeFile(outFile, Buffer.from(shot.data, "base64"));
}

async function waitForApartmentModel(client, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    last = await cdpEval(client, `(() => {
      const dbg = window.__havApartmentDebug;
      let snap = null;
      let snapError = "";
      try { snap = dbg?.snapshot?.() || null; }
      catch (e) { snapError = e?.stack || e?.message || String(e); }
      return {
        bootState: window.__bootState || null,
        apartmentDebugType: typeof dbg,
        apartmentDebugKeys: dbg && typeof dbg === "object" ? Object.keys(dbg) : [],
        snap,
        snapError,
        text: String(document.body?.textContent || "").slice(0, 1000),
      };
    })()`, true).catch((err) => ({ error: err?.message || String(err) }));
    if ((last?.snap?.devices || 0) > 0) return last.snap;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  const events = (client.events || []).slice(-20).map((event) => {
    if (event.method === "Runtime.consoleAPICalled") {
      return {
        method: event.method,
        type: event.params?.type,
        args: (event.params?.args || []).map((arg) => arg.value || arg.description || arg.type).slice(0, 4),
      };
    }
    if (event.method === "Runtime.exceptionThrown") {
      return {
        method: event.method,
        text: event.params?.exceptionDetails?.text,
        description: event.params?.exceptionDetails?.exception?.description,
      };
    }
    if (event.method === "Log.entryAdded") {
      return {
        method: event.method,
        level: event.params?.entry?.level,
        text: event.params?.entry?.text,
      };
    }
    return { method: event.method };
  });
  throw new Error(`apartment model did not load devices: ${JSON.stringify({ last, events }, null, 2).slice(0, 5000)}`);
}

const FEED_PROBE = `(() => {
  const snap = window.__havApartmentDebug?.snapshot?.() || {};
  const visible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 32 && rect.height > 32 &&
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      Number(style.opacity || "1") > 0.05;
  };
  const imgNodes = Array.from(document.querySelectorAll('[data-apt-live-feed="img"]'));
  const canvasNodes = Array.from(document.querySelectorAll('[data-apt-live-feed="canvas"]'));
  const imgs = imgNodes.map((el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return {
      tag: "img",
      visible: visible(el),
      complete: !!el.complete,
      naturalWidth: el.naturalWidth || 0,
      naturalHeight: el.naturalHeight || 0,
      src: el.getAttribute("data-apt-frame-src") || "",
      rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height },
      opacity: Number(style.opacity || "1"),
    };
  });
  const canvases = canvasNodes.map((el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return {
      tag: "canvas",
      visible: visible(el),
      width: el.width || 0,
      height: el.height || 0,
      rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height },
      opacity: Number(style.opacity || "1"),
    };
  });
  const loadedVisibleImg = imgs.find((img) =>
    img.visible && img.complete && img.naturalWidth > 80 && img.naturalHeight > 80
  );
  const loadedHiddenImg = imgs.find((img) =>
    img.complete && img.naturalWidth > 80 && img.naturalHeight > 80
  );
  const visibleCanvas = canvases.find((canvas) =>
    canvas.visible && canvas.width > 80 && canvas.height > 80
  );
  const status = String(snap.liveFeedStatus || "");
  const badStatus = status === "connecting" || status === "retrying" || status === "error";
  const mobile = !!snap.mobile;
  return {
    ok: !!snap.liveCam && !!snap.liveOn && !badStatus &&
      (mobile ? !!loadedVisibleImg && !visibleCanvas : !!(loadedVisibleImg || (loadedHiddenImg && visibleCanvas))),
    snap,
    imgs,
    canvases,
    mobileRawRequired: mobile,
    badStatus,
    loadedVisibleImg: !!loadedVisibleImg,
    loadedHiddenImg: !!loadedHiddenImg,
    visibleCanvas: !!visibleCanvas,
    bodyText: String(document.body?.textContent || "").slice(0, 600),
  };
})()`;

async function runOverlayAudit(args, endpointProbe) {
  const chrome = await launchChrome(args.viewport, args.headed);
  let client = null;
  try {
    client = await createPage(chrome, args.url, args.viewport);
    await cdpWaitFor(client, "window.__bootState && (window.__bootState.done || window.__bootState.failed)", 45000);
    const bootFailed = await cdpEval(client, "window.__bootState?.failed ? String(window.__bootState.failed.error || window.__bootState.failed) : ''");
    if (bootFailed) throw new Error(`boot failed: ${bootFailed}`);

    await cdpWaitFor(client, "!!window.__havUiDebug?.openApartmentForAudit", 15000);
    await cdpEval(client, "window.__havUiDebug.openApartmentForAudit()", true);
    await cdpWaitFor(client, "!!window.__havApartmentDebug", 20000);
    await waitForApartmentModel(client, 30000);
    const opened = await cdpEval(client, "window.__havApartmentDebug.flyFirstCalibratedCamera()", true);
    if (!opened) throw new Error("app did not find a calibrated camera to fly to");

    let probe = null;
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      probe = await cdpEval(client, FEED_PROBE, true);
      if (probe?.ok) break;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    if (!probe?.ok) {
      throw new Error(`live camera overlay did not render media; last state: ${JSON.stringify(probe, null, 2).slice(0, 3000)}`);
    }

    await fs.mkdir(args.out, { recursive: true });
    const screenshotFile = path.join(args.out, "apartment-live-camera.png");
    await cdpScreenshot(client, screenshotFile);
    return { ok: true, endpointProbe, overlayProbe: probe, screenshotFile };
  } finally {
    client?.close();
    await chrome.close();
  }
}

async function writeReport(outDir, args, result, error) {
  await fs.mkdir(outDir, { recursive: true });
  const lines = [
    "# Apartment Live Camera Audit",
    "",
    `- URL: ${args.url}`,
    `- Viewport: ${args.viewport.width}x${args.viewport.height}`,
    `- Status: ${error ? "FAIL" : "PASS"}`,
    "",
  ];
  if (result?.endpointProbe) {
    lines.push("## Endpoint Probe", "");
    lines.push(`- Camera: ${result.endpointProbe.cameraName || result.endpointProbe.cameraId} (${result.endpointProbe.frigateName})`);
    lines.push(`- Calibration RMS: ${result.endpointProbe.rmsPx ?? "unknown"} px`);
    lines.push(`- JPEG: ${result.endpointProbe.imageBytes} bytes, ${result.endpointProbe.imageType}`);
    lines.push("");
  }
  if (result?.overlayProbe) {
    lines.push("## Overlay Probe", "");
    lines.push("```json");
    lines.push(JSON.stringify(result.overlayProbe, null, 2));
    lines.push("```", "");
    lines.push(`Screenshot: ${path.basename(result.screenshotFile)}`);
    lines.push("");
  }
  if (error) {
    lines.push("## Error", "");
    lines.push("```text");
    lines.push(error?.stack || error?.message || String(error));
    lines.push("```", "");
  }
  await fs.writeFile(path.join(outDir, "REPORT.md"), lines.join("\n"), "utf8");
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return;
  }
  let result = null;
  let endpointProbe = null;
  try {
    endpointProbe = await probeRealCameraEndpoints(args.url);
    result = await runOverlayAudit(args, endpointProbe);
    await writeReport(args.out, args, result, null);
    console.log(`apartment live camera audit passed: ${args.out}`);
  } catch (err) {
    if (!result && endpointProbe) result = { endpointProbe };
    await writeReport(args.out, args, result, err);
    console.error(err?.stack || err);
    console.error(`apartment live camera audit failed: ${args.out}`);
    process.exit(1);
  }
}

main();
