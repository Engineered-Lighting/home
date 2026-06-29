import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

export const VALID_TARGETS = new Set(["desktop", "web", "backend", "docs", "internal"]);
export const VALID_TYPES = new Set(["added", "changed", "fixed", "removed"]);
export const TYPE_HEADINGS = {
  added: "Added",
  changed: "Changed",
  fixed: "Fixed",
  removed: "Removed",
};

export function argValue(name, args = process.argv.slice(2)) {
  const prefix = `${name}=`;
  const direct = args.find((arg) => arg.startsWith(prefix));
  if (direct) return direct.slice(prefix.length);
  const idx = args.indexOf(name);
  if (idx >= 0 && idx + 1 < args.length) return args[idx + 1];
  return null;
}

export function repoRoot(args = process.argv.slice(2)) {
  return path.resolve(argValue("--root", args) || process.cwd());
}

export function positionalArgs(args = process.argv.slice(2)) {
  const out = [];
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg.startsWith("--")) {
      if (!arg.includes("=") && i + 1 < args.length && !args[i + 1].startsWith("--")) i += 1;
      continue;
    }
    out.push(arg);
  }
  return out;
}

export function normalizeVersion(input) {
  const raw = String(input || "").trim().replace(/^v/i, "");
  if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(raw)) {
    throw new Error(`version must be SemVer X.Y.Z, got: ${input || "(empty)"}`);
  }
  return raw;
}

export function tagFor(version) {
  return `v${normalizeVersion(version)}`;
}

export function readText(file) {
  return fs.readFileSync(file, "utf8");
}

export function writeText(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text.endsWith("\n") ? text : `${text}\n`, "utf8");
}

export function readJson(file) {
  return JSON.parse(readText(file));
}

export function writeJson(file, data) {
  writeText(file, `${JSON.stringify(data, null, 2)}\n`);
}

export function parseFragment(file) {
  const text = readText(file);
  const match = text.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/);
  if (!match) throw new Error(`${file}: missing YAML-style front matter`);

  const meta = {};
  for (const line of match[1].split(/\r?\n/)) {
    if (!line.trim()) continue;
    const kv = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/);
    if (!kv) throw new Error(`${file}: invalid front matter line: ${line}`);
    meta[kv[1]] = kv[2].trim().replace(/^["']|["']$/g, "");
  }

  const fragment = {
    file,
    title: meta.title || "",
    target: meta.target || "",
    type: meta.type || "",
    description: match[2].trim().replace(/\s+/g, " "),
  };
  validateFragment(fragment);
  return fragment;
}

export function validateFragment(fragment) {
  if (!fragment.title) throw new Error(`${fragment.file}: missing title`);
  if (!VALID_TARGETS.has(fragment.target)) {
    throw new Error(`${fragment.file}: target must be one of ${Array.from(VALID_TARGETS).join(", ")}`);
  }
  if (!VALID_TYPES.has(fragment.type)) {
    throw new Error(`${fragment.file}: type must be one of ${Array.from(VALID_TYPES).join(", ")}`);
  }
  if (!fragment.description) throw new Error(`${fragment.file}: missing description body`);
}

export function unreleasedFragmentFiles(root) {
  const dir = path.join(root, "changes", "unreleased");
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir)
    .filter((name) => name.endsWith(".md"))
    .sort((a, b) => a.localeCompare(b))
    .map((name) => path.join(dir, name));
}

export function readUnreleasedFragments(root) {
  return unreleasedFragmentFiles(root).map(parseFragment);
}

export function formatChangelogSection(version, fragments, date = new Date().toISOString().slice(0, 10)) {
  const normalized = normalizeVersion(version);
  const lines = [`## v${normalized} - ${date}`, ""];
  for (const type of ["added", "changed", "fixed", "removed"]) {
    const group = fragments.filter((fragment) => fragment.type === type);
    if (!group.length) continue;
    lines.push(`### ${TYPE_HEADINGS[type]}`, "");
    for (const fragment of group) {
      lines.push(`- **${fragment.title}** (${fragment.target}) - ${fragment.description}`);
    }
    lines.push("");
  }
  return lines.join("\n").trimEnd();
}

export function changelogSection(root, version) {
  const normalized = normalizeVersion(version);
  const file = path.join(root, "CHANGELOG.md");
  const text = readText(file).replace(/\r\n/g, "\n");
  const escaped = normalized.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const heading = new RegExp(`^##\\s+(?:\\[?v?${escaped}\\]?)(?:\\s+.*)?$`, "m");
  const match = heading.exec(text);
  if (!match) throw new Error(`CHANGELOG.md has no section for v${normalized}`);
  const start = match.index;
  const next = text.slice(start + match[0].length).search(/\n##\s+/);
  return next < 0 ? text.slice(start).trim() : text.slice(start, start + match[0].length + next).trim();
}

export function updateVersionFiles(root, version) {
  const normalized = normalizeVersion(version);

  const packageFile = path.join(root, "package.json");
  const pkg = readJson(packageFile);
  pkg.version = normalized;
  writeJson(packageFile, pkg);

  const tauriFile = path.join(root, "app", "src-tauri", "tauri.conf.json");
  const tauri = readJson(tauriFile);
  tauri.version = normalized;
  writeJson(tauriFile, tauri);

  const cargoToml = path.join(root, "app", "src-tauri", "Cargo.toml");
  const cargoText = readText(cargoToml);
  writeText(cargoToml, replaceCargoPackageVersion(cargoText, normalized, cargoToml));

  const cargoLock = path.join(root, "app", "src-tauri", "Cargo.lock");
  const lockText = readText(cargoLock);
  writeText(cargoLock, replaceCargoPackageVersion(lockText, normalized, cargoLock));
}

function replaceCargoPackageVersion(text, version, file) {
  const replaced = text.replace(/(\[\[?package\]?\]\r?\nname = "home"\r?\nversion = ")[^"]+(")/, `$1${version}$2`);
  if (replaced === text) throw new Error(`${file}: could not find home package version`);
  return replaced;
}

export function currentVersions(root) {
  const pkg = readJson(path.join(root, "package.json")).version;
  const tauri = readJson(path.join(root, "app", "src-tauri", "tauri.conf.json")).version;
  const cargoToml = readText(path.join(root, "app", "src-tauri", "Cargo.toml"))
    .match(/\[package\][\s\S]*?^version = "([^"]+)"/m)?.[1];
  const cargoLock = readText(path.join(root, "app", "src-tauri", "Cargo.lock"))
    .match(/\[\[package\]\]\r?\nname = "home"\r?\nversion = "([^"]+)"/)?.[1];
  return { package: pkg, tauri, cargoToml, cargoLock };
}

export function gitChangedFiles(base) {
  const out = execFileSync("git", ["diff", "--name-only", `${base}...HEAD`], { encoding: "utf8" });
  return out.split(/\r?\n/).filter(Boolean).map((file) => file.replace(/\\/g, "/"));
}
