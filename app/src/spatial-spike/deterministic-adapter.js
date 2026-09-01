import { ADAPTER_API_VERSION, defineCandidateAdapter } from "./candidate-adapter.js";

const createElement = (documentRef, tag, className, text) => {
  const element = documentRef.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};

export function createDeterministicAdapter({ onActivateSite = () => {} } = {}) {
  let surface = null;
  let world = null;
  let markerLayer = null;
  let altitude = null;
  let owner = null;
  let sites = [];
  let snapshot = null;
  const markerById = new Map();

  const adapter = {
    apiVersion: ADAPTER_API_VERSION,
    id: "deterministic-dom",

    mount(host) {
      if (!(host instanceof host.ownerDocument.defaultView.HTMLElement)) {
        throw new TypeError("renderer surface must be an HTMLElement");
      }
      surface = host;
      const documentRef = host.ownerDocument;
      surface.replaceChildren();

      world = createElement(documentRef, "div", "fixture-world");
      world.setAttribute("aria-hidden", "true");
      const atmosphere = createElement(documentRef, "div", "fixture-atmosphere");
      const globe = createElement(documentRef, "div", "fixture-globe");
      globe.append(
        createElement(documentRef, "div", "fixture-meridian fixture-meridian-a"),
        createElement(documentRef, "div", "fixture-meridian fixture-meridian-b"),
        createElement(documentRef, "div", "fixture-latitude fixture-latitude-a"),
        createElement(documentRef, "div", "fixture-latitude fixture-latitude-b"),
        createElement(documentRef, "div", "fixture-land fixture-land-a"),
        createElement(documentRef, "div", "fixture-land fixture-land-b"),
      );
      markerLayer = createElement(documentRef, "div", "fixture-marker-layer");
      world.append(atmosphere, globe, markerLayer);

      const readout = createElement(documentRef, "div", "fixture-readout");
      const altitudeRow = createElement(documentRef, "p", "fixture-readout-row");
      altitudeRow.append(createElement(documentRef, "span", "fixture-readout-label", "Camera altitude"));
      altitude = createElement(documentRef, "strong", "fixture-readout-value", "18,000 km");
      altitudeRow.append(altitude);
      const ownerRow = createElement(documentRef, "p", "fixture-readout-row");
      ownerRow.append(createElement(documentRef, "span", "fixture-readout-label", "Rendering owner"));
      owner = createElement(documentRef, "strong", "fixture-readout-value", "World adapter");
      ownerRow.append(owner);
      readout.append(altitudeRow, ownerRow);

      surface.append(world, readout);
      return adapter;
    },

    setSites(nextSites) {
      if (!surface || !markerLayer) throw new Error("renderer adapter must be mounted before setSites()");
      sites = nextSites.map((site) => ({ ...site, anchor: { ...site.anchor } }));
      markerLayer.replaceChildren();
      markerById.clear();
      const documentRef = surface.ownerDocument;
      for (const site of sites) {
        const marker = createElement(documentRef, "button", "fixture-marker");
        marker.type = "button";
        marker.dataset.siteId = site.id;
        marker.setAttribute("aria-label", `Select ${site.label}`);
        marker.style.setProperty("--marker-x", `${((site.anchor.longitude + 180) / 360) * 100}%`);
        marker.style.setProperty("--marker-y", `${((90 - site.anchor.latitude) / 180) * 100}%`);
        marker.append(
          createElement(documentRef, "span", "fixture-marker-pulse"),
          createElement(documentRef, "span", "fixture-marker-core"),
        );
        marker.addEventListener("click", () => onActivateSite(site.id));
        markerLayer.append(marker);
        markerById.set(site.id, marker);
      }
    },

    render(nextSnapshot) {
      if (!surface || !world) throw new Error("renderer adapter must be mounted before render()");
      snapshot = structuredClone(nextSnapshot);
      const progress = Number(nextSnapshot.journey?.progress || 0);
      const interiorProgress = nextSnapshot.journey?.destination === "planet" ? 1 - progress : progress;
      surface.style.setProperty("--journey-progress", interiorProgress.toFixed(4));
      surface.dataset.scale = nextSnapshot.scale;
      surface.dataset.owner = nextSnapshot.owner;
      surface.dataset.network = nextSnapshot.environment.network;
      surface.dataset.provider = nextSnapshot.environment.provider;
      surface.dataset.reducedMotion = String(nextSnapshot.reducedMotion);

      const heightKm = nextSnapshot.camera.heightKm;
      altitude.textContent = heightKm >= 1000
        ? `${Math.round(heightKm).toLocaleString("en-US")} km`
        : heightKm >= 1
          ? `${heightKm.toFixed(1)} km`
          : `${Math.max(1, Math.round(heightKm * 1000))} m`;
      owner.textContent = nextSnapshot.owner === "world"
        ? "World adapter"
        : nextSnapshot.owner === "coordinator"
          ? "Scene coordinator"
          : "Interior adapter boundary";

      for (const site of sites) {
        const marker = markerById.get(site.id);
        if (!marker) continue;
        const active = site.id === nextSnapshot.selectedSiteId;
        marker.classList.toggle("is-selected", active);
        marker.setAttribute("aria-pressed", String(active));
        marker.dataset.health = nextSnapshot.environment.siteHealth[site.id] || "unknown";
      }
    },

    getSnapshot() {
      return snapshot ? structuredClone(snapshot) : null;
    },

    dispose() {
      if (surface) surface.replaceChildren();
      markerById.clear();
      sites = [];
      snapshot = null;
      surface = null;
      world = null;
      markerLayer = null;
      altitude = null;
      owner = null;
    },
  };

  return defineCandidateAdapter(adapter);
}

