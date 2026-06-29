#!/usr/bin/env node
import { gitChangedFiles, parseFragment, repoRoot, unreleasedFragmentFiles } from "./release-lib.mjs";

const root = repoRoot();
const base = process.env.CHANGE_NOTE_BASE || "origin/main";
const noReleaseNote = String(process.env.NO_RELEASE_NOTE || "").toLowerCase() === "true";

const changed = (process.env.CHANGE_NOTE_FILES || "")
  .split(/\r?\n/)
  .map((file) => file.trim())
  .filter(Boolean);
const changedFiles = changed.length ? changed : gitChangedFiles(base);

const fragmentFiles = unreleasedFragmentFiles(root);
for (const file of fragmentFiles) parseFragment(file);

const changedFragments = changedFiles.filter((file) => /^changes\/unreleased\/[^/]+\.md$/.test(file));
const archivedFragments = changedFiles.filter((file) => /^changes\/archive\/v[^/]+\/[^/]+\.md$/.test(file));
const changelogChanged = changedFiles.includes("CHANGELOG.md");
const relevant = changedFiles.filter(isReleaseRelevant);

if (!relevant.length) {
  console.log("No release-relevant files changed.");
  process.exit(0);
}

if (noReleaseNote) {
  console.log("Release note check bypassed by no-release-note label.");
  process.exit(0);
}

if (changedFragments.length) {
  console.log("Release note fragment found.");
  process.exit(0);
}

if (changelogChanged && archivedFragments.length) {
  console.log("Release notes compiled into CHANGELOG.md.");
  process.exit(0);
}

console.error("Release-relevant files changed without a changes/unreleased/*.md fragment:");
for (const file of relevant) console.error(`- ${file}`);
console.error("Add a fragment or apply the no-release-note label with a PR explanation.");
process.exit(1);

function isReleaseRelevant(file) {
  const normalized = file.replace(/\\/g, "/");
  if (normalized.startsWith("changes/")) return false;
  if (normalized.endsWith(".md") && !normalized.startsWith("app/")) return false;
  if (/^(app\/src\/|app\/src-tauri\/|apps\/|web-gateway\/|stack\/)/.test(normalized)) return true;
  if (/^tools\/(deploy|release)\//.test(normalized)) return true;
  if (/^tools\/deploy-/.test(normalized)) return true;
  if (/^\.github\/workflows\/.*\.ya?ml$/.test(normalized)) return true;
  if (["package.json", "package-lock.json"].includes(normalized)) return true;
  return false;
}
