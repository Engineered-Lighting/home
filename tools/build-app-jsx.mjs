#!/usr/bin/env node
/* ============================================================================
 * build-app-jsx.mjs — compile Home's JSX ahead of time.
 *
 * Writes app/src/precompiled/ (gitignored) so the browser stops compiling
 * JSX on every load; see tools/lib/precompiled-jsx.mjs for the format and
 * app/src/home-precompiled-jsx.js for how the loaders trust it.
 *
 * Usage:
 *   node tools/build-app-jsx.mjs           build or refresh app/src/precompiled
 *   node tools/build-app-jsx.mjs --check   exit 1 unless every JSX file is fresh
 *   node tools/build-app-jsx.mjs --clean   remove app/src/precompiled
 *
 * Run by tools/deploy-home-web.sh (web gateway) and by Tauri's
 * beforeBuildCommand (desktop installer). Optional for local dev.
 * ========================================================================= */
import path from "node:path";
import fs from "node:fs";
import { APP_SRC, OUT_DIR_NAME, buildPrecompiledJsx, stalePrecompiledJsx } from "./lib/precompiled-jsx.mjs";

const REPO = path.resolve(APP_SRC, "../..");

function main(argv) {
  const mode = argv[0] || "build";
  if (argv.length > 1 || !["build", "--check", "--clean"].includes(mode)) {
    process.stderr.write("usage: node tools/build-app-jsx.mjs [--check | --clean]\n");
    return 2;
  }
  const outDir = path.join(APP_SRC, OUT_DIR_NAME);
  const relOut = path.relative(REPO, outDir).split(path.sep).join("/");
  if (mode === "--clean") {
    fs.rmSync(outDir, { recursive: true, force: true });
    process.stdout.write(`removed ${relOut}\n`);
    return 0;
  }
  if (mode === "--check") {
    const stale = stalePrecompiledJsx();
    if (stale.length) {
      process.stderr.write(`${relOut} is stale for ${stale.length} file(s): ${stale.join(", ")}; ` +
        "run `node tools/build-app-jsx.mjs`\n");
      return 1;
    }
    process.stdout.write(`checked ${relOut}: every JSX file is precompiled and fresh\n`);
    return 0;
  }
  const started = Date.now();
  const result = buildPrecompiledJsx({ log: (line) => process.stdout.write(`  ${line}\n`) });
  process.stdout.write(`built ${relOut}: ${result.compiled.length} compiled, ${result.reused.length} unchanged, ` +
    `${result.failed.length} failed, ${result.removed.length} removed (${Date.now() - started} ms)\n`);
  if (result.failed.length) {
    process.stderr.write(`could not precompile ${result.failed.map((f) => f.name).join(", ")}; ` +
      "those files keep compiling in the browser\n");
    return 1;
  }
  return 0;
}

try {
  process.exitCode = main(process.argv.slice(2));
} catch (error) {
  process.stderr.write(`JSX precompile failed: ${error?.message || String(error)}\n`);
  process.exitCode = 1;
}
