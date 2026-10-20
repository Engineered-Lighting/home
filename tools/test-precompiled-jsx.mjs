/* Ahead-of-time JSX: build, gateway verification and the browser loaders.
 *
 *   node --test tools/test-precompiled-jsx.mjs
 *
 * The browser suite builds the real app/src into a temporary directory (never
 * into app/src), serves it the way each surface does, and proves that:
 * - with the gateway's verified manifest the app boots and every lazy feature
 *   loads without a JSX source request, a transform or Babel itself;
 * - with a plain-file manifest (Tauri, dev servers) an edited source is the
 *   only file compiled in the browser, and nothing stale runs;
 * - with no manifest the app compiles everything in the browser, as before;
 * - precompiled output is byte-identical to the in-browser transform.
 */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { APP_SRC, buildPrecompiledJsx, listJsx, loadPrecompiledRuntime, stalePrecompiledJsx } from "./lib/precompiled-jsx.mjs";
import { createPrecompiledManifestVerifier } from "../web-gateway/precompiled-jsx.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { chromium } = createRequire(import.meta.url)("playwright");
const runtime = loadPrecompiledRuntime();

function tempDir(label) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `home-precompiled-${label}-`));
}

function miniApp() {
  const dir = tempDir("mini");
  fs.copyFileSync(path.join(APP_SRC, "home-precompiled-jsx.js"), path.join(dir, "home-precompiled-jsx.js"));
  fs.mkdirSync(path.join(dir, "vendor", "babel-7.29.0"), { recursive: true });
  fs.copyFileSync(path.join(APP_SRC, runtime.BABEL_SRC), path.join(dir, runtime.BABEL_SRC));
  fs.writeFileSync(path.join(dir, "a.jsx"), "const A = () => <b>a</b>;\nwindow.A = A;\n");
  fs.writeFileSync(path.join(dir, "b.jsx"), "﻿class B { x = 1; }\nwindow.B = <B />;\n");
  fs.writeFileSync(path.join(dir, "bad.jsx"), "const broken = <div>;\n");
  return dir;
}

test("build is incremental, drops failures and stale outputs, and the gateway serves only fresh entries", () => {
  const src = miniApp();
  try {
    const first = buildPrecompiledJsx({ srcDir: src });
    assert.deepEqual(first.compiled, ["a.jsx", "b.jsx"]);
    assert.deepEqual(first.failed.map((f) => f.name), ["bad.jsx"]);
    assert.deepEqual(Object.keys(first.manifest.files), ["a.jsx", "b.jsx"]);
    assert.equal(first.manifest.files["a.jsx"].output, "precompiled/a.js");
    assert.equal(first.manifest.files["b.jsx"].checksum, runtime.checksum("class B { x = 1; }\nwindow.B = <B />;\n"),
      "a UTF-8 BOM is not part of the checksum, matching what XHR hands the loader");
    const code = fs.readFileSync(path.join(src, "precompiled", "a.js"), "utf8");
    assert.match(code, /React\.createElement\("b", null, "a"\)/);
    assert.match(code, /\n\/\/# sourceMappingURL=precompiled\/a\.js\.map\n$/);
    const map = JSON.parse(fs.readFileSync(path.join(src, "precompiled", "a.js.map"), "utf8"));
    assert.deepEqual(map.sources, ["../a.jsx"]);
    assert.deepEqual(stalePrecompiledJsx({ srcDir: src }), ["bad.jsx"]);

    const second = buildPrecompiledJsx({ srcDir: src });
    assert.deepEqual(second.compiled, []);
    assert.deepEqual(second.reused, ["a.jsx", "b.jsx"]);

    const verifier = createPrecompiledManifestVerifier(src);
    let served = verifier.verifiedManifest();
    assert.equal(served.verified, true);
    assert.deepEqual(Object.keys(served.files), ["a.jsx", "b.jsx"]);

    fs.appendFileSync(path.join(src, "a.jsx"), "// edited\n");
    assert.deepEqual(stalePrecompiledJsx({ srcDir: src }), ["a.jsx", "bad.jsx"]);
    served = verifier.verifiedManifest();
    assert.deepEqual(Object.keys(served.files), ["b.jsx"], "an edited source leaves the verified manifest");

    fs.rmSync(path.join(src, "b.jsx"));
    fs.rmSync(path.join(src, "bad.jsx"));
    const third = buildPrecompiledJsx({ srcDir: src });
    assert.deepEqual(third.compiled, ["a.jsx"]);
    assert.deepEqual(third.removed.sort(), ["b.js", "b.js.map"]);
    assert.deepEqual(Object.keys(verifier.verifiedManifest().files), ["a.jsx"]);

    const manifestFile = path.join(src, "precompiled", "manifest.json");
    const manifest = JSON.parse(fs.readFileSync(manifestFile, "utf8"));
    fs.writeFileSync(manifestFile, JSON.stringify({ ...manifest, files: { ...manifest.files,
      "evil.jsx": { output: "precompiled/../../secret.js", checksum: "x" } } }));
    assert.deepEqual(Object.keys(verifier.verifiedManifest().files), ["a.jsx"], "outputs outside precompiled/ are never served");
    fs.writeFileSync(manifestFile, JSON.stringify({ ...manifest, babel: "7.0.0" }));
    assert.equal(verifier.verifiedManifest(), null, "a manifest from another Babel version is not served");
    fs.rmSync(manifestFile);
    assert.equal(verifier.verifiedManifest(), null);
  } finally {
    fs.rmSync(src, { recursive: true, force: true });
  }
});

const CONTENT_TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".mjs": "text/javascript",
  ".jsx": "text/plain; charset=utf-8", ".css": "text/css", ".json": "application/json", ".map": "application/json",
  ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".wasm": "application/wasm" };

