/* home-3d/world-layer-catalog.js — policy metadata for outer-world layers.
 *
 * Category coverage and lifecycle policy are selectively derived from God's
 * Eye View at pinned revision d8f1742783cddd6bbc86033d0db06dc6ec746304
 * (Copyright (c) 2026 Bilawal Sidhu, MIT). Home does not copy upstream data,
 * provider locations, credentials, or product UI. Third-party source terms
 * remain independent and must pass Home's provenance and license reviews.
 */

export const WORLD_LAYER_CATALOG_SCHEMA = "home.world-layer-catalog.v1";

export const SEMANTIC_ZOOM_BANDS = Object.freeze([
  "parcel-building",
  "city",
  "country",
  "planet",
]);

const DISPOSITIONS = new Set(["build", "defer", "reject"]);
const LICENSE_REVIEW_STATES = new Set(["approved", "not-applicable", "pending", "blocked"]);
const NETWORK_POLICIES = new Set([
  "bundled-offline-only",
  "local-authority-only",
  "first-party-proxy-only",
  "prohibited",
]);
const BUILD_LICENSE_STATES = new Set(["approved", "not-applicable"]);
const ID_PATTERN = /^[a-z0-9][a-z0-9-]{1,63}$/;

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.values(value).forEach(deepFreeze);
  return Object.freeze(value);
}

function layer({
  id,
  category,
  semanticZoomBands,
  defaultEnabled = false,
  sourceFamily,
  provenanceRequirement,
  licenseReview,
  privacyClass,
  networkPolicy,
  disposition,
}) {
  return deepFreeze({
    id,
    category,
    semanticZoomBands: [...semanticZoomBands],
    defaultEnabled,
    sourceFamily,
    provenanceRequirement,
    licenseReview,
    privacyClass,
    networkPolicy,
    disposition,
  });
}

