#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const repo = path.resolve(__dirname, "..");
const babelFile = path.join(repo, "app", "src", "vendor", "babel-7.29.0", "babel.min.js");
const input = path.join(repo, "app", "src", "home-agent", "panel.jsx");
const output = path.join(repo, "app", "src", "home-agent", "panel.js");

function renderBundle() {
  const mod = { exports: {} };
  new Function("module", "exports", fs.readFileSync(babelFile, "utf8"))(
    mod,
    mod.exports,
  );
  const Babel = mod.exports;
  if (!Babel?.transform) throw new Error("bundled Babel transform unavailable");
  const source = fs.readFileSync(input, "utf8");
  const runtime = [
    path.join(
      repo,
      "app",
      "src",
      "vendor",
      "react-18.3.1",
      "react.production.min.js",
    ),
    path.join(
      repo,
      "app",
      "src",
      "vendor",
      "react-18.3.1",
      "react-dom.production.min.js",
    ),
  ]
    .map((file) => fs.readFileSync(file, "utf8"))
    .join(";\n");
  const transformed = Babel.transform(source, {
    presets: ["react"],
    filename: input,
    sourceType: "script",
    comments: false,
    compact: false,
  }).code;
  return `/* Generated local-only Agent bundle. */\n${runtime};\n${transformed}\n`;
}

function main(args) {
  if (
    args.length > 1 ||
    (args.length === 1 && args[0] !== "--check")
  ) {
    process.stderr.write(
      "usage: node tools/build-home-agent-panel.js [--check]\n",
    );
    return 2;
  }

  const expected = renderBundle();
  const relativeOutput = path.relative(repo, output);
  if (args[0] === "--check") {
    let current;
    try {
      const stat = fs.lstatSync(output);
      if (!stat.isFile() || stat.isSymbolicLink()) {
        throw new Error("generated bundle is not a regular file");
      }
      current = fs.readFileSync(output);
    } catch {
      process.stderr.write(
        `${relativeOutput} is missing or invalid; ` +
          "run `node tools/build-home-agent-panel.js`\n",
      );
      return 1;
    }
    if (!current.equals(Buffer.from(expected, "utf8"))) {
      process.stderr.write(
        `${relativeOutput} is stale; ` +
          "run `node tools/build-home-agent-panel.js`\n",
      );
      return 1;
    }
    process.stdout.write(`checked ${relativeOutput}\n`);
    return 0;
  }

  fs.writeFileSync(output, expected, "utf8");
  process.stdout.write(`built ${relativeOutput}\n`);
  return 0;
}

try {
  process.exitCode = main(process.argv.slice(2));
} catch (error) {
  process.stderr.write(
    `home-agent panel build failed: ${error?.message || String(error)}\n`,
  );
  process.exitCode = 1;
}
