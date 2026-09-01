/* home-metrics-lab.jsx â€” Lab tab (Addendum 10 in keen-doodling-parasol.md)
 *
 * NEW metrics dashboard. Coexists with the existing AI + INFRA tabs;
 * non-destructive. Centerpiece is a time-aligned chart correlating
 * voice-call stages with GPU / VRAM / CPU / RAM. Two render modes:
 *   HISTORY â€” 21 recent calls in sequence, horizontal scroll
 *   NOW     â€” single selected/current turn expanded to card width
 *
 * Driven by composite buffers (labTurnsRef + labSamplesRef) populated
 * in home-app.jsx from the existing metrics + trace polls. Sim mode
 * injects fixtures via window.useSimulation's snapshot.lab key.
 *
 * Helpers (deriveLabTier / computeBaseline) are pure functions exposed
 * via window for unit tests.
 *
 * Hand-rolled SVG (no chart library) â€” matches HmSparkline precedent.
 */

const { useState, useEffect, useRef, useCallback, useMemo } = React;

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Constants â€” colors, thresholds, layout
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */

const STAGE_TINT = {
  // Pure neutral ink â€” matches the AI tab "LAST VOICE TURN" card,
  // which uses the same gray/white stack with no blue accent. Color
  // (amber / red) is reserved exclusively for tier-driven warn/crit
  // signals via STAGE_TINT_SLOW.
  // Phase 8 (theme pinning): entries are { color, opacity } consumed
  // as SVG fill + fillOpacity (see tintFillProps). color is the fg-0
  // ink token, so dark renders pixel-identical to the former
  // rgba(255,255,255,a) fills and the light "paper" theme renders
  // ink-on-paper instead of invisible white-on-white.
  stt:      { color: "var(--hg-fg-0)", opacity: 0.18 },
  llm:      { color: "var(--hg-fg-0)", opacity: 0.40 },
  synth:    { color: "var(--hg-fg-0)", opacity: 0.55 },
  audio:    { color: "var(--hg-fg-0)", opacity: 0.75 },
  // 2026-05-18: typed-turn stages derived from sidecar ttft_observed_ms.
  // prefill = model loading prompt â†’ first token (mostly KV-cache work).
  // gen     = token-by-token streaming after first token. Brighter than
  // prefill so visually you read "thinking â†’ speaking" left-to-right.
  prefill:  { color: "var(--hg-fg-0)", opacity: 0.28 },
  gen:      { color: "var(--hg-fg-0)", opacity: 0.62 },
  // External-routed turns: same neutral palette but lower opacity so
  // the bar reads as a dimmer/ghosted version of a local turn â€”
  // visually conveys "local stack was idle, this was off-box work".
  ext_req:  { color: "var(--hg-fg-0)", opacity: 0.10 },
  ext_api:  { color: "var(--hg-fg-0)", opacity: 0.22 },
  ext_recv: { color: "var(--hg-fg-0)", opacity: 0.35 },
  external: { color: "var(--hg-fg-0)", opacity: 0.28 },
  // Ambient â€” pre-turn background-activity segment. Very dim so the
  // user reads it as "context, not the actual LLM call". The line
  // graphs use the same window so background GPU/CPU spikes from
  // perception dispatches (which don't have their own bar) surface
  // in the bar that came right after them.
  ambient:  { color: "var(--hg-fg-0)", opacity: 0.05 },
};

// Addendum 29 Change 4: tool segments spliced between LLM rounds get
// a distinct (faintly cooler) tint so the user can visually distinguish
// "model thinking" (prefill/gen) from "service work" (tool dispatch).
// The lookup below tries the literal tool name first, then falls back
// to TOOL_DEFAULT_TINT. New tools added to const.py default tools
// don't need a manual STAGE_TINT entry to render â€” they just inherit
// the default tool color.
const TOOL_DEFAULT_TINT = "rgba(120,180,240,0.45)";  // muted cyan
const TOOL_DEFAULT_TINT_SLOW = "rgba(255,178,90,0.55)";
const STAGE_TINT_TOOL_LOOKUP = (label) => {
  if (!label) return null;
  // Named tints for the most-common tools so they read distinctly
  // when several appear in one bar.
  const named = {
    who_is_in:          "rgba(120,180,240,0.55)",
    find_person:        "rgba(120,180,240,0.45)",
    get_room_state:     "rgba(100,160,220,0.55)",
    get_all_rooms_state:"rgba(100,160,220,0.50)",
    refresh_perception: "rgba(140,200,250,0.60)",
    find_clips:         "rgba(150,210,200,0.55)",
    describe_clip:      "rgba(150,210,200,0.60)",
    recap:              "rgba(170,220,180,0.55)",
    execute_services:   "rgba(200,180,140,0.55)",
    invoke_script:      "rgba(200,180,140,0.60)",
    get_attributes:     "rgba(180,170,210,0.50)",
    get_history:        "rgba(180,170,210,0.55)",
    entities_in_area:   "rgba(170,200,170,0.50)",
    find_entity:        "rgba(170,200,170,0.55)",
  };
  return named[label] || TOOL_DEFAULT_TINT;
};

const STAGE_TINT_SLOW = {
  stt:      "rgba(255,200,120,0.22)",
  llm:      "rgba(255,200,120,0.50)",
  synth:    "rgba(255,200,120,0.40)",
  audio:    "rgba(255,178,90,0.78)",
  prefill:  "rgba(255,200,120,0.34)",
  gen:      "rgba(255,178,90,0.65)",
  ext_req:  "rgba(255,200,120,0.14)",
  ext_api:  "rgba(255,178,90,0.32)",
  ext_recv: "rgba(255,178,90,0.50)",
  external: "rgba(255,178,90,0.35)",
  ambient:  "rgba(255,200,120,0.06)",
};

/* Phase 8: resolve a stage-tint spec to SVG fill props. Token-based
 * STAGE_TINT entries are { color, opacity } â†’ fill + fillOpacity so the
 * ink token re-themes per theme while the alpha stays the dark-mode
 * value. Legacy string specs (amber STAGE_TINT_SLOW + the cyan tool
 * family â€” semantic hues) pass through with their alpha baked into the
 * rgba and no fillOpacity attribute. */
function tintFillProps(spec) {
  if (spec && typeof spec === "object") {
    return { fill: spec.color, fillOpacity: spec.opacity };
  }
  return { fill: spec, fillOpacity: undefined };
}

const LAB_THRESHOLDS = {
  vram: { warn: 0.85, crit: 0.95 },
  gpu:  { warn: 0.90, crit: 0.97 },
  cpu:  { warn: 0.80, crit: 0.95 },
  ram:  { warn: 0.75, crit: 0.90 },
};

const HG_FONT_MONO = "'Geist Mono', ui-monospace, monospace";
const HG_FONT_SANS = "'Geist', system-ui, sans-serif";

function isLabMobileViewport() {
  if (typeof window === "undefined") return false;
  const vp = window.__hav_viewport;
  const width = Math.round(
    (window.visualViewport && window.visualViewport.width) ||
    window.innerWidth ||
    (vp && vp.width) ||
    1024
  );
  return !!(vp && vp.mobile) || width < 700;
}

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Pure helpers â€” exposed via window for unit tests
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */

/* deriveLabTier â€” single-tier classifier from multi-signal world.
 * Priority (highest wins): offline > error > pressured > partial >
 * degraded > slow > warming > ready. Returns { tier, label, sub,
 * actions, banner, progress, chartBadge, chipTone, statusWord,
 * statusWordTone }. */
function deriveLabTier(metrics, aiStackState, lastTrace, baseline) {
  const helper = (typeof window !== "undefined" && window.HomeMetricsLabHelpers) || null;
  if (helper && typeof helper.deriveLabTier === "function") {
    return helper.deriveLabTier(metrics, aiStackState, lastTrace, baseline);
  }

  metrics = metrics || {};
  aiStackState = aiStackState || {};
  baseline = baseline || {};

  const overall = aiStackState.overall || "unknown";
  const services = aiStackState.services || [];
  const inFlight = aiStackState.in_flight;
  const vramPct = (metrics.vramMax || 0) > 0
    ? Math.min(1, (metrics.vram || 0) / metrics.vramMax)
    : 0;
  const lastTurnMs = lastTrace?.t_done || lastTrace?.totalMs || 0;
  const baselineP50 = baseline.p50 || null;

  // Service health roll-up
  const downServices = services.filter((s) =>
    s.health === "offline" || s.container === "exited" || s.container === "dead"
  );
  const errorServices = services.filter((s) =>
    s.health === "error" || s.health === "crashed" || s.probe === "error"
  );
  const degradedServices = services.filter((s) =>
    s.health === "degraded" || s.health === "stale" || s.probe === "stale"
  );

  /* â”€â”€ Tier priority â”€â”€ */
  // offline
  if (overall === "offline" || overall === "down" || downServices.length >= 3) {
    return {
      tier: "offline",
      chipTone: "crit",
      chipText: "offline",
      statusWord: "ai stack offline",
      statusWordTone: "crit",
      sub: "last seen â€” tap start to bring it up",
      actions: [
        { label: "start ai stack", verb: "start", style: "cool", glyph: "â–¶" },
        { label: "logs", verb: "logs", style: "default", glyph: "â–¤" },
      ],
      banner: {
        tone: "crit", lbl: "offline",
        why: "ai box unreachable Â· last seen recently",
        act: "retry",
      },
      progress: null,
      chartBadge: { text: "â€”", tone: "default" },
    };
  }

  // warming
  if (overall === "starting" || overall === "warming" || (inFlight && inFlight.verb === "start")) {
    const eta = inFlight?.eta_s ? `~${inFlight.eta_s}s` : "";
    const step = inFlight?.step || "starting ai stack";
    const pct = inFlight?.progress_pct || 50;
    return {
      tier: "warming",
      chipTone: "cool",
      chipText: "warming",
      statusWord: "starting ai stack",
      statusWordTone: "default",
      sub: `${step} ${eta ? "Â· " + eta : ""}`,
      actions: [],
      banner: null,
      progress: { step, pct, eta },
      chartBadge: { text: "â€”", tone: "default" },
    };
  }

  // error (vllm crash, etc.)
  if (errorServices.length > 0 || overall === "error") {
    const e = errorServices[0] || { name: aiStackState.crashed_service || "service" };
    return {
      tier: "error",
      chipTone: "crit",
      chipText: "error",
      statusWord: `${e.name} crashed`,
      statusWordTone: "crit",
      sub: e.detail || "service crashed Â· retrying",
      actions: [
        { label: "view logs", verb: "logs", style: "default", glyph: "â–¤" },
        { label: "restart", verb: "restart", style: "danger", glyph: "â–¶" },
      ],
      banner: {
        tone: "crit", lbl: "error",
        why: `<b>${e.name}</b> crashed Â· ${e.detail || "see logs"}`,
        act: "logs",
      },
      progress: null,
      chartBadge: { text: "â€”", tone: "default" },
    };
  }

  // pressured (vram > 85%)
  if (vramPct > 0.85) {
    return {
      tier: "pressured",
      chipTone: "warn",
      chipText: "pressured",
      statusWord: `vram at ${Math.round(vramPct * 100)}%`,
      statusWordTone: "warn",
      sub: `voice synthesis may queue Â· ${((metrics.vramMax - metrics.vram) || 0).toFixed(1)}g headroom`,
      actions: [
        { label: "stop ai models", verb: "stopStack", style: "danger", glyph: "âœ•", confirm: true },
      ],
      banner: {
        tone: "warn", lbl: "pressured",
        why: `vram at <b>${Math.round(vramPct * 100)}%</b>`,
        act: "stop ai models",
        verb: "stopStack",
        confirm: true,
        tooltip: `Frees ~${Math.round(metrics.vram || 0)} GB of VRAM by stopping vLLM (LLM), Parakeet (STT), Kokoro (TTS), and the vision sidecar. Voice and LLM go offline until you bring the stack back up.`,
      },
      progress: null,
      chartBadge: { text: "pressured", tone: "warn" },
    };
  }

  // partial (â‰¥2 degraded)
  if (degradedServices.length >= 2) {
    return {
      tier: "partial",
      chipTone: "warn",
      chipText: "partial",
      statusWord: `${degradedServices.length} services down`,
      statusWordTone: "warn",
      sub: degradedServices.map((s) => s.name).join(" Â· ") + " Â· core stack healthy",
      actions: [
        { label: "restart all", verb: "restart", style: "danger", glyph: "â–¶", confirm: true },
        { label: "logs", verb: "logs", style: "default", glyph: "â–¤" },
      ],
      banner: {
        tone: "warn", lbl: "partial",
        why: `<b>${degradedServices.length} of ${services.length || 6}</b> services degraded Â· core stack healthy`,
        act: "view",
      },
      progress: null,
      chartBadge: { text: "ok", tone: "ok" },
    };
  }

  // degraded (1 service stale)
  if (degradedServices.length === 1) {
    const d = degradedServices[0];
    return {
      tier: "degraded",
      chipTone: "warn",
      chipText: "degraded",
      statusWord: `${d.name} stale`,
      statusWordTone: "warn",
      sub: d.detail || "auto-recovering",
      actions: [
        { label: "restart", verb: "restart", style: "danger", glyph: "â–¶", confirm: true },
        { label: "logs", verb: "logs", style: "default", glyph: "â–¤" },
      ],
      banner: {
        tone: "warn", lbl: "degraded",
        why: `${d.name} heartbeat missed Â· auto-retrying`,
        act: "view logs",
      },
      progress: null,
      chartBadge: { text: "ok", tone: "ok" },
    };
  }

  // slow (lastTurn > 1.5Ã— p50 baseline)
  if (lastTurnMs > 0 && baselineP50 && lastTurnMs > 1.5 * baselineP50) {
    const xs = (lastTurnMs / baselineP50).toFixed(1);
    return {
      tier: "slow",
      chipTone: "warn",
      chipText: "slow",
      statusWord: `last turn ${(lastTurnMs / 1000).toFixed(1)}s`,
      statusWordTone: "warn",
      sub: `${xs}Ã— baseline Â· investigate stage breakdown`,
      actions: [],
      banner: null,
      progress: null,
      chartBadge: { text: "slowing", tone: "warn" },
    };
  }

  // ready (default healthy)
  const uptime = aiStackState.uptime_h ? `warm ${aiStackState.uptime_h}h Â· ` : "";
  return {
    tier: "ready",
    chipTone: "cool",
    chipText: "ready",
    statusWord: metrics.model || "ai stack",
    statusWordTone: "default",
    sub: `${uptime}ai stack online Â· everything healthy`,
    actions: [],
    banner: null,
    progress: null,
    chartBadge: { text: "fast", tone: "ok" },
  };
}

