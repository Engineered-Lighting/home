import { ADAPTER_API_VERSION, defineCandidateAdapter } from "./candidate-adapter.js";

const RUNTIME_ROOT = new URL("./runtime/maplibre/", import.meta.url);
const RUNTIME_ENTRY = new URL("maplibre-gl.mjs", RUNTIME_ROOT);
const WORKER_ENTRY = new URL("maplibre-gl-worker.mjs", RUNTIME_ROOT);
const RUNTIME_STYLE = new URL("maplibre-gl.css", RUNTIME_ROOT);
const OFFLINE_RASTER = new URL("./runtime/fixtures/offline-planet.png", import.meta.url);

const HEIGHT_TO_ZOOM = Object.freeze([
  [18000, 0],
  [4200, 1.5],
  [780, 3],
  [96, 5.5],
  [8.4, 9],
  [0.32, 14],
  [0.08, 16],
  [0.012, 18],
]);

let runtimePromise = null;

function loadRuntime() {
  runtimePromise ||= import(RUNTIME_ENTRY.href);
  return runtimePromise;
}

function zoomForHeight(heightKm) {
  const height = Math.max(HEIGHT_TO_ZOOM.at(-1)[0], Math.min(HEIGHT_TO_ZOOM[0][0], heightKm));
  for (let index = 1; index < HEIGHT_TO_ZOOM.length; index += 1) {
    const [lowerHeight, lowerZoom] = HEIGHT_TO_ZOOM[index];
    const [upperHeight, upperZoom] = HEIGHT_TO_ZOOM[index - 1];
    if (height > upperHeight || height < lowerHeight) continue;
    const span = Math.log(upperHeight) - Math.log(lowerHeight);
    const progress = span === 0 ? 1 : (Math.log(upperHeight) - Math.log(height)) / span;
    return lowerZoom + ((upperZoom - lowerZoom) * (1 - progress));
  }
  return HEIGHT_TO_ZOOM.at(-1)[1];
}

function ensureRuntimeStyle(documentRef) {
  if (documentRef.querySelector("link[data-spatial-maplibre-style]")) return;
  const link = documentRef.createElement("link");
  link.rel = "stylesheet";
  link.href = RUNTIME_STYLE.href;
  link.dataset.spatialMaplibreStyle = "true";
  documentRef.head.append(link);
}

function siteCollection(sites, snapshot = null) {
  return {
    type: "FeatureCollection",
    features: sites.map((site) => ({
      type: "Feature",
      id: site.id,
      properties: {
        selected: site.id === snapshot?.selectedSiteId,
        health: snapshot?.environment.siteHealth[site.id] || site.availability,
      },
      geometry: {
        type: "Point",
        coordinates: [site.anchor.longitude, site.anchor.latitude],
      },
    })),
  };
}

export function createMapLibreAdapter() {
  let maplibre = null;
  let surface = null;
  let map = null;
  let sites = [];
  let snapshot = null;
  let mounted = false;

  const adapter = {
    apiVersion: ADAPTER_API_VERSION,
    id: "maplibre-sandbox",

    async mount(host) {
      if (!(host instanceof host.ownerDocument.defaultView.HTMLElement)) {
        throw new TypeError("renderer surface must be an HTMLElement");
      }
      surface = host;
      surface.replaceChildren();
      ensureRuntimeStyle(host.ownerDocument);
      maplibre = await loadRuntime();
      maplibre.setWorkerUrl(WORKER_ENTRY.href);

      map = new maplibre.Map({
        container: surface,
        attributionControl: false,
        center: [0, 24],
        fadeDuration: 0,
        interactive: false,
        maxPitch: 60,
        preserveDrawingBuffer: false,
        projection: { type: "globe" },
        refreshExpiredTiles: false,
        renderWorldCopies: false,
        style: {
          version: 8,
          sources: {
            "offline-planet": {
              type: "image",
              url: OFFLINE_RASTER.href,
              coordinates: [
                [-180, 85],
                [180, 85],
                [180, -85],
                [-180, -85],
              ],
            },
            "synthetic-sites": {
              type: "geojson",
              data: siteCollection([]),
            },
          },
          layers: [
            {
              id: "ocean",
              type: "background",
              paint: { "background-color": "#081a28" },
            },
            {
              id: "offline-planet",
              type: "raster",
              source: "offline-planet",
              paint: { "raster-opacity": 1, "raster-fade-duration": 0 },
            },
            {
              id: "site-halo",
              type: "circle",
              source: "synthetic-sites",
              paint: {
                "circle-color": "rgba(0,0,0,0)",
                "circle-radius": ["case", ["get", "selected"], 15, 11],
                "circle-stroke-color": [
                  "case",
                  ["==", ["get", "health"], "online"],
                  "#62cad4",
                  "#e27b62",
                ],
                "circle-stroke-opacity": 0.48,
                "circle-stroke-width": 1,
              },
            },
            {
              id: "site-point",
              type: "circle",
              source: "synthetic-sites",
              paint: {
                "circle-color": [
                  "case",
                  ["==", ["get", "health"], "online"],
                  ["case", ["get", "selected"], "#b5edf0", "#62cad4"],
                  "#e27b62",
                ],
                "circle-radius": ["case", ["get", "selected"], 6, 4],
                "circle-stroke-color": "#071116",
                "circle-stroke-width": 2,
              },
            },
          ],
        },
        zoom: 0,
      });

      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error("maplibre-load-timeout")), 10_000);
        map.once("load", () => {
          clearTimeout(timeout);
          resolve();
        });
        map.once("error", (event) => {
          if (map.loaded()) return;
          clearTimeout(timeout);
          reject(event.error || new Error("maplibre-load-failed"));
        });
      });
      map.getCanvas().setAttribute("aria-hidden", "true");
      map.getCanvas().tabIndex = -1;
      mounted = true;
      return adapter;
    },

    setSites(nextSites) {
      if (!map) throw new Error("renderer adapter must be mounted before setSites()");
      sites = nextSites.map((site) => ({ ...site, anchor: { ...site.anchor } }));
      map.getSource("synthetic-sites")?.setData(siteCollection(sites, snapshot));
    },

    render(nextSnapshot) {
      if (!map) throw new Error("renderer adapter must be mounted before render()");
      snapshot = structuredClone(nextSnapshot);
      const selected = sites.find((site) => site.id === nextSnapshot.selectedSiteId) || sites[0];
      if (!selected) return;
      map.stop();
      map.jumpTo({
        center: [selected.anchor.longitude, selected.anchor.latitude],
        zoom: zoomForHeight(nextSnapshot.camera.heightKm),
        bearing: nextSnapshot.camera.bearingDeg,
        pitch: Math.max(0, Math.min(60, 90 + nextSnapshot.camera.pitchDeg)),
      });
      map.getSource("synthetic-sites")?.setData(siteCollection(sites, nextSnapshot));
      map.triggerRepaint();
    },

    getSnapshot() {
      return Object.freeze({
        adapterId: adapter.id,
        mounted,
        rendererVersion: maplibre?.getVersion?.() || null,
        siteCount: sites.length,
        stateRevision: snapshot?.revision ?? null,
      });
    },

    dispose() {
      if (map) map.remove();
      maplibre?.clearPrewarmedResources?.();
      if (surface) surface.replaceChildren();
      mounted = false;
      sites = [];
      snapshot = null;
      map = null;
      surface = null;
      maplibre = null;
    },
  };

  return defineCandidateAdapter(adapter);
}

export { zoomForHeight };
