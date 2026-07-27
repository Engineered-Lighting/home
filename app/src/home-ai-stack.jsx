/* Home — AI Stack control card.
 *
 * Current scope: supervisor status, per-service chips, start/restart/stop,
 * free-GPU, and the SSE log drawer. The supervisor endpoint we hit for
 * start/restart is
 *   POST /api/stack/start                          → 202 + { task_id }
 *                                                 or 409 + { task_id, ... }
 *   GET  /api/stack/logs/stream?task_id=<id>      → text/event-stream
 *
 * EventSource can't send custom headers, so we use the streamSse helper
 * from home-sse-fetch.js (fetch + ReadableStream).
 *
 * State + polling still live in HomeApp (home-app.jsx); this component
 * just consumes `aiStackOnline` / `aiStackState` from props and owns its
 * own ephemeral action state (which start/stop/restart it spawned + a
 * bounded local log buffer for the drawer).
 *
 * Patterns reused: HmCard, HmDepHealthStrip from home-metrics.jsx (those
 * are exported to window under Hm* names — see home-metrics.jsx:1312).
 * Hooks accessed via `React.useState` etc. (each text/babel script has
 * its own scope; same pattern as home-proactive.jsx).
 */

const HG_FONT_MONO = "'Geist Mono', ui-monospace, monospace";

const _STARTABLE = new Set(["offline", "down", "partial", "error", "unknown"]);
const _RESTARTABLE = new Set(["ready", "warming"]);
const _STOPPABLE = new Set(["ready", "warming"]);
const AI_STACK_SERVICE_RESTARTS = Object.freeze([
  { verb: "restart_vllm", service: "vllm", label: "restart vllm" },
  { verb: "restart_parakeet", service: "wyoming-parakeet", label: "restart parakeet" },
  { verb: "restart_kokoro", service: "kokoro", label: "restart kokoro" },
  { verb: "restart_vision_sidecar", service: "vision-sidecar", label: "restart vision" },
  { verb: "restart_metrics_sidecar", service: "metrics-sidecar", label: "restart metrics" },
]);
const AI_STACK_VERBS = Object.freeze([
  "start",
  "stop",
  "restart",
  "free_gpu",
  ...AI_STACK_SERVICE_RESTARTS.map((item) => item.verb),
]);

function _aiStackServiceRestartSpec(verb) {
  return AI_STACK_SERVICE_RESTARTS.find((item) => item.verb === verb) || null;
}

function _aiStackConfirmButtonLabel(confirmVerb, verb, label) {
  return confirmVerb === verb ? "✓ confirm" : label;
}

/* Tone for the overall badge / dot. Mirrors the dep-strip palette:
 *   ready              -> ok       (alive)
 *   warming | partial  -> warn     (in-progress / degraded)
 *   starting|stopping  -> warn     (transitional)
 *   offline|error      -> crit     (red)
 *   unknown            -> idle     (supervisor unreachable)
 */
function _overallTone(overall) {
  switch (overall) {
    case "ready":                   return "ok";
    case "warming":
    case "partial":
    case "starting":
    case "stopping":                return "warn";
    case "offline":
    case "down":
    case "error":                   return "crit";
    case "unknown":
    default:                        return "idle";
  }
}

function _serviceTone(svc) {
  if (svc.container === "exited" || svc.container === "missing") return "crit";
  if (svc.container === "starting") return "warn";
  if (svc.probe === "fail") return "warn";
  if (svc.health === "unhealthy") return "crit";
  if (svc.health === "starting") return "warn";
  return "ok";
}

function _serviceState(svc) {
  if (svc.container === "missing") return "missing";
  if (svc.container === "exited") return "exited";
  if (svc.container === "starting") return "starting";
  if (svc.probe === "fail") return svc.detail || "probe fail";
  if (svc.health === "unhealthy") return "unhealthy";
  if (svc.health === "starting") return "warming";
  return "up";
}