/* manifest: "verified" (as the gateway), "static" (a plain file, as Tauri or
   a dev server) or "none". overrides: { "name.jsx": text } replaces a source. */
function startServer({ outDir, manifest, overrides = {} }) {
  const verifier = createPrecompiledManifestVerifier(APP_SRC, outDir);
  const requests = [];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://local");
    const pathname = decodeURIComponent(url.pathname);
    requests.push(pathname);
    const send = (status, body, type = "application/json") => {
      res.writeHead(status, { "Content-Type": type, "Cache-Control": "no-store" });
      res.end(body);
    };
    if (pathname.startsWith("/proxy/") || pathname.startsWith("/api/")) {
      return send(200, pathname.endsWith("/api/states") ? "[]" : "{}");
    }
    if (pathname === "/precompiled/manifest.json") {
      if (manifest === "none") return send(404, "not found", "text/plain");
      if (manifest === "verified") return send(200, JSON.stringify(verifier.verifiedManifest()));
    }
    let file;
    if (pathname.startsWith("/precompiled/")) {
      if (manifest === "none") return send(404, "not found", "text/plain");
      file = path.join(outDir, pathname.slice("/precompiled/".length));
    } else {
      file = path.join(APP_SRC, pathname === "/" ? "index.html" : pathname);
    }
    if (!path.resolve(file).startsWith(path.resolve(pathname.startsWith("/precompiled/") ? outDir : APP_SRC))) return send(403, "");
    const name = pathname.slice(1);
    let body;
    if (Object.prototype.hasOwnProperty.call(overrides, name)) body = Buffer.from(overrides[name]);
    else if (fs.existsSync(file) && fs.statSync(file).isFile()) body = fs.readFileSync(file);
    else return send(404, "not found", "text/plain");
    if (pathname === "/" || pathname === "/index.html") {
      body = Buffer.from(String(body).replace("</head>",
        '<script>window.__HOME_ASSET_VERSION="precompiled-test";window.__HOME_WEB_STACK_TOKEN_PROXY=false;</script>\n</head>'));
    }
    send(200, body, CONTENT_TYPES[path.extname(file).toLowerCase()] || "application/octet-stream");
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () =>
    resolve({ server, requests, origin: `http://127.0.0.1:${server.address().port}` })));
}

