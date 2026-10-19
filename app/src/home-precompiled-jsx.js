/* Precompiled JSX runtime.
 *
 * The boot loader (index.html) and the feature loader run JSX through here.
 * tools/build-app-jsx.mjs compiles every app/src/*.jsx ahead of time into
 * precompiled/, with precompiled/manifest.json recording, per file, the
 * compiled output and a checksum of the exact source text it came from.
 * Runtime Babel stays as the fallback for anything not precompiled, and is
 * only downloaded when some file actually needs it.
 *
 * Trust rules, so an edit can never run stale compiled code:
 * - A manifest served as a plain file (Tauri, `npm run dev`, test harnesses)
 *   is only a hint. The loader still fetches the source, and uses the
 *   compiled file only when the source checksum matches; otherwise that one
 *   file is compiled in the browser exactly as before.
 * - The web gateway re-checks every entry against the files on disk for each
 *   manifest request and marks the result `verified`, so browsers skip the
 *   source download (web-gateway/precompiled-jsx.mjs).
 * - No manifest, an unreadable one, or one built by another Babel version or
 *   option set means everything is compiled in the browser, as before.
 *
 * tools/build-app-jsx.mjs and the gateway load this same file in Node, so the
 * Babel options and the checksum cannot drift between build and runtime.
 */
(function (root) {
  "use strict";

  var BABEL_VERSION = "7.29.0";
  var BABEL_SRC = "./vendor/babel-7.29.0/babel.min.js";
  var MANIFEST_SCHEMA = "home-precompiled-jsx-1";
  var MANIFEST_PATH = "precompiled/manifest.json";
  // @babel/standalone's script-tag defaults: presets react+env plus the three
  // legacy plugins. Changing these changes OPTIONS_ID, which retires every
  // existing manifest until it is rebuilt.
  var PRESETS = ["react", "env"];
  var PLUGINS = [
    "transform-class-properties",
    "transform-object-rest-spread",
    "transform-flow-strip-types",
  ];
  var OPTIONS_ID = PRESETS.join("+") + "|" + PLUGINS.join("+");

  function babelOptions(name, sourceMaps) {
    return {
      filename: name,
      presets: PRESETS.slice(),
      plugins: PLUGINS.slice(),
      sourceMaps: sourceMaps,
      sourceFileName: name,
    };
  }

  // XHR decoding drops a UTF-8 BOM; Node's readFileSync keeps it. Both sides
  // hash and compile the text without it.
  function sourceText(text) {
    return text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
  }

  // cyrb53 over UTF-16 code units, plus the length. Not cryptographic: it only
  // has to notice that a source changed after it was precompiled.
  function checksum(text) {
    var s = sourceText(String(text));
    var h1 = 0xdeadbeef;
    var h2 = 0x41c6ce57;
    for (var i = 0; i < s.length; i++) {
      var ch = s.charCodeAt(i);
      h1 = Math.imul(h1 ^ ch, 2654435761);
      h2 = Math.imul(h2 ^ ch, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    var hash = 4294967296 * (2097151 & h2) + (h1 >>> 0);
    return s.length.toString(36) + "-" + hash.toString(36);
  }

  function usableManifest(manifest) {
    return !!(manifest && manifest.schema === MANIFEST_SCHEMA &&
      manifest.babel === BABEL_VERSION && manifest.options === OPTIONS_ID &&
      manifest.files && typeof manifest.files === "object");
  }

  function entryFor(manifest, name) {
    if (!usableManifest(manifest)) return null;
    var entry = Object.prototype.hasOwnProperty.call(manifest.files, name) ? manifest.files[name] : null;
    return entry && typeof entry.output === "string" && typeof entry.checksum === "string" ? entry : null;
  }

  var manifestPromise = null;

  /* fetchText(path) -> Promise<string>, supplied by the caller so the boot
     loader keeps its own retry/timeout/credential rules. One fetch is shared
     by every caller. A 404 (nothing precompiled) is final for the page; any
     other failure is retried by the next caller. */
  function loadManifest(fetchText) {
    if (!manifestPromise) {
      manifestPromise = fetchText(MANIFEST_PATH).then(function (text) {
        var manifest = JSON.parse(text);
        if (!usableManifest(manifest)) {
          console.warn("[precompiled-jsx] ignoring a manifest built for another Babel setup; compiling JSX in the browser");
          return null;
        }
        return manifest;
      }).catch(function (err) {
        if (!/HTTP status 404/.test(String(err && err.message))) {
          manifestPromise = null;
          console.warn("[precompiled-jsx] manifest unavailable; compiling JSX in the browser -", (err && err.message) || err);
        }
        return null;
      });
    }
    return manifestPromise;
  }

  /* Resolves { text, precompiled }. precompiled === true means `text` is
     ready-to-run JavaScript; otherwise it is the JSX source for transform(). */
  function fetchJsx(name, fetchText) {
    function source(text) { return { text: text, precompiled: false }; }
    function compiled(text) { return { text: text, precompiled: true }; }
    return loadManifest(fetchText).then(function (manifest) {
      var entry = entryFor(manifest, name);
      if (!entry) return fetchText(name).then(source);
      if (manifest.verified === true) {
        return fetchText(entry.output).then(compiled, function (err) {
          console.warn("[precompiled-jsx] " + entry.output + " unavailable; compiling " + name + " in the browser -", (err && err.message) || err);
          return fetchText(name).then(source);
        });
      }
      return Promise.all([
        fetchText(name),
        fetchText(entry.output).catch(function () { return null; }),
      ]).then(function (pair) {
        if (pair[1] !== null && checksum(pair[0]) === entry.checksum) return compiled(pair[1]);
        console.warn("[precompiled-jsx] " + name + (pair[1] === null ? " has no precompiled output" : " changed since it was precompiled") +
          "; compiling it in the browser");
        return source(pair[0]);
      });
    });
  }

  var babelPromise = null;

  function ensureBabel() {
    if (root.Babel && typeof root.Babel.transform === "function") return Promise.resolve(root.Babel);
    if (!babelPromise) {
      babelPromise = new Promise(function (resolve, reject) {
        var script = document.createElement("script");
        script.src = BABEL_SRC;
        script.onload = function () {
          if (root.Babel && typeof root.Babel.transform === "function") resolve(root.Babel);
          else reject(new Error("Babel loaded without a transform"));
        };
        script.onerror = function () {
          if (script.parentNode) script.parentNode.removeChild(script);
          reject(new Error("could not load " + BABEL_SRC));
        };
        document.head.appendChild(script);
      }).catch(function (err) {
        babelPromise = null;
        throw err;
      });
    }
    return babelPromise;
  }

  // In-browser compile; call after ensureBabel() has resolved.
  function transform(name, text) {
    return root.Babel.transform(sourceText(text), babelOptions(name, "inline")).code;
  }

  root.HomePrecompiledJsx = Object.freeze({
    BABEL_VERSION: BABEL_VERSION,
    BABEL_SRC: BABEL_SRC,
    MANIFEST_SCHEMA: MANIFEST_SCHEMA,
    MANIFEST_PATH: MANIFEST_PATH,
    OPTIONS_ID: OPTIONS_ID,
    babelOptions: babelOptions,
    sourceText: sourceText,
    checksum: checksum,
    entryFor: entryFor,
    loadManifest: loadManifest,
    fetchJsx: fetchJsx,
    ensureBabel: ensureBabel,
    transform: transform,
  });
})(typeof window !== "undefined" ? window : globalThis);