function _fmtElapsed(startedAtIso) {
  if (!startedAtIso) return "";
  const t0 = Date.parse(startedAtIso);
  if (!isFinite(t0)) return "";
  const s = Math.max(0, Math.round((Date.now() - t0) / 1000));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function _aiStackHasReadySignals(aiStackState = {}) {
  const services = Array.isArray(aiStackState.services) ? aiStackState.services : [];
  const requiredServicesUp = services.length > 0 && services.every((svc) =>
    svc &&
    svc.container !== "missing" &&
    svc.container !== "exited" &&
    svc.container !== "starting" &&
    svc.health !== "unhealthy" &&
    svc.health !== "starting" &&
    svc.probe !== "fail"
  );
  const expected = aiStackState.expected_vllm_model;
  const loaded = aiStackState.vllm_model_loaded;
  const modelReady = !expected || loaded === expected;
  return requiredServicesUp && modelReady;
}

function _aiStackEffectiveState(aiStackState = {}) {
  const overall = aiStackState.overall || "unknown";
  const readySignals = _aiStackHasReadySignals(aiStackState);
  const staleStarting =
    readySignals &&
    (overall === "starting" || overall === "warming") &&
    aiStackState.in_flight &&
    (aiStackState.in_flight.recovered || aiStackState.in_flight.verb === "start");
  return {
    overall: staleStarting ? "ready" : overall,
    inFlight: staleStarting ? null : aiStackState.in_flight,
    staleStarting,
  };
}

function _aiStackCardActionState(overall, aiStackState = {}, action = { kind: "idle" }) {
  const effective = _aiStackEffectiveState({ ...aiStackState, overall });
  const supervisorInFlight = !!effective.inFlight;
  const localBusy = action && (action.kind === "starting" || action.kind === "streaming");
  const busy = supervisorInFlight || localBusy;
  const effectiveOverall = effective.overall;
  const serviceRestartable = ["ready", "warming", "partial", "error", "down"].includes(effectiveOverall);
  return {
    showStart: _STARTABLE.has(effectiveOverall) && !busy,
    showRestart: _RESTARTABLE.has(effectiveOverall) && !busy,
    showStop: _STOPPABLE.has(effectiveOverall) && !busy,
    showFreeGpu: _STOPPABLE.has(effectiveOverall) && !busy,
    showServiceRestarts: serviceRestartable && !busy,
    showProgress: busy,
    showTerminal: action && (action.kind === "done" || action.kind === "error"),
  };
}

function _aiStackLogsStreamUrl(supervisorUrl, taskId) {
  const HSA = window.HomeStackActions;
  if (HSA && typeof HSA.logsStreamUrl === "function") {
    return HSA.logsStreamUrl({ supervisorUrl, taskId });
  }
  const base = String(supervisorUrl || "").replace(/\/+$/, "");
  return `${base}/api/stack/logs/stream?task_id=${encodeURIComponent(taskId)}`;
}

function _aiStackEmptyState({ tokenConfigured, aiStackOnline, aiStackState, statusError }) {
  if (!tokenConfigured) {
    return {
      badge: { text: "not configured", tone: "warn" },
      body: "STACK_TOKEN is not configured on this workstation. Use /stack-token with the token from /opt/home-ai-voice/.env.",
    };
  }
  if (aiStackOnline === null) {
    return {
      badge: { text: "polling", tone: "ice" },
      body: "Contacting supervisor...",
    };
  }
  if (aiStackOnline === false) {
    return {
      badge: { text: "supervisor offline", tone: "crit" },
      body: "Cannot reach the stack supervisor at :8093. Stack status unknown - Home app remains in degraded mode.",
    };
  }
  if (!aiStackState) {
    if (statusError === "auth") {
      return {
        badge: { text: "auth failed", tone: "crit" },
        body: "Supervisor is reachable, but STACK_TOKEN was rejected. Use /stack-token with the token from /opt/home-ai-voice/.env.",
      };
    }
    if (statusError === "rate_limited") {
      return {
        badge: { text: "rate limited", tone: "warn" },
        body: "Supervisor is reachable, but status auth is rate-limited. Wait briefly, then retry.",
      };
    }
    return {
      badge: { text: "status unavailable", tone: "warn" },
      body: statusError
        ? `Supervisor is reachable, but stack status is unavailable (${statusError}).`
        : "Supervisor is reachable, but stack status is unavailable.",
    };
  }
  return null;
}

function _aiStackFailureDiagnosis({ action = {}, logLines = [] } = {}) {
  const failed =
    action.kind === "error" ||
    (action.kind === "done" && action.exit_code !== 0 && action.exit_code !== null);
  if (!failed) return null;

  const text = [
    action.error,
    ...(Array.isArray(logLines) ? logLines : []),
  ].filter(Boolean).join("\n").toLowerCase();

  if (
    text.includes("oci runtime create failed") ||
    text.includes("failed to create shim task") ||
    text.includes("prestart hook") ||
    text.includes("nvidia-container") ||
    text.includes("auto-detected mode as 'legacy'")
  ) {
    return {
      code: "gpu_runtime",
      tone: "crit",
      title: "GPU runtime failed",
      detail: "The app reached the supervisor and Docker accepted the start request, then container creation failed before the AI process launched.",
      next: "Check the AI box GPU, NVIDIA Container Toolkit, and Docker GPU runtime before retrying the stack start.",
    };
  }

  if (text.includes("no space left on device")) {
    return {
      code: "disk_full",
      tone: "crit",
      title: "AI box storage is full",
      detail: "Docker could not create or start one of the stack containers because the host or Docker data volume is out of space.",
      next: "Free disk space on the AI box or prune old Docker artifacts, then retry the stack start.",
    };
  }

  if (text.includes("address already in use") || text.includes("bind:")) {
    return {
      code: "port_conflict",
      tone: "warn",
      title: "Port conflict",
      detail: "A stack container could not bind one of its expected ports because another process is already using it.",
      next: "Find the process holding the port on the AI box or stop the duplicate container before retrying.",
    };
  }

  if (
    text.includes("pull access denied") ||
    text.includes("manifest unknown") ||
    text.includes("repository does not exist") ||
    text.includes("not found")
  ) {
    return {
      code: "image_unavailable",
      tone: "warn",
      title: "Container image unavailable",
      detail: "Docker could not find or pull one of the images needed by the AI stack.",
      next: "Check image names, tags, registry access, and cached images on the AI box before retrying.",
    };
  }

  if (text.includes("cannot connect to the docker daemon") || text.includes("is the docker daemon running")) {
    return {
      code: "docker_unavailable",
      tone: "crit",
      title: "Docker unavailable",
      detail: "The supervisor could not talk to Docker on the AI box.",
      next: "Check Docker daemon health on the AI box, then retry from the Home app after status recovers.",
    };
  }

  return {
    code: "unknown",
    tone: "warn",
    title: "Stack action failed",
    detail: "The supervisor command exited unsuccessfully, but the streamed logs did not match a known failure pattern.",
    next: "Open the full task logs and AI box Docker logs for the failing service before retrying.",
  };
}


/* Phase 2 — small "pill button" with status-bar styling that matches
 * the rest of the metrics drawer. Kept inline so we don't add a new
 * component to home-metrics.jsx unless we need to reuse it elsewhere.
 */
function _ActionButton({ children, onClick, disabled, tone = "ok" }) {
  const c =
    tone === "warn" ? "var(--hg-warn)" :
    tone === "crit" ? "var(--hg-crit)" :
                      "var(--hg-ice-bright)";
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        fontFamily: HG_FONT_MONO,
        fontSize: 10.5,
        letterSpacing: "0.12em",
        textTransform: "uppercase",
        color: disabled ? "var(--hg-fg-5)" : c,
        background: "transparent",
        border: `1px solid ${disabled ? "var(--hg-border-soft)" : c}`,
        borderRadius: 2,
        padding: "4px 10px",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.6 : 1,
      }}
    >{children}</button>
  );
}