/* computeBaseline â€” rolling p10/p50/p90 + trend from recent turns. */
function computeBaseline(turns) {
  turns = turns || [];
  if (turns.length < 5) {
    return { p10: null, p50: null, p90: null, trend: "awaiting", trendPct: 0, hasBaseline: false };
  }
  const window = turns.slice(-30);
  const totals = window.map((t) => t.totalMs || 0).filter((v) => v > 0).sort((a, b) => a - b);
  if (totals.length === 0) {
    return { p10: null, p50: null, p90: null, trend: "awaiting", trendPct: 0, hasBaseline: false };
  }
  const p = (q) => totals[Math.min(totals.length - 1, Math.max(0, Math.floor(q * (totals.length - 1))))];
  const p10 = p(0.10);
  const p50 = p(0.50);
  const p90 = p(0.90);

  let trend = "flat";
  let trendPct = 0;
  if (window.length >= 10) {
    const recent5 = window.slice(-5).map((t) => t.totalMs || 0);
    const prior5 = window.slice(-10, -5).map((t) => t.totalMs || 0);
    const mr = recent5.reduce((a, b) => a + b, 0) / recent5.length;
    const mp = prior5.reduce((a, b) => a + b, 0) / prior5.length;
    if (mp > 0) {
      trendPct = Math.round(((mr - mp) / mp) * 100);
      if (Math.abs(trendPct) >= 8) {
        trend = trendPct > 0 ? `+${trendPct}%` : `${trendPct}%`;
      }
    }
  }
  return { p10, p50, p90, trend, trendPct, hasBaseline: true };
}

/* Stage extraction from raw trace (live trace shape uses t_*; LabTurn
 * shape uses stages[]). Both supported. */
function extractStages(turnOrTrace) {
  if (turnOrTrace?.stages && Array.isArray(turnOrTrace.stages)) {
    return turnOrTrace.stages;
  }
  const t = turnOrTrace || {};
  const stt   = t.t_parakeet_done || 0;
  const llm   = Math.max(0, (t.t_pipeline_intent_end || 0) - stt);
  const synth = Math.max(0, (t.t_synth_done || 0) - (t.t_pipeline_intent_end || 0));
  const audio = Math.max(0, (t.t_done || 0) - (t.t_first_audio_sent || 0));
  return [
    { label: "stt",   ms: stt   },
    { label: "llm",   ms: llm   },
    { label: "synth", ms: synth },
    { label: "audio", ms: audio },
  ];
}

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Sub-component: LabSummaryHeader
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
function LabSummaryHeader({ tier, onAction }) {
  const chipColor = {
    ok:   "var(--hg-fg-2)",
    cool: "var(--hg-ice)",
    warn: "var(--hg-warn)",
    crit: "var(--hg-crit)",
    default: "var(--hg-fg-3)",
  }[tier.chipTone || "default"];
  const wordColor = {
    crit: "var(--hg-crit)",
    warn: "var(--hg-warn)",
    default: "var(--hg-fg-0)",
  }[tier.statusWordTone || "default"];

  return (
    <div style={{
      display: "flex", alignItems: "flex-start",
      gap: 18, padding: "14px 4px 12px", borderBottom: "1px solid var(--hg-border-soft)",
    }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: 1 }}>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 12 }}>
          <span style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "3px 8px", border: `1px solid ${chipColor}`,
            fontFamily: HG_FONT_MONO, fontSize: 9.5, letterSpacing: "0.20em",
            textTransform: "uppercase", color: chipColor,
          }}>
            <span style={{ width: 5, height: 5, borderRadius: "50%", background: "currentColor" }}></span>
            {tier.chipText}
          </span>
          <span style={{
            fontFamily: HG_FONT_SANS, fontWeight: 300, fontSize: 22,
            letterSpacing: "-0.025em", lineHeight: 1, color: wordColor,
          }}>
            {tier.statusWord}
          </span>
        </div>
        <span style={{
          fontFamily: HG_FONT_MONO, fontSize: 10.5, color: "var(--hg-fg-3)",
          letterSpacing: "0.04em",
        }}>{tier.sub}</span>
      </div>
      <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
        {(tier.actions || []).map((a) => (
          <LabActionButton key={a.label} action={a} onAction={onAction} />
        ))}
      </div>
    </div>
  );
}

/* LabActionButton â€” two-click arm â†’ confirm guard when action.confirm===true.
 * Pattern B1 (home-ai-stack.jsx confirmOrDispatch): the armed flag lives in
 * React state, NOT in DOM dataset/innerHTML, so a poll-driven re-render
 * cannot restore the resting label while a stale armed flag makes the next
 * click destructive. First click arms (label reads "✓ confirm"); second
 * click within 3s fires; otherwise arming expires. */
function LabActionButton({ action, onAction }) {
  const [armed, setArmed] = useState(false);
  const confirmTimerRef = useRef(null);
  const clearConfirmTimer = () => {
    if (confirmTimerRef.current) {
      try { clearTimeout(confirmTimerRef.current); } catch {}
      confirmTimerRef.current = null;
    }
  };
  useEffect(() => clearConfirmTimer, []);
  const handleClick = () => {
    if (!action.confirm) { onAction?.(action.verb); return; }
    if (!armed) {
      clearConfirmTimer();
      setArmed(true);
      confirmTimerRef.current = setTimeout(() => {
        setArmed(false);
        confirmTimerRef.current = null;
      }, 3000);
      return;
    }
    clearConfirmTimer();
    setArmed(false);
    onAction?.(action.verb);
  };
  const styleVariant = {
    cool: { color: "var(--hg-ice)", borderColor: "var(--hg-ice)" },
    warn: { color: "var(--hg-warn)", borderColor: "var(--hg-warn)" },
    danger: { color: "var(--hg-crit)", borderColor: "var(--hg-crit)" },
    default: { color: "var(--hg-fg-2)", borderColor: "var(--hg-border)" },
  }[action.style || "default"];
  return (
    <button
      onClick={handleClick}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: "5px 11px", border: `1px solid ${styleVariant.borderColor}`,
        background: "transparent",
        fontFamily: HG_FONT_MONO, fontSize: 10, letterSpacing: "0.10em",
        textTransform: "lowercase", color: styleVariant.color,
        cursor: "pointer", transition: "color 120ms, border-color 120ms, background 120ms",
      }}
      onMouseEnter={(e) => { e.currentTarget.style.background = "color-mix(in oklab, var(--hg-fg-0) 3%, transparent)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
    >
      <span style={{
        fontFamily: HG_FONT_MONO,
        color: armed ? "var(--hg-warn)" : "var(--hg-fg-3)",
      }}>{armed ? "✓" : action.glyph}</span>
      {armed ? "confirm" : action.label}
    </button>
  );
}

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Sub-component: LabProgressRow (warming state)
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
function LabProgressRow({ progress }) {
  if (!progress) return null;
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 14,
      padding: "12px 4px", borderBottom: "1px solid var(--hg-border-soft)",
      fontFamily: HG_FONT_MONO, fontSize: 10.5, color: "var(--hg-fg-3)",
      letterSpacing: "0.04em",
    }}>
      <span style={{ color: "var(--hg-fg-0)", fontWeight: 500 }}>{progress.step}</span>
      <div style={{
        flex: 1, height: 4,
        background: "color-mix(in oklab, var(--hg-fg-0) 6%, transparent)",
        position: "relative", overflow: "hidden",
      }}>
        <div style={{
          position: "absolute", left: 0, top: 0, bottom: 0,
          width: `${progress.pct || 0}%`,
          background: "var(--hg-ice)",
          boxShadow: "0 0 8px var(--hg-ice-glow)",
          transition: "width 320ms cubic-bezier(0.16,1,0.3,1)",
        }}></div>
      </div>
      <span>{progress.eta}</span>
    </div>
  );
}

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Sub-component: LabBanner (degraded/partial/pressured/offline/error)
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
function LabBanner({ banner, onAction }) {
  // Two-click confirm state for destructive actions. First click arms;
  // second click within 3s fires onAction; otherwise reverts to label.
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return undefined;
    const t = setTimeout(() => setArmed(false), 3000);
    return () => clearTimeout(t);
  }, [armed]);

  if (!banner) return null;
  const isCrit = banner.tone === "crit";
  const borderColor = isCrit ? "var(--hg-crit)" : "var(--hg-warn)";
  const lblColor = borderColor;
  const hasVerb = !!banner.verb && typeof onAction === "function";
  const wantsConfirm = !!banner.confirm;
  const handleClick = () => {
    if (!hasVerb) return;
    if (wantsConfirm && !armed) { setArmed(true); return; }
    setArmed(false);
    onAction(banner.verb);
  };
  const btnLabel = (wantsConfirm && armed) ? "click again to confirm" : banner.act;
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12,
      padding: "10px 14px", margin: "10px 0 6px",
      borderLeft: `2px solid ${borderColor}`,
      background: isCrit
        ? "color-mix(in oklab, var(--hg-crit) 6%, transparent)"
        : "color-mix(in oklab, var(--hg-warn) 6%, transparent)",
      fontFamily: HG_FONT_MONO, fontSize: 11,
      color: "var(--hg-fg-1)", letterSpacing: "0.02em",
    }}>
      <span style={{
        fontSize: 9.5, letterSpacing: "0.18em", textTransform: "uppercase",
        color: lblColor, fontWeight: 500,
      }}>{banner.lbl}</span>
      <span style={{ color: "var(--hg-fg-2)" }}
            dangerouslySetInnerHTML={{ __html: banner.why }} />
      <button
        onClick={handleClick}
        disabled={!hasVerb}
        title={banner.tooltip || ""}
        style={{
          marginLeft: "auto", padding: "4px 9px",
          background: armed
            ? "color-mix(in oklab, var(--hg-warn) 22%, transparent)"
            : "transparent",
          border: `1px solid ${armed ? "var(--hg-warn)" : "var(--hg-border)"}`,
          fontFamily: HG_FONT_MONO, fontSize: 9.5, letterSpacing: "0.10em",
          textTransform: "lowercase",
          color: hasVerb
            ? (armed ? "var(--hg-warn)" : "var(--hg-fg-2)")
            : "var(--hg-fg-5)",
          cursor: hasVerb ? "pointer" : "default",
        }}
      >{btnLabel}</button>
    </div>
  );
}

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Addendum 26 â€” pipeline schema + per-service copy.
 *
 * SERVICE_META: one-sentence hover description per service. Keyed by
 *   the same `name` field buildStackList() returns. Both LabStackList
 *   (list view) and LabStackFlow (diagram view) read from this map.
 *   Tooltip lookup is `SERVICE_META[name]?.tooltip` â€” defensive: a
 *   future-added service that lacks a copy entry renders without a
 *   tooltip rather than breaking.
 *
 * ENDPOINT_META: same shape, for the input/output nodes the flow
 *   diagram shows (camera, mic, devices, sonos) that aren't in the
 *   live status list. Smaller fixed map.
 *
 * PIPELINE: declarative Lâ†’R pipeline schema. Each entry is one row
 *   in the flow diagram. `nodes[].id` matches a SERVICE_META key for
 *   service-kind nodes (drives status color + tooltip from the live
 *   `services` array) or an ENDPOINT_META key for input/output nodes.
 *   `showModel: true` appends `metrics.model` to the label (used for
 *   vllm so swapping models updates the diagram automatically). The
 *   renderer scans for service ids appearing in multiple rows and
 *   auto-draws a dashed "same service" connector between instances
 *   (e.g., vllm shows in both vision + voice; one underlying service).
 *
 * What stays automatic without editing this file: live status colors,
 * vllm model name, service health â†’ node color. What requires a one-
 * line edit here: adding/removing/renaming a service in the pipeline,
 * adding a new pipeline row, changing topology.
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */

const SERVICE_META = {
  vllm: {
    tooltip: "Local LLM (Qwen3-VL-30B). Reasons over voice commands and image captions; runs on the Ubuntu AI box.",
  },
  "wyoming-parakeet": {
    tooltip: "Speech-to-text. Voice PE â†’ Wyoming protocol â†’ Parakeet â†’ text transcript.",
  },
  kokoro: {
    tooltip: "Fallback text-to-speech engine. Kept online behind the Wyoming TTS bridge when Chatterbox is primary.",
  },
  "chatterbox-tts": {
    tooltip: "Primary OpenAI-compatible TTS engine for Home voice mode. Runs on the Ubuntu AI box and is fronted by the Wyoming TTS bridge.",
  },
  "wyoming-kokoro": {
    tooltip: "Wyoming TTS bridge. Presents HA-compatible TTS while routing to Chatterbox primary and Kokoro fallback.",
  },
  "vision-sidecar": {
    tooltip: "Fetches camera snapshots and calls vLLM to generate prose captions ('a person stands on the drivewayâ€¦').",
  },
  "metrics-sidecar": {
    tooltip: "Telemetry, conversation SSE, and LLM proxy sidecar. The Home app uses it for Lab traces, stack health, and chat visibility.",
  },
  intelligence: {
    tooltip: "Home Intelligence API. Provides lighting evidence, preferences, memory review, and atlas data for the Home app.",
  },
  haos: {
    tooltip: "Home Assistant OS on the LattePanda. Hosts Frigate, the conversation integration, and executes device service calls when vLLM emits tool calls.",
  },
  frigate: {
    tooltip: "Camera object + face detection. Publishes person-occupancy and face-rec events to HA via MQTT.",
  },
  "ha bridge": {
    tooltip: "S2S / direct-dispatch bridge container (personaplex-bridge) on the Ubuntu AI box. Only in the request path when S2S mode is active; otherwise this is just the bridge's heartbeat.",
  },
  network: {
    tooltip: "LAN connectivity between this app, the AI box, and HAOS. Hardcoded 'ok' today â€” not a live probe.",
  },
  openai: {
    tooltip: "OpenAI Chat Completions API. The HA classifier (external_routing.py) sends 'general knowledge' questions here instead of vLLM, so the local model isn't asked things outside its directive.",
  },
};

const ENDPOINT_META = {
  camera:  { label: "camera",  tooltip: "RTSP feeds (5 cameras). Source of the vision pipeline." },
  mic:     { label: "mic",     tooltip: "Voice PE microphone (or browser mic via the S2S bridge). Source of the voice pipeline." },
};

// Canonical supervisor display order for default docker-compose services.
// Compose calls the fallback TTS service `kokoro-tts`; supervisor/UI call the
// user-facing row `kokoro`.
const STACK_SERVICE_ORDER = [
  "vllm",
  "wyoming-parakeet",
  "kokoro",
  "chatterbox-tts",
  "wyoming-kokoro",
  "vision-sidecar",
  "metrics-sidecar",
  "intelligence",
];

const STACK_INFRA_ROWS = [
  { name: "haos", dot: "good", meta: "connected", cls: "default" },
  { name: "ha bridge", dot: "good", meta: "online", cls: "default" },
  { name: "frigate", dot: "good", meta: "5 cams", cls: "default" },
  { name: "network", dot: "good", meta: "ok", cls: "default" },
];

// Pipeline schema â€” one row per primary data flow.
//   nodes[]   : the main Lâ†’R sequence (input/service nodes)
//   branches  : OPTIONAL alternate / side-effect paths hanging OFF a
//               main-row node, drawn BELOW it. Each branch entry has:
//                 { from: <main-node-id>, to: [<branch-node>, ...],
//                   note: <short caption> }
//               The `to` array is the sub-chain (drawn left-to-right,
//               smaller, dim) starting under the `from` node.
//               Used for: openai (alternate routing pre-vllm) and
//               haos-dispatch (vllm tool calls â†’ device side effect).
//
// What's deliberately NOT in the main rows:
//   - vision row ends at vllm because the perception output is a
//     caption returned to LLM context â€” there's no device endpoint.
//   - voice row ends at kokoro (TTS audio). HA's media_player plays
//     the audio on Sonos/Voice PE; we don't draw the speaker as a
//     separate node â€” it would imply an explicit dispatch step the
//     architecture doesn't have.
//   - haos is NOT between vllm and kokoro on the voice path â€” the
//     TTS pipeline (vllm â†’ kokoro â†’ audio) doesn't route through
//     haos. haos comes into play only when vllm emits tool calls;
//     modeled as a branch below vllm in the voice row.
const PIPELINE = [
  {
    id: "vision",
    label: "vision",
    nodes: [
      { id: "camera",         kind: "input"   },
      { id: "frigate",        kind: "service" },
      { id: "vision-sidecar", kind: "service" },
      { id: "vllm",           kind: "service", showModel: true },
    ],
  },
  {
    id: "voice",
    label: "voice",
    nodes: [
      { id: "mic",              kind: "input"   },
      { id: "wyoming-parakeet", kind: "service" },
      { id: "vllm",             kind: "service", showModel: true },
      { id: "kokoro",           kind: "service" },
    ],
    branches: [
      {
        from: "wyoming-parakeet",
        to: [{ id: "openai", kind: "service" }],
        note: "if general-knowledge",
      },
      {
        from: "vllm",
        to: [{ id: "haos", kind: "service" }],
        note: "if tool call",
      },
    ],
  },
];

