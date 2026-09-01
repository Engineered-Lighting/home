import { assertCandidateAdapter, CANDIDATE_RUNTIME_MANIFEST } from "./candidate-adapter.js";
import { createCesiumAdapter } from "./cesium-adapter.js";
import { createDeterministicAdapter } from "./deterministic-adapter.js";
import { createMapLibreAdapter } from "./maplibre-adapter.js";

const factories = new Map([
  ["deterministic-dom", createDeterministicAdapter],
  ["cesium-separate", createCesiumAdapter],
  ["maplibre-sandbox", createMapLibreAdapter],
]);

export function availableAdapterIds() {
  return CANDIDATE_RUNTIME_MANIFEST.candidates
    .filter((candidate) => candidate.status === "available" && factories.has(candidate.id))
    .map((candidate) => candidate.id);
}

export function createCandidateAdapter(adapterId = CANDIDATE_RUNTIME_MANIFEST.activeAdapterId, options = {}) {
  const factory = factories.get(adapterId);
  if (!factory) throw new RangeError(`renderer adapter ${adapterId} is not available in this sandbox`);
  return assertCandidateAdapter(factory(options));
}
