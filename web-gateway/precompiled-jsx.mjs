/* Verified precompiled-JSX manifest for the web gateway.
 *
 * tools/build-app-jsx.mjs writes app/src/precompiled/manifest.json. Served
 * as a plain file, browsers treat it as a hint and still download every JSX
 * source to check it (app/src/home-precompiled-jsx.js). Here the gateway does
 * that check against the files on disk instead, on every manifest request,
 * keeps only entries whose source still matches and whose output exists, and
 * marks the result `verified` so browsers fetch the compiled file alone.
 *
 * Source checksums are cached by mtime and size, so a request costs a few
 * stat calls; an edited file is re-read once and then dropped from the
 * manifest until the build is rerun.
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const SOURCE_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*\.jsx$/;
const OUTPUT_NAME = /^precompiled\/[A-Za-z0-9][A-Za-z0-9._-]*\.js$/;

function loadRuntime(appDir) {
  const sandbox = { console };
  vm.runInNewContext(fs.readFileSync(path.join(appDir, "home-precompiled-jsx.js"), "utf8"), sandbox,
    { filename: "home-precompiled-jsx.js" });
  return sandbox.HomePrecompiledJsx;
}

function statFile(file) {
  try {
    const stat = fs.statSync(file);
    return stat.isFile() ? stat : null;
  } catch {
    return null;
  }
}

/* precompiledDir defaults to the build's own output, appDir/precompiled. */
export function createPrecompiledManifestVerifier(appDir, precompiledDir = path.join(appDir, "precompiled")) {
  let runtime = null;
  const checksums = new Map();

  function sourceChecksum(file) {
    const stat = statFile(file);
    if (!stat) return null;
    const cached = checksums.get(file);
    if (cached && cached.mtimeMs === stat.mtimeMs && cached.size === stat.size) return cached.checksum;
    const checksum = runtime.checksum(fs.readFileSync(file, "utf8"));
    checksums.set(file, { mtimeMs: stat.mtimeMs, size: stat.size, checksum });
    return checksum;
  }

  /* The manifest to serve, or null when there is nothing usable to serve. */
  function verifiedManifest() {
    let manifest;
    try {
      manifest = JSON.parse(fs.readFileSync(path.join(precompiledDir, "manifest.json"), "utf8"));
      runtime ||= loadRuntime(appDir);
    } catch {
      return null;
    }
    if (manifest?.schema !== runtime.MANIFEST_SCHEMA || manifest.babel !== runtime.BABEL_VERSION ||
        manifest.options !== runtime.OPTIONS_ID || !manifest.files || typeof manifest.files !== "object") {
      return null;
    }
    const files = {};
    for (const name of Object.keys(manifest.files)) {
      const entry = runtime.entryFor(manifest, name);
      if (!entry || !SOURCE_NAME.test(name) || !OUTPUT_NAME.test(entry.output)) continue;
      if (!statFile(path.join(precompiledDir, entry.output.slice("precompiled/".length)))) continue;
      if (sourceChecksum(path.join(appDir, name)) !== entry.checksum) continue;
      files[name] = { output: entry.output, checksum: entry.checksum };
    }
    return {
      schema: manifest.schema,
      babel: manifest.babel,
      options: manifest.options,
      verified: true,
      files,
    };
  }

  /* One-line startup summary, e.g. "37 of 37 JSX files precompiled". */
  function describe() {
    const manifest = verifiedManifest();
    const total = fs.readdirSync(appDir).filter((name) => name.endsWith(".jsx")).length;
    if (!manifest) return `no precompiled JSX (${total} files compile in the browser; run node tools/build-app-jsx.mjs)`;
    const fresh = Object.keys(manifest.files).length;
    return `${fresh} of ${total} JSX files precompiled` +
      (fresh < total ? " (the rest compile in the browser; run node tools/build-app-jsx.mjs)" : "");
  }

  return { verifiedManifest, describe };
}
