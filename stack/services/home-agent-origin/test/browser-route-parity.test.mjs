// The origin and the BFF each keep their own browser-route allowlist. The
// duplication is deliberate -- the origin must never widen what it forwards
// just because the BFF gained a capability -- but it means the two lists can
// silently drift apart in the narrowing direction, which is a capability
// outage rather than a security hole and so fails quietly.
//
// That is not hypothetical. The BFF served the three parent-relationship
// routes while the origin's allowlist never gained them, so every browser
// request for the parent-relationship ceremony returned 404 route_not_allowed.
// The panel, unable to read its own state, rendered a fail-closed
// "contained / unavailable" card, which reads as a broken deployment rather
// than a missing allowlist entry.
//
// Parse both tables from source rather than importing the BFF: the origin's
// test process has no business evaluating the BFF module, and a textual
// comparison is exactly the invariant we want -- the two literals must agree.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { browserApiRouteAllowed } from "../src/origin.mjs";

// browserApiRouteAllowed takes a parsed URL, not a string: a bare string is
// rejected outright, which would make every assertion below pass vacuously.
const PUBLIC_ORIGIN = "https://agent.test:8443";
const at = (target) => new URL(target, PUBLIC_ORIGIN);

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ORIGIN_SOURCE = path.join(HERE, "..", "src", "origin.mjs");
const BFF_SOURCE = path.join(
  HERE, "..", "..", "home-agent-bff", "src", "bff.mjs",
);

function routeTable(file, name) {
  const source = fs.readFileSync(file, "utf8");
  const block = new RegExp(
    `const ${name} = Object\\.freeze\\(\\[([\\s\\S]*?)\\]\\);`,
  ).exec(source);
  assert.ok(block, `${name} not found in ${file}`);
  const entries = [];
  for (const line of block[1].split("\n")) {
    const entry = /^\s*\["([A-Z]+)",\s*(.+?)\],?\s*$/.exec(line.trim());
    if (entry) entries.push(`${entry[1]} ${entry[2]}`);
  }
  assert.ok(entries.length > 0, `${name} parsed empty in ${file}`);
  return entries;
}

test("the origin forwards every browser route the BFF serves", () => {
  const origin = new Set(routeTable(ORIGIN_SOURCE, "BROWSER_API_ROUTES"));
  const bff = routeTable(BFF_SOURCE, "ROUTES");
  const missing = bff.filter((route) => !origin.has(route));
  assert.deepEqual(
    missing,
    [],
    "the BFF serves browser routes the origin will not forward, so they are " +
      "unreachable from the panel; add them to BROWSER_API_ROUTES",
  );
});

test("the parent-relationship ceremony is reachable through the origin", () => {
  // The concrete regression: without these the step 34 ceremony cannot be
  // performed from the browser at all.
  assert.equal(
    browserApiRouteAllowed("GET", at("/api/agent/v1/parent-relationship-proposal")),
    true,
  );
  assert.equal(
    browserApiRouteAllowed("POST", at("/api/agent/v1/parent-relationship-proposal")),
    true,
  );
  assert.equal(
    browserApiRouteAllowed(
      "POST", at("/api/agent/v1/parent-relationship-proposal/confirm"),
    ),
    true,
  );
});

test("the origin still refuses methods and paths outside the allowlist", () => {
  assert.equal(
    browserApiRouteAllowed("DELETE", at("/api/agent/v1/parent-relationship-proposal")),
    false,
  );
  assert.equal(
    browserApiRouteAllowed("GET", at("/api/agent/v1/parent-relationship-proposal/confirm")),
    false,
  );
  assert.equal(
    browserApiRouteAllowed("POST", at("/api/agent/v1/operator/principal-binding-proposals")),
    false,
  );
  // A query string still disqualifies these routes, so a cache-buster appended
  // in frustration presents as a routing outage rather than a widened surface.
  assert.equal(
    browserApiRouteAllowed("GET", at("/api/agent/v1/parent-relationship-proposal?x=1")),
    false,
  );
});
