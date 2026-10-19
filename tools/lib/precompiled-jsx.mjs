/* Ahead-of-time JSX compile for Home (CLI: tools/build-app-jsx.mjs).
 *
 * Output lives in app/src/precompiled/ (gitignored): one <name>.js plus
 * source map per app/src/*.jsx, and manifest.json mapping each JSX file to
 * its output and a checksum of the source it was compiled from. The browser
 * loaders use an entry only while that checksum still matches (see
 * app/src/home-precompiled-jsx.js), so a stale build is never wrong, only
 * slower for the files edited since.
 *
 * The compile is the browser's own: the vendored @babel/standalone with the
 * options from home-precompiled-jsx.js, so the executed code is identical to
 * what in-browser Babel produced. Unchanged files are skipped on rebuild.
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
export const APP_SRC = path.join(REPO, "app", "src");
export const OUT_DIR_NAME = "precompiled";

/* Loads the browser runtime into a bare context: the Babel options, manifest
   constants and checksum come from the same file the loaders run. */
export function loadPrecompiledRuntime(srcDir = APP_SRC) {
  const sandbox = { console };
  vm.runInNewContext(fs.readFileSync(path.join(srcDir, "home-precompiled-jsx.js"), "utf8"), sandbox,
    { filename: "home-precompiled-jsx.js" });
  return sandbox.HomePrecompiledJsx;
}

function loadBabel(srcDir, runtime) {
  const file = path.join(srcDir, runtime.BABEL_SRC);
  const mod = { exports: {} };
  new Function("module", "exports", fs.readFileSync(file, "utf8"))(mod, mod.exports);
  const Babel = mod.exports;
  if (typeof Babel?.transform !== "function") throw new Error(`no Babel transform in ${file}`);
  if (Babel.version !== runtime.BABEL_VERSION) {
    throw new Error(`vendored Babel is ${Babel.version}, runtime expects ${runtime.BABEL_VERSION}`);
  }
  return Babel;
}

// The loaders request JSX by bare file name, so only top-level files qualify.
export function listJsx(srcDir = APP_SRC) {
  return fs.readdirSync(srcDir, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(".jsx"))
    .map((entry) => entry.name)
    .sort();
}

function outputName(name) {
  return `${OUT_DIR_NAME}/${name.replace(/\.jsx$/, ".js")}`;
}

function readManifest(file) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return null; }
}

/* Executed code is followed by the loader's `//# sourceURL=<name>.jsx`, so
   DevTools resolves this map URL against the JSX file's own location. */
export function compileJsx(Babel, runtime, name, source) {
  // JSON round trip: plain objects from this realm, not the runtime's vm context.
  const options = JSON.parse(JSON.stringify(runtime.babelOptions(name, true)));
  const result = Babel.transform(runtime.sourceText(source), options);
  const map = { ...result.map, sources: result.map.sources.map((s) => `../${s}`) };
  return {
    code: `${result.code}\n//# sourceMappingURL=${outputName(name)}.map\n`,
    map: `${JSON.stringify(map)}\n`,
  };
}

/* Returns { manifest, compiled, reused, failed, removed }. A file that fails
   to compile is left out of the manifest (the browser then reports the same
   error at runtime, as before) and makes the build exit non-zero. */
export function buildPrecompiledJsx({ srcDir = APP_SRC, outDir = path.join(srcDir, OUT_DIR_NAME), log = () => {} } = {}) {
  const runtime = loadPrecompiledRuntime(srcDir);
  const manifestFile = path.join(outDir, "manifest.json");
  const previous = readManifest(manifestFile);
  const reusable = previous?.schema === runtime.MANIFEST_SCHEMA && previous.babel === runtime.BABEL_VERSION &&
    previous.options === runtime.OPTIONS_ID ? previous.files || {} : {};
  let Babel = null;
  const files = {};
  const summary = { compiled: [], reused: [], failed: [], removed: [] };
  fs.mkdirSync(outDir, { recursive: true });

  for (const name of listJsx(srcDir)) {
    const source = fs.readFileSync(path.join(srcDir, name), "utf8");
    const checksum = runtime.checksum(source);
    const output = outputName(name);
    const outFile = path.join(outDir, path.basename(output));
    if (reusable[name]?.checksum === checksum && reusable[name].output === output &&
        fs.existsSync(outFile) && fs.existsSync(`${outFile}.map`)) {
      files[name] = { output, checksum };
      summary.reused.push(name);
      continue;
    }
    Babel ||= loadBabel(srcDir, runtime);
    const started = Date.now();
    try {
      const { code, map } = compileJsx(Babel, runtime, name, source);
      fs.writeFileSync(outFile, code);
      fs.writeFileSync(`${outFile}.map`, map);
      files[name] = { output, checksum };
      summary.compiled.push(name);
      log(`compiled ${name} in ${Date.now() - started} ms`);
    } catch (err) {
      summary.failed.push({ name, error: String(err?.message || err).split("\n")[0] });
      log(`FAILED ${name}: ${summary.failed.at(-1).error}`);
    }
  }

  const keep = new Set(Object.values(files).flatMap(({ output }) => {
    const base = path.basename(output);
    return [base, `${base}.map`];
  }));
  keep.add("manifest.json");
  for (const entry of fs.readdirSync(outDir)) {
    if (keep.has(entry)) continue;
    fs.rmSync(path.join(outDir, entry), { recursive: true, force: true });
    summary.removed.push(entry);
  }

  const manifest = {
    schema: runtime.MANIFEST_SCHEMA,
    babel: runtime.BABEL_VERSION,
    options: runtime.OPTIONS_ID,
    files,
  };
  // Written last and renamed into place: a gateway still serving during a
  // deploy reads either the old manifest or the new one, never half of one.
  // Outputs need no such care, since an entry only names finished files.
  const pending = `${manifestFile}.${process.pid}.tmp`;
  fs.writeFileSync(pending, `${JSON.stringify(manifest, null, 2)}\n`);
  fs.renameSync(pending, manifestFile);
  return { manifest, ...summary };
}

/* Names of JSX files whose precompiled entry is missing or stale. */
export function stalePrecompiledJsx({ srcDir = APP_SRC, outDir = path.join(srcDir, OUT_DIR_NAME) } = {}) {
  const runtime = loadPrecompiledRuntime(srcDir);
  const manifest = readManifest(path.join(outDir, "manifest.json"));
  return listJsx(srcDir).filter((name) => {
    const entry = runtime.entryFor(manifest, name);
    return !entry || entry.checksum !== runtime.checksum(fs.readFileSync(path.join(srcDir, name), "utf8")) ||
      !fs.existsSync(path.join(outDir, path.basename(entry.output)));
  });
}