export const WORLD_LAYER_CATALOG = deepFreeze([
  layer({
    id: "site-health",
    category: "home",
    semanticZoomBands: SEMANTIC_ZOOM_BANDS,
    defaultEnabled: true,
    sourceFamily: "home-authoritative-site-registry",
    provenanceRequirement: "authoritative-site-id-and-observed-at",
    licenseReview: "not-applicable",
    privacyClass: "home-sensitive",
    networkPolicy: "local-authority-only",
    disposition: "build",
  }),
  layer({
    id: "basemap",
    category: "foundation",
    semanticZoomBands: SEMANTIC_ZOOM_BANDS,
    sourceFamily: "home-bundled-low-detail-geography",
    provenanceRequirement: "artifact-revision-and-generation-lineage",
    licenseReview: "approved",
    privacyClass: "public",
    networkPolicy: "bundled-offline-only",
    disposition: "build",
  }),
  layer({
    id: "buildings-parcel",
    category: "built-environment",
    semanticZoomBands: ["parcel-building", "city"],
    sourceFamily: "reviewed-building-and-cadastral-records",
    provenanceRequirement: "source-jurisdiction-vintage-and-registration",
    licenseReview: "pending",
    privacyClass: "public-location",
    networkPolicy: "first-party-proxy-only",
    disposition: "defer",
  }),
  layer({
    id: "weather-severe",
    category: "hazards",
    semanticZoomBands: SEMANTIC_ZOOM_BANDS,
    sourceFamily: "public-weather-and-severe-alerts",
    provenanceRequirement: "issuing-authority-issued-at-and-expires-at",
    licenseReview: "pending",
    privacyClass: "public-aggregate",
    networkPolicy: "first-party-proxy-only",
    disposition: "defer",
  }),
  layer({
    id: "air-quality",
    category: "environment",
    semanticZoomBands: ["city", "country", "planet"],
    sourceFamily: "public-environmental-monitoring",
    provenanceRequirement: "station-or-model-lineage-and-observed-at",
    licenseReview: "pending",
    privacyClass: "public-aggregate",
    networkPolicy: "first-party-proxy-only",
    disposition: "defer",
  }),
  layer({
    id: "earthquakes",
    category: "hazards",
    semanticZoomBands: SEMANTIC_ZOOM_BANDS,
    sourceFamily: "public-seismic-events",
    provenanceRequirement: "issuing-authority-event-id-and-revision",
    licenseReview: "pending",
    privacyClass: "public-event",
    networkPolicy: "first-party-proxy-only",
    disposition: "defer",
  }),
  layer({
    id: "wildfire-hotspots",
    category: "hazards",
    semanticZoomBands: SEMANTIC_ZOOM_BANDS,
    sourceFamily: "public-remote-sensing-hotspots",
    provenanceRequirement: "sensor-capture-time-and-confidence",
    licenseReview: "pending",
    privacyClass: "public-event",
    networkPolicy: "first-party-proxy-only",
    disposition: "defer",
  }),
  layer({
    id: "smoke",
    category: "hazards",
    semanticZoomBands: ["city", "country", "planet"],
    sourceFamily: "public-smoke-observation-and-model",
    provenanceRequirement: "observation-or-model-cycle-and-valid-at",
    licenseReview: "pending",
    privacyClass: "public-aggregate",
    networkPolicy: "first-party-proxy-only",
    disposition: "defer",
  }),
  layer({
    id: "flood",
    category: "hazards",
    semanticZoomBands: ["parcel-building", "city", "country"],
    sourceFamily: "public-flood-alert-and-model",
    provenanceRequirement: "issuing-jurisdiction-effective-at-and-uncertainty",
    licenseReview: "pending",
    privacyClass: "public-event",
    networkPolicy: "first-party-proxy-only",
    disposition: "defer",
  }),
  layer({
    id: "road-incidents",
    category: "mobility",
    semanticZoomBands: ["parcel-building", "city", "country"],
    sourceFamily: "public-road-event-reports",
    provenanceRequirement: "reporting-authority-event-id-and-updated-at",
    licenseReview: "pending",
    privacyClass: "public-location",
    networkPolicy: "first-party-proxy-only",
    disposition: "defer",
  }),
  layer({
    id: "traffic",
    category: "mobility",
    semanticZoomBands: ["city", "country"],
    sourceFamily: "aggregated-traffic-conditions",
    provenanceRequirement: "aggregation-lineage-window-and-updated-at",
    licenseReview: "blocked",
    privacyClass: "mobility-sensitive",
    networkPolicy: "prohibited",
    disposition: "reject",
  }),
  layer({
    id: "transit",
    category: "mobility",
    semanticZoomBands: ["parcel-building", "city", "country"],
    sourceFamily: "public-transit-schedule-and-vehicle-status",
    provenanceRequirement: "operator-service-date-and-updated-at",
    licenseReview: "pending",
    privacyClass: "public-location",
    networkPolicy: "first-party-proxy-only",
    disposition: "defer",
  }),
  layer({
    id: "routes",
    category: "mobility",
    semanticZoomBands: ["parcel-building", "city", "country"],
    sourceFamily: "home-user-route-intent",
    provenanceRequirement: "principal-purpose-and-expiry",
    licenseReview: "blocked",
    privacyClass: "home-sensitive",
    networkPolicy: "prohibited",
    disposition: "reject",
  }),
  layer({
    id: "outages",
    category: "infrastructure",
    semanticZoomBands: ["parcel-building", "city", "country"],
    sourceFamily: "home-and-public-utility-status",
    provenanceRequirement: "authority-service-area-and-observed-at",
    licenseReview: "pending",
    privacyClass: "home-sensitive",
    networkPolicy: "first-party-proxy-only",
    disposition: "defer",
  }),
  layer({
    id: "flights",
    category: "movement",
    semanticZoomBands: ["city", "country", "planet"],
    sourceFamily: "aircraft-movement-feed",
    provenanceRequirement: "source-capture-time-and-retention-policy",
    licenseReview: "blocked",
    privacyClass: "movement-sensitive",
    networkPolicy: "prohibited",
    disposition: "reject",
  }),
  layer({
    id: "military",
    category: "sensitive-activity",
    semanticZoomBands: ["country", "planet"],
    sourceFamily: "military-activity-feed",
    provenanceRequirement: "source-lawful-purpose-and-retention-policy",
    licenseReview: "blocked",
    privacyClass: "highly-sensitive",
    networkPolicy: "prohibited",
    disposition: "reject",
  }),
  layer({
    id: "vessels",
    category: "movement",
    semanticZoomBands: ["city", "country", "planet"],
    sourceFamily: "vessel-movement-feed",
    provenanceRequirement: "source-capture-time-and-retention-policy",
    licenseReview: "blocked",
    privacyClass: "movement-sensitive",
    networkPolicy: "prohibited",
    disposition: "reject",
  }),
  layer({
    id: "satellites",
    category: "space-activity",
    semanticZoomBands: ["country", "planet"],
    sourceFamily: "public-orbital-object-catalog",
    provenanceRequirement: "catalog-authority-epoch-and-revision",
    licenseReview: "pending",
    privacyClass: "public-event",
    networkPolicy: "first-party-proxy-only",
    disposition: "defer",
  }),
  layer({
    id: "launches",
    category: "space-activity",
    semanticZoomBands: ["country", "planet"],
    sourceFamily: "public-launch-events",
    provenanceRequirement: "issuing-organization-schedule-and-updated-at",
    licenseReview: "pending",
    privacyClass: "public-event",
    networkPolicy: "first-party-proxy-only",
    disposition: "defer",
  }),
  layer({
    id: "cctv",
    category: "surveillance",
    semanticZoomBands: ["parcel-building", "city"],
    sourceFamily: "camera-observation-feed",
    provenanceRequirement: "controller-lawful-purpose-consent-and-retention",
    licenseReview: "blocked",
    privacyClass: "surveillance-sensitive",
    networkPolicy: "prohibited",
    disposition: "reject",
  }),
  layer({
    id: "radio",
    category: "communications",
    semanticZoomBands: ["city", "country", "planet"],
    sourceFamily: "radio-communications-feed",
    provenanceRequirement: "source-lawful-purpose-and-retention-policy",
    licenseReview: "blocked",
    privacyClass: "communications-sensitive",
    networkPolicy: "prohibited",
    disposition: "reject",
  }),
  layer({
    id: "bikeshare",
    category: "mobility",
    semanticZoomBands: ["parcel-building", "city"],
    sourceFamily: "public-shared-mobility-status",
    provenanceRequirement: "operator-station-or-vehicle-id-and-updated-at",
    licenseReview: "pending",
    privacyClass: "public-location",
    networkPolicy: "first-party-proxy-only",
    disposition: "defer",
  }),
  layer({
    id: "datacenters",
    category: "critical-infrastructure",
    semanticZoomBands: ["city", "country", "planet"],
    sourceFamily: "infrastructure-location-inventory",
    provenanceRequirement: "source-lawful-purpose-vintage-and-sensitivity-review",
    licenseReview: "blocked",
    privacyClass: "critical-infrastructure-sensitive",
    networkPolicy: "prohibited",
    disposition: "reject",
  }),
  layer({
    id: "dams",
    category: "infrastructure",
    semanticZoomBands: ["city", "country"],
    sourceFamily: "public-infrastructure-inventory",
    provenanceRequirement: "maintaining-authority-vintage-and-sensitivity-review",
    licenseReview: "pending",
    privacyClass: "public-sensitive",
    networkPolicy: "first-party-proxy-only",
    disposition: "defer",
  }),
  layer({
    id: "cables",
    category: "critical-infrastructure",
    semanticZoomBands: ["city", "country", "planet"],
    sourceFamily: "infrastructure-network-inventory",
    provenanceRequirement: "source-lawful-purpose-vintage-and-sensitivity-review",
    licenseReview: "blocked",
    privacyClass: "critical-infrastructure-sensitive",
    networkPolicy: "prohibited",
    disposition: "reject",
  }),
]);

