#!/usr/bin/env node
/* ============================================================================
 * check-jsx.js — offline JSX/Babel parse check for the home app frontend.
 *
 * The home app loads its .jsx files through Babel-standalone IN THE WEBVIEW
 * at runtime — `cargo build` compiles only the Rust side and never sees a
 * JSX syntax error. This tool closes that gap: it downloads the exact
 * Babel-standalone build index.html pins, transforms every frontend file
 * with the `react` preset, and reports parse errors with line numbers.
 *
 * Plain .js files are checked with `new Function(...)` (a full parse).
 * .jsx files are checked with Babel.transform.
 *
 * Usage:
 *   node tools/check-jsx.js                 # check all app/src frontend files
 *   node tools/check-jsx.js home-spatial.jsx  # check specific file(s)
 *
 * Exit 0 = all parse clean.
 * ============================================================================ */
"use strict";

const fs = require("fs");
const path = require("path");
const https = require("https");
const os = require("os");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src");
const BABEL_VERSION = "7.29.0";                 // must match index.html
const BABEL_URL =
  "https://unpkg.com/@babel/standalone@" + BABEL_VERSION + "/babel.min.js";
const CACHE = path.join(os.tmpdir(),
                        "babel-standalone-" + BABEL_VERSION + ".js");

function getBabelSource() {
  return new Promise((resolve, reject) => {
    // The exact pinned build is vendored for the app itself — prefer it so
    // the check works offline and in CI without touching the network.
    const vendored = path.join(SRC, "vendor", "babel-" + BABEL_VERSION, "babel.min.js");
    if (fs.existsSync(vendored)) {
      resolve(fs.readFileSync(vendored, "utf8"));
      return;
    }
    if (fs.existsSync(CACHE)) {
      resolve(fs.readFileSync(CACHE, "utf8"));
      return;
    }
    https.get(BABEL_URL, (res) => {
      if (res.statusCode !== 200) {
        reject(new Error("babel download HTTP " + res.statusCode));
        return;
      }
      let data = "";
      res.on("data", (c) => { data += c; });
      res.on("end", () => {
        try { fs.writeFileSync(CACHE, data); } catch (e) { /* cache best-effort */ }
        resolve(data);
      });
    }).on("error", reject);
  });
}

function loadBabel(src) {
  const mod = { exports: {} };
  // @babel/standalone is UMD — in a CJS shim it assigns module.exports.
  new Function("module", "exports", src)(mod, mod.exports);
  return mod.exports && mod.exports.transform ? mod.exports
       : (global.Babel || null);
}

async function main() {
  let Babel;
  try {
    Babel = loadBabel(await getBabelSource());
  } catch (e) {
    console.error("check-jsx: could not load Babel — " + e.message);
    console.error("  (network needed once to fetch " + BABEL_URL + ")");
    return 2;
  }
  if (!Babel || !Babel.transform) {
    console.error("check-jsx: Babel loaded but transform() missing");
    return 2;
  }

  // Resolve the file list.
  const args = process.argv.slice(2);
  let files;
  if (args.length) {
    files = args.map((a) => path.isAbsolute(a) ? a : path.join(SRC, a));
  } else {
    files = fs.readdirSync(SRC)
      .filter((f) => f.endsWith(".jsx") || f.endsWith(".js"))
      .map((f) => path.join(SRC, f));
  }

  let ok = 0;
  let bad = 0;
  for (const file of files) {
    const name = path.relative(REPO, file);
    let src;
    try {
      src = fs.readFileSync(file, "utf8");
    } catch (e) {
      console.log("  FAIL  " + name + "  (" + e.message + ")");
      bad += 1;
      continue;
    }
    try {
      if (file.endsWith(".jsx")) {
        Babel.transform(src, { presets: ["react"], filename: file });
      } else {
        // Plain JS — a full parse via Function constructor.
        new Function(src);
      }
      ok += 1;
      console.log("  PASS  " + name);
    } catch (e) {
      bad += 1;
      console.log("  FAIL  " + name);
      console.log("        " + (e.message || String(e)).split("\n")[0]);
    }
  }

  console.log("\n" + ok + " ok, " + bad + " error(s)");
  return bad === 0 ? 0 : 1;
}

main().then((code) => process.exit(code))
      .catch((e) => { console.error(e); process.exit(2); });
