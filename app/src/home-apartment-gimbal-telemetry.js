/* Read-only engineered-gimbal telemetry adapter.
 * Browser mode is deliberately unavailable and performs no loopback fetch.
 * No write method is exposed from this module.
 */
(function () {
  "use strict";
  const BASE = "http://127.0.0.1:8765";

  function runtimeKind() {
    return (window.IS_TAURI || window.__TAURI__) && typeof window.tauriFetch === "function"
      ? "tauri" : "browser";
  }

  function createPoller({ onSnapshot, onStatus, interval_ms = 160 } = {}) {
    if (runtimeKind() !== "tauri") {
      onStatus?.({ state: "unavailable", blocker: "loopback telemetry is available only in the Tauri desktop app" });
      return { start() {}, stop() {}, runtime: "browser", read_only: true };
    }
    let stopped = true;
    let timer = null;
    let requestSeq = 0;

    const schedule = () => {
      if (!stopped && !document.hidden) timer = setTimeout(poll, interval_ms);
    };
    const poll = async () => {
      if (stopped || document.hidden) return;
      const seq = ++requestSeq;
      try {
        const response = await window.tauriFetch(`${BASE}/api/state`, {
          method: "GET", cache: "no-store", headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error(`state HTTP ${response.status}`);
        const snapshot = await response.json();
        if (!stopped && seq === requestSeq) {
          onSnapshot?.({ snapshot, received_at_ms: Date.now() });
          onStatus?.({ state: "connected", read_only: true });
        }
      } catch (error) {
        if (!stopped) onStatus?.({ state: "offline", blocker: String(error?.message || error), read_only: true });
      } finally { schedule(); }
    };
    const onVisibility = () => {
      if (document.hidden) {
        clearTimeout(timer); timer = null;
        onStatus?.({ state: "paused", blocker: "Apartment is hidden", read_only: true });
      } else if (!stopped) poll();
    };
    return {
      runtime: "tauri",
      read_only: true,
      async start() {
        if (!stopped) return;
        stopped = false;
        document.addEventListener("visibilitychange", onVisibility);
        try {
          const response = await window.tauriFetch(`${BASE}/healthz`, {
            method: "GET", cache: "no-store", headers: { Accept: "application/json" },
          });
          if (!response.ok) throw new Error(`health HTTP ${response.status}`);
          onStatus?.({ state: "healthy", read_only: true });
          poll();
        } catch (error) {
          onStatus?.({ state: "offline", blocker: String(error?.message || error), read_only: true });
          schedule();
        }
      },
      stop() {
        stopped = true;
        requestSeq += 1;
        clearTimeout(timer); timer = null;
        document.removeEventListener("visibilitychange", onVisibility);
      },
    };
  }

  window.HomeApartmentGimbalTelemetry = Object.freeze({ BASE, runtimeKind, createPoller });
})();
