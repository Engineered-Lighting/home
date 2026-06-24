#!/usr/bin/env node
/* Lightweight Stage 4 browser/app smoke.
 *
 * This does not replace rendered Playwright screenshot coverage. It gives the
 * production QA orchestrator a cheap autonomous UI gate: the app shell must
 * answer, the shell HTML must contain the expected script graph, and the
 * current Atlas entry points must still exist in source.
 */

"use strict";

const fs = require("fs");
const http = require("http");
const https = require("https");
const path = require("path");

const REPO = path.resolve(__dirname, "..");
const SRC_DIR = path.join(REPO, "app", "src");
const APP_URL = process.env.HOME_APP_URL || "";
const REPORT_DIR = process.env.QA_REPORT_DIR || "";

function get(url, timeoutMs = 5000) {
  return new Promise((resolve) => {
    let parsed;
    try {
      parsed = new URL(url);
    } catch (err) {
      resolve({ ok: false, status: 0, body: "", error: `invalid URL: ${err.message || err}` });
      return;
    }
    const client = parsed.protocol === "https:" ? https : http;
    const req = client.get(parsed, (res) => {
      let data = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { data += chunk; });
      res.on("end", () => resolve({ ok: true, status: res.statusCode, headers: res.headers, body: data }));
    });
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error("timeout"));
    });
    req.on("error", (err) => resolve({ ok: false, status: 0, body: "", error: String(err.message || err) }));
  });
}

function check(name, ok, detail = "") {
  return { name, status: ok ? "PASS" : "FAIL", detail };
}

function bootLoaderFiles(indexSource) {
  const match = indexSource.match(/var\s+files\s*=\s*\[([\s\S]*?)\];/);
  if (!match) return [];
  return Array.from(match[1].matchAll(/\[\s*['"]([^'"]+)['"]\s*,\s*(?:true|false)\s*\]/g))
    .map((item) => item[1]);
}

function contentType(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".html") return "text/html; charset=utf-8";
  if (ext === ".js" || ext === ".jsx") return "text/javascript; charset=utf-8";
  if (ext === ".css") return "text/css; charset=utf-8";
  if (ext === ".json") return "application/json; charset=utf-8";
  if (ext === ".svg") return "image/svg+xml";
  if (ext === ".png") return "image/png";
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
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
      fs.readFile(filePath, (err, data) => {
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
      resolve({
        server,
        url: `http://127.0.0.1:${address.port}/`,
      });
    });
  });
}

async function closeServer(server) {
  if (!server) return;
  await new Promise((resolve) => server.close(resolve));
}

async function main() {
  const checks = [];
  let appUrl = APP_URL;
  let staticServer = null;

  try {
    if (!appUrl) {
      try {
        const started = await startStaticServer();
        staticServer = started.server;
        appUrl = started.url;
        checks.push(check("self-hosted static app server starts", true, appUrl));
      } catch (err) {
        checks.push(check("self-hosted static app server starts", false, String(err && err.message || err)));
      }
    }

    const response = appUrl ? await get(appUrl) : { ok: false, status: 0, body: "", error: "no app URL" };
    checks.push(check("app shell responds", response.ok && response.status === 200, response.error || `HTTP ${response.status}`));
    checks.push(check("shell loads home-app script", response.body.includes("home-app.jsx"), "index.html script graph"));

    const app = fs.readFileSync(path.join(SRC_DIR, "home-app.jsx"), "utf8");
    const intel = fs.readFileSync(path.join(SRC_DIR, "home-intelligence.jsx"), "utf8");
    const index = fs.readFileSync(path.join(SRC_DIR, "index.html"), "utf8");
    const bootFiles = bootLoaderFiles(index);

    checks.push(check("Atlas launcher exists", app.includes("Open intelligence atlas"), "header action"));
    checks.push(check("Atlas component is script-loaded", index.includes("home-intelligence.jsx"), "index.html"));
    checks.push(check("Atlas root class exists", intel.includes("intel-atlas"), "home-intelligence.jsx"));
    checks.push(check("Atlas top badge panels exist", intel.includes("globalPanel"), "badge panel state"));
    checks.push(check("Atlas correction workflow exists", intel.includes("correct_primary_activity"), "human label audit action"));
    checks.push(check("boot-loader file list is discoverable", bootFiles.length >= 40, `${bootFiles.length} files`));

    const criticalBootFiles = ["home-app.jsx", "simulation.jsx", "home-intelligence.jsx", "home-mount.jsx"];
    for (const fileName of criticalBootFiles) {
      const served = appUrl ? await get(new URL(`${fileName}?cb=qa-smoke`, appUrl).toString()) : { ok: false, status: 0, headers: {}, body: "" };
      const content = String(served.headers && served.headers["content-type"] || "");
      checks.push(check(`${fileName} served through app shell`, served.ok && served.status === 200, served.error || `HTTP ${served.status}`));
      checks.push(check(`${fileName} served as JavaScript`, content.includes("javascript"), content));
      checks.push(check(`${fileName} response is non-empty`, served.body.length > 200, `${served.body.length} bytes`));
    }

    const failed = checks.filter((c) => c.status !== "PASS");
    const result = {
      app_url: appUrl,
      self_hosted: !APP_URL,
      checks,
      summary: `${checks.length - failed.length} PASS, ${failed.length} FAIL`,
    };
    if (REPORT_DIR) {
      fs.writeFileSync(path.join(REPORT_DIR, "stage4-browser-smoke.json"), JSON.stringify(result, null, 2));
    }
    for (const c of checks) {
      console.log(`${c.status.padEnd(4)} ${c.name}${c.detail ? ` - ${c.detail}` : ""}`);
    }
    console.log("");
    console.log(`${checks.length - failed.length} pass . ${failed.length} fail`);
    await closeServer(staticServer);
    process.exit(failed.length ? 1 : 0);
  } catch (err) {
    await closeServer(staticServer);
    throw err;
  }
}

main().catch((err) => {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
