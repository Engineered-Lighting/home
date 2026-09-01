export const ADAPTER_API_VERSION = "home.spatial-renderer-adapter.v1";

export const REQUIRED_ADAPTER_METHODS = Object.freeze([
  "mount",
  "setSites",
  "render",
  "getSnapshot",
  "dispose",
]);

export function assertCandidateAdapter(adapter) {
  if (!adapter || typeof adapter !== "object") throw new TypeError("renderer adapter must be an object");
  if (adapter.apiVersion !== ADAPTER_API_VERSION) throw new TypeError("renderer adapter API version mismatch");
  if (!/^[a-z0-9][a-z0-9-]{1,63}$/.test(adapter.id || "")) throw new TypeError("renderer adapter id is invalid");
  for (const method of REQUIRED_ADAPTER_METHODS) {
    if (typeof adapter[method] !== "function") throw new TypeError(`renderer adapter is missing ${method}()`);
  }
  return adapter;
}

export function defineCandidateAdapter(definition) {
  return Object.freeze(assertCandidateAdapter(definition));
}

export const CANDIDATE_RUNTIME_MANIFEST = Object.freeze({
  schema: "home.spatial-runtime-manifest.v1",
  activeAdapterId: "deterministic-dom",
  candidates: Object.freeze([
    Object.freeze({ id: "deterministic-dom", status: "available", rendererKind: "dom-fixture", vendorRuntime: null }),
    Object.freeze({ id: "cesium-separate", status: "available", rendererKind: "geospatial", vendorRuntime: "cesium@1.144.0" }),
    Object.freeze({ id: "maplibre-sandbox", status: "available", rendererKind: "geospatial", vendorRuntime: "maplibre-gl@6.6.0" }),
  ]),
});
