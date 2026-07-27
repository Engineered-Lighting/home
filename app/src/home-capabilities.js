/* ============================================================================
 * home-capabilities.js — Addendum 27 A-1: service registry + capability flags.
 *
 * Maps the 8-service stack list to USER-FACING capabilities so the
 * header can show precise "vision down · face rec unavailable" copy
 * instead of vague "ai stack offline". Pure JS, dual-export.
 *
 * A capability is a thing the USER cares about (chat, voice, vision,
 * identity, perception). Each one declares its required services. If
 * a required service's dot is "err" → capability is offline. If "warn"
 * → degraded. If "good" / "cool" → ok.
 *
 * The registry is intentionally narrow: only capabilities the user
 * would notice if they broke. Internal infra (network, haos) isn't
 * a user-facing capability — it's a substrate every other capability
 * inherits.
 * ============================================================================ */

"use strict";

/* Capability metadata. `requires` lists service names matching the
 * shape buildStackList() emits in home-metrics-lab.jsx. `impact`
 * is the user-facing copy when the capability is degraded/offline.
 *
 * Service-name matching is exact — buildStackList normalizes
 * `wyoming-parakeet` (hyphenated) etc. so use the same form here. */
// `affected_tools` lists the LLM tool names that stop working when
// the capability degrades. Used by summarizeCapabilityHealth to add
// precise impact lines in the chip tooltip — e.g., "vision offline ·
// find_clips, describe_clip, refresh_perception unavailable" instead
// of the generic "face captions unavailable". Empty list = no
// agent-visible tool is gated by this capability (chat is one such
// case — losing vllm breaks every tool, so listing individual ones
// adds noise).
// `fallback_hint` (optional): user-facing suggestion for what they
// CAN still do when the capability degrades. Surfaces in the chip
// tooltip below the impact line. Empty/missing → no hint shown.
// The hint is most useful for capabilities where SOME paths still
// work — e.g., voice down doesn't mean the user is locked out, they
// can still type. Vision down doesn't kill find_person (which reads
// HA presence as a fallback). Etc.
const CAPABILITY_REGISTRY = [
  {
    id: "chat",
    label: "chat",
    requires: ["vllm"],
    impact_offline: "agent can't respond. voice + typed both down.",
    impact_degraded: "agent slow or queued.",
    affected_tools: [],   // chat down = every tool down; don't list each
    fallback_hint: "no fallback — chat-down breaks every agent path.",
  },
  {
    id: "voice",
    label: "voice",
    // Voice needs BOTH STT + TTS to fully work. Either alone leaves
    // half the pipeline broken.
    requires: ["wyoming-parakeet", "kokoro"],
    impact_offline: "can't transcribe or speak.",
    impact_degraded: "voice pipeline unstable.",
    affected_tools: [],   // voice is the I/O layer; tools themselves still callable via typed
    fallback_hint: "typed input + slash commands still work.",
  },
  {
    id: "vision",
    label: "vision",
    requires: ["vision-sidecar"],
    impact_offline: "face captions + describe_clip unavailable.",
    impact_degraded: "vision sidecar slow.",
    // Tools that hit the vision-sidecar directly. refresh_perception
    // is the world_state-driven path; describe_clip is the F-3
    // multi-frame path; both route through the sidecar.
    affected_tools: ["refresh_perception", "describe_clip", "look"],
    fallback_hint: "frigate occupancy still works; find_person uses HA presence.",
  },
  {
    id: "perception",
    label: "perception",
    requires: ["frigate"],
    impact_offline: "no occupancy detection or face recognition.",
    impact_degraded: "frigate slow or stale.",
    // Tools that hit Frigate's API or rely on Frigate-derived state.
    // find_clips queries /api/events?search=; find_person + who_is_in +
    // get_room_state + get_all_rooms_state all read world_state which
    // ingests Frigate events; recap composes find_clips + state.
    affected_tools: [
      "find_clips", "find_person", "who_is_in",
      "get_room_state", "get_all_rooms_state", "recap",
    ],
    fallback_hint: "find_person can still report HA presence (device tracker / person.*).",
  },
];

/** Map dot-state → severity. Mirrors buildStackList's tone scheme:
 *  - cool/good → 0 (ok)
 *  - warn → 1 (degraded)
 *  - err  → 2 (offline)
 *  - idle → -1 (unknown; treated as ok for capability purposes —
 *            don't alarm the user about something we haven't probed)
 */
function _severityOf(dot) {
  if (dot === "err") return 2;
  if (dot === "warn") return 1;
  if (dot === "good" || dot === "cool") return 0;
  return -1;  // idle / unknown
}

function _severityLabel(sev) {
  if (sev === 2) return "offline";
  if (sev === 1) return "degraded";
  return "ok";
}

function _rowClassForDot(dot) {
  return dot === "err" || dot === "warn" ? dot : "default";
}

