#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const repo = path.resolve(__dirname, "..");
const babelFile = path.join(repo, "app", "src", "vendor", "babel-7.29.0", "babel.min.js");
const input = path.join(repo, "app", "src", "home-agent", "panel.jsx");
const output = path.join(repo, "app", "src", "home-agent", "panel.js");
const mod = { exports: {} };
new Function("module", "exports", fs.readFileSync(babelFile, "utf8"))(mod, mod.exports);
const Babel = mod.exports;
if (!Babel?.transform) throw new Error("bundled Babel transform unavailable");
const source = fs.readFileSync(input, "utf8");
const runtime = [
  path.join(repo, "app", "src", "vendor", "react-18.3.1", "react.production.min.js"),
  path.join(repo, "app", "src", "vendor", "react-18.3.1", "react-dom.production.min.js"),
].map((file) => fs.readFileSync(file, "utf8")).join(";\n");
const transformed = Babel.transform(source, {
  presets: ["react"],
  filename: input,
  sourceType: "script",
  comments: false,
  compact: false,
}).code;
fs.writeFileSync(output, `/* Generated local-only Agent bundle. */\n${runtime};\n${transformed}\n`, "utf8");
process.stdout.write(`built ${path.relative(repo, output)}\n`);
