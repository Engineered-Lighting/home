/* Ordinary interior selection uses the existing registry and travel lifetime.
 * Callers own rendering; this module owns the identity checks around awaits. */
(function (global) {
  "use strict";
  function identity(site) {
    return { siteId: site.id, contentRevision: site.contentRevision || null,
      registrationVersion: site.sceneRegistration?.version ?? 0 };
  }
  function sameIdentity(left, right) {
    return !!left && !!right && left.siteId === right.siteId
      && left.contentRevision === right.contentRevision
      && left.registrationVersion === right.registrationVersion;
  }
  function canControlEcho({ selectedSiteId, sceneSiteId, transitioning = false, isolated = false } = {}) {
    return !isolated && !transitioning && selectedSiteId === "echo" && sceneSiteId === "echo";
  }
  function create({ registry, begin, prepare, beforeSelect = () => {}, onState = () => {}, getEngine = () => null }) {
    let state = { siteId: registry.getSelectedSiteId(), phase: "idle", contentRevision: null, registrationVersion: 0, error: null };
    function emit(scope, phase, error = null) {
      state = Object.freeze({ ...scope, phase, error });
      onState(state);
    }
    async function request(siteId, { force = false } = {}) {
      const site = registry.getSite(siteId);
      if (!site) return false;
      const scope = identity(site);
      const current = getEngine();
      if (!force && state.phase === "ready" && sameIdentity(scope, state)
          && current?.siteId === siteId && registry.getSelectedSiteId() === siteId) return true;
      const operation = begin();
      const isCurrent = () => operation.isCurrent()
        && registry.getSelectedSiteId() === siteId
        && sameIdentity(scope, identity(registry.getSite(siteId) || { id: null }));
      /* A superseded operation cannot publish state. A still-current request
       * whose pack changed must leave loading, however: consumers otherwise
       * cannot distinguish a pending current load from an obsolete binding. */
      const invalidated = () => {
        if (operation.isCurrent() && registry.getSelectedSiteId() === siteId
            && !sameIdentity(scope, identity(registry.getSite(siteId) || { id: null }))) {
          emit(scope, "invalidated", "This home's content changed during loading. Reload its current revision.");
        }
        return false;
      };
      beforeSelect(scope);
      registry.selectSite(siteId);
      emit(scope, "loading");
      try {
        await operation.cancelled;
        if (!isCurrent()) return invalidated();
        if (site.sceneAvailable !== true) { emit(scope, "unavailable"); return false; }
        const ready = await prepare(siteId, isCurrent);
        if (!isCurrent()) return invalidated();
        emit(scope, ready ? "ready" : "error", ready ? null : "The interior could not be loaded. Retry or choose another home.");
        return ready;
      } catch (error) {
        if (isCurrent()) emit(scope, "error", String(error?.message || error));
        else invalidated();
        return false;
      }
    }
    return Object.freeze({ request, readback: () => ({ ...state }) });
  }
  const api = Object.freeze({ create, identity, sameIdentity, canControlEcho });
  if (global) global.HomeApartmentSiteSession = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
