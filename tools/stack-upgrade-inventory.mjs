#!/usr/bin/env node
"use strict";

import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const jsonMode = process.argv.includes("--json");

function read(rel) {
  try {
    return fs.readFileSync(path.join(repo, rel), "utf8");
  } catch {
    return "";
  }
}

function firstMatch(text, regex) {
  const m = text.match(regex);
  return m ? m[1].trim() : null;
}

function allMatches(text, regex) {
  const out = [];
  let m;
  while ((m = regex.exec(text))) out.push(m[1].trim());
  return out;
}

function run(cmd, args, opts = {}) {
  try {
    return execFileSync(cmd, args, {
      cwd: repo,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      timeout: opts.timeoutMs || 20000,
    }).trim();
  } catch (err) {
    return null;
  }
}

function parseJson(rel) {
  try {
    return JSON.parse(read(rel));
  } catch {
    return null;
  }
}

const compose = read("stack/docker-compose.yml");
const rootPackage = parseJson("package.json");
const home2Package = parseJson("apps/home-2/package.json");
const lock = parseJson("package-lock.json");

const composeVllmModel = firstMatch(compose, /-\s*--model\s*\r?\n\s*-\s*([^\r\n#]+)/);
const servedModelName = firstMatch(compose, /-\s*--served-model-name\s*\r?\n\s*-\s*([^\r\n#]+)/);
const maxModelLen = firstMatch(compose, /-\s*--max-model-len\s*\r?\n\s*-\s*"?([^"\r\n#]+)"?/);
const vllmImage = firstMatch(compose, /vllm:[\s\S]*?image:\s*([^\r\n]+)/);
const composeHeaderModel = firstMatch(compose, /#\s*LLM:\s*([^\r\n]+)/);

const services = allMatches(compose, /^ {2}([a-zA-Z0-9_-]+):\r?$/gm);
const envDefaults = {};
for (const key of [
  "VISION_MODEL",
  "INTELLIGENCE_QWEN_MODEL",
  "BRIDGE_VLLM_MODEL",
  "TTS_ENGINE",
  "TTS_FALLBACK",
  "TTS_MODEL",
  "TTS_VOICE",
  "MOSHI_MODEL",
  "DIRECT_DISPATCH",
]) {
  const re = new RegExp(`${key}[^\\n]*\\$\\{[^:}]+:-([^}]+)\\}`);
  envDefaults[key] = firstMatch(compose, re);
}

const packageVersions = {
  root: rootPackage ? {
    version: rootPackage.version,
    scripts: Object.keys(rootPackage.scripts || {}).filter((s) => /llm|test|web|stack|upgrade|inventory/.test(s)).sort(),
  } : null,
  home2: home2Package ? {
    dependencies: home2Package.dependencies || {},
    devDependencies: home2Package.devDependencies || {},
  } : null,
  lockfile: lock ? {
    lockfileVersion: lock.lockfileVersion,
    packages: {
      react: lock.packages?.["node_modules/react"]?.version || null,
      vite: lock.packages?.["node_modules/vite"]?.version || null,
      three: lock.packages?.["node_modules/three"]?.version || null,
      vitest: lock.packages?.["node_modules/vitest"]?.version || null,
      lucideReact: lock.packages?.["node_modules/lucide-react"]?.version || null,
    },
  } : null,
};

const dockerfiles = {
  parakeet: read("stack/services/stt/Dockerfile.parakeet"),
  kokoro: read("stack/services/tts/Dockerfile.kokoro-blackwell"),
};

const runtimePins = {
  parakeetBase: firstMatch(dockerfiles.parakeet, /^FROM\s+(.+)$/m),
  parakeetTorch: firstMatch(dockerfiles.parakeet, /torch==([0-9.]+)/),
  parakeetTorchaudio: firstMatch(dockerfiles.parakeet, /torchaudio==([0-9.]+)/),
  kokoroBase: firstMatch(dockerfiles.kokoro, /^FROM\s+(.+)$/m),
  kokoroTorch: firstMatch(dockerfiles.kokoro, /torch==([0-9.]+)/),
  kokoroTorchaudio: firstMatch(dockerfiles.kokoro, /torchaudio==([0-9.]+)/),
};

const warnings = [];
if (composeHeaderModel && composeVllmModel && !composeHeaderModel.includes(composeVllmModel) && !composeHeaderModel.includes(servedModelName || "")) {
  warnings.push({
    id: "compose-comment-model-mismatch",
    message: `Compose header says "${composeHeaderModel}", but vLLM command uses "${composeVllmModel}" served as "${servedModelName}".`,
  });
}
if (/latest\b/.test(vllmImage || "")) {
  warnings.push({
    id: "unpinned-vllm-image",
    message: `vLLM image is "${vllmImage}", so upstream image changes can alter behavior without a repo diff.`,
  });
}
if (/latest\b/.test(compose)) {
  warnings.push({
    id: "compose-latest-tags",
    message: "Compose contains one or more :latest image tags; candidate testing should pin exact image digests/tags.",
  });
}

let npmOutdated = null;
if (process.argv.includes("--npm-outdated")) {
  const raw = run("npm", ["outdated", "--json"], { timeoutMs: 30000 });
  if (raw) {
    try {
      npmOutdated = JSON.parse(raw);
    } catch {
      npmOutdated = { parseError: true, raw };
    }
  }
}

const report = {
  generatedAt: new Date().toISOString(),
  repo,
  productionRule: "Do not make disruptive production upgrades while traveling unless recovering a blocker.",
  vllm: {
    composeHeaderModel,
    image: vllmImage,
    model: composeVllmModel,
    servedModelName,
    maxModelLen,
  },
  services,
  envDefaults,
  runtimePins,
  packageVersions,
  npmOutdated,
  warnings,
  nextSteps: [
    "Run candidate benchmarks in staging profiles before any production change.",
    "Do not combine CUDA/PyTorch changes with model changes.",
    "Record rollback commands for each subsystem before promotion.",
  ],
};

if (jsonMode) {
  process.stdout.write(JSON.stringify(report, null, 2) + "\n");
} else {
  process.stdout.write("stack upgrade inventory\n");
  process.stdout.write("=======================\n\n");
  process.stdout.write(`repo: ${report.repo}\n`);
  process.stdout.write(`production rule: ${report.productionRule}\n\n`);
  process.stdout.write("vllm\n");
  process.stdout.write(`  image: ${report.vllm.image || "unknown"}\n`);
  process.stdout.write(`  compose comment: ${report.vllm.composeHeaderModel || "unknown"}\n`);
  process.stdout.write(`  command model: ${report.vllm.model || "unknown"}\n`);
  process.stdout.write(`  served model: ${report.vllm.servedModelName || "unknown"}\n`);
  process.stdout.write(`  max model len: ${report.vllm.maxModelLen || "unknown"}\n\n`);
  process.stdout.write("runtime pins\n");
  for (const [key, value] of Object.entries(report.runtimePins)) {
    process.stdout.write(`  ${key}: ${value || "unknown"}\n`);
  }
  process.stdout.write("\nfrontend lockfile\n");
  for (const [key, value] of Object.entries(report.packageVersions.lockfile?.packages || {})) {
    process.stdout.write(`  ${key}: ${value || "unknown"}\n`);
  }
  if (warnings.length) {
    process.stdout.write("\nwarnings\n");
    for (const warning of warnings) {
      process.stdout.write(`  - ${warning.id}: ${warning.message}\n`);
    }
  }
  process.stdout.write("\n");
}
