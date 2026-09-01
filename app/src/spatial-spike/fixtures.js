const deepFreeze = (value) => {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    Object.values(value).forEach(deepFreeze);
  }
  return value;
};

// These broad country-centre anchors are deliberately synthetic. They are not
// addresses, acceptance-test locations, or product defaults.
export const SYNTHETIC_SITES = deepFreeze([
  {
    id: "synthetic-us-01",
    label: "Redwood test home",
    countryCode: "US",
    privacyClass: "synthetic",
    anchor: {
      latitude: 39,
      longitude: -98,
      accuracyMeters: 250000,
      source: "fixture",
    },
    availability: "online",
    capabilities: ["world", "interior-handoff", "home-assistant-fixture"],
  },
  {
    id: "synthetic-ca-01",
    label: "Aurora test home",
    countryCode: "CA",
    privacyClass: "synthetic",
    anchor: {
      latitude: 56,
      longitude: -106,
      accuracyMeters: 250000,
      source: "fixture",
    },
    availability: "online",
    capabilities: ["world", "interior-handoff", "home-assistant-fixture"],
  },
]);

export const SCALE_STOPS = deepFreeze([
  { id: "planet", label: "Planet", owner: "world" },
  { id: "country", label: "Country", owner: "world" },
  { id: "region", label: "Region", owner: "world" },
  { id: "city", label: "City", owner: "world" },
  { id: "neighborhood", label: "Neighborhood", owner: "world" },
  { id: "exterior", label: "Building", owner: "world" },
  { id: "handoff", label: "Handoff", owner: "coordinator" },
  { id: "interior", label: "Interior boundary", owner: "interior-placeholder" },
]);

const ENTRY_KEYFRAMES = [
  { atMs: 0, scale: "planet", owner: "world", heightKm: 18000, pitchDeg: -88, bearingDeg: 0, anchorBlend: 0 },
  { atMs: 800, scale: "country", owner: "world", heightKm: 4200, pitchDeg: -78, bearingDeg: 4, anchorBlend: 0.42 },
  { atMs: 1600, scale: "region", owner: "world", heightKm: 780, pitchDeg: -68, bearingDeg: 9, anchorBlend: 0.7 },
  { atMs: 2500, scale: "city", owner: "world", heightKm: 96, pitchDeg: -56, bearingDeg: 14, anchorBlend: 0.88 },
  { atMs: 3400, scale: "neighborhood", owner: "world", heightKm: 8.4, pitchDeg: -45, bearingDeg: 18, anchorBlend: 0.96 },
  { atMs: 4300, scale: "exterior", owner: "world", heightKm: 0.32, pitchDeg: -31, bearingDeg: 22, anchorBlend: 1 },
  { atMs: 5200, scale: "handoff", owner: "coordinator", heightKm: 0.08, pitchDeg: -20, bearingDeg: 24, anchorBlend: 1 },
  { atMs: 6000, scale: "interior", owner: "interior-placeholder", heightKm: 0.012, pitchDeg: -8, bearingDeg: 24, anchorBlend: 1 },
];

const reverseJourney = (frames) => {
  const duration = frames.at(-1).atMs;
  return frames
    .slice()
    .reverse()
    .map((frame) => ({ ...frame, atMs: duration - frame.atMs }));
};

export const CAMERA_JOURNEYS = deepFreeze({
  interior: ENTRY_KEYFRAMES,
  planet: reverseJourney(ENTRY_KEYFRAMES),
});

export const DEFAULT_ENVIRONMENT = deepFreeze({
  network: "online",
  provider: "live",
  core: "online",
  ai: "online",
  siteHealth: {
    "synthetic-us-01": "online",
    "synthetic-ca-01": "online",
  },
});

export const ENVIRONMENT_PRESETS = deepFreeze({
  nominal: DEFAULT_ENVIRONMENT,
  offline: {
    network: "offline",
    provider: "unavailable",
    core: "offline",
    ai: "offline",
    siteHealth: {
      "synthetic-us-01": "unknown",
      "synthetic-ca-01": "unknown",
    },
  },
  providerDegraded: {
    network: "online",
    provider: "degraded",
    core: "online",
    ai: "online",
    siteHealth: {
      "synthetic-us-01": "online",
      "synthetic-ca-01": "online",
    },
  },
  oneSiteOffline: {
    network: "online",
    provider: "live",
    core: "online",
    ai: "online",
    siteHealth: {
      "synthetic-us-01": "online",
      "synthetic-ca-01": "offline",
    },
  },
  aiOffline: {
    network: "online",
    provider: "live",
    core: "online",
    ai: "offline",
    siteHealth: {
      "synthetic-us-01": "online",
      "synthetic-ca-01": "online",
    },
  },
});

export const FIXTURE_VERSION = "spatial-spike-fixtures-v1";