async function openApp(browser, served) {
  const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1280, height: 800 } });
  await context.addInitScript(() => {
    window.HG_DISABLE_SERVICE_WORKER = true;
    try { localStorage.setItem("home.perf.backgroundWarmup", "off"); } catch (_) {}
    // Every boot-file record, from the first. __homePerf.snapshot() keeps only
    // the newest 160, and a full boot plus the Apartment feature writes more.
    const records = window.__precompiledTestRecords = [];
    let perf;
    Object.defineProperty(window, "__homePerf", {
      configurable: true,
      get: () => perf,
      set(value) {
        const record = value.recordBootFile;
        value.recordBootFile = function (file, phase, ...rest) {
          records.push({ file, phase });
          return record.call(this, file, phase, ...rest);
        };
        perf = value;
      },
    });
  });
  await context.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (["data:", "blob:"].includes(url.protocol) || url.origin === served.origin) return route.continue();
    return route.abort();
  });
  await context.routeWebSocket("**/*", (socket) => socket.close());
  const page = await context.newPage();
  const pageErrors = [];
  const warnings = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => { if (/precompiled-jsx/.test(message.text())) warnings.push(message.text()); });
  await page.goto(served.origin + "/", { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => window.__bootState?.failed ||
    (window.__bootState?.done && document.getElementById("root")?.children.length > 0), null, { timeout: 60000 });
  assert.equal(await page.evaluate(() => window.__bootState.failed), null, "the boot chain completes");
  return { context, page, pageErrors, warnings };
}

async function loadEveryFeature(page) {
  return page.evaluate(async () => {
    const out = {};
    for (const feature of Object.keys(window.HomeFeatureLoader.definitions)) {
      try { out[feature] = (await window.HomeFeatureLoader.load(feature, "precompiled-test")).state; }
      catch (error) { out[feature] = "error: " + error.message; }
    }
    return out;
  });
}

function transformedFiles(page) {
  return page.evaluate(() => window.__precompiledTestRecords
    .filter((entry) => /transform/.test(entry.phase)).map((entry) => entry.file));
}