function isBuildApproved(layerRecord) {
  return layerRecord.disposition === "build"
    && BUILD_LICENSE_STATES.has(layerRecord.licenseReview)
    && layerRecord.networkPolicy !== "prohibited";
}

export function validateWorldLayerCatalog(catalog = WORLD_LAYER_CATALOG) {
  if (!Array.isArray(catalog) || catalog.length === 0) {
    throw new TypeError("world layer catalog must be a non-empty array");
  }

  const ids = new Set();
  for (const entry of catalog) {
    if (!entry || typeof entry !== "object") throw new TypeError("world layer entry must be an object");
    if (!ID_PATTERN.test(entry.id || "") || ids.has(entry.id)) {
      throw new TypeError("world layer ids must be unique stable identifiers");
    }
    ids.add(entry.id);
    if (typeof entry.category !== "string" || !entry.category) throw new TypeError(`world layer ${entry.id} has no category`);
    if (!Array.isArray(entry.semanticZoomBands) || entry.semanticZoomBands.length === 0
      || entry.semanticZoomBands.some((band) => !SEMANTIC_ZOOM_BANDS.includes(band))) {
      throw new TypeError(`world layer ${entry.id} has invalid semantic zoom bands`);
    }
    if (typeof entry.defaultEnabled !== "boolean") throw new TypeError(`world layer ${entry.id} has invalid default state`);
    if (entry.defaultEnabled !== (entry.id === "site-health")) {
      throw new TypeError("site-health must be the only default-enabled world layer");
    }
    for (const field of ["sourceFamily", "provenanceRequirement", "privacyClass"]) {
      if (typeof entry[field] !== "string" || !entry[field]) throw new TypeError(`world layer ${entry.id} has invalid ${field}`);
    }
    if (!LICENSE_REVIEW_STATES.has(entry.licenseReview)) throw new TypeError(`world layer ${entry.id} has invalid license review state`);
    if (!NETWORK_POLICIES.has(entry.networkPolicy)) throw new TypeError(`world layer ${entry.id} has invalid network policy`);
    if (!DISPOSITIONS.has(entry.disposition)) throw new TypeError(`world layer ${entry.id} has invalid disposition`);
    if (entry.disposition === "reject" && (entry.defaultEnabled || entry.networkPolicy !== "prohibited")) {
      throw new TypeError(`rejected world layer ${entry.id} cannot be enabled or networked`);
    }
    if (entry.disposition === "build" && !isBuildApproved(entry)) {
      throw new TypeError(`build world layer ${entry.id} has not passed policy review`);
    }
  }
  return true;
}

