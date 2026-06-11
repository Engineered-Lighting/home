/* home-apartment-data.js — data sources for the /apartment view (P0 minimal).
 *
 * P0 only needs the apartment_model stub (devices/zones land in P1/P2; the
 * point cloud itself resolves through home-3d/assets.js). Live mode tries the
 * HA endpoint (added in P1); 404 falls back to an empty model. Sim mode uses
 * the fixture when present.
 */
(function () {
  const EMPTY_MODEL = {
    schema_version: 1, revision: 0,
    meta: { frame: "z_up_metric_floor0" },
    zones: [], devices: [],
  };

  async function getModel({ endpoint, token, sim } = {}) {
    if (sim) {
      try {
        if (typeof window.__SIM_APARTMENT_MODEL_FIXTURE === "function") {
          return window.__SIM_APARTMENT_MODEL_FIXTURE() || EMPTY_MODEL;
        }
      } catch (e) { /* */ }
      return EMPTY_MODEL;
    }
    if (!endpoint || !token) return EMPTY_MODEL;
    try {
      const base = endpoint.replace(/\/+$/, "");
      const fetcher = window.tauriFetch || fetch;
      const r = await fetcher(`${base}/api/extended_openai_conversation/apartment_model`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
      });
      if (r.ok) return await r.json();
    } catch (e) { /* */ }
    return EMPTY_MODEL;
  }

  window.HomeApartmentData = { getModel, EMPTY_MODEL };
})();
