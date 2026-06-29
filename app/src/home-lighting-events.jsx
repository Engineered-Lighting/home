/* home-lighting-events.jsx — subscribe to Living Lights override + articulation
 * HA bus events and render them as chat-feed entries.
 *
 * Two separate subscribeEvents calls so we don't depend on multi-type or
 * wildcard filtering. The proactive coordinator (home-proactive.jsx) is NOT
 * involved — its normalization pipeline expects proactive-only event types
 * and would suppress or mangle override events.
 *
 * Default rendering is non-verbose ("Office light adjusted manually"). When
 * debugMode() returns true (e.g., user has typed /debug on), the entry adds
 * delta + contextual state. Articulation events render as assistant-styled
 * lines.
 */

(function () {
  function capitalize(s) {
    if (!s) return "";
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function renderOverrideText(d, verbose) {
    const zoneName = (d.zone || "").replace(/_/g, " ");
    if (verbose) {
      const ctx = [
        d.profile,
        `TV ${d.tv_playing ? "on" : "off"}`,
        `state=${d.state}`,
      ];
      if (d.shadow_mode) ctx.push("shadow=on");
      const ctxStr = ctx.join(" · ");
      return `${capitalize(zoneName)} set to ${d.actual_pct}% manually (system was targeting ${d.predicted_pct}% · ${ctxStr})`;
    }
    return `${capitalize(zoneName)} light adjusted manually`;
  }

  window.HomeLightingEvents = {
    /**
     * Subscribe to override + articulation events.
     *
     * @param {object} client - HAClient instance (from home-ha.jsx).
     * @param {function} addEvent - chat-feed event sink ({kind, ts, text, meta}).
     * @param {object} opts - { debugMode: () => boolean } for verbosity toggle.
     * @returns {function} composed unsubscribe that tears down both subscriptions.
     */
    subscribe(client, addEvent, opts = {}) {
      if (!client || typeof client.subscribeEvents !== "function") {
        return undefined;
      }
      const debugMode = typeof opts.debugMode === "function"
        ? opts.debugMode
        : () => false;

      let unsubOverride;
      let unsubArticulation;

      try {
        unsubOverride = client.subscribeEvents(
          "living_lights_override_detected",
          (ev) => {
            const d = (ev && ev.data) || {};
            const ts = (ev && ev.time_fired) || new Date().toISOString();
            const verbose = debugMode() === true;
            addEvent({
              kind: "system",
              ts,
              text: renderOverrideText(d, verbose),
              // Stash the full payload so /why-light + other introspection
              // tooling can read it without re-fetching.
              meta: { lightingOverride: d },
            });
          },
        );
      } catch (e) {
        console.warn("[home-lighting-events] failed to subscribe to overrides:", e);
      }

      try {
        unsubArticulation = client.subscribeEvents(
          "living_lights_articulation",
          (ev) => {
            const d = (ev && ev.data) || {};
            const ts = (ev && ev.time_fired) || new Date().toISOString();
            addEvent({
              kind: "assistant",
              ts,
              text: (d.text || "Acknowledged.").slice(0, 200),
              meta: {
                source: "lighting-articulation",
                zone: d.zone,
                context_id: d.context_id,
              },
            });
          },
        );
      } catch (e) {
        console.warn("[home-lighting-events] failed to subscribe to articulations:", e);
      }

      return () => {
        try { if (typeof unsubOverride === "function") unsubOverride(); } catch (_) { /* ignore */ }
        try { if (typeof unsubArticulation === "function") unsubArticulation(); } catch (_) { /* ignore */ }
      };
    },
  };
})();