test("browser loaders run precompiled JSX, fall back per file, and match in-browser Babel exactly", { timeout: 145000 }, async () => {
  const outDir = tempDir("out");
  const browser = await chromium.launch({ headless: true });
  try {
    const build = buildPrecompiledJsx({ outDir });
    assert.deepEqual(build.failed, []);
    const jsx = listJsx();
    assert.deepEqual(Object.keys(build.manifest.files), jsx, "every top-level JSX file is precompiled");

    {
      const served = await startServer({ outDir, manifest: "verified" });
      const app = await openApp(browser, served);
      try {
        const features = await loadEveryFeature(app.page);
        assert.ok(Object.keys(features).length >= 9, JSON.stringify(features));
        assert.deepEqual(Object.entries(features).filter(([, state]) => state !== "loaded"), [], "every lazy feature loads");
        assert.deepEqual(await transformedFiles(app.page), [], "nothing is compiled in the browser");
        assert.equal(await app.page.evaluate(() => typeof window.Babel), "undefined", "Babel is never downloaded");
        assert.deepEqual(served.requests.filter((p) => p.endsWith(".jsx") || p.includes("babel")), [],
          "no JSX source or Babel request is made");
        assert.ok(served.requests.includes("/precompiled/home-app.js"));
        assert.deepEqual(app.pageErrors, []);
      } finally { await app.context.close(); served.server.close(); }
    }

    {
      const edited = fs.readFileSync(path.join(APP_SRC, "home-icons.jsx"), "utf8") + "\nwindow.__precompiledTestEdited = true;\n";
      const served = await startServer({ outDir, manifest: "static", overrides: { "home-icons.jsx": edited } });
      const app = await openApp(browser, served);
      try {
        assert.deepEqual(await transformedFiles(app.page), ["home-icons.jsx"], "only the edited file is compiled in the browser");
        assert.equal(await app.page.evaluate(() => window.__precompiledTestEdited), true, "the edited source runs, not the stale build");
        assert.ok(app.warnings.some((line) => line.includes("home-icons.jsx changed since it was precompiled")));
        assert.ok(served.requests.includes("/home-app.jsx"), "a plain-file manifest is checked against the sources");
        assert.deepEqual(app.pageErrors, []);
      } finally { await app.context.close(); served.server.close(); }
    }

    {
      const served = await startServer({ outDir, manifest: "none" });
      const app = await openApp(browser, served);
      try {
        const bootJsx = await app.page.evaluate(() => window.__HOME_BOOT_FILES.filter((name) => name.endsWith(".jsx")));
        const transformed = await transformedFiles(app.page);
        assert.deepEqual(bootJsx.filter((name) => !transformed.includes(name)), [],
          "without a manifest every boot JSX file is compiled in the browser, as before");
        assert.ok(transformed.every((name) => name.endsWith(".jsx")), transformed.join(", "));
        assert.deepEqual(app.pageErrors, []);
        assert.deepEqual(app.warnings, [], "a missing manifest is the normal dev case, not a warning");

        const outputs = Object.fromEntries(jsx.map((name) => [name,
          fs.readFileSync(path.join(outDir, path.basename(build.manifest.files[name].output)), "utf8")]));
        const mismatched = await app.page.evaluate(async ({ names, outputs }) => {
          const strip = (code) => code.replace(/\n\/\/# sourceMappingURL=[^\n]*\n?$/, "");
          const bad = [];
          for (const name of names) {
            const text = await (await fetch(name)).text();
            if (strip(window.HomePrecompiledJsx.transform(name, text)) !== strip(outputs[name])) bad.push(name);
          }
          return bad;
        }, { names: jsx, outputs });
        assert.deepEqual(mismatched, [], "precompiled code equals the in-browser transform for every file");
      } finally { await app.context.close(); served.server.close(); }
    }
  } finally {
    await browser.close();
    fs.rmSync(outDir, { recursive: true, force: true });
  }
});

test("index.html, the feature loader and the build share one Babel setup", () => {
  const index = fs.readFileSync(path.join(APP_SRC, "index.html"), "utf8");
  const loader = fs.readFileSync(path.join(APP_SRC, "home-feature-loader.js"), "utf8");
  assert.doesNotMatch(index, /Babel\.transform\(|BABEL_PLUGINS|babel\.min\.js/);
  assert.doesNotMatch(loader, /Babel\.transform\(|BABEL_PLUGINS/);
  assert.equal(runtime.OPTIONS_ID, "react+env|transform-class-properties+transform-object-rest-spread+transform-flow-strip-types");
  assert.deepEqual(JSON.parse(JSON.stringify(runtime.babelOptions("x.jsx", "inline"))), {
    filename: "x.jsx", presets: ["react", "env"],
    plugins: ["transform-class-properties", "transform-object-rest-spread", "transform-flow-strip-types"],
    sourceMaps: "inline", sourceFileName: "x.jsx",
  }, "the in-browser options are @babel/standalone's script-tag defaults, as before");
  const tauri = JSON.parse(fs.readFileSync(path.join(ROOT, "app", "src-tauri", "tauri.conf.json"), "utf8"));
  assert.equal(tauri.build.beforeBuildCommand, "node ../tools/build-app-jsx.mjs", "installer builds precompile first (hook cwd is app/)");
  assert.equal(tauri.build.frontendDist, "../src");
  assert.match(fs.readFileSync(path.join(ROOT, "tools", "deploy-home-web.sh"), "utf8"), /node tools\/build-app-jsx\.mjs/);
  assert.match(fs.readFileSync(path.join(ROOT, ".gitignore"), "utf8"), /^app\/src\/precompiled\/$/m);
});
