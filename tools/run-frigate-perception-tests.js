#!/usr/bin/env node
"use strict";

const path = require("path");
const api = require(path.join("..", "app", "src", "home-frigate-perception.js"));

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, condition, detail) {
  if (condition) {
    passes += 1;
    process.stdout.write(`  PASS  ${name}\n`);
  } else {
    fails += 1;
    failures.push({ name, detail });
    process.stdout.write(`  FAIL  ${name}`);
    if (detail !== undefined) {
      const dumped = typeof detail === "string" ? detail : JSON.stringify(detail, null, 2);
      process.stdout.write(`\n        ${dumped.replace(/\n/g, "\n        ")}`);
    }
    process.stdout.write("\n");
  }
}

const NOW = Date.parse("2026-07-10T18:00:00.000Z");

process.stdout.write("\nfrigate_perception_normalizer_tests\n");

const reviewPayload = {
  topic: "frigate/reviews",
  received_at: "2026-07-10T18:00:01.000Z",
  payload_json: {
    type: "review",
    review_id: "review-1",
    camera: "living_room",
    label: "person",
    start_time: NOW / 1000,
    zones: ["sofa"],
    event_ids: ["evt-a"],
    thumbnail_url: "/api/frigate/notifications/evt-a/thumbnail.jpg",
    summary: "living room: possible person near sofa",
  },
};
const review = api.normalizeHomePerceptionEvent(reviewPayload, { nowMs: NOW });
assert("normalizes frigate/reviews payload", review && review.source === "frigate_review" && review.camera === "living_room", review);
assert("review event has required fresh window", review.fresh_until === "2026-07-10T18:01:30.000Z", review);
assert("indoor identity is allowed", review.identity_allowed === true, review);
assert("thumbnail URL is preserved", review.urls.thumbnail.includes("thumbnail"), review);

const eventPayload = {
  topic: "frigate/events",
  payload_json: {
    type: "update",
    after: {
      id: "evt-driveway-1",
      camera: "driveway",
      label: "person",
      sub_label: "delivery",
      score: 0.91,
      current_zones: ["front_walk"],
      attributes: { semantic_trigger: "delivery person" },
      start_time: NOW / 1000,
      snapshot_url: "/api/events/evt-driveway-1/snapshot.jpg",
    },
  },
};
const driveway = api.normalizeHomePerceptionEvent(eventPayload, { nowMs: NOW });
assert("semantic event source is detected", driveway.source === "semantic_trigger", driveway);
assert("driveway identity is never allowed", driveway.identity_allowed === false, driveway);
assert("semantic hint is preserved", driveway.semantic_hint === "delivery person", driveway);
assert("confidence is numeric", driveway.confidence === 0.91, driveway);

const malformed = api.normalizeHomePerceptionEvent({ topic: "frigate/events", payload: "{bad json" }, { nowMs: NOW });
assert("malformed payload without camera is ignored", malformed === null, malformed);

process.stdout.write("\nfrigate_perception_dedupe_tests\n");
const feedEvent = api.toPerceptionFeedEvent(review, { nextId: () => "feed-1", fmtTime: () => "10:00:00" });
assert("feed event is passive perception only", feedEvent.kind === "perception" && feedEvent.channel === "perception" && !feedEvent.streaming, feedEvent);
assert("feed event does not look like assistant text", feedEvent.kind !== "home" && feedEvent.kind !== "external", feedEvent);
assert("duplicate review is detected by perception key", api.isDuplicatePerceptionEvent([feedEvent], review, { nowMs: NOW }), feedEvent);
const distinct = api.normalizeHomePerceptionEvent({ topic: "frigate/reviews", payload_json: { review_id: "review-2", camera: "kitchen", label: "person" } }, { nowMs: NOW });
assert("distinct camera/event is not deduped", !api.isDuplicatePerceptionEvent([feedEvent], distinct, { nowMs: NOW }), distinct);
const derivedThumb = api.toPerceptionFeedEvent(
  { ...driveway, urls: undefined },
  { nextId: () => "feed-2", fmtTime: () => "10:00:01", frigateBase: "/proxy/frigate" },
);
assert("Frigate event id derives thumbnail URL for perception card", derivedThumb.snapshotUrl.includes("/proxy/frigate/api/events/evt-driveway-1/thumbnail.jpg"), derivedThumb);

process.stdout.write("\nfrigate_perception_freshness_tests\n");
assert("fresh hints include current events", api.freshPerceptionHints([feedEvent], NOW + 10_000).length === 1);
assert("stale hints are excluded", api.freshPerceptionHints([feedEvent], NOW + 180_000).length === 0);

process.stdout.write("\nfrigate_live_shape_contract_tests\n");
const liveApiShape = {
  topic: "frigate/events",
  payload_json: {
    after: {
      id: "1783707710.910376-ti0bxy",
      camera: "driveway",
      label: "person",
      zones: ["e28"],
      start_time: NOW / 1000,
      has_snapshot: true,
      data: {
        score: 0.796875,
        top_score: 0.83203125,
        attributes: [],
      },
    },
  },
};
const liveNormalized = api.normalizeHomePerceptionEvent(liveApiShape, { nowMs: NOW });
assert("live Frigate API-style zones are preserved", liveNormalized.zones?.[0] === "e28", liveNormalized);
assert("live Frigate API-style nested score is preserved", liveNormalized.confidence === 0.796875, liveNormalized);
assert("live driveway person remains identity-disallowed", liveNormalized.identity_allowed === false, liveNormalized);

if (fails) {
  console.log("\nFailures:");
  for (const f of failures) console.log("- " + f.name + (f.detail ? `: ${JSON.stringify(f.detail)}` : ""));
}
console.log(`\n${passes} pass . ${fails} fail`);
process.exit(fails ? 1 : 0);
