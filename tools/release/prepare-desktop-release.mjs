#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import {
  changelogSection,
  formatChangelogSection,
  normalizeVersion,
  positionalArgs,
  readText,
  readUnreleasedFragments,
  repoRoot,
  tagFor,
  updateVersionFiles,
  writeText,
} from "./release-lib.mjs";

const root = repoRoot();
const version = normalizeVersion(positionalArgs()[0]);
const tag = tagFor(version);
const fragments = readUnreleasedFragments(root);

if (!fragments.length) {
  throw new Error("No changes/unreleased/*.md fragments found for this release.");
}

try {
  changelogSection(root, version);
  throw new Error(`CHANGELOG.md already has a section for ${tag}`);
} catch (err) {
  if (!/has no section/.test(String(err.message))) throw err;
}

updateVersionFiles(root, version);

const changelogFile = path.join(root, "CHANGELOG.md");
const existing = fs.existsSync(changelogFile)
  ? readText(changelogFile).replace(/\r\n/g, "\n")
  : "# Changelog\n\nAll notable changes to this project will be documented here.\n";
const section = formatChangelogSection(version, fragments);
const firstSection = existing.search(/^##\s/m);
const nextChangelog = firstSection < 0
  ? `${existing.trimEnd()}\n\n${section}\n`
  : `${existing.slice(0, firstSection).trimEnd()}\n\n${section}\n\n${existing.slice(firstSection).trimStart()}`;
writeText(changelogFile, nextChangelog);

const archiveDir = path.join(root, "changes", "archive", tag);
fs.mkdirSync(archiveDir, { recursive: true });
for (const fragment of fragments) {
  const dest = path.join(archiveDir, path.basename(fragment.file));
  if (fs.existsSync(dest)) throw new Error(`Archive fragment already exists: ${dest}`);
  fs.renameSync(fragment.file, dest);
}

console.log(`Prepared ${tag} with ${fragments.length} change-note fragment(s).`);