function _setServiceDot(rows, name, dot, meta) {
  const idx = rows.findIndex((s) => s && s.name === name);
  const next = { name, dot, meta: meta || "runtime signal", cls: _rowClassForDot(dot) };
  if (idx >= 0) {
    rows[idx] = { ...rows[idx], ...next };
  } else {
    rows.push(next);
  }
}

function _hasActiveCameraLabels(cameraLabels) {
  return Object.values(cameraLabels || {}).some((labels) =>
    Array.isArray(labels) && labels.length > 0
  );
}

function _allCameraStatesIn(cameraStates, states) {
  const values = Object.values(cameraStates || {});
  return values.length > 0 && values.every((v) => states.includes(v));
}

/** Overlay runtime reachability signals onto the supervisor/service list.
 * buildStackList() is intentionally based on the stack-supervisor payload plus
 * static infra rows. Runtime app state knows more: HA connection, bridge
 * health, Frigate telemetry, camera-label flow, and Simulation Mode outage
 * maps. This helper keeps that interpretation pure/testable so the header and
 * AI drawer can use the same service vocabulary before deriving capabilities.
 */
function deriveRuntimeServiceHealth(services, runtime) {
  const rows = Array.isArray(services)
    ? services.map((s) => ({ ...(s || {}) })).filter((s) => s.name)
    : [];
  runtime = runtime || {};

  if (runtime.sidecarOnline === false) {
    _setServiceDot(rows, "metrics-sidecar", "err", "offline");
  } else if (runtime.sidecarOnline === true) {
    _setServiceDot(rows, "metrics-sidecar", "good", "online");
  }

  if (runtime.bridgeOnline === false) {
    _setServiceDot(rows, "ha bridge", "err", "offline");
    _setServiceDot(rows, "wyoming-parakeet", "err", "bridge offline");
    _setServiceDot(rows, "kokoro", "err", "bridge offline");
    _setServiceDot(rows, "chatterbox-tts", "err", "bridge offline");
    _setServiceDot(rows, "wyoming-kokoro", "err", "bridge offline");
  } else if (runtime.bridgeOnline === true) {
    _setServiceDot(rows, "ha bridge", "good", "online");
  }

  const bridgeSeesHa = runtime.bridgeHealth && runtime.bridgeHealth.ha_connected;
  if (runtime.connection === "auth_invalid") {
    _setServiceDot(rows, "haos", "err", "bad token");
  } else if (runtime.connection === "online" && bridgeSeesHa === true) {
    _setServiceDot(rows, "haos", "good", "connected");
  } else if (runtime.connection === "online" || bridgeSeesHa === true) {
    _setServiceDot(rows, "haos", "warn", "degraded");
  } else if (runtime.connection === "offline" || runtime.connection === "disconnected") {
    _setServiceDot(rows, "haos", "err", "offline");
  }

  if (
    runtime.bridgeOnline !== false &&
    runtime.bridgeHealth &&
    Object.prototype.hasOwnProperty.call(runtime.bridgeHealth, "tts_engine") &&
    !runtime.bridgeHealth.tts_engine
  ) {
    _setServiceDot(rows, "kokoro", "err", "tts unavailable");
    _setServiceDot(rows, "chatterbox-tts", "err", "tts unavailable");
    _setServiceDot(rows, "wyoming-kokoro", "err", "tts unavailable");
  }

  const visionHealthHasOk =
    runtime.visionHealth &&
    Object.prototype.hasOwnProperty.call(runtime.visionHealth, "ok");
  if (runtime.visionSidecarOnline === false || (visionHealthHasOk && runtime.visionHealth.ok === false)) {
    _setServiceDot(rows, "vision-sidecar", "err", "offline");
  } else if (
    runtime.visionSidecarOnline === true ||
    (visionHealthHasOk && runtime.visionHealth.ok === true) ||
    (runtime.visionHealth && !visionHealthHasOk)
  ) {
    _setServiceDot(rows, "vision-sidecar", "good", "online");
  }

  const hasFrigateRuntimeSignal =
    Object.prototype.hasOwnProperty.call(runtime, "frigateMetrics") ||
    Object.prototype.hasOwnProperty.call(runtime, "cameraLabels") ||
    Object.prototype.hasOwnProperty.call(runtime, "cameraStates");
  const frigateExplicitlyStale = _allCameraStatesIn(runtime.cameraStates, ["stale", "offline"]);
  if (runtime.frigateOnline === false || frigateExplicitlyStale) {
    _setServiceDot(rows, "frigate", "err", "offline");
  } else if (runtime.frigateMetrics || _hasActiveCameraLabels(runtime.cameraLabels)) {
    _setServiceDot(rows, "frigate", "good", "online");
  } else if (hasFrigateRuntimeSignal) {
    _setServiceDot(rows, "frigate", "idle", "unknown");
  }

  return rows;
}

