import { ADAPTER_API_VERSION, defineCandidateAdapter } from "./candidate-adapter.js";

const RUNTIME_ROOT = new URL("./runtime/cesium/", import.meta.url);
const RUNTIME_ENTRY = new URL("index.js", RUNTIME_ROOT);
const RUNTIME_STYLE = new URL("Widgets/widgets.css", RUNTIME_ROOT);
const OFFLINE_RASTER = new URL("./runtime/fixtures/offline-planet.png", import.meta.url);

let runtimePromise = null;

function loadRuntime() {
  globalThis.CESIUM_BASE_URL = RUNTIME_ROOT.href;
  runtimePromise ||= import(RUNTIME_ENTRY.href);
  return runtimePromise;
}

function ensureRuntimeStyle(documentRef) {
  if (documentRef.querySelector("link[data-spatial-cesium-style]")) return;
  const link = documentRef.createElement("link");
  link.rel = "stylesheet";
  link.href = RUNTIME_STYLE.href;
  link.dataset.spatialCesiumStyle = "true";
  documentRef.head.append(link);
}

export function createCesiumAdapter() {
  let Cesium = null;
  let surface = null;
  let viewer = null;
  let sites = [];
  let snapshot = null;
  let mounted = false;

  const adapter = {
    apiVersion: ADAPTER_API_VERSION,
    id: "cesium-separate",

    async mount(host) {
      if (!(host instanceof host.ownerDocument.defaultView.HTMLElement)) {
        throw new TypeError("renderer surface must be an HTMLElement");
      }
      surface = host;
      surface.replaceChildren();
      ensureRuntimeStyle(host.ownerDocument);
      Cesium = await loadRuntime();
      Cesium.Ion.defaultAccessToken = undefined;

      viewer = new Cesium.Viewer(surface, {
        animation: false,
        baseLayer: false,
        baseLayerPicker: false,
        fullscreenButton: false,
        geocoder: false,
        homeButton: false,
        infoBox: false,
        navigationHelpButton: false,
        scene3DOnly: true,
        sceneModePicker: false,
        selectionIndicator: false,
        shouldAnimate: false,
        timeline: false,
        terrainProvider: new Cesium.EllipsoidTerrainProvider(),
        useBrowserRecommendedResolution: true,
        requestRenderMode: true,
        maximumRenderTimeChange: Infinity,
        contextOptions: {
          webgl: {
            alpha: false,
            antialias: true,
            preserveDrawingBuffer: false,
          },
        },
      });
      viewer.canvas.setAttribute("aria-hidden", "true");
      viewer.canvas.tabIndex = -1;
      viewer.scene.backgroundColor = Cesium.Color.fromCssColorString("#08131d");
      viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString("#0a2133");
      viewer.scene.globe.depthTestAgainstTerrain = false;
      viewer.scene.fog.enabled = false;
      viewer.scene.screenSpaceCameraController.enableInputs = false;

      const imagery = await Cesium.SingleTileImageryProvider.fromUrl(OFFLINE_RASTER.href, {
        rectangle: Cesium.Rectangle.fromDegrees(-180, -90, 180, 90),
      });
      viewer.imageryLayers.addImageryProvider(imagery);
      mounted = true;
      return adapter;
    },

    setSites(nextSites) {
      if (!viewer || !Cesium) throw new Error("renderer adapter must be mounted before setSites()");
      sites = nextSites.map((site) => ({ ...site, anchor: { ...site.anchor } }));
      viewer.entities.removeAll();
      for (const site of sites) {
        viewer.entities.add({
          id: site.id,
          position: Cesium.Cartesian3.fromDegrees(site.anchor.longitude, site.anchor.latitude, 0),
          point: {
            color: Cesium.Color.fromCssColorString("#62cad4"),
            outlineColor: Cesium.Color.fromCssColorString("#dff9fa"),
            outlineWidth: 2,
            pixelSize: 9,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
        });
      }
      viewer.scene.requestRender();
    },

    render(nextSnapshot) {
      if (!viewer || !Cesium) throw new Error("renderer adapter must be mounted before render()");
      snapshot = structuredClone(nextSnapshot);
      const selected = sites.find((site) => site.id === nextSnapshot.selectedSiteId) || sites[0];
      if (!selected) return;
      const heightMeters = Math.max(80, nextSnapshot.camera.heightKm * 1000);
      viewer.camera.setView({
        destination: Cesium.Cartesian3.fromDegrees(
          selected.anchor.longitude,
          selected.anchor.latitude,
          heightMeters,
        ),
        orientation: {
          heading: Cesium.Math.toRadians(nextSnapshot.camera.bearingDeg),
          pitch: Cesium.Math.toRadians(nextSnapshot.camera.pitchDeg),
          roll: 0,
        },
      });
      for (const site of sites) {
        const entity = viewer.entities.getById(site.id);
        if (!entity?.point) continue;
        const health = nextSnapshot.environment.siteHealth[site.id] || "unknown";
        entity.point.color = health === "online"
          ? Cesium.Color.fromCssColorString(site.id === nextSnapshot.selectedSiteId ? "#b5edf0" : "#62cad4")
          : Cesium.Color.fromCssColorString("#e27b62");
        entity.point.pixelSize = site.id === nextSnapshot.selectedSiteId ? 12 : 8;
      }
      viewer.scene.requestRender();
    },

    getSnapshot() {
      return Object.freeze({
        adapterId: adapter.id,
        mounted,
        rendererVersion: Cesium?.VERSION || null,
        siteCount: sites.length,
        stateRevision: snapshot?.revision ?? null,
      });
    },

    dispose() {
      if (viewer && !viewer.isDestroyed()) viewer.destroy();
      if (surface) surface.replaceChildren();
      mounted = false;
      sites = [];
      snapshot = null;
      viewer = null;
      surface = null;
      Cesium = null;
    },
  };

  return defineCandidateAdapter(adapter);
}