export function getBuildApprovedLayers(catalog = WORLD_LAYER_CATALOG) {
  validateWorldLayerCatalog(catalog);
  return Object.freeze(catalog.filter(isBuildApproved));
}

export function getDefaultEnabledLayers(catalog = WORLD_LAYER_CATALOG) {
  return Object.freeze(getBuildApprovedLayers(catalog).filter((entry) => entry.defaultEnabled));
}

export function assertWorldLayerEnablement(layerIds, catalog = WORLD_LAYER_CATALOG) {
  validateWorldLayerCatalog(catalog);
  if (!Array.isArray(layerIds)) throw new TypeError("enabled world layer ids must be an array");
  const byId = new Map(catalog.map((entry) => [entry.id, entry]));
  const enabled = [];
  const seen = new Set();
  for (const id of layerIds) {
    if (seen.has(id)) throw new TypeError(`world layer ${id} is enabled more than once`);
    seen.add(id);
    const entry = byId.get(id);
    if (!entry) throw new RangeError(`unknown world layer ${id}`);
    if (!isBuildApproved(entry)) throw new RangeError(`world layer ${id} is not approved to enable`);
    enabled.push(entry);
  }
  return Object.freeze(enabled);
}

validateWorldLayerCatalog(WORLD_LAYER_CATALOG);
