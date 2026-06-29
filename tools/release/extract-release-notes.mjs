#!/usr/bin/env node
import { changelogSection, normalizeVersion, positionalArgs, repoRoot } from "./release-lib.mjs";

const root = repoRoot();
const version = normalizeVersion(positionalArgs()[0]);
console.log(changelogSection(root, version));