// Service ids that aren't in the primary pipeline rows but still need visible
// managed-service status in the flow view.
const FLOW_LEGEND_SERVICES = [
  "chatterbox-tts",
  "wyoming-kokoro",
  "metrics-sidecar",
  "intelligence",
  "ha bridge",
];

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Sub-component: LabStackList â€” 8-row compact services
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
function LabStackList({ services }) {
  // Design (Metrics Drawer Demo.html .stack): two-column grid, 28px
  // horizontal gap between columns. Services auto-flow row-by-row so
  // an 8-service stack reads as 4 rows Ã— 2 columns. Each .svc keeps its
  // own 3-column inner grid (14px dot Â· 1fr name Â· auto meta) with a
  // border-bottom divider on EVERY row (matches the design).
  return (
    <div style={{
      margin: "10px 0",
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: "0 28px",
    }}>
      {services.map((s) => {
        const dotColor = {
          good: "var(--hg-fg-2)",  // muted "running"
          cool: "var(--hg-ice)",   // primary (vllm warm)
          warn: "var(--hg-warn)",
          err:  "var(--hg-crit)",
          idle: "var(--hg-fg-5)",
          default: "var(--hg-fg-3)",
        }[s.dot || "default"];
        const nmColor = {
          warn: "var(--hg-warn)",
          err:  "var(--hg-crit)",
          default: "var(--hg-fg-0)",
        }[s.cls || "default"];
        const metaColor = {
          warn: "var(--hg-warn)",
          err:  "var(--hg-crit)",
          default: "var(--hg-fg-3)",
        }[s.cls || "default"];
        return (
          <div key={s.name}
            title={SERVICE_META[s.name]?.tooltip || ""}
            style={{
            display: "grid", gridTemplateColumns: "14px 1fr auto",
            gap: 10, alignItems: "center", padding: "7px 0",
            borderBottom: "1px solid var(--hg-border-soft)",
            color: "var(--hg-fg-2)", cursor: "default",
            transition: "color 120ms cubic-bezier(0.16,1,0.3,1)",
            minWidth: 0,   // allow inner ellipsis instead of column overflow
          }}
          onMouseEnter={(e) => {
            e.currentTarget.querySelectorAll("span.nm, span.meta")
              .forEach((el) => el.style.color = "var(--hg-fg-0)");
          }}
          onMouseLeave={(e) => {
            e.currentTarget.querySelectorAll("span.nm").forEach((el) => el.style.color = nmColor);
            e.currentTarget.querySelectorAll("span.meta").forEach((el) => el.style.color = metaColor);
          }}>
            <span style={{
              width: 6, height: 6, borderRadius: "50%",
              background: dotColor, justifySelf: "center",
              boxShadow: s.dot === "cool" ? "0 0 5px var(--hg-ice-glow)" : "none",
            }}></span>
            <span className="nm" style={{
              fontFamily: HG_FONT_MONO, fontSize: 11.5, color: nmColor,
              letterSpacing: "0.04em",
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              minWidth: 0,
            }}>{s.name}</span>
            <span className="meta" style={{
              fontFamily: HG_FONT_MONO, fontSize: 10, color: metaColor,
              letterSpacing: "0.04em",
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              maxWidth: 180,
            }}>{s.meta}</span>
          </div>
        );
      })}
    </div>
  );
}

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Sub-component: LabStackFlow (Addendum 26) â€” schema-driven SVG
 *
 * Renders PIPELINE (vision + voice rows) with live status from the
 * `services` array. Same dot colors as LabStackList. Renames /
 * model swaps propagate automatically via the schema lookup; only
 * pipeline topology changes require editing PIPELINE (one line).
 *
 * Layout:
 *   [row-label]  [endpoint] â†’ [service] â†’ [service] â†’ ... â†’ [endpoint]
 *
 * Same-service connector: a service id appearing in multiple rows
 * (currently: vllm, haos) gets a dashed vertical line linking the
 * instances, signaling "same underlying service" so a `vllm err`
 * showing as two red nodes reads as one problem not two.
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */

const FLOW_DOT_COLOR = {
  good:    "var(--hg-fg-2)",
  cool:    "var(--hg-ice)",
  warn:    "var(--hg-warn)",
  err:     "var(--hg-crit)",
  idle:    "var(--hg-fg-5)",
  default: "var(--hg-fg-3)",
};