/** Compute per-capability health from a services array (the shape
 *  buildStackList returns: [{name, dot, meta, cls}, ...]).
 *  Returns an array of:
 *    { id, label, status: "ok"|"degraded"|"offline", impact, missing: [name, ...] }
 *  Where `missing` lists services whose status pushed the capability
 *  out of "ok" — useful for tooltips and the M5 explain drawer. */
function deriveCapabilityHealth(services) {
  const byName = {};
  if (Array.isArray(services)) {
    for (const s of services) {
      if (s && s.name) byName[s.name] = s;
    }
  }
  const out = [];
  for (const cap of CAPABILITY_REGISTRY) {
    let worst = 0;     // capability severity = worst of its required services
    const missing = [];
    for (const reqName of cap.requires) {
      const s = byName[reqName];
      if (!s) {
        // Service missing from the list altogether → treat as offline.
        // (Not "idle" — buildStackList ALWAYS emits the 8 services,
        // so a true absence here means a misconfigured registry.)
        worst = Math.max(worst, 2);
        missing.push(reqName);
        continue;
      }
      const sev = _severityOf(s.dot);
      if (sev > 0) {
        missing.push(reqName);
        if (sev > worst) worst = sev;
      }
    }
    const status = _severityLabel(worst);
    const impact = status === "offline" ? cap.impact_offline
                 : status === "degraded" ? cap.impact_degraded
                 : null;
    out.push({
      id: cap.id, label: cap.label, status,
      impact, missing,
      // Propagate affected_tools so the chip tooltip can name specific
      // LLM tools that just stopped working. Empty array → no tools
      // explicitly gated by this capability (chat / voice cases).
      affected_tools: Array.isArray(cap.affected_tools) ? cap.affected_tools.slice() : [],
      // fallback_hint surfaces in the tooltip when the cap is non-ok;
      // tells the user what they CAN still do. Empty/missing → no hint.
      fallback_hint: cap.fallback_hint || "",
    });
  }
  return out;
}

/** Summarize capability health into a single chip-friendly payload.
 *  Returns:
 *    {
 *      worst_status: "ok" | "degraded" | "offline",
 *      degraded_count: <int>,
 *      offline_count: <int>,
 *      chip_text:     "N degraded" / "vision offline" / null when ok,
 *      tooltip_text:  "vision: face captions + describe_clip unavailable.\nperception: ..." OR null,
 *    }
 *  When everything is ok, returns null — caller should hide chip
 *  entirely. */
function summarizeCapabilityHealth(services) {
  const caps = deriveCapabilityHealth(services);
  const offline = caps.filter((c) => c.status === "offline");
  const degraded = caps.filter((c) => c.status === "degraded");
  if (offline.length === 0 && degraded.length === 0) return null;
  // Worst status drives the chip's color + headline word.
  const worst = offline.length > 0 ? "offline" : "degraded";
  // Chip text logic:
  //   1 offline cap → "<label> offline"
  //   2+ offline    → "N offline"
  //   else (only degraded) → similar logic for degraded
  let chip;
  if (offline.length === 1) {
    chip = `${offline[0].label} offline`;
  } else if (offline.length > 1) {
    chip = `${offline.length} offline`;
  } else if (degraded.length === 1) {
    chip = `${degraded[0].label} degraded`;
  } else {
    chip = `${degraded.length} degraded`;
  }
  // Tooltip: every non-ok cap renders up to 3 lines:
  //   1. "<label>: <impact>"                  (always)
  //   2. "  tools: a, b, c"                    (if affected_tools non-empty)
  //   3. "  → <fallback_hint>"                 (if fallback_hint non-empty)
  // The arrow prefix marks the fallback line as actionable (what you
  // CAN still do) vs the impact + tools lines which describe what's
  // broken. Helps the user reason about the situation at a glance.
  const tooltipLines = [];
  for (const c of [...offline, ...degraded]) {
    tooltipLines.push(`${c.label}: ${c.impact}`);
    if (Array.isArray(c.affected_tools) && c.affected_tools.length > 0) {
      tooltipLines.push(`  tools: ${c.affected_tools.join(", ")}`);
    }
    if (c.fallback_hint) {
      tooltipLines.push(`  → ${c.fallback_hint}`);
    }
  }
  return {
    worst_status: worst,
    degraded_count: degraded.length,
    offline_count: offline.length,
    chip_text: chip,
    tooltip_text: tooltipLines.join("\n"),
    capabilities: caps,
  };
}

// Dual export
if (typeof window !== "undefined") {
  window.CAPABILITY_REGISTRY = CAPABILITY_REGISTRY;
  window.deriveRuntimeServiceHealth = deriveRuntimeServiceHealth;
  window.deriveCapabilityHealth = deriveCapabilityHealth;
  window.summarizeCapabilityHealth = summarizeCapabilityHealth;
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    CAPABILITY_REGISTRY,
    deriveRuntimeServiceHealth,
    deriveCapabilityHealth,
    summarizeCapabilityHealth,
  };
}
