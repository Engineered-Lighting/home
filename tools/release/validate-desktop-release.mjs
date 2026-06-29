#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import {
  changelogSection,
  currentVersions,
  normalizeVersion,
  positionalArgs,
  repoRoot,
  tagFor,
  unreleasedFragmentFiles,
} from "./release-lib.mjs";

const root = repoRoot();
const version = normalizeVersion(positionalArgs()[0]);
const tag = tagFor(version);
const versions = currentVersions(root);

for (const [name, value] of Object.entries(versions)) {
  if (value !== version) throw new Error(`${name} version is ${value || "(missing)"}, expected ${version}`);
}

changelogSection(root, version);

const fragments = unreleasedFragmentFiles(root);
if (fragments.length) {
  throw new Error(`Unreleased fragments remain after ${tag}: ${fragments.join(", ")}`);
}

const existingTag = execFileSync("git", ["tag", "--list", tag], { encoding: "utf8" }).trim();
if (existingTag) throw new Error(`Git tag already exists: ${tag}`);

console.log(`${tag} is ready to tag.`);