function LabStackFlow({ services, metrics }) {
  // Index live services by name for O(1) lookups by node.id.
  const byName = useMemo(() => {
    const out = {};
    for (const s of (services || [])) out[s.name] = s;
    return out;
  }, [services]);

  // Layout â€” tuned with two priorities:
  //   1. Fixed pixel sizing: the SVG renders at its computed natural
  //      width regardless of container width. Wider windows = more
  //      empty space to the right, not a stretched (and blurry,
  //      text-clipping) chart. Matches Apple-style restraint: the
  //      diagram has a "correct" size; don't fight the container.
  //   2. Generous whitespace: row label has real breathing room
  //      before the first node; rows are separated by a clear vertical
  //      gap; branch nodes are CLEARLY subordinate via size + dim.
  const NODE_H = 28;
  const NODE_GAP = 30;                  // horizontal gap between main-row nodes
  const BRANCH_NODE_H = 22;
  const BRANCH_GAP_Y = 30;              // vertical drop from main-row to branch
  const BRANCH_NODE_GAP = 22;
  const BRANCH_CAPTION_H = 14;          // height reserved for italic caption below branch
  const ROW_GAP = 42;                   // breathing room between vision + voice rows
  const PLAIN_ROW_HEIGHT = NODE_H;
  const BRANCH_ROW_HEIGHT = NODE_H + BRANCH_GAP_Y + BRANCH_NODE_H + BRANCH_CAPTION_H;
  const ROW_LABEL_W = 72;               // generous gap from "VISION" label to first node
  const ENDPOINT_NODE_W = 78;
  const PAD_X = 16;
  const PAD_Y = 10;
  const NODE_TEXT_PAD_LEFT = 20;        // dot + breathing space
  const NODE_TEXT_PAD_RIGHT = 14;
  const APPROX_CHAR_W = 6.1;

  // Compute the display label + tooltip for a node. Service nodes
  // (including those in branches) honor showModel; endpoint nodes use
  // ENDPOINT_META.label. Tooltips fall back to "" when missing.
  function nodeLabel(node) {
    if (node.kind === "service") {
      let lbl = node.id;
      if (node.showModel && metrics?.model) lbl = `${node.id} Â· ${metrics.model}`;
      return lbl;
    }
    return ENDPOINT_META[node.id]?.label || node.id;
  }
  function nodeTooltip(node) {
    if (node.kind === "service") return SERVICE_META[node.id]?.tooltip || "";
    return ENDPOINT_META[node.id]?.tooltip || "";
  }
  function nodeWidth(node, opts) {
    if (node.kind === "input" || node.kind === "output") return ENDPOINT_NODE_W;
    const lbl = nodeLabel(node);
    const textW = lbl.length * APPROX_CHAR_W;
    const min = opts?.minWidth ?? 80;
    return Math.max(min, Math.ceil(NODE_TEXT_PAD_LEFT + textW + NODE_TEXT_PAD_RIGHT));
  }

  // Place all rows + branches. Each row may have a branch sub-row;
  // the row's allocated height grows accordingly.
  const layout = useMemo(() => {
    const rows = [];
    let maxW = 0;
    let yCursor = PAD_Y;
    for (const row of PIPELINE) {
      const hasBranches = Array.isArray(row.branches) && row.branches.length > 0;
      const placed = [];
      let x = ROW_LABEL_W;
      for (const node of row.nodes) {
        const w = nodeWidth(node);
        placed.push({
          ...node,
          x, y: yCursor,
          w, h: NODE_H,
          cx: x + w / 2,
          cy: yCursor + NODE_H / 2,
          rightX: x + w,
        });
        x += w + NODE_GAP;
      }
      // Position branches below the main row, each anchored at the
      // x-position of its `from` parent. Sub-chain extends RIGHTWARD
      // from the parent's left edge â€” that's enough horizontal room
      // because branches are short (1-2 nodes) and parents are wide.
      const placedBranches = [];
      if (hasBranches) {
        const parentMap = {};
        for (const p of placed) parentMap[p.id] = p;
        for (const b of row.branches) {
          const parent = parentMap[b.from];
          if (!parent) continue;
          const baseY = parent.y + NODE_H + BRANCH_GAP_Y;
          let bx = parent.x;
          const placedNodes = [];
          for (const bn of b.to) {
            const w = nodeWidth(bn, { minWidth: 60 });
            placedNodes.push({
              ...bn,
              x: bx, y: baseY,
              w, h: BRANCH_NODE_H,
              cx: bx + w / 2,
              cy: baseY + BRANCH_NODE_H / 2,
              rightX: bx + w,
            });
            bx += w + BRANCH_NODE_GAP;
          }
          placedBranches.push({
            parent,
            note: b.note,
            nodes: placedNodes,
            // Extend SVG bounds if the branch sticks past the main row
            rightEdge: bx - BRANCH_NODE_GAP,
          });
        }
      }
      // Row right edge = max(main right edge, branch right edges)
      const mainRightEdge = x - NODE_GAP;
      let rowRightEdge = mainRightEdge;
      for (const pb of placedBranches) {
        if (pb.rightEdge > rowRightEdge) rowRightEdge = pb.rightEdge;
      }
      maxW = Math.max(maxW, rowRightEdge);
      rows.push({ row, placed, branches: placedBranches });
      const rowHeight = hasBranches ? BRANCH_ROW_HEIGHT : PLAIN_ROW_HEIGHT;
      yCursor += rowHeight + ROW_GAP;
    }
    return { rows, totalW: maxW + PAD_X, totalH: yCursor + PAD_Y };
  }, [metrics?.model]);

  // Same-service connectors â€” find any service id (incl. branches)
  // appearing in >1 location. Draws a dashed line linking them so
  // "vllm" in vision row + "vllm" in voice row clearly reads as the
  // same underlying service.
  const sameServiceLinks = useMemo(() => {
    const byId = {};
    for (const r of layout.rows) {
      for (const n of r.placed) {
        if (n.kind !== "service") continue;
        (byId[n.id] = byId[n.id] || []).push(n);
      }
      for (const b of r.branches) {
        for (const n of b.nodes) {
          if (n.kind !== "service") continue;
          (byId[n.id] = byId[n.id] || []).push(n);
        }
      }
    }
    const links = [];
    for (const id of Object.keys(byId)) {
      const instances = byId[id];
      if (instances.length < 2) continue;
      // Sort by y so adjacent links go topâ†’bottom
      instances.sort((a, b) => a.cy - b.cy);
      for (let i = 1; i < instances.length; i++) {
        const a = instances[i - 1];
        const b = instances[i];
        const x = (a.cx + b.cx) / 2;
        const y1 = a.cy + (a.h / 2);
        const y2 = b.cy - (b.h / 2);
        links.push({ id, x, y1, y2 });
      }
    }
    return links;
  }, [layout]);

  const renderServiceOrEndpoint = (n, opts) => {
    const isService = n.kind === "service";
    const isBranch = !!opts?.branch;
    const svc = isService ? byName[n.id] : null;
    const dotKey = svc?.dot || (isService ? "idle" : "default");
    const dotColor = FLOW_DOT_COLOR[dotKey] || FLOW_DOT_COLOR.default;
    // Apple discipline: color is reserved for signal. Healthy/cool/idle
    // services get a neutral border â€” the dot carries the status. Only
    // warn + err escalate to a colored border (because those NEED
    // visual urgency to be noticed). This stops "cool" (primary warmed
    // model) from screaming "I'm different!" via a blue box.
    const isUrgent = dotKey === "warn" || dotKey === "err";
    const baseStroke = isService
      ? (isUrgent ? dotColor : "var(--hg-border)")
      : "var(--hg-fg-5)";
    const strokeOpacity = isBranch ? 0.55 : (isService ? (isUrgent ? 0.85 : 0.7) : 0.55);
    const strokeWidth = isUrgent ? 1 : 0.75;
    const dash = isService ? "" : "4,3";
    const label = nodeLabel(n);
    const tooltip = nodeTooltip(n);
    const labelColor = isService
      ? (isBranch ? "var(--hg-fg-3)" : "var(--hg-fg-0)")
      : "var(--hg-fg-4)";
    const fontStyle = isService ? "normal" : "italic";
    const fontSize = isService ? (isBranch ? 9.5 : 10) : 9.5;
    const showDot = isService;
    return (
      <g key={`${n.id}-${n.x}-${n.y}`}>
        <title>{tooltip}</title>
        <rect
          x={n.x} y={n.y} width={n.w} height={n.h}
          rx={5} ry={5}
          fill="var(--hg-bg-1)"
          stroke={baseStroke} strokeWidth={strokeWidth}
          strokeDasharray={dash}
          strokeOpacity={strokeOpacity}
        />
        {showDot && (
          <circle
            cx={n.x + 11} cy={n.cy}
            r={2.5}
            fill={dotColor}
            style={dotKey === "cool"
              ? { filter: "drop-shadow(0 0 2.5px var(--hg-ice-glow))" }
              : undefined}
          />
        )}
        <text
          x={isService ? (n.x + NODE_TEXT_PAD_LEFT) : n.cx}
          y={n.cy + 3}
          fontFamily={HG_FONT_MONO} fontSize={fontSize}
          fontStyle={fontStyle}
          fill={labelColor}
          textAnchor={isService ? "start" : "middle"}
          letterSpacing="0.01em"
          style={{ pointerEvents: "none" }}
        >{label}</text>
      </g>
    );
  };

  const renderArrowsBetween = (placed, keyPrefix) => {
    const arrows = [];
    for (let i = 0; i < placed.length - 1; i++) {
      const a = placed[i];
      const b = placed[i + 1];
      arrows.push(
        <line
          key={`${keyPrefix}-${a.id}-${b.id}-${i}`}
          x1={a.rightX + 3} y1={a.cy}
          x2={b.x - 5}      y2={b.cy}
          stroke="var(--hg-fg-4)" strokeWidth={0.9}
          strokeOpacity={0.85}
          markerEnd="url(#lab-flow-arrow)"
        />
      );
    }
    return arrows;
  };

  // Drop-arrow from parent's bottom-center to the branch's first node.
  // The branch caption sits CENTERED BELOW the first branch node â€” this
  // avoids the previous bug where horizontal captions would extend into
  // the column of the next branch and get visually clipped by it.
  const renderBranchAnchor = (branch, keyPrefix) => {
    if (!branch.nodes.length) return null;
    const parent = branch.parent;
    const first = branch.nodes[0];
    const startX = parent.cx;
    const startY = parent.y + parent.h;
    const endX = first.cx;
    const endY = first.y;
    return (
      <g key={`${keyPrefix}-anchor-${parent.id}-${first.id}`}>
        <line
          x1={startX} y1={startY}
          x2={endX}   y2={endY - 2}
          stroke="var(--hg-fg-5)" strokeWidth={0.7}
          strokeDasharray="2,3"
          strokeOpacity={0.75}
          markerEnd="url(#lab-flow-arrow-dim)"
        />
        {branch.note && (
          <text
            x={first.cx}
            y={first.y + first.h + 11}
            fontFamily={HG_FONT_MONO} fontSize={8.5}
            fontStyle="italic"
            fill="var(--hg-fg-4)"
            textAnchor="middle"
            letterSpacing="0.02em"
            style={{ pointerEvents: "none" }}
          >{branch.note}</text>
        )}
      </g>
    );
  };

  const containerStyle = {
    margin: "10px 0 6px",
    overflowX: "auto",
    // Fixed natural size â€” don't stretch to fill the container.
    // Wider windows show empty space; narrower windows scroll.
    // This is the Apple-style choice: the diagram has a "correct"
    // size, the container adapts around it.
  };
  return (
    <div className="hg-scroll" style={containerStyle}>
      <svg
        viewBox={`0 0 ${layout.totalW} ${layout.totalH}`}
        width={layout.totalW}
        height={layout.totalH}
        style={{ display: "block" }}
      >
        <defs>
          <marker
            id="lab-flow-arrow"
            viewBox="0 0 6 6"
            refX="5" refY="3"
            markerWidth="5" markerHeight="5"
            orient="auto-start-reverse"
          >
            <path d="M0,0 L6,3 L0,6 z" fill="var(--hg-fg-4)" fillOpacity="0.9" />
          </marker>
          <marker
            id="lab-flow-arrow-dim"
            viewBox="0 0 6 6"
            refX="5" refY="3"
            markerWidth="4" markerHeight="4"
            orient="auto-start-reverse"
          >
            <path d="M0,0 L6,3 L0,6 z" fill="var(--hg-fg-5)" fillOpacity="0.7" />
          </marker>
        </defs>

        {/* Row labels â€” tightened letter-spacing + small for
            refinement. fg-4 reads as a deliberate label without
            stealing focus from the nodes. */}
        {layout.rows.map((r) => (
          <text
            key={`label-${r.row.id}`}
            x={PAD_X}
            y={r.placed[0].cy + 3}
            fontFamily={HG_FONT_MONO} fontSize={8.5}
            letterSpacing="0.22em"
            fill="var(--hg-fg-4)"
            style={{ textTransform: "uppercase", pointerEvents: "none" }}
          >{r.row.label}</text>
        ))}

        {/* Same-service connectors â€” subtle but discoverable. They
            communicate "this is the same underlying service" without
            competing with the main arrows. */}
        {sameServiceLinks.map((l) => (
          <line
            key={`same-${l.id}-${l.x}-${l.y1}`}
            x1={l.x} y1={l.y1} x2={l.x} y2={l.y2}
            stroke="var(--hg-fg-5)" strokeWidth={0.6}
            strokeDasharray="2,4"
            strokeOpacity={0.65}
          />
        ))}

        {/* Per-row: main arrows + main nodes + branch anchors + branch nodes */}
        {layout.rows.map((r) => (
          <g key={`row-${r.row.id}`}>
            {renderArrowsBetween(r.placed, `m-${r.row.id}`)}
            {r.placed.map((n) => renderServiceOrEndpoint(n))}
            {r.branches.map((b, bi) => (
              <g key={`b-${r.row.id}-${b.parent.id}-${bi}`}>
                {renderBranchAnchor(b, `b-${r.row.id}`)}
                {renderArrowsBetween(b.nodes, `bn-${r.row.id}-${bi}`)}
                {b.nodes.map((n) => renderServiceOrEndpoint(n, { branch: true }))}
              </g>
            ))}
          </g>
        ))}
      </svg>

      {/* Legend chips for services not in the pipeline rows â€” sit
          subtle below the SVG. Same dot conventions as in-flow nodes
          but smaller; the typography matches row labels. */}
      <div style={{
        display: "flex", justifyContent: "flex-end", gap: 14, flexWrap: "wrap",
        padding: "6px 16px 2px", marginTop: 2,
        fontFamily: HG_FONT_MONO, fontSize: 9, color: "var(--hg-fg-5)",
        letterSpacing: "0.08em",
      }}>
        {FLOW_LEGEND_SERVICES.map((id) => {
          const svc = byName[id];
          const dotKey = svc?.dot || "idle";
          const dotColor = FLOW_DOT_COLOR[dotKey] || FLOW_DOT_COLOR.default;
          const meta = svc?.meta || "â€”";
          return (
            <span
              key={id}
              title={SERVICE_META[id]?.tooltip || ""}
              style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
            >
              <span style={{
                width: 4, height: 4, borderRadius: "50%",
                background: dotColor, display: "inline-block",
                opacity: 0.85,
                boxShadow: dotKey === "cool" ? "0 0 3px var(--hg-ice-glow)" : "none",
              }}></span>
              {id} Â· {meta}
            </span>
          );
        })}
      </div>
    </div>
  );
}

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Sub-component: LabStackPane (Addendum 26) â€” list/flow toggle wrapper
 *
 * Holds the local view state, renders the compact pill toggle, then
 * either LabStackList or LabStackFlow. Default view: list. Toggle is
 * per-session (no localStorage) â€” matches the LabChartCard history/now
 * toggle pattern.
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
function LabStackPane({ services, metrics }) {
  const [view, setView] = useState("list");
  return (
    <div>
      <div style={{
        display: "flex", justifyContent: "flex-end",
        margin: "10px 0 0",
      }}>
        <div style={{
          display: "inline-flex",
          border: "1px solid var(--hg-border-soft)",
          borderRadius: 4,
          overflow: "hidden",
        }}>
          {["list", "flow"].map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              style={{
                padding: "3px 12px",
                background: view === v ? "color-mix(in oklab, var(--hg-fg-0) 4.5%, transparent)" : "transparent",
                border: "none",
                fontFamily: HG_FONT_MONO, fontSize: 9.5,
                letterSpacing: "0.14em", textTransform: "lowercase",
                color: view === v ? "var(--hg-fg-0)" : "var(--hg-fg-4)",
                cursor: "pointer",
                transition: "color 160ms cubic-bezier(0.16,1,0.3,1), background 160ms",
              }}
            >{v}</button>
          ))}
        </div>
      </div>
      {view === "list"
        ? <LabStackList services={services} />
        : <LabStackFlow services={services} metrics={metrics} />}
    </div>
  );
}

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Sub-component: LabChartHero â€” big number + baseline + p50/p90/trend
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
function LabChartHero({ lastTurn, baseline, slow, prevMs }) {
  const num = lastTurn ? Math.round(lastTurn.totalMs) : null;
  const numColor = slow ? "var(--hg-warn)" : "var(--hg-fg-0)";
  return (
    <div style={{
      display: "flex", alignItems: "baseline", gap: 24,
      margin: "14px 0 16px",
    }}>
      <span style={{
        fontFamily: HG_FONT_SANS, fontWeight: 300, fontSize: 36,
        letterSpacing: "-0.03em", lineHeight: 1, color: numColor,
        paddingLeft: 14, position: "relative",
      }}>
        <span style={{
          content: "''", position: "absolute", left: 0, top: "10%", bottom: "10%",
          width: 1, background: "var(--hg-fg-5)",
        }}></span>
        {num != null ? num : "â€”"}
        <span style={{
          fontFamily: HG_FONT_MONO, fontSize: 11, letterSpacing: "0.08em",
          color: "var(--hg-fg-3)", marginLeft: 4, fontWeight: 400,
        }}>ms</span>
      </span>
      <div style={{
        fontFamily: HG_FONT_MONO, fontSize: 10, color: "var(--hg-fg-3)",
        letterSpacing: "0.04em", lineHeight: 1.7,
      }}>
        {prevMs != null && (
          <>
            prev <b style={{ color: "var(--hg-fg-0)", fontWeight: 500 }}>
              {(window.HomeMetricsLabHelpers && window.HomeMetricsLabHelpers.formatMs)
                ? window.HomeMetricsLabHelpers.formatMs(prevMs)
                : Math.round(prevMs)}
            </b><br />
          </>
        )}
        {baseline.hasBaseline ? (
          <>
            baseline <b style={{ color: "var(--hg-fg-0)", fontWeight: 500 }}>
              {(baseline.p10 / 1000).toFixed(1)}â€“{(baseline.p90 / 1000).toFixed(1)} s
            </b>
          </>
        ) : (
          <span style={{ color: "var(--hg-fg-4)" }}>awaiting baseline Â· 5+ turns needed</span>
        )}
      </div>
      <div style={{
        marginLeft: "auto", textAlign: "right", lineHeight: 1.7,
        fontFamily: HG_FONT_MONO, fontSize: 10, color: "var(--hg-fg-3)",
        letterSpacing: "0.04em",
      }}>
        {baseline.hasBaseline ? (
          <>
            p50 <b style={{ color: "var(--hg-fg-0)", fontWeight: 500 }}>{(baseline.p50 / 1000).toFixed(1)} s</b><br />
            p90 <b style={{
              color: baseline.p90 > baseline.p50 * 1.5 ? "var(--hg-warn)" : "var(--hg-fg-0)",
              fontWeight: 500,
            }}>{(baseline.p90 / 1000).toFixed(1)} s</b><br />
            trend <b style={{
              color: baseline.trendPct > 8 ? "var(--hg-warn)" : "var(--hg-fg-0)",
              fontWeight: 500,
            }}>{baseline.trend}</b>
          </>
        ) : null}
      </div>
    </div>
  );
}

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Sub-component: LabChartSvg â€” renders HISTORY or NOW mode
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
function LabChartSvg({ turns, mode, selectedTurn, samplesShared, onSegmentClick, onSegmentHover, onLineHover }) {
  // turns = LabTurn[] (history) OR [selectedTurn] (now mode forces one)
  // mode = "history" | "now"

  if (mode === "now" && selectedTurn) {
    return <LabChartSvgNow turn={selectedTurn} onSegmentHover={onSegmentHover} onLineHover={onLineHover} />;
  }
  return <LabChartSvgHistory
    turns={turns} samplesShared={samplesShared}
    onSegmentClick={onSegmentClick} onSegmentHover={onSegmentHover}
    onLineHover={onLineHover}
  />;
}

