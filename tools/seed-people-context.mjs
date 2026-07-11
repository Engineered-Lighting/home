#!/usr/bin/env node
/*
 * Seed relationship context for Frigate face buckets that were imported
 * from Marcelo's trusted Drive folder. This updates the Home identity store;
 * it does not upload images or change Frigate face crops.
 *
 * Usage:
 *   HA_URL=https://home-app.taild52a15.ts.net/proxy/ha HA_TOKEN=... \
 *     node tools/seed-people-context.mjs --apply
 *
 * Defaults to dry-run. The token is never printed.
 */

const PEOPLE_CONTEXT = {
  ashley: {
    display_name: "Ashley",
    relationship_type: "family_extended",
    relationship_subrole: "sister-in-law",
    notes: "Felipe's wife. Imported from the trusted Drive face seed folder.",
  },
  aurelio: {
    display_name: "Aurelio",
    relationship_type: "family_immediate",
    relationship_subrole: "nephew",
    notes: "Felipe's son. Imported from the trusted Drive face seed folder.",
  },
  ben: {
    display_name: "Ben",
    relationship_type: "family_immediate",
    relationship_subrole: "stepson",
    notes: "Holly's son. Imported from the trusted Drive face seed folder.",
  },
  peter: {
    display_name: "Peter",
    relationship_type: "family_immediate",
    relationship_subrole: "stepson",
    notes: "Holly's son. Imported from the trusted Drive face seed folder.",
  },
  felipe: {
    display_name: "Felipe",
    relationship_type: "family_immediate",
    relationship_subrole: "brother",
    notes: "Marcelo's brother. Imported from the trusted Drive face seed folder.",
  },
  holly: {
    display_name: "Holly",
    relationship_type: "partner",
    relationship_subrole: "girlfriend",
    notes: "Marcelo's girlfriend. Imported from the trusted Drive face seed folder.",
  },
  mark: {
    display_name: "Mark",
    relationship_type: "friend",
    relationship_subrole: "friend",
    notes: "Marcelo's friend. Imported from the trusted Drive face seed folder.",
  },
  pat: {
    display_name: "Pat",
    relationship_type: "friend",
    relationship_subrole: "friend",
    notes: "Marcelo's friend. Imported from the trusted Drive face seed folder.",
  },
};

const RELATIONSHIP_CONTEXT = [
  ["holly", "ben", "parent"],
  ["holly", "peter", "parent"],
  ["felipe", "ashley", "partner"],
  ["ashley", "felipe", "partner"],
  ["felipe", "aurelio", "parent"],
  ["ashley", "aurelio", "parent"],
];

const args = new Set(process.argv.slice(2));
const apply = args.has("--apply");
const haUrl = (process.env.HA_URL || "https://home-app.taild52a15.ts.net/proxy/ha").replace(/\/+$/, "");
const token = process.env.HA_TOKEN || process.env.HOME_ASSISTANT_TOKEN || "";

function usage(message) {
  if (message) console.error(message);
  console.error("Usage: HA_TOKEN=<token> node tools/seed-people-context.mjs [--apply]");
  process.exit(message ? 1 : 0);
}

if (args.has("--help") || args.has("-h")) usage();
if (!token) usage("HA_TOKEN or HOME_ASSISTANT_TOKEN is required.");

function identityNames(identity) {
  const out = new Set();
  if (identity?.display_name) out.add(String(identity.display_name).trim().toLowerCase());
  for (const alias of identity?.aliases || []) {
    if (alias?.alias) out.add(String(alias.alias).trim().toLowerCase());
  }
  return out;
}

function patchFor(identity, desired) {
  const patch = {};
  for (const key of ["display_name", "relationship_type", "relationship_subrole", "notes"]) {
    if ((identity?.[key] || null) !== (desired[key] || null)) {
      patch[key] = desired[key] || null;
    }
  }
  return patch;
}

async function api(path, options = {}) {
  const response = await fetch(`${haUrl}${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    cache: "no-store",
  });
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} ${response.statusText}: ${typeof body === "string" ? body : JSON.stringify(body)}`);
  }
  return body;
}

const identitiesPayload = await api("/api/extended_openai_conversation/identities");
const identities = identitiesPayload.identities || [];
const byName = new Map();
for (const identity of identities) {
  for (const name of identityNames(identity)) byName.set(name, identity);
}

const results = [];
const identityBySeedName = new Map();
for (const [frigateName, desired] of Object.entries(PEOPLE_CONTEXT)) {
  const identity = byName.get(frigateName);
  if (!identity) {
    results.push({ frigateName, status: "missing_identity" });
    continue;
  }
  identityBySeedName.set(frigateName, identity);
  const patch = patchFor(identity, desired);
  if (Object.keys(patch).length === 0) {
    results.push({ frigateName, displayName: identity.display_name, status: "unchanged" });
    continue;
  }
  if (!apply) {
    results.push({ frigateName, displayName: identity.display_name, status: "would_update", patch });
    continue;
  }
  await api(`/api/extended_openai_conversation/identity/${encodeURIComponent(identity.uuid)}`, {
    method: "PATCH",
    body: JSON.stringify({
      ...patch,
      expected_version: identity.version,
      actor: "seed_people_context",
    }),
  });
  results.push({ frigateName, displayName: desired.display_name, status: "updated", patch });
}

const relationshipResults = [];
for (const [fromName, toName, relType] of RELATIONSHIP_CONTEXT) {
  const from = identityBySeedName.get(fromName) || byName.get(fromName);
  const to = identityBySeedName.get(toName) || byName.get(toName);
  if (!from || !to) {
    relationshipResults.push({ fromName, toName, relType, status: "missing_identity" });
    continue;
  }
  const existing = await api(`/api/extended_openai_conversation/identity/${encodeURIComponent(from.uuid)}`);
  const already = (existing.relationships || []).some((r) =>
    r.to_uuid === to.uuid && r.rel_type === relType && r.status !== "ended"
  );
  if (already) {
    relationshipResults.push({ fromName, toName, relType, status: "unchanged" });
    continue;
  }
  if (!apply) {
    relationshipResults.push({ fromName, toName, relType, status: "would_create" });
    continue;
  }
  try {
    await api(`/api/extended_openai_conversation/identity/${encodeURIComponent(from.uuid)}/relationships`, {
      method: "POST",
      body: JSON.stringify({
        to_uuid: to.uuid,
        rel_type: relType,
        edge_data: { source: "seed_people_context" },
        actor: "seed_people_context",
      }),
    });
    relationshipResults.push({ fromName, toName, relType, status: "created" });
  } catch (error) {
    const message = String(error?.message || error);
    if (message.includes("UNIQUE") || message.includes("constraint")) {
      relationshipResults.push({ fromName, toName, relType, status: "unchanged" });
    } else {
      throw error;
    }
  }
}

console.log(JSON.stringify({
  applied: apply,
  identityCount: identities.length,
  results,
  relationshipResults,
}, null, 2));