/* ── AiStackCard (presentational + Phase-2 action) ─────────────────── */
function AiStackCard({
  aiStackOnline,
  aiStackState,
  aiStackStatusError = null,
  tokenConfigured,
  supervisorUrl,
  stackToken,
}) {
  // ── Phase 2 ephemeral action state ────────────────────────────────
  //   "idle"      -> no action currently dispatched by this card
  //   "starting"  -> POST sent, awaiting 202/409
  //   "streaming" -> SSE attached, log lines coming in
  //   "done"      -> terminal 'done' event received, exit_code captured
  //   "error"     -> network or HTTP error during start/attach
  const [action, setAction] = React.useState({ kind: "idle" });
  const [logLines, setLogLines] = React.useState([]);
  const [expanded, setExpanded] = React.useState(false);
  // Inline two-click confirmation shared by every stack-control verb.
  const [confirmVerb, setConfirmVerb] = React.useState(null);
  const streamCtrlRef = React.useRef(null);
  const confirmTimerRef = React.useRef(null);

  // Cleanup the SSE stream on unmount (e.g. tab switch unmounts MetricsStrip).
  React.useEffect(() => {
    return () => {
      if (streamCtrlRef.current) {
        try { streamCtrlRef.current.abort(); } catch {}
        streamCtrlRef.current = null;
      }
      if (confirmTimerRef.current) {
        try { clearTimeout(confirmTimerRef.current); } catch {}
        confirmTimerRef.current = null;
      }
    };
  }, []);

  // Attach to a known task_id's SSE log stream. Used by both "fresh start"
  // (after 202) and "attach to existing" (after 409). Idempotent — if a
  // previous stream is still open we abort it first.
  const attachStream = React.useCallback((taskId, verb) => {
    if (streamCtrlRef.current) {
      try { streamCtrlRef.current.abort(); } catch {}
      streamCtrlRef.current = null;
    }
    setAction({ kind: "streaming", taskId, verb });
    setLogLines([]);
    const url = _aiStackLogsStreamUrl(supervisorUrl, taskId);
    streamCtrlRef.current = window.streamSse(
      url,
      { Authorization: `Bearer ${stackToken}` },
      {
        onLine: (line) => {
          setLogLines((prev) => {
            // bounded local copy — 200 lines max, latest at end
            if (prev.length >= 200) return [...prev.slice(-199), line];
            return [...prev, line];
          });
        },
        onEvent: (event, data) => {
          if (event === "done") {
            setAction({ kind: "done", taskId, verb, exit_code: data?.exit_code ?? null });
            if (streamCtrlRef.current) {
              try { streamCtrlRef.current.abort(); } catch {}
              streamCtrlRef.current = null;
            }
          }
        },
        onError: (err) => {
          setAction({
            kind: "error",
            taskId,
            verb,
            error: (err && err.message) ? err.message : String(err),
          });
        },
      }
    );
  }, [supervisorUrl, stackToken]);

  // Click handler — POST /api/stack/<verb>, attach to the resulting (or
  // existing) task's log stream. For verb=="stop" we add the
  // X-Confirm: stop-ai-stack header (backend rejects stop without it).
  const dispatch = React.useCallback(async (verb) => {
    setAction({ kind: "starting", verb });
    setLogLines([]);
    setExpanded(false);
    setConfirmVerb(null);
    // Phase 4A.10: delegated to window.HomeStackActions so the new lab
    // tab can reuse the same auth + retry logic without duplication.
    // Behavior unchanged from the prior inline impl (verbs map to the
    // module's start/restart/stop wrappers; SSE attach is still local).
    const HSA = window.HomeStackActions;
    if (!HSA) {
      setAction({ kind: "error", verb, error: "HomeStackActions not loaded" });
      return;
    }
    const serviceSpec = _aiStackServiceRestartSpec(verb);
    let result;
    if (verb === "start")   result = await HSA.start({ supervisorUrl, stackToken });
    else if (verb === "restart") result = await HSA.restart({ supervisorUrl, stackToken });
    else if (verb === "stop")    result = await HSA.stop({ supervisorUrl, stackToken });
    else if (verb === "free_gpu") result = await HSA.freeGpu({ supervisorUrl, stackToken });
    else if (serviceSpec) result = await HSA.serviceRestart({ supervisorUrl, stackToken, service: serviceSpec.service });
    else {
      setAction({ kind: "error", verb, error: `unknown verb: ${verb}` });
      return;
    }
    if (result.ok) {
      const taskId = HSA.extractTaskId(result);
      const usedVerb = serviceSpec || verb === "free_gpu"
        ? verb
        : HSA.extractVerb(result, verb);
      if (taskId) {
        attachStream(taskId, usedVerb);
      } else {
        setAction({
          kind: "done",
          taskId: null,
          verb: usedVerb,
          exit_code: result.body?.exit_code ?? 0,
        });
      }
    } else {
      setAction({ kind: "error", verb, error: result.error || `http ${result.status}` });
    }
  }, [supervisorUrl, stackToken, attachStream]);

  const confirmOrDispatch = React.useCallback((verb) => {
    if (confirmVerb !== verb) {
      setConfirmVerb(verb);
      if (confirmTimerRef.current) {
        try { clearTimeout(confirmTimerRef.current); } catch {}
      }
      confirmTimerRef.current = setTimeout(() => {
        setConfirmVerb((current) => (current === verb ? null : current));
        confirmTimerRef.current = null;
      }, 3000);
      return;
    }
    if (confirmTimerRef.current) {
      try { clearTimeout(confirmTimerRef.current); } catch {}
      confirmTimerRef.current = null;
    }
    setConfirmVerb(null);
    dispatch(verb);
  }, [confirmVerb, dispatch]);

  // ── Empty-state renders ───────────────────────────────────────────
  const emptyState = _aiStackEmptyState({
    tokenConfigured,
    aiStackOnline,
    aiStackState,
    statusError: aiStackStatusError,
  });
  if (emptyState) {
    return (
      <HmCard title="AI STACK" badge={emptyState.badge}>
        <div style={{
          fontFamily: HG_FONT_MONO, fontSize: 10.5,
          color: "var(--hg-fg-4)", lineHeight: 1.5,
        }}>
          {emptyState.body}
        </div>
      </HmCard>
    );
  }

  // ── Live state derivations ────────────────────────────────────────
  const effective = _aiStackEffectiveState(aiStackState);
  const overall = effective.overall;
  const overallTone = _overallTone(overall);
  const serviceChips = (aiStackState.services || []).map((s) => ({
    label: s.name,
    state: _serviceState(s),
    tone: _serviceTone(s),
    hint: s.detail
      ? `${s.name} · ${s.container} · ${s.health} · ${s.probe_kind}: ${s.probe} (${s.detail})`
      : `${s.name} · ${s.container} · ${s.health} · ${s.probe_kind}: ${s.probe}`,
  }));

  const inflight = effective.inFlight;
  const inflightLine = inflight
    ? `${inflight.verb}... ${_fmtElapsed(inflight.started_at)}${inflight.recovered ? " (recovered)" : ""}`
    : null;

  const modelLoaded = aiStackState.vllm_model_loaded;
  const modelExpected = aiStackState.expected_vllm_model;
  const modelEl =
    modelExpected
      ? (modelLoaded === modelExpected
          ? <span style={{ color: "var(--hg-fg-2)" }}>{modelExpected}</span>
          : modelLoaded
            ? <span style={{ color: "var(--hg-warn)" }}>
                {modelLoaded} (expected {modelExpected})
              </span>
            : <span style={{ color: "var(--hg-fg-5)" }}>not loaded</span>)
      : null;

  // ── Action row decision ───────────────────────────────────────────
  const {
    showStart,
    showRestart,
    showStop,
    showFreeGpu,
    showServiceRestarts,
    showProgress,
    showTerminal,
  } = _aiStackCardActionState(overall, aiStackState, action);

  const lastFew = expanded ? logLines : logLines.slice(-6);
  const terminalDiagnosis = showTerminal
    ? _aiStackFailureDiagnosis({ action, logLines })
    : null;

  return (
    <HmCard title="AI STACK" badge={{ text: overall, tone: overallTone }}>
      <HmDepHealthStrip items={serviceChips} />
      {inflightLine && (
        <div style={{
          fontFamily: HG_FONT_MONO, fontSize: 10.5,
          color: overallTone === "warn" ? "var(--hg-warn)" : "var(--hg-fg-4)",
          marginTop: 8, letterSpacing: "0.08em",
        }}>{inflightLine}</div>
      )}
      {modelEl && (
        <div style={{
          fontFamily: HG_FONT_MONO, fontSize: 9.5,
          color: "var(--hg-fg-5)", marginTop: 6, letterSpacing: "0.08em",
        }}>model: {modelEl}</div>
      )}
      {effective.staleStarting && (
        <div style={{
          fontFamily: HG_FONT_MONO,
          fontSize: 9.5,
          color: "var(--hg-fg-5)",
          marginTop: 6,
          letterSpacing: "0.08em",
        }}>supervisor task recovered; services are ready</div>
      )}

      {/* Stack actions use the same inline confirm pattern as Lab controls. */}
      {(showStart || showRestart || showStop || showFreeGpu) && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
          {showStart && (
            <_ActionButton onClick={() => confirmOrDispatch("start")} tone="ok">
              {_aiStackConfirmButtonLabel(confirmVerb, "start", "start ai stack")}
            </_ActionButton>
          )}
          {showRestart && (
            <_ActionButton onClick={() => confirmOrDispatch("restart")} tone="warn">
              {_aiStackConfirmButtonLabel(confirmVerb, "restart", "restart")}
            </_ActionButton>
          )}
          {showStop && (
            <_ActionButton onClick={() => confirmOrDispatch("stop")} tone="crit">
              {_aiStackConfirmButtonLabel(confirmVerb, "stop", "stop ai stack")}
            </_ActionButton>
          )}
          {showFreeGpu && (
            <_ActionButton onClick={() => confirmOrDispatch("free_gpu")} tone="crit">
              {_aiStackConfirmButtonLabel(confirmVerb, "free_gpu", "free gpu")}
            </_ActionButton>
          )}
        </div>
      )}

      {/* Service restart shortcuts share the same two-click guard. */}
      {showServiceRestarts && (
        <div style={{ marginTop: 10 }}>
          <div style={{
            fontFamily: HG_FONT_MONO,
            fontSize: 9.5,
            color: "var(--hg-fg-5)",
            letterSpacing: "0.14em",
            textTransform: "uppercase",
            marginBottom: 6,
          }}>service restarts</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {AI_STACK_SERVICE_RESTARTS.map((spec) => (
              <_ActionButton
                key={spec.verb}
                onClick={() => confirmOrDispatch(spec.verb)}
                tone="warn"
              >
                {_aiStackConfirmButtonLabel(confirmVerb, spec.verb, spec.label)}
              </_ActionButton>
            ))}
          </div>
        </div>
      )}

      {showProgress && (
        <div style={{
          marginTop: 10,
          padding: "6px 8px",
          background: "var(--hg-bg-2, rgba(255,255,255,0.04))",
          border: "1px solid var(--hg-border-soft)",
          borderRadius: 2,
        }}>
          <div style={{
            fontFamily: HG_FONT_MONO, fontSize: 9.5,
            color: "var(--hg-warn)", letterSpacing: "0.12em",
            textTransform: "uppercase", marginBottom: 4,
            display: "flex", justifyContent: "space-between",
          }}>
            <span>
              {action.kind === "starting" ? "dispatching..." :
               action.kind === "streaming" ? `${action.verb}...` :
               "in progress..."}
            </span>
            {logLines.length > 0 && (
              <span
                onClick={() => setExpanded((x) => !x)}
                style={{ cursor: "pointer", color: "var(--hg-fg-5)" }}
              >
                {expanded ? "[ collapse ]" : `[ ${logLines.length} lines ]`}
              </span>
            )}
          </div>
          {lastFew.length > 0 && (
            <div style={{
              fontFamily: HG_FONT_MONO, fontSize: 10,
              color: "var(--hg-fg-3)",
              maxHeight: expanded ? 300 : undefined,
              overflowY: expanded ? "auto" : "visible",
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
              lineHeight: 1.4,
            }}>
              {lastFew.map((ln, i) => <div key={i}>{ln}</div>)}
            </div>
          )}
        </div>
      )}

      {showTerminal && (
        <div style={{
          marginTop: 10,
          padding: "6px 8px",
          background: "var(--hg-bg-2, rgba(255,255,255,0.04))",
          border: "1px solid var(--hg-border-soft)",
          borderRadius: 2,
        }}>
          <div style={{
            fontFamily: HG_FONT_MONO, fontSize: 10.5,
            color: action.kind === "done" && action.exit_code === 0
              ? "var(--hg-fg-2)"
              : "var(--hg-warn)",
            letterSpacing: "0.08em",
          }}>
            {action.kind === "done"
              ? (action.exit_code === 0
                  ? `${action.verb} succeeded (exit ${action.exit_code})`
                  : `${action.verb} finished with exit ${action.exit_code}`)
              : `${action.verb || "action"} failed: ${action.error}`}
          </div>
          {terminalDiagnosis && (
            <div style={{
              marginTop: 8,
              padding: "7px 8px",
              border: `1px solid ${terminalDiagnosis.tone === "crit" ? "var(--hg-crit)" : "var(--hg-warn)"}`,
              borderRadius: 2,
              background: terminalDiagnosis.tone === "crit"
                ? "rgba(255,80,80,0.08)"
                : "rgba(255,180,80,0.08)",
            }}>
              <div style={{
                fontFamily: HG_FONT_MONO,
                fontSize: 10,
                color: terminalDiagnosis.tone === "crit" ? "var(--hg-crit)" : "var(--hg-warn)",
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                marginBottom: 5,
              }}>{terminalDiagnosis.title}</div>
              <div style={{
                fontFamily: HG_FONT_MONO,
                fontSize: 10,
                color: "var(--hg-fg-3)",
                lineHeight: 1.45,
              }}>{terminalDiagnosis.detail}</div>
              <div style={{
                fontFamily: HG_FONT_MONO,
                fontSize: 10,
                color: "var(--hg-fg-4)",
                lineHeight: 1.45,
                marginTop: 4,
              }}>next: {terminalDiagnosis.next}</div>
            </div>
          )}
          {logLines.length > 0 && (
            <div style={{
              fontFamily: HG_FONT_MONO, fontSize: 10,
              color: "var(--hg-fg-4)",
              marginTop: 6,
              maxHeight: expanded ? 300 : 80,
              overflowY: "auto",
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
              lineHeight: 1.4,
            }}>
              {(expanded ? logLines : logLines.slice(-6)).map((ln, i) => <div key={i}>{ln}</div>)}
            </div>
          )}
          <div style={{ marginTop: 6 }}>
            <span
              onClick={() => setAction({ kind: "idle" })}
              style={{
                fontFamily: HG_FONT_MONO, fontSize: 9.5,
                color: "var(--hg-fg-5)",
                cursor: "pointer",
                letterSpacing: "0.12em",
                textTransform: "uppercase",
              }}
            >[ dismiss ]</span>
            {logLines.length > 6 && (
              <span
                onClick={() => setExpanded((x) => !x)}
                style={{
                  fontFamily: HG_FONT_MONO, fontSize: 9.5,
                  color: "var(--hg-fg-5)",
                  cursor: "pointer",
                  marginLeft: 10,
                  letterSpacing: "0.12em",
                  textTransform: "uppercase",
                }}
              >{expanded ? "[ collapse logs ]" : "[ show all logs ]"}</span>
            )}
          </div>
        </div>
      )}

    </HmCard>
  );
}

/* Export to window so home-app.jsx can pick it up. */
Object.assign(window, { AiStackCard });
