/* simulation.jsx — Simulation Mode entry: hook + slash commands + apply.
 *
 * The HomeApp component imports `window.useSimulation()` and gets back:
 *   {
 *     active,              // bool
 *     scenario,            // current scenario id ("healthy", etc.) or null
 *     activate(scenarioId),
 *     deactivate(),
 *     setScenario(id),
 *     reset(),
 *   }
 *
 * It also installs a single useEffect that watches `scenario` and writes
 * the resolved snapshot to the HomeApp's setters + starts any scripted
 * timeline. Cleanup on scenario change cancels timers.
 *
 * The slash-command handler `handleSimulationCommand(rawCmd, args, ctx)`
 * is called by HomeApp's command dispatcher.
 */

(function () {
  // ── Tauri detection ───────────────────────────────────────────────
  // Inside Tauri webview, we IGNORE the localStorage sim flag so the
  // shipped app doesn't accidentally boot in sim mode. Sim still works
  // in Tauri but only if explicitly activated via /simulation.
  function isTauri() {
    return typeof window !== "undefined" && !!window.__TAURI__;
  }

  // Legacy localStorage keys — kept here only so we can DELETE them on
  // boot. We no longer persist sim state across reloads (it caused the
  // Tauri shipped app to occasionally boot mocked when an earlier
  // session had toggled sim on). Sim is now session-only: URL param OR
  // slash command in the current session, gone on next reload.
  const LEGACY_LS_KEY = "hg-simulation";
  const LEGACY_LS_SCENARIO_KEY = "hg-simulation-scenario";
  try {
    if (typeof window !== "undefined" && window.localStorage) {
      window.localStorage.removeItem(LEGACY_LS_KEY);
      window.localStorage.removeItem(LEGACY_LS_SCENARIO_KEY);
    }
  } catch (e) { /* ignore */ }

  function readInitialState() {
    // Only the URL ?simulation=1 param can pre-activate sim. Tauri
    // never has query params on tauri://localhost/index.html so it
    // ALWAYS boots in live mode. The browser path supports deep links.
    const out = { active: false, scenario: null };
    try {
      if (!isTauri() && typeof window !== "undefined") {
        const url = new URLSearchParams(window.location.search);
        if (url.get("simulation") === "1") {
          out.active = true;
          out.scenario = url.get("scenario") || "healthy";
        }
      }
    } catch (e) { /* ignore */ }
    return out;
  }

  // Set __SIM_ACTIVE at MODULE LOAD time (before React renders) so any
  // real-service code path that runs in the first render tick also
  // sees sim mode. The useEffect below keeps it in sync on toggles.
  try {
    const initial = readInitialState();
    if (typeof window !== "undefined") window.__SIM_ACTIVE = !!initial.active;
  } catch (e) { /* ignore */ }

  function persist(_active, _scenario) {
    // No-op. Sim state is session-only — see note above.
  }

  // ── Snapshot application ──────────────────────────────────────────
  // Takes a resolved snapshot { metrics, bridgeHealth, ... } and writes
  // each piece to the HomeApp setter. Setters object passed in once.

  /* Apply a snapshot OR a delta. Deltas can contain meta keys:
   *   __append           — append an event to events[]
   *   __updateLastAction — patch the most-recent action event
   * which is how scripted timelines mutate chat history without
   * needing the full events[] each step. */
  function applyToSetters(setters, payload) {
    if (!payload || typeof payload !== "object") return;
    for (const key of Object.keys(payload)) {
      const val = payload[key];
      switch (key) {
        case "metrics":
          setters.setMetrics((prev) => ({ ...prev, ...val }));
          break;
        case "metricsHistory":
          setters.setMetricsHistory(val); // full replace — sim controls full series
          break;
        case "bridgeHealth":
          setters.setBridgeHealth(val); // full replace; null is meaningful
          break;
        case "hostMetrics":
          setters.setHostMetrics(val);
          break;
        case "frigateMetrics":
          setters.setFrigateMetrics(val);
          break;
        case "networkMetrics":
          setters.setNetworkMetrics(val);
          break;
        case "roomContext":
          if (setters.setRoomContext) setters.setRoomContext(val);
          break;
        case "visionHealth":
          setters.setVisionHealth(val);
          break;
        case "visionSidecarOnline":
          if (setters.setVisionSidecarOnline) setters.setVisionSidecarOnline(val);
          break;
        case "traceSummary":
          setters.setTraceSummary(val);
          break;
        case "lastTrace":
          setters.setLastTrace(val);
          break;
        case "identity":
          setters.setIdentity(val);
          break;
        case "media":
          setters.setMedia(val);
          break;
        case "cameraLabels":
          setters.setCameraLabels(val);
          break;
        case "cameraStates":
          setters.setSimCameraStates(val);
          break;
        case "connection":
          setters.setConnection(val);
          break;
        case "sidecarOnline":
          setters.setSidecarOnline(val);
          break;
        case "bridgeOnline":
          setters.setBridgeOnline(val);
          break;
        case "aiStackOnline":
          if (setters.setAiStackOnline) setters.setAiStackOnline(val);
          break;
        case "aiStackState":
          if (setters.setAiStackState) setters.setAiStackState(val);
          break;
        case "voice":
          setters.setVoice((prev) => ({ ...prev, ...val }));
          break;
        case "proactive":
          // The proactive coordinator is inert in Simulation Mode; scenarios
          // drive its UI state directly. setters.setProactive already merges
          // (a timeline step may patch just `phase`). See home-app.jsx.
          if (setters.setProactive) setters.setProactive(val);
          break;
        case "events":
          setters.setEvents(val);
          break;
        case "__append":
          setters.setEvents((prev) => [...prev, { id: Math.random().toString(36).slice(2, 10), ...val }]);
          break;
        case "__updateLastAction":
          setters.setEvents((prev) => {
            const out = [...prev];
            for (let i = out.length - 1; i >= 0; i--) {
              if (out[i].kind === "action") {
                out[i] = { ...out[i], ...val };
                break;
              }
            }
            return out;
          });
          break;
        default:
          // Unknown key — ignore. Defensive.
          break;
      }
    }
  }

  function applyScenarioSnapshot(setters, scenarioId) {
    const sc = window.SimScenarios.get(scenarioId);
    if (!sc) {
      // Unknown scenario — fall back to healthy with warning.
      setters.setEvents((prev) => [...prev, {
        id: Math.random().toString(36).slice(2, 10),
        kind: "system",
        text: `[sim] unknown scenario '${scenarioId}' — falling back to 'healthy'. Type /simulation scenarios to list.`,
        tone: "warn",
      }]);
      return applyScenarioSnapshot(setters, "healthy");
    }
    const snap = window.SimScenarios.resolveSnapshot(scenarioId);
    applyToSetters(setters, snap);
    return sc;
  }

  // Module-level timeline ref (single active timeline at a time)
  let _activeTimeline = null;

  function startScenario(setters, scenarioId) {
    if (_activeTimeline) { _activeTimeline.cancel(); _activeTimeline = null; }
    const sc = applyScenarioSnapshot(setters, scenarioId);
    if (sc && Array.isArray(sc.timeline) && sc.timeline.length) {
      _activeTimeline = window.SimTimeline.create();
      _activeTimeline.play(sc.timeline, (delta) => applyToSetters(setters, delta));
    }
  }

  function stopTimelines() {
    if (_activeTimeline) { _activeTimeline.cancel(); _activeTimeline = null; }
  }

  // ── Public hook ───────────────────────────────────────────────────

  function useSimulation(setters) {
    const [state, setState] = React.useState(readInitialState);

    // Apply scenario whenever it changes (or on activation)
    React.useEffect(() => {
      if (!state.active) {
        stopTimelines();
        return;
      }
      window.__SIM_ACTIVE = true;
      startScenario(setters, state.scenario || "healthy");
      return () => { /* cleaned on next change */ };
    }, [state.active, state.scenario]);

    // Persist + flag
    React.useEffect(() => {
      window.__SIM_ACTIVE = !!state.active;
      persist(state.active, state.scenario);
    }, [state.active, state.scenario]);

    const activate = React.useCallback((scenarioId) => {
      setState({ active: true, scenario: scenarioId || "healthy" });
    }, []);
    const deactivate = React.useCallback(() => {
      stopTimelines();
      setState({ active: false, scenario: null });
      window.__SIM_ACTIVE = false;
      // Clear URL params so a reload doesn't auto-reactivate sim mode.
      // History.replaceState avoids a page navigation.
      try {
        if (!isTauri() && typeof window !== "undefined") {
          const url = new URL(window.location.href);
          if (url.searchParams.has("simulation") || url.searchParams.has("scenario")) {
            url.searchParams.delete("simulation");
            url.searchParams.delete("scenario");
            window.history.replaceState({}, "", url.toString());
          }
        }
      } catch (e) { /* ignore */ }
    }, []);
    const setScenario = React.useCallback((id) => {
      setState((prev) => ({ active: true, scenario: id || "healthy" }));
    }, []);
    const reset = React.useCallback(() => {
      stopTimelines();
      setState({ active: true, scenario: "healthy" });
    }, []);

    return { ...state, activate, deactivate, setScenario, reset };
  }

  // ── Slash-command handler ─────────────────────────────────────────

  function _formatScenarioList() {
    const lines = window.SimScenarios.list().map((sc) =>
      `  /sim ${sc.id.padEnd(20)} — ${sc.description}`
    );
    return lines.join("\n");
  }

  function _formatSimHelp(active, currentId) {
    const header = active
      ? `[sim] ACTIVE · scenario: ${currentId || "(none)"}\n`
      : "Simulation Mode lets you exercise every UI state without any real services\n"
        + "(no Home Assistant, no cameras, no model, no network). Type /simulation to enter.\n";
    return header + "\n" + _formatScenarioList()
      + "\n\n  /simulation off       — return to live mode"
      + "\n  /sim reset            — re-apply healthy baseline";
  }

  /* Handle /simulation, /sim — called from HomeApp's handleCommand.
   *
   * Returns true when the command was handled (handled flag for the dispatcher).
   * Posts feedback to chat via addEvent({kind:"system", text, tone}). */
  function handle(cmd, arg, ctx) {
    const { sim, addEvent } = ctx;

    if (cmd === "simulation") {
      const sub = (arg || "").trim().toLowerCase();
      if (!sub) {
        // /simulation — toggle ON to healthy if not active; else show status
        if (!sim.active) {
          sim.activate("healthy");
          addEvent({ kind: "system", text: "[sim] Simulation Mode activated · healthy", tone: "info" });
          addEvent({ kind: "system", text: _formatScenarioList(), tone: "info" });
        } else {
          addEvent({ kind: "system", text: _formatSimHelp(true, sim.scenario), tone: "info" });
        }
        return true;
      }
      if (sub === "off") {
        sim.deactivate();
        addEvent({ kind: "system", text: "[sim] Simulation Mode off — returning to live", tone: "info" });
        return true;
      }
      if (sub === "scenarios" || sub === "list") {
        addEvent({ kind: "system", text: _formatScenarioList(), tone: "info" });
        return true;
      }
      if (sub === "reset") {
        sim.reset();
        addEvent({ kind: "system", text: "[sim] reset to healthy baseline", tone: "info" });
        return true;
      }
      if (sub === "help") {
        addEvent({ kind: "system", text: _formatSimHelp(sim.active, sim.scenario), tone: "info" });
        return true;
      }
      // /simulation <scenarioId> — same as /sim <scenarioId>
      sim.activate(sub);
      addEvent({ kind: "system", text: `[sim] scenario · ${sub}`, tone: "info" });
      return true;
    }

    if (cmd === "sim") {
      const sub = (arg || "").trim().toLowerCase();
      if (!sub) {
        addEvent({ kind: "system", text: "usage: /sim <scenario>  or  /simulation off\n\n" + _formatScenarioList(), tone: "info" });
        return true;
      }
      if (sub === "off") { sim.deactivate(); addEvent({ kind: "system", text: "[sim] off", tone: "info" }); return true; }
      if (sub === "reset") { sim.reset(); addEvent({ kind: "system", text: "[sim] reset", tone: "info" }); return true; }
      if (sub === "scenarios" || sub === "list") {
        addEvent({ kind: "system", text: _formatScenarioList(), tone: "info" });
        return true;
      }
      sim.activate(sub);
      addEvent({ kind: "system", text: `[sim] scenario · ${sub}`, tone: "info" });
      return true;
    }

    return false;
  }

  Object.assign(window, {
    useSimulation,
    SimulationCommand: { handle, formatScenarioList: _formatScenarioList, formatSimHelp: _formatSimHelp },
  });
})();
