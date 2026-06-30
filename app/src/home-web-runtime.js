/* Home web runtime defaults.
 *
 * Loaded by both the Tauri shell and the browser-served app. It only enables
 * web mode for ordinary http(s) origins, leaving Tauri/custom-protocol builds
 * on their existing LAN/Tauri defaults.
 */
(function () {
  const loc = window.location || {};
  const host = String(loc.hostname || "");
  const isHttp = loc.protocol === "http:" || loc.protocol === "https:";
  const isTauriHost = host === "tauri.localhost" || host === "asset.localhost";
  const webMode = isHttp && !isTauriHost;
  if (!webMode) return;

  Object.assign(window, {
    HG_WEB_MODE: true,
    HG_DEFAULT_HA_BASE: "/proxy/ha",
    HG_DEFAULT_METRICS_BASE: "/proxy/metrics",
    HG_DEFAULT_VLLM_BASE: "/proxy/vllm",
    HG_DEFAULT_VISION_BASE: "/proxy/vision",
    HG_DEFAULT_INTELLIGENCE_BASE: "/proxy/intelligence",
    HG_DEFAULT_SUPERVISOR_BASE: "/proxy/supervisor",
    HG_DEFAULT_S2S_BASE: "/proxy/bridge",
    HG_DEFAULT_TRACKER_BASE: "/proxy/tracker",
    HG_DEFAULT_VIDEO_LABELER_BASE: "/proxy/video-labeler",
    HG_DEFAULT_FRIGATE_BASE: "/proxy/frigate",
    HG_DEFAULT_APARTMENT_ASSET_BASE: "/assets/apartment",
  });

  if ("serviceWorker" in navigator && !window.HG_DISABLE_SERVICE_WORKER) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("./home-service-worker.js", { scope: "./" })
        .catch((err) => console.warn("[home-web] service worker registration failed", err));
    }, { once: true });
  }
})();