function LabChartSvgHistory({ turns, samplesShared, onSegmentClick, onSegmentHover, onLineHover }) {
  const mobile = isLabMobileViewport();
  const PER_CALL = mobile ? 220 : 280;
  const STACK_H = mobile ? 22 : 26;
  const ROW_H = mobile ? 30 : 32;            // a hair taller so spikes have room to breathe
  const ROW_GAP = mobile ? 5 : 6;
  const N = Math.min(21, turns.length);
  const visibleTurns = turns.slice(-N);
  const SVG_W = N * PER_CALL;
  // Resource lines (topâ†’bottom): gpu, cpu, ram, temp.
  // VRAM dropped (vLLM holds a fixed allocation â†’ static line carries no
  // signal; lives in the pills below). TEMP added at the BOTTOM when the
  // metrics-sidecar exposes gpu_temp_c; if absent, falls back to 3 rows
  // (gpu/cpu/ram). N_METRICS is now derived from metricsCfg.length further
  // below; SVG_H computed AFTER metricsCfg so it tracks the row count.
  const callFrac = 0.82;
  const callPad = (1 - callFrac) / 2;
  const IDLE_FLOOR = 0.03;     // near-zero baseline between turns

  // Segments for stack-trace strip
  const segs = [];
  // Addendum 29 Change 2: in-flight slots get a special placeholder
  // marker instead of segments. We collect them separately so the SVG
  // renderer can draw a pulsing band over the slot.
  const inFlightSlots = [];
  visibleTurns.forEach((t, i) => {
    const slotStart = i * PER_CALL;
    const callStartX = slotStart + callPad * PER_CALL;
    const cw = callFrac * PER_CALL;
    if (t && t.inFlight) {
      inFlightSlots.push({ x: callStartX, w: cw, turnIdx: i });
      return; // skip stage segs for in-flight stubs
    }
    const stages = extractStages(t);
    const totalMs = t.totalMs || stages.reduce((a, s) => a + s.ms, 0);
    let x = callStartX;
    stages.forEach((stg) => {
      const w = totalMs > 0 ? (stg.ms / totalMs) * cw : 0;
      if (w > 0) {
        segs.push({
          x, w,
          stage: stg.label,
          // Change 4: surface stage kind so the renderer can pick the
          // tool tint for "tool" segments instead of the generic
          // STAGE_TINT lookup (which would render unknown labels gray).
          kind: stg.kind || null,
          ms: stg.ms,
          turnIdx: i,
          total: totalMs,
          slow: !!t.slow,
          turn: t,
        });
      }
      x += w;
    });
  });

  // Auto-scroll to right edge on first render so "now" is visible.
  const scrollRef = useRef(null);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollLeft = SVG_W;
  }, [SVG_W, N]);

  // Resource lines â€” VRAM dropped (static across the window per vLLM
  // fixed allocation; lives in pills below). TEMP added as the 4th line
  // at the BOTTOM when the metrics-sidecar exposes gpu_temp_c. If
  // sidecar lacks the field, TEMP_LINE_AVAILABLE stays false and the
  // chart collapses to 3 rows (gpu/cpu/ram).
  const TEMP_LINE_AVAILABLE =
    typeof window !== "undefined" &&
    window.__hav_metricsLatest &&
    typeof window.__hav_metricsLatest.gpu_temp_c === "number";
  const metricsCfg = TEMP_LINE_AVAILABLE
    ? [
        { id: "gpu",  label: "gpu"  },
        { id: "cpu",  label: "cpu"  },
        { id: "ram",  label: "ram"  },
        { id: "temp", label: "temp" },
      ]
    : [
        { id: "gpu",  label: "gpu"  },
        { id: "cpu",  label: "cpu"  },
        { id: "ram",  label: "ram"  },
      ];

  // Delegate path generation to the pure helper (home-metrics-lab-helpers.js).
  // It uses STAGE_PROFILES to synthesise per-stage anchors when real samples
  // are sparse (typical: 0-1 samples per 500-1500 ms turn @ 750 ms cadence
  // produces a flatline without synthesis). Tests:
  // tools/run-lab-tests.js â†’ "per-stage anchor synthesis" + "path generation
  // y-variance" suites.
  // Now uses makeHistoryPathWithAnchors so the line-hover overlay can call
  // valueAtX(anchors, localX) to look up the value under the cursor.
  const H = (typeof window !== "undefined" && window.HomeMetricsLabHelpers) || null;
  function makePath(metricId, yBase) {
    if (!H) {
      return { path: `M0,${yBase + ROW_H * 0.97} L${SVG_W},${yBase + ROW_H * 0.97}`, peak: 0, anchors: [] };
    }
    const result = H.makeHistoryPathWithAnchors(visibleTurns, metricId, {
      perCall: PER_CALL,
      callFrac: callFrac,
      svgW: SVG_W,
      yBase: yBase,
      rowH: ROW_H,
    });
    const displayAnchors = H.scaleAnchorsForDisplay
      ? H.scaleAnchorsForDisplay(result.anchors)
      : result.anchors;
    const displayPath = H.pathFromAnchors
      ? H.pathFromAnchors(displayAnchors, { yBase, rowH: ROW_H })
      : result.path;
    return { path: displayPath, peak: result.peak, anchors: result.anchors };
  }

  // Build SVG
  const stackRects = segs.map((s, i) => {
    // Tool segments (Change 4) use the tool-tint lookup so unknown
    // tool names still render in a distinct cyan/green family. Falls
    // back to STAGE_TINT for prefill/gen/stt/etc.
    let tint;
    if (s.kind === "tool") {
      tint = s.slow ? TOOL_DEFAULT_TINT_SLOW : (STAGE_TINT_TOOL_LOOKUP(s.stage) || TOOL_DEFAULT_TINT);
    } else {
      tint = s.slow ? STAGE_TINT_SLOW[s.stage] : STAGE_TINT[s.stage];
    }
    const fillProps = tintFillProps(tint);
    // Segment outline: fg-0 ink at the old white alpha (dual-theme);
    // slow keeps the semantic amber with its baked-in alpha.
    const stroke = s.slow ? "rgba(255,178,90,0.85)" : "var(--hg-fg-0)";
    const strokeOp = s.slow ? 1 : 0.18;
    return <rect
      key={i}
      x={s.x.toFixed(2)} y={2} width={s.w.toFixed(2)} height={STACK_H}
      fill={fillProps.fill} fillOpacity={fillProps.fillOpacity}
      stroke={stroke} strokeOpacity={strokeOp} strokeWidth={0.5}
      style={{ cursor: "pointer", transition: "opacity 120ms" }}
      onMouseEnter={(e) => {
        e.currentTarget.style.opacity = 0.8;
        onSegmentHover?.(s, e);
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.opacity = 1;
        onSegmentHover?.(null, e);
      }}
      onClick={() => onSegmentClick?.(s.turn, s.turnIdx)}
    />;
  });

  // Addendum 29 Change 2: render a pulsing band for in-flight stubs.
  // The animation is a simple opacity ramp via CSS keyframes injected
  // once into the document (LAB_INFLIGHT_KEYFRAMES_INJECTED). data-testid
  // gives the autonomous probe a stable handle to assert presence.
  const inFlightRects = inFlightSlots.map((s, i) => (
    <rect
      key={`inflight-${i}`}
      x={s.x.toFixed(2)} y={2}
      width={s.w.toFixed(2)} height={STACK_H}
      fill="var(--hg-ice)" fillOpacity={0.45}
      stroke="var(--hg-ice)" strokeOpacity={0.85} strokeWidth={0.8}
      data-testid="hm-inflight-bar"
      style={{ animation: "hmLabInflightPulse 1.4s ease-in-out infinite" }}
    />
  ));

  const callOutlines = visibleTurns.map((t, i) => {
    const cs = (i + callPad) * PER_CALL;
    const ce = (i + 1 - callPad) * PER_CALL;
    const isSlow = !!t.slow;
    // Neutral outline/shine/label inks are fg-0 + the old white alpha
    // (dual-theme); slow keeps the semantic amber rgba literals.
    const stroke = isSlow ? "rgba(255,178,90,0.85)" : "var(--hg-fg-0)";
    const strokeOp = isSlow ? 1 : 0.30;
    const shine = isSlow ? "rgba(255,230,180,0.55)" : "var(--hg-fg-0)";
    const shineOp = isSlow ? 1 : 0.45;
    const labelText = i === N - 1 ? "now" : `-${N - 1 - i}`;
    return (
      <g key={`outline-${i}`}>
        <rect x={cs.toFixed(2)} y={2} width={(ce - cs).toFixed(2)} height={1}
              fill={shine} fillOpacity={shineOp} />
        <rect x={cs.toFixed(2)} y={2} width={(ce - cs).toFixed(2)} height={STACK_H}
              fill="none" stroke={stroke} strokeOpacity={strokeOp} strokeWidth={0.6} />
        {!mobile && (
          <text x={((cs + ce) / 2).toFixed(1)} y={STACK_H + 12}
                textAnchor="middle" fontFamily="Geist Mono" fontSize={8}
                fill="var(--hg-fg-0)" fillOpacity={0.42} letterSpacing="0.16em">{labelText}</text>
        )}
      </g>
    );
  });

  const metricsRendered = metricsCfg.map((m, idx) => {
    const yBase = STACK_H + 22 + idx * (ROW_H + ROW_GAP);
    const { path, peak, anchors } = makePath(m.id, yBase);
    // Threshold-driven color via shared helper. Mirrored in labels +
    // values columns below. Tests: tools/run-lab-tests.js â†’ "line color
    // threshold boundaries" suite.
    const thresholds = H ? H.THRESHOLDS : null;
    const threshold = (thresholds && thresholds[m.id]) || { warn: 999, crit: 999 };
    const isCrit = peak >= threshold.crit;
    const isWarn = peak >= threshold.warn;
    const stroke = H ? H.pickLineColor(peak, m.id) :
      (isCrit ? "var(--hg-crit)" : (isWarn ? "var(--hg-warn)" : "var(--hg-ice)"));
    return { id: m.id, label: m.label, path, peak, anchors, isWarn, isCrit, stroke, yBase };
  });

  // SVG height grows when temp is present (3 or 4 rows).
  const N_METRICS = metricsCfg.length;
  const SVG_H = STACK_H + 18 + N_METRICS * (ROW_H + ROW_GAP);

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: mobile ? "42px minmax(0, 1fr) 58px" : "48px 1fr 70px",
      gap: mobile ? 8 : 12,
      alignItems: "stretch",
    }}>
      {/* Labels column */}
      <div style={{ display: "flex", flexDirection: "column" }}>
        <div style={{
          height: STACK_H + 18, display: "flex", alignItems: "center",
          fontFamily: HG_FONT_MONO, fontSize: 9, letterSpacing: "0.22em",
          textTransform: "uppercase", color: "var(--hg-fg-3)",
        }}>stack</div>
        {metricsRendered.map((m) => (
          <div key={m.id} style={{
            height: ROW_H + ROW_GAP, display: "flex", alignItems: "center",
            fontFamily: HG_FONT_MONO, fontSize: mobile ? 8.5 : 9,
            letterSpacing: mobile ? "0.14em" : "0.22em",
            textTransform: "uppercase",
            color: m.isCrit ? "var(--hg-crit)" : (m.isWarn ? "var(--hg-warn)" : "var(--hg-fg-3)"),
          }}>{m.label}</div>
        ))}
      </div>

      {/* SVG scroll â€” uses .hg-scroll for app-consistent thin scrollbar */}
      <div
        ref={scrollRef}
        className="hg-scroll"
        style={{
          overflowX: "auto", overflowY: "hidden",
          background: "color-mix(in oklab, var(--hg-fg-0) 2%, transparent)",
          position: "relative",
        }}
      >
        <svg
          width={SVG_W} height={SVG_H} viewBox={`0 0 ${SVG_W} ${SVG_H}`}
          preserveAspectRatio="none" style={{ display: "block" }}
        >
          {/* Per-row clipPath defs. SVG paths can otherwise overshoot
              their row's y-band (stroke half-width straddles the
              boundary). Clip each metric's line to its row's vertical
              bounds â€” guarantees lines NEVER visually overlap into a
              neighboring row regardless of value spikes or stroke
              thickness. */}
          <defs>
            {metricsRendered.map((m) => (
              <clipPath key={`clip-${m.id}`} id={`hm-row-clip-${m.id}`}>
                <rect
                  x={0}
                  y={m.yBase - 0.5}
                  width={SVG_W}
                  height={ROW_H + 1}
                />
              </clipPath>
            ))}
          </defs>
          {stackRects}
          {inFlightRects}
          {callOutlines}
          {metricsRendered.map((m) => (
            <path key={m.id} d={m.path} fill="none" stroke={m.stroke}
                  strokeWidth={1.4} strokeLinejoin="round" strokeLinecap="round"
                  opacity={0.95}
                  clipPath={`url(#hm-row-clip-${m.id})`} />
          ))}
          {/* Line-hover overlays â€” one transparent rect per metric row.
              On mousemove we compute SVG-local x and call valueAtX on the
              metric's anchor list. The cursor changes to crosshair to
              signal that the line graph is hoverable. Tests:
              tools/run-lab-tests.js â†’ "valueAtX line-graph hover lookup"
              + "makeHistoryPathWithAnchors" suites. */}
          {metricsRendered.map((m) => (
            <rect
              key={`hover-${m.id}`}
              x={0} y={m.yBase} width={SVG_W} height={ROW_H}
              fill="transparent"
              style={{ cursor: "crosshair" }}
              onMouseMove={(e) => {
                if (!onLineHover || !H || !m.anchors || m.anchors.length === 0) return;
                const svg = e.currentTarget.ownerSVGElement;
                if (!svg) return;
                const r = svg.getBoundingClientRect();
                if (r.width === 0) return;
                const localX = (e.clientX - r.left) / r.width * SVG_W;
                const v = H.valueAtX(m.anchors, localX);
                if (v == null) return;
                onLineHover({
                  metricId: m.id, label: m.label, value: v,
                  peak: m.peak, isWarn: m.isWarn, isCrit: m.isCrit,
                }, e);
              }}
              onMouseLeave={(e) => onLineHover && onLineHover(null, e)}
            />
          ))}
          {/* Crosshair vertical guide on the hovered metric row â€” purely
              decorative, drawn ABOVE the hover catcher rects but with
              pointer-events:none so it doesn't block hover. Could be
              added in a follow-up pass; deferred to keep this scope tight. */}
        </svg>
      </div>

      {/* Values column */}
      <div style={{ display: "flex", flexDirection: "column" }}>
        <div style={{
          height: STACK_H + 18, display: "flex", alignItems: "center",
          justifyContent: "flex-end", fontFamily: HG_FONT_MONO, fontSize: mobile ? 10 : 11,
          color: "var(--hg-fg-0)", fontWeight: 500,
        }}>{N} calls</div>
        {metricsRendered.map((m) => {
          const last = m.path ? (m.peak * 100).toFixed(0) : "â€”";
          const pk = (m.peak * 100).toFixed(0);
          return (
            <div key={m.id} style={{
              height: ROW_H + ROW_GAP, display: "flex", alignItems: "center",
              justifyContent: "flex-end",
              fontFamily: HG_FONT_MONO, fontSize: mobile ? 10 : 11, fontWeight: 500,
              color: m.isWarn ? "var(--hg-warn)" : "var(--hg-fg-0)",
            }}>
              {last}%
              <span style={{
                fontSize: mobile ? 8 : 8.5, color: "var(--hg-fg-4)",
                letterSpacing: "0.10em", marginLeft: mobile ? 4 : 6, fontWeight: 400,
              }}>pk {pk}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function LabChartSvgNow({ turn, onSegmentHover, onLineHover }) {
  const mobile = isLabMobileViewport();
  const SVG_W = 1000;
  const STACK_H = mobile ? 24 : 34;
  const ROW_H = mobile ? 30 : 32;
  const ROW_GAP = mobile ? 5 : 6;
  // Metrics â€” VRAM dropped (pill), TEMP added at bottom when sidecar
  // exposes gpu_temp_c (same TEMP_LINE_AVAILABLE gate as history mode).
  const TEMP_LINE_AVAILABLE =
    typeof window !== "undefined" &&
    window.__hav_metricsLatest &&
    typeof window.__hav_metricsLatest.gpu_temp_c === "number";
  const metricsCfg = TEMP_LINE_AVAILABLE
    ? [
        { id: "gpu",  label: "gpu"  },
        { id: "cpu",  label: "cpu"  },
        { id: "ram",  label: "ram"  },
        { id: "temp", label: "temp" },
      ]
    : [
        { id: "gpu",  label: "gpu"  },
        { id: "cpu",  label: "cpu"  },
        { id: "ram",  label: "ram"  },
      ];
  const N_METRICS = metricsCfg.length;
  const SVG_H = STACK_H + 24 + N_METRICS * (ROW_H + ROW_GAP);

  const stages = extractStages(turn);
  const total = turn.totalMs || stages.reduce((a, s) => a + s.ms, 0) || 1;
  let cursor = 0;
  const segs = stages.map((stg) => {
    const w = (stg.ms / total) * SVG_W;
    // Change 4: propagate stg.kind so the rect render below can pick
    // the tool tint for spliced tool segments.
    const seg = { x: cursor, w, stage: stg.label, kind: stg.kind || null, ms: stg.ms, total, slow: !!turn.slow };
    cursor += w;
    return seg;
  });

  // Resource lines â€” per-stage anchor synthesis from helper. Each turn
  // is a single 0..SVG_W slot. Real samples (when present) populate
  // their stage's anchor; sparse stages use STAGE_PROFILES.
  const samples = turn.samples || [];
  const H = (typeof window !== "undefined" && window.HomeMetricsLabHelpers) || null;
  const metricsRendered = metricsCfg.map((m, idx) => {
    const yBase = STACK_H + 24 + idx * (ROW_H + ROW_GAP);
    let path = "";
    let peak = 0;
    let last = null;
    let anchors = [];
    if (H) {
      // Use per-stage DENSE anchor synthesis for the single turn.
      // 4 stages Ã— 6 sub-anchors = ~24 anchors filling the full SVG width.
      anchors = H.buildDenseStageAnchors(turn, m.id, { slotStart: 0, slotEnd: SVG_W });
      const displayAnchors = H.scaleAnchorsForDisplay
        ? H.scaleAnchorsForDisplay(anchors)
        : anchors;
      path = H.pathFromAnchors(displayAnchors, { yBase, rowH: ROW_H });
      anchors.forEach((a) => { if (a.v > peak) peak = a.v; });
      last = anchors.length > 0 ? anchors[anchors.length - 1].v : null;
    } else {
      // Defensive fallback: render real samples only (was prior behavior).
      let prevValid = false;
      samples.forEach((s) => {
        const localT = Math.max(0, Math.min(total, (s.t || 0) - (turn.startedAt || 0)));
        const frac = total > 0 ? localT / total : 0;
        const v = s[`${m.id}Pct`];
        if (typeof v === "number") {
          const x = frac * SVG_W;
          const y = yBase + (1 - v) * ROW_H;
          path += (prevValid ? " L" : "M") + x.toFixed(1) + "," + y.toFixed(1);
          prevValid = true;
          if (v > peak) peak = v;
          last = v;
        } else { prevValid = false; }
      });
    }
    const thresholds = H ? H.THRESHOLDS : null;
    const threshold = (thresholds && thresholds[m.id]) || { warn: 999, crit: 999 };
    const isCrit = peak >= threshold.crit;
    const isWarn = peak >= threshold.warn;
    const stroke = H ? H.pickLineColor(peak, m.id) :
      (isCrit ? "var(--hg-crit)" : (isWarn ? "var(--hg-warn)" : "var(--hg-ice)"));
    return { id: m.id, label: m.label, path, peak, anchors, last, isWarn, isCrit, stroke, yBase };
  });

  const stackRects = segs.map((s, i) => {
    let tint;
    if (s.kind === "tool") {
      tint = s.slow ? TOOL_DEFAULT_TINT_SLOW : (STAGE_TINT_TOOL_LOOKUP(s.stage) || TOOL_DEFAULT_TINT);
    } else {
      tint = s.slow ? STAGE_TINT_SLOW[s.stage] : STAGE_TINT[s.stage];
    }
    const fillProps = tintFillProps(tint);
    const stroke = (s.slow && s.stage === "audio") ? "rgba(255,178,90,0.85)" : "var(--hg-fg-0)";
    const strokeOp = (s.slow && s.stage === "audio") ? 1 : 0.20;
    const showLbl = !mobile && s.w > 60;
    return (
      <g key={i}>
        <rect x={s.x.toFixed(2)} y={2} width={s.w.toFixed(2)} height={STACK_H}
              fill={fillProps.fill} fillOpacity={fillProps.fillOpacity}
              stroke={stroke} strokeOpacity={strokeOp} strokeWidth={0.6}
              style={{ cursor: "pointer", transition: "opacity 120ms" }}
              onMouseEnter={(e) => {
                e.currentTarget.style.opacity = 0.8;
                onSegmentHover?.(s, e);
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.opacity = 1;
                onSegmentHover?.(null, e);
              }} />
        {showLbl && (
          <>
            <text x={(s.x + 10).toFixed(2)} y={17}
                  fontFamily="Geist Mono" fontSize={9} fill="var(--hg-fg-0)" fontWeight={500}>
              {s.stage.toUpperCase()}
            </text>
            <text x={(s.x + 10).toFixed(2)} y={29}
                  fontFamily="Geist Mono" fontSize={10} fill="var(--hg-fg-1)" fontWeight={500}>
              {s.ms}<tspan fontSize={8} fill="var(--hg-fg-3)" dx={3}>ms</tspan>
            </text>
          </>
        )}
      </g>
    );
  });

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: mobile ? "42px minmax(0, 1fr) 58px" : "48px 1fr 70px",
      gap: mobile ? 8 : 12,
      alignItems: "stretch",
    }}>
      <div style={{ display: "flex", flexDirection: "column" }}>
        <div style={{
          height: STACK_H + 20, display: "flex", alignItems: "center",
          fontFamily: HG_FONT_MONO, fontSize: mobile ? 8.5 : 9,
          letterSpacing: mobile ? "0.14em" : "0.22em",
          textTransform: "uppercase", color: "var(--hg-fg-3)",
        }}>stack</div>
        {metricsRendered.map((m) => (
          <div key={m.id} style={{
            height: ROW_H + ROW_GAP, display: "flex", alignItems: "center",
            fontFamily: HG_FONT_MONO, fontSize: mobile ? 8.5 : 9,
            letterSpacing: mobile ? "0.14em" : "0.22em",
            textTransform: "uppercase",
            color: m.isCrit ? "var(--hg-crit)" : (m.isWarn ? "var(--hg-warn)" : "var(--hg-fg-3)"),
          }}>{m.label}</div>
        ))}
      </div>

      <div style={{
        overflowX: "hidden",
        background: "color-mix(in oklab, var(--hg-fg-0) 2%, transparent)",
        position: "relative",
      }}>
        <svg width="100%" viewBox={`0 0 ${SVG_W} ${SVG_H}`}
             preserveAspectRatio="none" style={{ display: "block", height: SVG_H }}>
          {stackRects}
          {metricsRendered.map((m) => (
            <path key={m.id} d={m.path} fill="none" stroke={m.stroke}
                  strokeWidth={1.3} strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {/* Line-hover overlays â€” see history-mode comment for rationale.
              In NOW mode the SVG is width:100% with viewBox, so we still
              normalize via getBoundingClientRect for accurate localX. */}
          {metricsRendered.map((m) => (
            <rect
              key={`hover-${m.id}`}
              x={0} y={m.yBase} width={SVG_W} height={ROW_H}
              fill="transparent"
              style={{ cursor: "crosshair" }}
              onMouseMove={(e) => {
                if (!onLineHover || !H || !m.anchors || m.anchors.length === 0) return;
                const svg = e.currentTarget.ownerSVGElement;
                if (!svg) return;
                const r = svg.getBoundingClientRect();
                if (r.width === 0) return;
                const localX = (e.clientX - r.left) / r.width * SVG_W;
                const v = H.valueAtX(m.anchors, localX);
                if (v == null) return;
                onLineHover({
                  metricId: m.id, label: m.label, value: v,
                  peak: m.peak, isWarn: m.isWarn, isCrit: m.isCrit,
                }, e);
              }}
              onMouseLeave={(e) => onLineHover && onLineHover(null, e)}
            />
          ))}
        </svg>
      </div>

      <div style={{ display: "flex", flexDirection: "column" }}>
        <div style={{
          height: STACK_H + 20, display: "flex", alignItems: "center",
          justifyContent: "flex-end", fontFamily: HG_FONT_MONO, fontSize: mobile ? 10 : 11,
          color: "var(--hg-fg-0)", fontWeight: 500,
        }}>{total}ms</div>
        {metricsRendered.map((m) => {
          const last = m.last != null ? (m.last * 100).toFixed(0) : "â€”";
          const pk = (m.peak * 100).toFixed(0);
          return (
            <div key={m.id} style={{
              height: ROW_H + ROW_GAP, display: "flex", alignItems: "center",
              justifyContent: "flex-end",
              fontFamily: HG_FONT_MONO, fontSize: mobile ? 10 : 11, fontWeight: 500,
              color: m.isWarn ? "var(--hg-warn)" : "var(--hg-fg-0)",
            }}>
              {last}%
              <span style={{
                fontSize: mobile ? 8 : 8.5, color: "var(--hg-fg-4)",
                letterSpacing: "0.10em", marginLeft: mobile ? 4 : 6, fontWeight: 400,
              }}>pk {pk}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* Tooltip â€” portal-mounted at document.body with position:fixed so
 * it floats above EVERYTHING (tray, drawer borders, chat input, etc).
 * Coords come from clientX/clientY directly â€” no scroll-container math.
 * Clamped to viewport so it doesn't overflow off-screen. */
function LabTooltip({ data, position }) {
  if (!data || !position) return null;
  const TIP_W = 260;
  const TIP_H_APPROX = 120;
  const PAD = 12;
  const vw = typeof window !== "undefined" ? window.innerWidth : 1280;
  const vh = typeof window !== "undefined" ? window.innerHeight : 800;
  // Place tooltip ABOVE the cursor by default, fall back to BELOW if it would clip.
  const wantsAbove = position.y - TIP_H_APPROX - PAD > 0;
  const top = wantsAbove
    ? Math.max(PAD, position.y - TIP_H_APPROX - PAD)
    : Math.min(vh - TIP_H_APPROX - PAD, position.y + PAD);
  const left = Math.max(
    PAD,
    Math.min(vw - TIP_W - PAD, position.x - TIP_W / 2)
  );
  const tip = (
    <div style={{
      position: "fixed", left, top,
      width: TIP_W,
      pointerEvents: "none",
      background: "var(--hg-bg-2)", border: "1px solid var(--hg-border)",
      padding: "10px 12px", fontFamily: HG_FONT_MONO,
      zIndex: 2147483647,
      boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
      transition: "opacity 120ms",
    }}>
      <div style={{
        fontSize: 8.5, letterSpacing: "0.22em", textTransform: "uppercase",
        color: "var(--hg-fg-3)", marginBottom: 6,
      }}>{data.kicker}</div>
      <div style={{
        fontFamily: HG_FONT_SANS, fontWeight: 300, fontSize: 22,
        color: data.crit ? "var(--hg-crit)" : data.warn ? "var(--hg-warn)" : "var(--hg-fg-0)",
        letterSpacing: "-0.025em", lineHeight: 1, marginBottom: 6,
      }}>
        {data.value}
        <span style={{
          fontFamily: HG_FONT_MONO, fontSize: 10, color: "var(--hg-fg-3)",
          marginLeft: 3, fontWeight: 400,
        }}>{data.unit}</span>
      </div>
      {data.micro && (
        <div style={{ fontSize: 9.5, color: "var(--hg-fg-3)", marginTop: 2 }}>
          {data.micro}
        </div>
      )}
      {/* F-15 (Addendum 27) â€” stage-attribution callout. Renders below
          micro when computeStageDelta returned a non-null summary
          (i.e., stage is at or above 1.5Ã— baseline AND baseline has
          enough samples). Color tracks band: slow = warn, very_slow =
          crit. */}
      {data.delta && (
        <div style={{
          marginTop: 6, padding: "4px 6px",
          background: data.deltaBand === "very_slow"
            ? "color-mix(in oklab, var(--hg-crit) 12%, transparent)"
            : "color-mix(in oklab, var(--hg-warn) 10%, transparent)",
          border: `1px solid ${data.deltaBand === "very_slow" ? "var(--hg-crit)" : "var(--hg-warn)"}`,
          color: data.deltaBand === "very_slow" ? "var(--hg-crit)" : "var(--hg-warn)",
          fontSize: 9, letterSpacing: "0.06em", fontFamily: HG_FONT_MONO,
        }}>
          {data.delta}
        </div>
      )}
      {/* Addendum 29 Change 5: dual-line tooltip â€” show BOTH user
          question (data.quote) AND assistant reply (data.quote_assistant).
          Each line gets a small prefix label ("you Â·" / "home Â·") and is
          truncated to ~140 chars. AV29-12: hide the assistant line when
          empty (tool-call-only rounds + in-flight stubs). */}
      {(data.quote || data.quote_assistant) && (
        <div style={{ marginTop: 6, lineHeight: 1.4 }}>
          {data.quote && (
            <div
              data-testid="hm-tooltip-user-line"
              style={{
                fontFamily: HG_FONT_SANS, fontSize: 11,
                color: "var(--hg-fg-3)", letterSpacing: "-0.005em",
              }}
            >
              <span style={{
                fontFamily: HG_FONT_MONO, fontSize: 8.5,
                letterSpacing: "0.22em", textTransform: "uppercase",
                color: "var(--hg-fg-4)", marginRight: 6,
              }}>you Â·</span>
              <span style={{ fontStyle: "italic" }}>
                "{String(data.quote).slice(0, 140)}"
              </span>
            </div>
          )}
          {data.quote_assistant && (
            <div
              data-testid="hm-tooltip-assistant-line"
              style={{
                fontFamily: HG_FONT_SANS, fontSize: 11,
                color: "var(--hg-fg-3)", letterSpacing: "-0.005em",
                marginTop: 3,
              }}
            >
              <span style={{
                fontFamily: HG_FONT_MONO, fontSize: 8.5,
                letterSpacing: "0.22em", textTransform: "uppercase",
                color: "var(--hg-fg-4)", marginRight: 6,
              }}>home Â·</span>
              <span style={{ fontStyle: "italic" }}>
                "{String(data.quote_assistant).slice(0, 140)}"
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
  return typeof document !== "undefined" && document.body
    ? ReactDOM.createPortal(tip, document.body)
    : tip;
}

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Sub-component: LabChartCard
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
function LabChartCard({ turns, samplesShared, tier, baseline, view, setView, selectedTurn, setSelectedTurn }) {
  const [tooltipData, setTooltipData] = useState(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });
  const containerRef = useRef(null);
  const mobile = isLabMobileViewport();

  const N = turns.length;
  const lastTurn = turns[N - 1] || null;
  const effectiveSelected = view === "now" ? (selectedTurn || lastTurn) : null;

  // Empty state â€” only when there are LITERALLY ZERO turns captured.
  // Addendum 13 fix C.1: 1+ turns render the chart with one filmstrip
  // slot. The chart-hero's baseline kv-block already degrades gracefully
  // via baseline.hasBaseline (shows "awaiting baseline Â· 5+ turns needed"
  // until enough turns exist for p10/p50/p90).
  if (N === 0) {
    return (
      <div style={{
        marginTop: 16, border: "1px solid var(--hg-border-soft)",
        padding: "32px 20px", textAlign: "center",
      }}>
        <div style={{
          fontFamily: HG_FONT_MONO, fontSize: 10.5, letterSpacing: "0.16em",
          textTransform: "uppercase", color: "var(--hg-fg-3)", marginBottom: 8,
        }}>turns / resources per turn</div>
        <div style={{
          fontFamily: HG_FONT_MONO, fontSize: 11.5, color: "var(--hg-fg-2)",
        }}>awaiting first turn (typed or voice)</div>
      </div>
    );
  }

  const onSegmentHover = (seg, e) => {
    if (!seg) {
      setTooltipData(null);
      return;
    }
    const turn = seg.turn || lastTurn;
    // Tooltip is rendered via React portal with position:fixed, so it
    // consumes CLIENT coords directly. Prior bug: subtracted container
    // rect â†’ fixed-positioned tooltip landed at viewport (small, small)
    // = top-left corner. Pure-function tests guard this in
    // tools/run-lab-tests.js â†’ "regression: cursor at (1500,600) â†’
    // tooltip near it, not at origin".
    setTooltipPos({ x: e.clientX, y: e.clientY });
    // Addendum 13 fix A: round ms before render â€” prevents
    // "282.10000000000000014 ms" floating-point artifacts from the
    // per-stage `total * pct / 100` arithmetic. Centralized in
    // window.HomeMetricsLabHelpers.formatMs. Tests:
    // tools/run-lab-tests.js â†’ "formatMs" suite.
    const fmt = (window.HomeMetricsLabHelpers && window.HomeMetricsLabHelpers.formatMs)
      || ((v) => (v == null ? "â€”" : String(Math.round(v))));
    // F-15 (Addendum 27): per-stage baseline callout. Hovering a slow
    // stage now shows "STT +400ms vs 200ms baseline (3.0Ã—)" beneath
    // the standard ms readout. Baseline = rolling p50 across the
    // trailing 30 turns (most-recent excluded so a slow turn doesn't
    // poison its own comparison). Suppressed when stage isn't above
    // 1.5Ã— baseline OR baseline is unstable (count < 4 same-stage turns).
    let stageDelta = null;
    const H = window.HomeMetricsLabHelpers;
    if (H && typeof H.computeStageBaselines === "function") {
      const baselines = H.computeStageBaselines(turns);
      if (typeof H.computeStageDelta === "function") {
        stageDelta = H.computeStageDelta(seg.ms, seg.stage, baselines);
      }
    }
    setTooltipData({
      kicker: view === "now"
        ? seg.stage
        : `${seg.stage} Â· ${seg.turnIdx === N - 1 ? "now" : (N - 1 - seg.turnIdx) + " turns ago"}`,
      value: fmt(seg.ms),
      unit: "ms",
      // Warn coloring: original `slow audio` heuristic OR explicit
      // stage-delta band (slow / very_slow). Very_slow uses crit color
      // for stronger signal.
      warn: !!(seg.slow && seg.stage === "audio") || (stageDelta && stageDelta.band === "slow"),
      crit: stageDelta && stageDelta.band === "very_slow",
      micro: view === "now"
        ? `${Math.round((seg.ms / seg.total) * 100)}% of turn`
        : `${Math.round((seg.ms / seg.total) * 100)}% of ${fmt(seg.total)} ms turn`,
      delta: stageDelta ? stageDelta.summary : null,
      deltaBand: stageDelta ? stageDelta.band : null,
      // Addendum 29 Change 5: dual-line tooltip. `quote` = user text,
      // `quote_assistant` = assistant reply. Both shown in history view
      // (where context matters); now view stays compact (current turn's
      // detail is in the segment durations + delta callout).
      quote:           view === "history" ? (turn?.transcript || null)       : null,
      quote_assistant: view === "history" ? (turn?.assistant_text || null) : null,
    });
  };

  const onSegmentClick = (turn, idx) => {
    setSelectedTurn(turn);
    setView("now");
  };

  // Line-graph hover: cursor is over a resource line (gpu/cpu/ram/temp).
  // `info = { metricId, label, value, peak, isWarn, isCrit }` where value
  // is the interpolated 0..1 value at the cursor's SVG-local x position
  // (computed by the chart SVG using H.valueAtX). Tooltip shows it as %
  // (or Â°C for temp). Pass null to dismiss.
  const onLineHover = (info, e) => {
    if (!info) {
      setTooltipData(null);
      return;
    }
    setTooltipPos({ x: e.clientX, y: e.clientY });
    const pct = Math.round((info.value || 0) * 100);
    const isWarn = !!info.isWarn || !!info.isCrit;
    let valueStr, unitStr;
    if (info.metricId === "temp") {
      valueStr = String(Math.round((info.value || 0) * 100));
      unitStr = "Â°c";
    } else {
      valueStr = String(pct);
      unitStr = "%";
    }
    setTooltipData({
      kicker: info.label,
      value: valueStr,
      unit: unitStr,
      warn: isWarn,
      micro: `peak ${Math.round((info.peak || 0) * 100)}${info.metricId === "temp" ? "Â°c" : "%"}`,
      quote: null,
    });
  };

  return (
    <div ref={containerRef} style={{
      marginTop: 16, border: "1px solid var(--hg-border-soft)",
      padding: mobile
        ? "15px 14px calc(28px + env(safe-area-inset-bottom, 0px))"
        : "18px 20px",
      position: "relative",
      marginBottom: mobile ? "calc(18px + env(safe-area-inset-bottom, 0px))" : 0,
    }}>
      <div style={{
        display: "flex",
        alignItems: mobile ? "center" : "baseline",
        flexWrap: mobile ? "wrap" : "nowrap",
        gap: mobile ? "8px 10px" : 14,
        marginBottom: mobile ? 12 : 14,
      }}>
        <span style={{
          fontFamily: HG_FONT_MONO,
          fontSize: mobile ? 9.5 : 10.5,
          letterSpacing: mobile ? "0.10em" : "0.18em",
          textTransform: "uppercase", color: "var(--hg-fg-2)",
          minWidth: mobile ? "100%" : "auto",
        }}>{mobile ? "turns / resources" : "turns / resources per turn"}</span>
        <div style={{ display: "inline-flex", border: "1px solid var(--hg-border-soft)", flexShrink: 0 }}>
          {["history", "now"].map((v) => (
            <button key={v} onClick={() => setView(v)} style={{
              padding: "4px 11px", background: view === v ? "color-mix(in oklab, var(--hg-fg-0) 4%, transparent)" : "transparent",
              border: "none", fontFamily: HG_FONT_MONO, fontSize: 10,
              letterSpacing: "0.10em", textTransform: "lowercase",
              color: view === v ? "var(--hg-fg-0)" : "var(--hg-fg-3)",
              cursor: "pointer", transition: "color 120ms",
            }}>{v}</button>
          ))}
        </div>
        <span style={{
          marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 6,
          padding: "3px 8px", border: `1px solid ${
            tier.chartBadge.tone === "warn" ? "var(--hg-warn)" :
            tier.chartBadge.tone === "crit" ? "var(--hg-crit)" :
            tier.chartBadge.tone === "ok" ? "var(--hg-fg-2)" : "var(--hg-border)"
          }`,
          fontFamily: HG_FONT_MONO, fontSize: 9.5, letterSpacing: "0.20em",
          textTransform: "uppercase",
          flexShrink: 0,
          color: tier.chartBadge.tone === "warn" ? "var(--hg-warn)" :
                 tier.chartBadge.tone === "crit" ? "var(--hg-crit)" :
                 tier.chartBadge.tone === "ok" ? "var(--hg-fg-2)" : "var(--hg-fg-3)",
        }}>
          <span style={{ width: 5, height: 5, borderRadius: "50%", background: "currentColor" }}></span>
          {tier.chartBadge.text}
        </span>
      </div>

      <LabChartHero
        lastTurn={lastTurn}
        baseline={baseline}
        slow={tier.tier === "slow"}
        prevMs={window.HomeMetricsLabHelpers
          ? window.HomeMetricsLabHelpers.computeBaselinePrev(turns)
          : null}
      />

      <LabChartSvg
        turns={turns}
        mode={view}
        selectedTurn={effectiveSelected}
        samplesShared={samplesShared}
        onSegmentClick={onSegmentClick}
        onSegmentHover={onSegmentHover}
        onLineHover={onLineHover}
      />
      <LabTooltip data={tooltipData} position={tooltipPos} />

      <div style={{
        display: "flex",
        flexWrap: mobile ? "wrap" : "nowrap",
        gap: mobile ? "7px 10px" : 14,
        paddingTop: 12,
        marginTop: 12,
        borderTop: "1px solid var(--hg-border-soft)",
        fontFamily: HG_FONT_MONO, fontSize: mobile ? 9.5 : 10, color: "var(--hg-fg-3)",
        letterSpacing: "0.04em", alignItems: "center",
      }}>
        {view === "history" ? (
          <>
            <span>window <b style={{ color: "var(--hg-fg-0)", fontWeight: 500 }}>{Math.min(21, N)} of {N} calls</b></span>
            <span style={{
              marginLeft: mobile ? 0 : "auto",
              fontSize: mobile ? 8.5 : 9,
              letterSpacing: mobile ? "0.08em" : "0.16em",
              textTransform: "uppercase", color: "var(--hg-fg-4)",
            }}>{mobile ? "swipe chart / tap blocks" : "history / scroll horizontally / hover for detail"}</span>
          </>
        ) : (
          <>
            <span>turn duration <b style={{ color: "var(--hg-fg-0)", fontWeight: 500 }}>
              {effectiveSelected?.totalMs || 0}</b> ms</span>
            <button onClick={() => setView("history")} style={{
              marginLeft: mobile ? 0 : "auto", padding: mobile ? "5px 9px" : "3px 9px",
              background: "transparent", border: "1px solid var(--hg-border-soft)",
              fontFamily: HG_FONT_MONO, fontSize: 9.5, letterSpacing: "0.10em",
              textTransform: "lowercase", color: "var(--hg-fg-2)", cursor: "pointer",
            }}>back to history</button>
          </>
        )}
      </div>
    </div>
  );
}

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Sub-component: LabResPills
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
function LabResPills({ metrics }) {
  metrics = metrics || {};
  const vramPct = (metrics.vramMax || 0) > 0
    ? Math.min(1, (metrics.vram || 0) / metrics.vramMax) : 0;
  const ramPct = (metrics.ramMax || 0) > 0
    ? Math.min(1, (metrics.ram || 0) / metrics.ramMax) : 0;
  const pills = [
    {
      id: "vram",
      value: Math.round(vramPct * 100),
      unit: `% Â· ${(metrics.vram || 0).toFixed(1)}/${(metrics.vramMax || 0).toFixed(0)}g`,
      pct: vramPct, warn: vramPct >= LAB_THRESHOLDS.vram.warn,
    },
    {
      id: "gpu",
      value: metrics.gpu || 0,
      unit: "%",
      pct: (metrics.gpu || 0) / 100, warn: (metrics.gpu || 0) / 100 >= LAB_THRESHOLDS.gpu.warn,
    },
    {
      id: "cpu",
      value: metrics.cpu || 0,
      unit: "%",
      pct: (metrics.cpu || 0) / 100, warn: (metrics.cpu || 0) / 100 >= LAB_THRESHOLDS.cpu.warn,
    },
    {
      id: "ram",
      value: Math.round((metrics.ram || 0)),
      unit: `g Â· ${Math.round(ramPct * 100)}%`,
      pct: ramPct, warn: ramPct >= LAB_THRESHOLDS.ram.warn,
    },
  ];
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "repeat(4, 1fr)",
      gap: 10, marginTop: 16,
    }}>
      {pills.map((p) => (
        <div key={p.id} style={{
          border: "1px solid var(--hg-border-soft)", padding: "12px 14px",
        }}>
          <span style={{
            display: "block", fontFamily: HG_FONT_MONO, fontSize: 9,
            letterSpacing: "0.20em", textTransform: "uppercase",
            color: "var(--hg-fg-3)", marginBottom: 6,
          }}>{p.id}</span>
          <span style={{
            fontFamily: HG_FONT_SANS, fontWeight: 300, fontSize: 22,
            color: p.warn ? "var(--hg-warn)" : "var(--hg-fg-0)",
            letterSpacing: "-0.02em", lineHeight: 1,
          }}>
            {p.value}
            <span style={{
              fontFamily: HG_FONT_MONO, fontSize: 10, color: "var(--hg-fg-3)",
              marginLeft: 3, fontWeight: 400,
            }}>{p.unit}</span>
          </span>
          <div style={{
            marginTop: 8, height: 3,
            background: "color-mix(in oklab, var(--hg-fg-0) 6%, transparent)",
            position: "relative",
          }}>
            <div style={{
              position: "absolute", left: 0, top: 0, bottom: 0,
              width: `${Math.min(100, p.pct * 100)}%`,
              background: p.warn ? "var(--hg-warn)" : "var(--hg-fg-1)",
            }}></div>
          </div>
        </div>
      ))}
    </div>
  );
}

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Sub-component: LabDiagPane
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
const DEFAULT_LAB_LOG_FILTERS = Object.freeze({
  all: false,
  info: true,
  warn: true,
  err: true,
  dbg: false,
});

function _labLogLineMatchesFilters(line, filters) {
  if (!filters) return true;
  if (filters.all) return true;
  const level = String(line?.level || "info").toLowerCase();
  return !!filters[level];
}

function _labEscapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function _labLogLineFromText(text, level = "info") {
  const now = new Date();
  const hh = String(now.getHours()).padStart(2, "0");
  const mm = String(now.getMinutes()).padStart(2, "0");
  const ss = String(now.getSeconds()).padStart(2, "0");
  return {
    t: `${hh}:${mm}:${ss}`,
    level,
    msg: _labEscapeHtml(text),
  };
}

function LabDiagPane({ services, supervisorUrl, stackToken, tokenConfigured, onAction, logLines }) {
  const HSA = window.HomeStackActions;
  const [logFilters, setLogFilters] = useState(DEFAULT_LAB_LOG_FILTERS);
  const visibleLogLines = (logLines || []).filter((line) =>
    _labLogLineMatchesFilters(line, logFilters)
  );
  const toggleLogFilter = (key) => {
    setLogFilters((current) => ({
      ...current,
      [key]: !current[key],
      ...(key === "all" ? {} : { all: false }),
    }));
  };
  return (
    <div style={{ padding: "12px 0" }}>
      {/* diag-globals */}
      <div style={{
        display: "flex", gap: 8, marginBottom: 14,
      }}>
        <DiagBtn label="free gpu" glyph="âœ•" tone="danger" confirm
          onClick={() => onAction?.("freeGpu")} disabled={!tokenConfigured} />
        <DiagBtn label="reload all" glyph="â–¶" tone="danger" confirm
          onClick={() => onAction?.("restart")} disabled={!tokenConfigured} />
        <DiagBtn label="pause perception" glyph="||" tone="warn" confirm
          onClick={() => onAction?.("pausePerception")} disabled={!tokenConfigured} />
        <DiagBtn label="clear cache" glyph="â†»" confirm
          onClick={() => onAction?.("clearCache")} />
      </div>

      <p style={{
        fontFamily: HG_FONT_MONO, fontSize: 9, letterSpacing: "0.22em",
        textTransform: "uppercase", color: "var(--hg-fg-4)",
        margin: "16px 0 8px",
      }}>services</p>

      {services.map((svc) => (
        <div key={svc.name} style={{
          padding: "12px 14px", border: "1px solid var(--hg-border-soft)",
          marginBottom: 6,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{
              width: 6, height: 6, borderRadius: "50%",
              background: svc.dot === "cool" ? "var(--hg-ice)" :
                          svc.dot === "warn" ? "var(--hg-warn)" :
                          svc.dot === "err"  ? "var(--hg-crit)" :
                          svc.dot === "idle" ? "var(--hg-fg-5)" : "var(--hg-fg-2)",
              boxShadow: svc.dot === "cool" ? "0 0 5px var(--hg-ice-glow)" : "none",
            }}></span>
            <span style={{
              fontFamily: HG_FONT_MONO, fontSize: 12, color: "var(--hg-fg-0)",
              fontWeight: 500, letterSpacing: "0.04em",
            }}>{svc.name}</span>
            <span style={{
              fontFamily: HG_FONT_MONO, fontSize: 10, color: "var(--hg-fg-3)",
              letterSpacing: "0.04em",
            }}>{svc.meta}</span>
            <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
              <DiagBtn label="logs" glyph="â–¤" small
                onClick={() => onAction?.("serviceLogs", svc.name)}
                disabled={!tokenConfigured} />
              <DiagBtn label="stop" glyph="â–¢" tone="danger" small confirm
                onClick={() => onAction?.("serviceStop", svc.name)}
                disabled={!tokenConfigured} />
              <DiagBtn label="restart" glyph="â–¶" tone="danger" small confirm
                onClick={() => onAction?.("serviceRestart", svc.name)}
                disabled={!tokenConfigured} />
            </div>
          </div>
        </div>
      ))}

      {/* Log filter strip */}
      <LabLogFilter filters={logFilters} onToggle={toggleLogFilter} />
      {/* Log pane */}
      <LabLogPane lines={visibleLogLines} />
    </div>
  );
}

/* DiagBtn â€” Pattern B1 two-click arm â†’ confirm when `confirm` is set.
 * Armed flag is React state (per-instance useState + timer ref), replacing
 * the legacy DOM confirmAct helper whose dataset armed flag silently
 * survived poll-driven re-renders after the label was repainted. */
function DiagBtn({ label, glyph, tone, small, confirm, onClick, disabled }) {
  const [armed, setArmed] = useState(false);
  const confirmTimerRef = useRef(null);
  const clearConfirmTimer = () => {
    if (confirmTimerRef.current) {
      try { clearTimeout(confirmTimerRef.current); } catch {}
      confirmTimerRef.current = null;
    }
  };
  useEffect(() => clearConfirmTimer, []);
  const handleClick = () => {
    if (disabled) return;
    if (!confirm) { onClick?.(); return; }
    if (!armed) {
      clearConfirmTimer();
      setArmed(true);
      confirmTimerRef.current = setTimeout(() => {
        setArmed(false);
        confirmTimerRef.current = null;
      }, 3000);
      return;
    }
    clearConfirmTimer();
    setArmed(false);
    onClick?.();
  };
  const colorMap = {
    danger: { color: "var(--hg-crit)", borderColor: "var(--hg-crit)" },
    warn: { color: "var(--hg-warn)", borderColor: "var(--hg-warn)" },
    default: { color: "var(--hg-fg-2)", borderColor: "var(--hg-border)" },
  };
  const c = colorMap[tone || "default"];
  return (
    <button
      disabled={disabled}
      title={disabled ? "STACK_TOKEN required â€” use /stack-token" : ""}
      onClick={handleClick}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: small ? "4px 9px" : "5px 11px",
        border: `1px solid ${disabled ? "var(--hg-border-soft)" : c.borderColor}`,
        background: "transparent",
        fontFamily: HG_FONT_MONO, fontSize: small ? 9 : 10,
        letterSpacing: small ? "0.12em" : "0.10em",
        textTransform: "lowercase",
        color: disabled ? "var(--hg-fg-5)" : c.color,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        transition: "color 120ms, border-color 120ms, background 120ms",
      }}
    >
      <span style={{
        color: disabled ? "var(--hg-fg-5)" : (armed ? "var(--hg-warn)" : "var(--hg-fg-3)"),
      }}>{armed ? "✓" : glyph}</span>
      {armed ? "confirm" : label}
    </button>
  );
}

function LabLogFilter({ filters = DEFAULT_LAB_LOG_FILTERS, onToggle }) {
  const toggle = (k) => onToggle?.(k);
  return (
    <div style={{
      display: "flex", gap: 6, alignItems: "center",
      fontFamily: HG_FONT_MONO, fontSize: 9.5, color: "var(--hg-fg-3)",
      letterSpacing: "0.10em", margin: "18px 0 8px",
    }}>
      <span style={{
        fontSize: 9, letterSpacing: "0.20em", textTransform: "uppercase",
        color: "var(--hg-fg-4)", marginRight: 4,
      }}>filter</span>
      {["all", "info", "warn", "err", "dbg"].map((k) => (
        <button key={k} type="button" className="hg-focusable" aria-pressed={!!filters[k]} onClick={() => toggle(k)} style={{
          padding: "3px 8px",
          border: `1px solid ${filters[k] ? "var(--hg-border)" : "var(--hg-border-soft)"}`,
          color: filters[k] ? "var(--hg-fg-0)" : "var(--hg-fg-3)",
          background: filters[k] ? "color-mix(in oklab, var(--hg-fg-0) 5%, transparent)" : "transparent",
          cursor: "pointer", textTransform: "lowercase",
          font: "inherit", letterSpacing: "inherit",
        }}>{k}</button>
      ))}
      <span style={{
        marginLeft: "auto", color: "var(--hg-fg-4)", fontSize: 9.5,
      }}>tailing</span>
    </div>
  );
}

function LabLogPane({ lines }) {
  if (!lines || lines.length === 0) {
    return (
      <div style={{
        background: "color-mix(in oklab, var(--hg-fg-0) 2%, transparent)",
        border: "1px solid var(--hg-border-soft)",
        padding: "20px 14px",
        fontFamily: HG_FONT_MONO, fontSize: 10.5,
        color: "var(--hg-fg-4)", textAlign: "center",
      }}>no recent log lines Â· streaming when stack is active</div>
    );
  }
  return (
    <div style={{
      background: "color-mix(in oklab, var(--hg-fg-0) 2%, transparent)",
      border: "1px solid var(--hg-border-soft)",
      padding: "10px 14px",
      fontFamily: HG_FONT_MONO, fontSize: 10.5,
      lineHeight: 1.7,
      maxHeight: 200, overflowY: "auto",
    }}>
      {lines.map((line, i) => {
        const lvColor = {
          info: "var(--hg-fg-2)", warn: "var(--hg-warn)",
          err:  "var(--hg-crit)", dbg:  "var(--hg-fg-3)",
        }[line.level] || "var(--hg-fg-2)";
        const msgColor = (line.level === "warn") ? "var(--hg-warn)" :
                         (line.level === "err")  ? "var(--hg-crit)" : "var(--hg-fg-1)";
        return (
          <div key={i} style={{
            display: "grid", gridTemplateColumns: "56px 36px 1fr",
            gap: 8, alignItems: "baseline",
            padding: "2px 6px", margin: "0 -6px",
          }}>
            <span style={{ color: "var(--hg-fg-3)", fontSize: 9.5, letterSpacing: "0.04em" }}>{line.t}</span>
            <span style={{ fontSize: 8.5, letterSpacing: "0.14em", textTransform: "uppercase", color: lvColor }}>
              {line.level}
            </span>
            <span style={{ color: msgColor }}
                  dangerouslySetInnerHTML={{ __html: line.msg }} />
          </div>
        );
      })}
    </div>
  );
}

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Main: HmLabTab
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
function HmLabTab({
  // From MetricsStrip / home-app
  metrics, metricsHistory,
  aiStackOnline, aiStackState,
  lastTrace, traceSummary,
  // Lab buffers â€” refs from home-app
  labTurns, labSamples,
  // Supervisor wiring
  supervisorUrl, stackToken, tokenConfigured,
  // Sim
  simActive,
  // Sub-pane selection
  diagOpen, setDiagOpen,
}) {
  const [view, setView] = useState("history");
  const [selectedTurn, setSelectedTurn] = useState(null);
  const [actionResult, setActionResult] = useState(null);
  const [logLines, setLogLines] = useState([]);
  const logStreamCtrlRef = useRef(null);

  // Reset selection if turns change such that the selected turn isn't there
  useEffect(() => {
    if (selectedTurn && !labTurns.find((t) => t.id === selectedTurn.id)) {
      setSelectedTurn(null);
    }
  }, [labTurns, selectedTurn]);

  const stopLogStream = useCallback(() => {
    if (logStreamCtrlRef.current) {
      try { logStreamCtrlRef.current.abort(); } catch {}
      logStreamCtrlRef.current = null;
    }
  }, []);

  useEffect(() => () => stopLogStream(), [stopLogStream]);

  useEffect(() => {
    if (!diagOpen) stopLogStream();
  }, [diagOpen, stopLogStream]);

  const baseline = useMemo(() => computeBaseline(labTurns), [labTurns]);
  const tier = useMemo(
    () => deriveLabTier(metrics, aiStackState, lastTrace, baseline),
    [metrics, aiStackState, lastTrace, baseline]
  );

  // Action dispatcher â€” delegates to HomeStackActions for the real ones
  const onAction = useCallback(async (verb, serviceName) => {
    if (verb === "clearCache") {
      stopLogStream();
      setLogLines([]);
      setActionResult({ verb, kind: "ok" });
      return;
    }
    const HSA = window.HomeStackActions;
    if (!HSA) {
      setActionResult({ verb, kind: "error", error: "HomeStackActions not loaded" });
      return;
    }
    setActionResult({ verb, kind: "starting" });
    let result;
    if (verb === "start")          result = await HSA.start({ supervisorUrl, stackToken });
    else if (verb === "restart")   result = await HSA.restart({ supervisorUrl, stackToken });
    else if (verb === "stop")      result = await HSA.stop({ supervisorUrl, stackToken });
    else if (verb === "freeGpu")   result = await HSA.freeGpu({ supervisorUrl, stackToken });
    else if (verb === "pausePerception") result = await HSA.pausePerception({ supervisorUrl, stackToken });
    else if (verb === "serviceLogs") {
      stopLogStream();
      setLogLines([]);
      if (typeof window.streamSse === "function" && typeof HSA.logsStreamUrl === "function") {
        const url = HSA.logsStreamUrl({ supervisorUrl });
        logStreamCtrlRef.current = window.streamSse(
          url,
          { Authorization: `Bearer ${stackToken}` },
          {
            onLine: (line) => {
              setLogLines((prev) => {
                const next = _labLogLineFromText(line, "info");
                return prev.length >= 200 ? [...prev.slice(-199), next] : [...prev, next];
              });
            },
            onEvent: (event, data) => {
              if (event === "done") {
                setActionResult({
                  verb,
                  service: serviceName,
                  kind: (data?.exit_code ?? 0) === 0 ? "ok" : "error",
                  error: (data?.exit_code ?? 0) === 0 ? undefined : `exit ${data?.exit_code}`,
                });
              }
            },
            onError: (err) => {
              const msg = (err && err.message) ? err.message : String(err);
              setLogLines((prev) => [...prev.slice(-199), _labLogLineFromText(msg, "err")]);
              setActionResult({ verb, service: serviceName, kind: "error", error: msg });
            },
            onClose: () => {
              logStreamCtrlRef.current = null;
            },
          }
        );
        setActionResult({ verb, service: serviceName, kind: "streaming" });
        return;
      }
      result = await HSA.serviceLogs({ supervisorUrl, stackToken, service: serviceName });
      if (result.ok && Array.isArray(result.body?.lines)) {
        setLogLines(result.body.lines.map((line) => _labLogLineFromText(line, "info")));
      }
    }
    else if (verb === "serviceStop")    result = await HSA.serviceStop({ supervisorUrl, stackToken, service: serviceName });
    else if (verb === "serviceRestart") result = await HSA.serviceRestart({ supervisorUrl, stackToken, service: serviceName });
    else if (verb === "logs") {
      // Generic logs: fetch full-stack supervisor logs
      stopLogStream();
      result = await HSA.serviceLogs({ supervisorUrl, stackToken, service: "supervisor" });
      if (result.ok && Array.isArray(result.body?.lines)) {
        setLogLines(result.body.lines.map((line) => _labLogLineFromText(line, "info")));
      }
    } else return;
    setActionResult({ verb, kind: result.ok ? "ok" : "error", error: result.error });
  }, [supervisorUrl, stackToken, stopLogStream]);

  // Build compact stack list from aiStackState.services
  const services = useMemo(() => buildStackList(aiStackState, metrics), [aiStackState, metrics]);

  return (
    <div style={{ padding: "4px 0" }}>
      {/* LabSummaryHeader / LabProgressRow / LabBanner / LabStackList /
          LabResPills all migrated to the AI tab (renderAi in
          home-app.jsx). This tab ('trace') is now chart-focused â€” the
          AI tab is the "what's happening right now" surface. */}

      {!diagOpen && (
        <>
          <LabChartCard
            turns={labTurns}
            samplesShared={labSamples}
            tier={tier}
            baseline={baseline}
            view={view}
            setView={setView}
            selectedTurn={selectedTurn}
            setSelectedTurn={setSelectedTurn}
          />
        </>
      )}

      {diagOpen && (
        <LabDiagPane
          services={services}
          supervisorUrl={supervisorUrl}
          stackToken={stackToken}
          tokenConfigured={tokenConfigured}
          onAction={onAction}
          logLines={logLines}
        />
      )}

      <div style={{
        display: "flex", justifyContent: "flex-end", marginTop: 14,
        paddingTop: 10, borderTop: "1px solid var(--hg-border-soft)",
      }}>
        <button onClick={() => setDiagOpen(!diagOpen)} style={{
          padding: "4px 11px", background: "transparent",
          border: "1px solid var(--hg-border-soft)",
          fontFamily: HG_FONT_MONO, fontSize: 10, letterSpacing: "0.10em",
          textTransform: "lowercase",
          color: diagOpen ? "var(--hg-fg-0)" : "var(--hg-fg-3)", cursor: "pointer",
        }}>{diagOpen ? "â† back" : "diag Â· logs + service ops â†’"}</button>
      </div>

      {actionResult && (
        <div style={{
          marginTop: 8, fontFamily: HG_FONT_MONO, fontSize: 10,
          color: actionResult.kind === "error" ? "var(--hg-crit)" : "var(--hg-fg-3)",
          letterSpacing: "0.04em", textAlign: "right",
        }}>
          {actionResult.verb} {actionResult.kind === "error" ? `Â· ${actionResult.error}` : "Â· dispatched"}
        </div>
      )}
    </div>
  );
}

/* Build the compact stack list from supervisor service data + known infra
 * hints. Resilient to missing services / sim mode. */
function buildStackList(aiStackState, metrics) {
  const supServices = (aiStackState?.services || []);

  const displayNameFor = (name) => {
    const normalized = String(name || "").replace(/_/g, "-");
    return normalized === "kokoro-tts" ? "kokoro" : normalized;
  };

  const find = (name) => {
    const wanted = displayNameFor(name);
    return supServices.find((s) => displayNameFor(s.name) === wanted);
  };

  function rowFor(name, primary = false) {
    const s = find(name);
    const displayName = displayNameFor(name);
    name = displayName;
    if (s) {
      const tone = s.container === "exited" || s.container === "missing" ||
                   s.health === "error" || s.health === "unhealthy" || s.probe === "error" ? "err" :
                   s.container === "starting" || s.health === "starting" ||
                   s.health === "stale" || s.health === "degraded" ||
                   s.probe === "fail" || s.probe === "stale" ? "warn" :
                   s.probe === "ok" ? (primary ? "cool" : "good") : "good";
      const meta = s.detail || `${s.container || "running"}${s.health ? " Â· " + s.health : ""}`;
      const cls = (tone === "err" || tone === "warn") ? tone : "default";
      return { name: displayName, dot: tone, meta, cls };
    }
    return { name, dot: "idle", meta: "â€”", cls: "default" };
  }

  const ordered = STACK_SERVICE_ORDER.map((name) => rowFor(name, name === "vllm"));
  const vllm = ordered.find((s) => s.name === "vllm");
  if (vllm && metrics?.model) {
    vllm.meta = `${metrics.model}` + (vllm.meta !== "â€”" ? ` Â· ${vllm.meta}` : "");
  }

  const known = new Set(STACK_SERVICE_ORDER);
  const extras = supServices
    .map((s) => displayNameFor(s.name))
    .filter((name, i, arr) => name && !known.has(name) && arr.indexOf(name) === i)
    .map((name) => rowFor(name));

  return [
    ...ordered,
    ...extras,
    ...STACK_INFRA_ROWS.map((row) => ({ ...row })),
  ];
}

/* â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 * Window exports
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
window.HmLabTab = HmLabTab;
window.deriveLabTier = deriveLabTier;
window.computeBaseline = computeBaseline;
window.extractStages = extractStages;
window.LAB_THRESHOLDS = LAB_THRESHOLDS;
window.STAGE_TINT = STAGE_TINT;
window.STAGE_TINT_SLOW = STAGE_TINT_SLOW;
// Phase-N (AI-tab migration): expose the 3 sub-components AI tab now
// renders directly. Banner + service list + resource pills are
// machine-relevant at-a-glance info; they live on the AI tab (the
// "what's going on right now" view), not the Lab tab (the chart-driven
// diagnostic view).
window.LabBanner = LabBanner;
window.LabStackList = LabStackList;
window.LabResPills = LabResPills;
window.buildStackList = buildStackList;
// Addendum 26: list/flow toggle wrapper. AI tab uses this instead of
// LabStackList directly. Backwards-compat: LabStackList stays exported
// so anything else referencing it (e.g., tests, alternate panes) still
// works.
window.LabStackPane = LabStackPane;
// Addendum 26: schema-driven pipeline metadata â€” exported so tests can
// assert PIPELINE / SERVICE_META / ENDPOINT_META stay in sync with the
// services buildStackList() emits.
window.LabPipelineSchema = {
  SERVICE_META,
  ENDPOINT_META,
  PIPELINE,
  FLOW_LEGEND_SERVICES,
  STACK_SERVICE_ORDER,
  STACK_INFRA_ROWS,
};

// Addendum 29 Change 2: one-time CSS keyframes for the in-flight pulse
// animation. Injected lazily so re-imports / hot-reloads don't duplicate.
(function injectInflightKeyframes() {
  if (typeof document === "undefined") return;
  if (window.__LAB_INFLIGHT_KEYFRAMES_INJECTED) return;
  try {
    const style = document.createElement("style");
    style.setAttribute("data-source", "home-metrics-lab inflight pulse");
    style.textContent = `
      @keyframes hmLabInflightPulse {
        0%   { opacity: 0.35; }
        50%  { opacity: 0.85; }
        100% { opacity: 0.35; }
      }
    `;
    document.head.appendChild(style);
    window.__LAB_INFLIGHT_KEYFRAMES_INJECTED = true;
  } catch (e) {
    // No-op if document not ready / CSP blocks inline styles.
  }
})();
