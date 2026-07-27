/* home-people-prewarm.js
 *
 * Compatibility stub. Identity, face, and avatar content is private and may
 * only be fetched after the currently authenticated HA user is confirmed as
 * an administrator. It is never prefetched or retained across surface closes.
 */
(function () {
  try { delete window.__HOME_PEOPLE_DATA_CACHE; } catch {}

  const disabled = {
    ok: false,
    skipped: true,
    reason: "sensitive_people_cache_disabled",
  };

  async function start() {
    try { delete window.__HOME_PEOPLE_DATA_CACHE; } catch {}
    return { ...disabled };
  }

  function status() {
    return {
      state: "disabled",
      reason: "sensitive_people_cache_disabled",
    };
  }

  window.HomePeoplePrewarm = {
    start,
    status,
    readCache: () => null,
    writeCache: () => null,
  };
})();
