/* Home — the main React app.
 *
 * Encapsulates: header, status bar, scrolling event feed, metrics strip,
 * input row, voice mode lifecycle, theme switching, and the scripted
 * demo player. Designed to live inside a fixed-size frame (default 420×720).
 */

const { useState, useEffect, useRef, useCallback, useMemo } = React;

/* ── helpers ─────────────────────────────────────────────────────────── */
function fmtTime(d = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/* Adds an id to each event for stable list keying */
let _id = 0;
const nextId = () => `e-${++_id}`;

/* ── Header ──────────────────────────────────────────────────────────── */
function HomeHeader({ theme, onToggleTheme, voice, connection, sidecarOnline, bridgeOnline }) {
  const isLive = voice.state !== "inactive" && voice.state !== "no-mic";
  // Phase 1 bugfix: header no longer mirrors the voice state (listening /
  // processing / speaking) — that's already shown in the bottom VoiceBanner
  // with a waveform animation. Header only conveys CONNECTION state now,
  // plus the optional offline-pill for downstream services.
  const statusText =
    connection === "online"       ? "online"     :
    connection === "connecting"   ? "connecting" :
    connection === "auth_invalid" ? "bad token"  : "offline";
  // Phase B F0-08: surface sidecar/bridge offline as a warning pill.
  // sidecarOnline = false means SSE chat-tee is broken → assistant
  // replies won't reach the feed even if HA fires. bridgeOnline = false
  // means voice mode is broken (no identity/media events, no s2s WS).
  // Both null = unknown (first probe pending) — hide pill.
  const sidecarDown = sidecarOnline === false;
  const bridgeDown = bridgeOnline === false;
  const showWarn = (connection === "online") && (sidecarDown || bridgeDown);
  const warnText = sidecarDown && bridgeDown ? "voice + chat offline"
                 : sidecarDown ? "chat-tee offline"
                 : bridgeDown ? "voice bridge offline" : "";
  const iconBtn = {
    background: "transparent", border: "none", padding: 4, cursor: "pointer",
    color: "var(--hg-fg-3)",
    display: "inline-flex", alignItems: "center", justifyContent: "center",
    lineHeight: 0,
  };
  const hoverBright = (e) => { e.currentTarget.style.color = "var(--hg-fg-0)"; };
  const hoverWarn   = (e) => { e.currentTarget.style.color = "var(--hg-warn)"; };
  const unhover     = (e) => { e.currentTarget.style.color = "var(--hg-fg-3)"; };
  return (
    <div
      data-tauri-drag-region
      style={{
        display: "flex", alignItems: "baseline", gap: 12,
        padding: "16px 20px 14px",
        borderBottom: "1px solid var(--hg-border-soft)",
        background: "var(--hg-bg-0)",
        fontFamily: "'Geist Mono', ui-monospace, monospace",
        userSelect: "none",
      }}
    >
      <div data-tauri-drag-region style={{ display: "flex", flexDirection: "column", lineHeight: 1.05, pointerEvents: "none" }}>
        <span style={{
          fontFamily: "'Geist', system-ui, sans-serif",
          fontSize: 17, fontWeight: 500, color: "var(--hg-fg-0)",
          letterSpacing: "-0.02em",
        }}>home</span>
        <span style={{
          fontFamily: "'Geist Mono', monospace",
          fontSize: 8.5, letterSpacing: "0.24em",
          fontWeight: 500, color: "var(--hg-fg-4)", marginTop: 3,
        }}>engineered lighting</span>
      </div>
      <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 10, fontSize: 11.5 }}>
        {/* Phase B F0-08: backend liveness warning pill. Only renders
            when HA itself is online but a downstream service (sidecar
            chat-tee OR bridge for voice) is unreachable. */}
        {showWarn && (
          <span title={warnText} style={{
            display: "inline-flex", alignItems: "center", gap: 5,
            border: "1px solid var(--hg-warn)",
            color: "var(--hg-warn)",
            padding: "2px 7px",
            borderRadius: 2,
            fontFamily: "'Geist Mono', monospace",
            fontSize: 9,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
          }}>
            <span style={{
              width: 6, height: 6, borderRadius: 999,
              background: "var(--hg-warn)",
              animation: "hgPulse 2s ease-in-out infinite",
            }} />
            {warnText}
          </span>
        )}
        {/* Phase 1.5b: SEEN identity pill removed from header. The
            face-rec affordances live below now — name chip in the
            vision drawer, named perception line in the chat feed.
            The header stays clean. */}
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          {/* Phase 1 bugfix: dot pulses ice-blue while a voice session is
              live (mic open OR speaking), but the text label only shows
              non-online states (connecting / bad token / offline). The
              voice state itself lives in the bottom VoiceBanner. */}
          <ConnectionDot state={isLive ? "live" : connection} />
          {connection !== "online" && (
            <span style={{
              color: connection === "auth_invalid" ? "var(--hg-warn)" : "var(--hg-fg-3)",
              fontFamily: "'Geist Mono', monospace",
              fontSize: 10, letterSpacing: "0.12em",
            }}>{statusText}</span>
          )}
        </span>
        <button
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          className="hg-focusable"
          onClick={onToggleTheme}
          style={iconBtn}
          onMouseEnter={hoverBright} onMouseLeave={unhover}
        >{theme === "dark" ? <IconSun size={14} /> : <IconMoon size={14} />}</button>
        {IS_TAURI && (
          <>
            <button
              aria-label="Minimize"
              className="hg-focusable"
              onClick={winMinimize}
              style={iconBtn}
              onMouseEnter={hoverBright} onMouseLeave={unhover}
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                <path d="M2 6h8" stroke="currentColor" strokeWidth="1" strokeLinecap="round" />
              </svg>
            </button>
            <button
              aria-label="Close"
              className="hg-focusable"
              onClick={winClose}
              style={iconBtn}
              onMouseEnter={hoverWarn} onMouseLeave={unhover}
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1" strokeLinecap="round" />
              </svg>
            </button>
          </>
        )}
      </span>
    </div>
  );
}

function ConnectionDot({ state }) {
  const toneMap = {
    online: "live", live: "live",
    connecting: "pending",
    offline: "error",
  };
  return <StatusDot tone={toneMap[state] || "idle"} size={5} />;
}

/* ── WelcomeBanner — Phase 1 identity UX ─────────────────────────────
 *
 * Two trigger paths feed this:
 *   1. ARRIVAL — bridge fires {type:"presence", event:"arrived", display_name}
 *      via either WS or chat-tee SSE when person.<X> flips not_home → home.
 *      Always fires, ignores cooldown. The strongest signal.
 *   2. SESSION-START — when app boots with a recent high-confidence face
 *      identity (within 5 min) AND no recent welcome (welcomedAt > 2h ago).
 *
 * Both auto-dismiss after 8 seconds or after the user interacts.
 *
 * Time-of-day phrasing for greetings — only "Good morning" inside the
 * 6am-9pm window so we don't say "Good morning" at 3am.
 */
const WELCOMED_AT_KEY = "hg-welcomedAt";
function getGreeting(name) {
  const h = new Date().getHours();
  if (h >= 6 && h < 12) return `Good morning, ${name}.`;
  if (h >= 12 && h < 17) return `Good afternoon, ${name}.`;
  if (h >= 17 && h < 21) return `Good evening, ${name}.`;
  return `Hey ${name}.`;
}

/* (Removed) DebugPanel — its functionality moved into MetricsStrip
 * expanded view so trace + bridge health live next to GPU/VRAM/CPU
 * (single drawer, no floating overlay). See MetricsStrip for the
 * current implementation. */
// eslint-disable-next-line no-unused-vars
function _DebugPanel_REMOVED_({ metricsBase, bridgeHealth }) {
  const [summary, setSummary] = useState(null);
  const [latest, setLatest] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    if (!metricsBase) return undefined;
    let cancelled = false;
    const fetchData = async () => {
      try {
        const [sRes, lRes] = await Promise.all([
          fetch(`${metricsBase}/traces/summary?window=1h`, { cache: "no-store" }),
          fetch(`${metricsBase}/traces/latest?n=1`, { cache: "no-store" }),
        ]);
        if (cancelled) return;
        if (sRes.ok) setSummary(await sRes.json());
        if (lRes.ok) {
          const j = await lRes.json();
          setLatest(j.traces?.[j.traces.length - 1] || null);
        }
        setErr(null);
      } catch (e) {
        if (!cancelled) setErr(String(e.message || e));
      }
    };
    fetchData();
    const id = setInterval(fetchData, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [metricsBase]);
  const fmtMs = (v) => (v == null || v === 0) ? "—" : `${Math.round(v)}ms`;
  const stages = [
    { key: "t_parakeet_done", label: "STT (Parakeet)" },
    { key: "t_pipeline_start", label: "pipeline start" },
    { key: "t_pipeline_intent_end", label: "pipeline end" },
    { key: "t_synth_start", label: "synth start" },
    { key: "t_synth_done", label: "synth done" },
    { key: "t_first_audio_sent", label: "ttfa" },
    { key: "t_done", label: "turn done" },
  ];
  return (
    <div className="hg-fade" style={{
      position: "fixed",
      top: 60,
      right: 12,
      width: 320,
      maxHeight: "calc(100vh - 84px)",
      overflowY: "auto",
      background: "var(--hg-bg-1)",
      border: "1px solid var(--hg-border)",
      borderRadius: 4,
      padding: 14,
      zIndex: 99,
      fontFamily: "'Geist Mono', monospace",
      fontSize: 10,
      lineHeight: 1.5,
      boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
    }}>
      <div style={{
        textTransform: "uppercase",
        letterSpacing: "0.18em",
        color: "var(--hg-fg-3)",
        marginBottom: 10,
        borderBottom: "1px solid var(--hg-border-soft)",
        paddingBottom: 6,
      }}>debug · latency</div>
      {err && (
        <div style={{ color: "var(--hg-warn)", marginBottom: 8 }}>err · {err.slice(0, 60)}</div>
      )}
      {/* Bridge health snapshot */}
      {bridgeHealth && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ color: "var(--hg-fg-3)", marginBottom: 4 }}>bridge</div>
          <div style={{ color: "var(--hg-fg-1)" }}>
            uptime {Math.floor((bridgeHealth.uptime_s || 0) / 60)}m ·
            {" "}rooms {bridgeHealth.rooms_loaded || 0} ·
            {" "}media {bridgeHealth.media_players_registered || 0}
          </div>
          <div style={{ color: bridgeHealth.warmup_complete ? "var(--hg-fg-2)" : "var(--hg-warn)" }}>
            warmup {bridgeHealth.warmup_complete ? "done" : "in progress"} ·
            {" "}ha {bridgeHealth.ha_connected ? "✓" : "✗"} ·
            {" "}tts {bridgeHealth.tts_engine || "—"}
          </div>
          {bridgeHealth.stale_media_integrations && bridgeHealth.stale_media_integrations.length > 0 && (
            <div style={{ color: "var(--hg-warn)" }}>
              ⚠ {bridgeHealth.stale_media_integrations.length} stale media
            </div>
          )}
        </div>
      )}
      {/* Last turn breakdown */}
      {latest && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ color: "var(--hg-fg-3)", marginBottom: 4 }}>
            last turn · {latest.is_voice ? "voice" : "text"}{latest.cold ? " · cold" : " · warm"}
          </div>
          <div style={{ color: "var(--hg-fg-2)", marginBottom: 4, fontSize: 9 }}>
            {(latest.user_text || "").slice(0, 40)}
          </div>
          {stages.map(({ key, label }) => (
            <div key={key} style={{ display: "grid", gridTemplateColumns: "1fr auto", color: "var(--hg-fg-1)" }}>
              <span style={{ color: "var(--hg-fg-3)" }}>{label}</span>
              <span>{fmtMs(latest[key])}</span>
            </div>
          ))}
        </div>
      )}
      {/* p50 summary */}
      {summary && summary.stamps && (
        <div>
          <div style={{ color: "var(--hg-fg-3)", marginBottom: 4 }}>
            p50 / p90 (n={summary.count})
          </div>
          {stages.map(({ key, label }) => {
            const s = summary.stamps[key];
            if (!s) return null;
            return (
              <div key={key} style={{ display: "grid", gridTemplateColumns: "1fr auto auto", columnGap: 8, color: "var(--hg-fg-1)" }}>
                <span style={{ color: "var(--hg-fg-3)" }}>{label}</span>
                <span>{fmtMs(s.p50)}</span>
                <span style={{ color: "var(--hg-fg-3)" }}>{fmtMs(s.p90)}</span>
              </div>
            );
          })}
          {summary.ttfa_ms && (
            <div style={{
              marginTop: 8, paddingTop: 6,
              borderTop: "1px solid var(--hg-border-soft)",
              display: "grid", gridTemplateColumns: "1fr auto auto", columnGap: 8,
              color: "var(--hg-ice-bright)",
            }}>
              <span>ttfa (voice)</span>
              <span>{fmtMs(summary.ttfa_ms.p50)}</span>
              <span>{fmtMs(summary.ttfa_ms.p90)}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function WelcomeBanner({ identity, arrival, onDismiss }) {
  const [bannerText, setBannerText] = useState(null);
  const [bannerKey, setBannerKey] = useState(0);

  // Arrival path — always fires, regardless of welcomedAt cooldown.
  useEffect(() => {
    if (!arrival || !arrival.display_name) return;
    const name = arrival.display_name;
    const greeting = getGreeting(name);
    setBannerText(`Welcome back. ${greeting}`);
    setBannerKey((k) => k + 1);
    try {
      localStorage.setItem(WELCOMED_AT_KEY, String(Date.now() / 1000));
    } catch {}
  }, [arrival && arrival.ts, arrival && arrival.display_name]);

  // Session-start path — checks recent face match + cooldown on mount.
  useEffect(() => {
    if (!identity || !identity.name) return;
    if (identity.confidence_band !== "high") return;
    const ageS = (Date.now() / 1000) - (identity.ts || 0);
    if (ageS > 300) return; // 5 min freshness
    let welcomedAt = 0;
    try {
      welcomedAt = parseFloat(localStorage.getItem(WELCOMED_AT_KEY) || "0");
    } catch {}
    const cooldownExpired = (Date.now() / 1000 - welcomedAt) > 7200; // 2h
    if (!cooldownExpired) return;
    setBannerText(getGreeting(identity.name));
    setBannerKey((k) => k + 1);
    try {
      localStorage.setItem(WELCOMED_AT_KEY, String(Date.now() / 1000));
    } catch {}
    // Intentionally only runs on first match per mount — onMount semantics.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-dismiss after 8 seconds.
  useEffect(() => {
    if (!bannerText) return;
    const t = setTimeout(() => {
      setBannerText(null);
      if (typeof onDismiss === "function") onDismiss();
    }, 8000);
    return () => clearTimeout(t);
  }, [bannerKey]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!bannerText) return null;
  return (
    <div
      key={bannerKey}
      style={{
        padding: "10px 20px",
        borderBottom: "1px solid var(--hg-border-soft)",
        background: "var(--hg-bg-0)",
        fontFamily: "'Geist Mono', ui-monospace, monospace",
        fontSize: 12,
        color: "var(--hg-ice-bright)",
        letterSpacing: "0.04em",
        display: "flex",
        alignItems: "center",
        gap: 10,
        animation: "hg-fade 320ms ease-out",
      }}
    >
      <span style={{
        width: 6, height: 6, borderRadius: 999,
        background: "var(--hg-ice-bright)",
        boxShadow: "0 0 6px var(--hg-ice-glow)",
      }}/>
      <span>{bannerText}</span>
    </div>
  );
}

/* ── Metrics: collapsed strip + expandable mini-dashboard ───────────── */
function Sparkline({ data, color = "var(--hg-fg-1)", height = 28, suffix = "" }) {
  if (!data || data.length < 2) return null;
  const w = 100, h = height;
  const min = Math.min(...data), max = Math.max(...data);
  const span = max - min || 1;
  const step = w / (data.length - 1);
  const pts = data.map((v, i) => `${(i * step).toFixed(1)},${(h - ((v - min) / span) * (h - 4) - 2).toFixed(1)}`).join(" ");
  const last = data[data.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height, display: "block" }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1" vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={w} cy={h - ((last - min) / span) * (h - 4) - 2} r="1.5" fill={color} />
    </svg>
  );
}

function MetricTile({ label, value, suffix, history, color, accent }) {
  return (
    <div style={{
      padding: "10px 12px 8px",
      borderRight: "1px solid var(--hg-border-soft)",
      display: "flex", flexDirection: "column", gap: 4,
      minWidth: 0,
    }}>
      <div style={{
        fontFamily: "'Geist Mono', monospace", fontSize: 9, letterSpacing: "0.18em",
        textTransform: "uppercase", color: "var(--hg-fg-5)",
      }}>{label}</div>
      <div style={{
        display: "flex", alignItems: "baseline", gap: 3,
        fontFamily: "'Geist Mono', monospace", fontVariantNumeric: "tabular-nums",
      }}>
        <span style={{ fontSize: 16, color: accent || "var(--hg-fg-0)", fontWeight: 500 }}>{value}</span>
        {suffix && <span style={{ fontSize: 10, color: "var(--hg-fg-4)" }}>{suffix}</span>}
      </div>
      <Sparkline data={history} color={color || "var(--hg-fg-3)"} height={20} />
    </div>
  );
}

/* ── Metrics Tray v2 ────────────────────────────────────────────────────
 *
 * The previous Phase 2 implementation used a chaotic visual vocabulary
 * (BigBar with rainbow hues, StatusDotGrid, WaterfallTrace per-stage
 * hue rotation, MetricTile sparklines mixed in one drawer). Tray v2
 * replaces all of it with the strict primitives from home-metrics.jsx
 * (HmSection / HmMeterRow / HmTimelineRow / HmStatusLine / HmHealthDot
 * / HmEmptyState / HmMeterGroup). Single accent color, single bar style,
 * consistent typography, consistent spacing. Data subscriptions
 * (metrics, bridgeHealth, networkMetrics, visionHealth, hostMetrics,
 * traceSummary, lastTrace) are unchanged — only presentation is rewritten.
 */

/* Health-tone helper — distills a metric into ok/warn/crit/idle for
 * the section-header HealthDot and the collapsed view. */
function _toneFromPct(pct, warnAt = 70, critAt = 90) {
  if (pct == null || !isFinite(pct)) return "idle";
  if (pct > critAt) return "crit";
  if (pct > warnAt) return "warn";
  return "ok";
}

function _aiBoxTone(metrics, bridgeHealth) {
  if (!bridgeHealth?.ha_connected) return "warn";
  const vramPct = metrics.vramMax ? (metrics.vram / metrics.vramMax) * 100 : null;
  return _toneFromPct(vramPct);
}

function _homeTone(hostMetrics) {
  if (!hostMetrics) return "idle";
  const max = Math.max(hostMetrics.cpu ?? 0, hostMetrics.ram ?? 0, hostMetrics.disk ?? 0);
  return _toneFromPct(max);
}

function _networkTone(networkMetrics) {
  if (!networkMetrics?.udm && (!networkMetrics?.switches || networkMetrics.switches.length === 0)) return "idle";
  const samples = [];
  if (networkMetrics.udm) {
    if (networkMetrics.udm.cpu != null) samples.push(networkMetrics.udm.cpu);
    if (networkMetrics.udm.mem != null) samples.push(networkMetrics.udm.mem);
  }
  for (const sw of networkMetrics.switches || []) {
    if (sw.cpu != null) samples.push(sw.cpu);
    if (sw.mem != null) samples.push(sw.mem);
  }
  if (!samples.length) return "ok";
  return _toneFromPct(Math.max(...samples));
}

/* ── MetricsStrip v3 ────────────────────────────────────────────────────
 *
 * Compact, tabbed, glanceable. Summary row always visible; expanded
 * body is tabbed with scroll-clamped max-height so the chat input is
 * always reachable.
 */
function MetricsStrip({
  metrics, metricsHistory, metricsBase,
  bridgeHealth, networkMetrics, visionHealth, hostMetrics,
  roomContext,
  voice, identity, media,
  recentPerceptions = [],
}) {
  const [expanded, setExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState("now");
  const [diagOpen, setDiagOpen] = useState(false);
  const [traceSummary, setTraceSummary] = useState(null);
  const [lastTrace, setLastTrace] = useState(null);

  /* Trace polling — only when expanded AND on now/ai tab (others don't need it). */
  useEffect(() => {
    if (!expanded || !metricsBase) return undefined;
    if (activeTab !== "now" && activeTab !== "ai" && !diagOpen) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const [s, l] = await Promise.all([
          tauriFetch(`${metricsBase}/traces/summary?window=1h`, { cache: "no-store" }),
          tauriFetch(`${metricsBase}/traces/latest?n=1`, { cache: "no-store" }),
        ]);
        if (cancelled) return;
        if (s.ok) setTraceSummary(await s.json());
        if (l.ok) {
          const j = await l.json();
          setLastTrace(j.traces?.[j.traces.length - 1] || null);
        }
      } catch (e) {
        console.warn("[metrics] trace poll failed:", e?.message || e);
      }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [expanded, activeTab, diagOpen, metricsBase]);

  /* ── Derived state ──────────────────────────────────────────────────── */
  const aiTone   = _aiBoxTone(metrics, bridgeHealth);
  const homeTone = _homeTone(hostMetrics);
  const netTone  = _networkTone(networkMetrics);

  // Voice + chat state for summary row
  const voiceState = voice?.state || "inactive";
  const aiPhrase =
    voiceState === "listening"   ? "listening"
    : voiceState === "transcribing" ? "transcribing"
    : voiceState === "thinking"  ? "thinking"
    : voiceState === "speaking"  ? "speaking"
    : voiceState === "ready"     ? "ready"
    : bridgeHealth?.warmup_complete === false ? "warming"
    : "idle";

  // Last activity: derive from identity (most recent face match)
  const lastActivity = identity?.name && identity?.camera
    ? `${identity.name} · ${identity.camera.replace(/_/g, " ")}`
    : null;
  const lastActivityAge = identity?.ts
    ? Math.max(0, Math.floor(Date.now() / 1000 - identity.ts))
    : null;

  // Any critical warning to surface?
  let warnChip = null;
  if (bridgeHealth?.stale_media_integrations?.length > 0) {
    warnChip = { text: `${bridgeHealth.stale_media_integrations.length} stale media`, tone: "warn" };
  } else if (bridgeHealth && !bridgeHealth.ha_connected) {
    warnChip = { text: "ha disconnected", tone: "crit" };
  } else if (networkMetrics?.switches?.some((sw) => (sw.mem || 0) > 90)) {
    warnChip = { text: "switch ram high", tone: "warn" };
  }

  // Overall summary tone
  const summaryTone = warnChip?.tone || (aiTone === "warn" || homeTone === "warn" || netTone === "warn" ? "warn"
    : aiTone === "crit" || homeTone === "crit" || netTone === "crit" ? "crit"
    : "ok");

  // ttfa headline value
  const ttfaP50 = traceSummary?.ttfa_ms?.p50 != null ? Math.round(traceSummary.ttfa_ms.p50) : null;

  /* ── Tabs definition ────────────────────────────────────────────────── */
  const tabs = [
    { id: "now",     label: "now" },
    { id: "ai",      label: "ai" },
    { id: "home",    label: "home", warn: homeTone === "warn" || homeTone === "crit" },
    { id: "network", label: "network", warn: netTone === "warn" || netTone === "crit" },
  ];

  /* ── Tab content renderers (each is a small focused subtree) ───────── */

  const renderNow = () => {
    const total = lastTrace?.t_done || 1;
    const stages = lastTrace ? [
      { label: "stt",   start: 0, dur: lastTrace.t_parakeet_done || 0 },
      { label: "llm",   start: lastTrace.t_parakeet_done || 0,
        dur: Math.max(0, (lastTrace.t_pipeline_intent_end || 0) - (lastTrace.t_parakeet_done || 0)) },
      { label: "synth", start: lastTrace.t_pipeline_intent_end || 0,
        dur: Math.max(0, (lastTrace.t_synth_done || 0) - (lastTrace.t_pipeline_intent_end || 0)) },
      { label: "audio", start: lastTrace.t_first_audio_sent || 0,
        dur: Math.max(0, (lastTrace.t_done || 0) - (lastTrace.t_first_audio_sent || 0)) },
    ] : [];

    // Active room context: combine identity + media
    const activeRoom = identity?.camera || null;
    const activeRoomMedia = activeRoom && media?.[activeRoom];
    const activeRoomMediaText = activeRoomMedia?.app_name
      ? `${activeRoomMedia.app_name} · ${activeRoomMedia.state}`
      : null;

    return (
      <>
        {/* Last voice turn timeline */}
        {lastTrace ? (
          <div style={{ marginBottom: 14 }}>
            <div style={{
              display: "flex", alignItems: "baseline", justifyContent: "space-between",
              marginBottom: 6, gap: 12, flexWrap: "wrap",
            }}>
              <span style={{
                fontFamily: "'Geist Mono', monospace",
                fontSize: 9.5, letterSpacing: "0.18em",
                textTransform: "uppercase",
                color: "var(--hg-fg-4)",
              }}>last voice turn</span>
              <span style={{
                fontFamily: "'Geist Mono', monospace",
                fontSize: 10,
                color: "var(--hg-fg-3)",
                fontVariantNumeric: "tabular-nums",
              }}>
                {Math.round(lastTrace.t_first_audio_sent || 0)}ms ttfa · {lastTrace.cold ? "cold" : "warm"}
              </span>
            </div>
            {lastTrace.user_text && (
              <div style={{
                fontFamily: "'Geist', system-ui, sans-serif",
                fontSize: 12.5, color: "var(--hg-fg-2)",
                marginBottom: 8,
              }}>"{lastTrace.user_text.slice(0, 80)}"</div>
            )}
            {stages.map((s) => (
              <HmTimelineRow key={s.label}
                label={s.label}
                startMs={s.start} durMs={s.dur} totalMs={total} />
            ))}
          </div>
        ) : (
          <HmEmptyState message="no recent voice turn" />
        )}

        {/* Active room mini-card */}
        {(activeRoom || lastActivity) && (
          <div style={{
            marginTop: 14, paddingTop: 14,
            borderTop: "1px solid var(--hg-border-soft)",
          }}>
            <div style={{
              fontFamily: "'Geist Mono', monospace",
              fontSize: 9.5, letterSpacing: "0.18em",
              textTransform: "uppercase",
              color: "var(--hg-fg-4)",
              marginBottom: 8,
            }}>active room {activeRoom && `· ${activeRoom.replace(/_/g, " ")}`}</div>
            {identity?.name && (
              <div style={{
                fontFamily: "'Geist Mono', monospace",
                fontSize: 11,
                color: "var(--hg-fg-1)",
                marginBottom: 4,
              }}>
                • {identity.name} {lastActivityAge != null && (
                  <span style={{ color: "var(--hg-fg-4)" }}>· {lastActivityAge < 60 ? `${lastActivityAge}s` : `${Math.round(lastActivityAge / 60)}m`} ago</span>
                )}
              </div>
            )}
            {activeRoomMediaText && (
              <div style={{
                fontFamily: "'Geist Mono', monospace",
                fontSize: 11,
                color: "var(--hg-fg-2)",
              }}>▶ {activeRoomMediaText}</div>
            )}
          </div>
        )}

        {/* System status pills */}
        <div style={{
          marginTop: 14, paddingTop: 14,
          borderTop: "1px solid var(--hg-border-soft)",
        }}>
          <div style={{
            fontFamily: "'Geist Mono', monospace",
            fontSize: 9.5, letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
            marginBottom: 8,
          }}>system</div>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
            gap: "6px 14px",
            fontFamily: "'Geist Mono', monospace",
            fontSize: 10.5,
          }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
              <HmHealthDot tone={aiTone} />
              <span style={{ color: "var(--hg-fg-2)" }}>ai box</span>
              <span style={{ color: "var(--hg-fg-1)", marginLeft: "auto" }}>{aiPhrase}</span>
            </span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
              <HmHealthDot tone={bridgeHealth?.ha_connected ? "ok" : "crit"} />
              <span style={{ color: "var(--hg-fg-2)" }}>haos</span>
              <span style={{ color: "var(--hg-fg-1)", marginLeft: "auto" }}>{bridgeHealth?.ha_connected ? "ok" : "down"}</span>
            </span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
              <HmHealthDot tone={bridgeHealth?.warmup_complete ? "ok" : "warn"} />
              <span style={{ color: "var(--hg-fg-2)" }}>bridge</span>
              <span style={{ color: "var(--hg-fg-1)", marginLeft: "auto" }}>{Math.floor((bridgeHealth?.uptime_s || 0) / 60)}m</span>
            </span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
              <HmHealthDot tone={netTone} />
              <span style={{ color: "var(--hg-fg-2)" }}>network</span>
              <span style={{ color: "var(--hg-fg-1)", marginLeft: "auto" }}>{networkMetrics?.clientsKnown ? `${networkMetrics.clientsOnline}/${networkMetrics.clientsKnown}` : "—"}</span>
            </span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
              <HmHealthDot tone={visionHealth?.phash_enabled ? "ok" : "idle"} />
              <span style={{ color: "var(--hg-fg-2)" }}>vision</span>
              <span style={{ color: "var(--hg-fg-1)", marginLeft: "auto" }}>{visionHealth?.phash_enabled ? `${Math.round((visionHealth.phash_hit_rate || 0) * 100)}%` : "—"}</span>
            </span>
          </div>
        </div>
      </>
    );
  };

  const renderAi = () => (
    <>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
        gap: 18,
      }}>
        <HmMetricCard label="gpu"  value={metrics.gpu}  unit="%"
                      history={metricsHistory.gpu} max={100} />
        <HmMetricCard label="vram" value={metrics.vram}
                      unit={`/ ${metrics.vramMax || 96} g`}
                      history={metricsHistory.vram} max={metrics.vramMax || 96} />
        <HmMetricCard label="cpu"  value={metrics.cpu}  unit="%"
                      history={metricsHistory.cpu} max={100} />
        <HmMetricCard label="ram"  value={metrics.ram}
                      unit={`/ ${metrics.ramMax || 64} g`}
                      history={metricsHistory.ram} max={metrics.ramMax || 64} />
        <HmMetricCard label="ttft" value={metrics.ttft} unit="ms"
                      history={metricsHistory.ttft} max={1500} warnAt={50} critAt={80} />
        <HmMetricCard label="tok/s" value={metrics.tps}
                      history={metricsHistory.tps} max={120} warnAt={101} critAt={101} />
      </div>
      <div style={{
        marginTop: 18, paddingTop: 12,
        borderTop: "1px solid var(--hg-border-soft)",
      }}>
        <HmStatusLine items={[
          { label: "model",  value: metrics.model || "—" },
          { label: "tts",    value: bridgeHealth?.tts_engine || "—" },
          { label: "uptime", value: `${Math.floor((bridgeHealth?.uptime_s || 0) / 60)}m` },
          bridgeHealth?.warmup_complete
            ? { label: "", value: "warm", tone: "ok" }
            : { label: "", value: "warming", tone: "warn" },
          { label: "rooms", value: bridgeHealth?.rooms_loaded || 0 },
          { label: "media", value: bridgeHealth?.media_players_registered || 0 },
        ]} />
      </div>
      {traceSummary?.count > 0 && (
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--hg-border-soft)" }}>
          <div style={{
            fontFamily: "'Geist Mono', monospace",
            fontSize: 9.5, letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
            marginBottom: 6,
          }}>rolling latency p50 · p90 · n={traceSummary.count}</div>
          {[
            ["llm response",  "t_pipeline_intent_end"],
            ["synth done",    "t_synth_done"],
            ["voice ttfa",    "t_first_audio_sent"],
          ].map(([label, key]) => {
            const s = traceSummary.stamps?.[key];
            if (!s) return null;
            return (
              <HmMeterRow key={key}
                label={label}
                text={`${Math.round(s.p50)} · ${Math.round(s.p90)} ms`}
                hideBar />
            );
          })}
        </div>
      )}
    </>
  );

  const renderHome = () => {
    const occupancy = roomContext?.state?.occupancy || {};
    const visual = roomContext?.state?.visual || {};
    const roomNames = Object.keys(roomContext?.config || {}).sort();
    const nowS = Date.now() / 1000;

    return (
      <>
        {/* Host system metrics */}
        <div style={{ marginBottom: 14 }}>
          <div style={{
            fontFamily: "'Geist Mono', monospace",
            fontSize: 9.5, letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
            marginBottom: 8,
          }}>haos host</div>
          {hostMetrics ? (
            <div style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
              gap: 18,
            }}>
              <HmMetricCard label="cpu"  value={hostMetrics.cpu}  unit="%" max={100} />
              <HmMetricCard label="ram"  value={hostMetrics.ram}  unit="%" max={100} />
              {hostMetrics.disk != null && (
                <HmMetricCard label="disk" value={hostMetrics.disk} unit="%" max={100} />
              )}
              {hostMetrics.uptime && (
                <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                  <div style={{ fontFamily: "'Geist Mono', monospace", fontSize: 9.5, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--hg-fg-4)" }}>uptime</div>
                  <div style={{ fontFamily: "'Geist Mono', monospace", fontSize: 14, color: "var(--hg-fg-1)" }}>{hostMetrics.uptime}</div>
                </div>
              )}
            </div>
          ) : (
            <HmEmptyState
              message="system monitor not enabled"
              action={{
                label: "how",
                tooltip: "HA → Settings → Devices & Services → + Add Integration → System Monitor.\nSelect processor_use, memory_use_percent, disk_use_percent.\nRestart HA if entities don't appear within 60s.",
              }} />
          )}
        </div>

        {/* Frigate */}
        <div style={{ marginBottom: 14 }}>
          <div style={{
            fontFamily: "'Geist Mono', monospace",
            fontSize: 9.5, letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
            marginBottom: 8,
          }}>frigate</div>
          <HmEmptyState
            message="telemetry not enabled"
            action={{
              label: "how",
              tooltip: "Drop tools/ha_packages/frigate_stats.yaml from HomeAIVoice repo into\n/config/packages/frigate_stats.yaml on HAOS.\nEnsure configuration.yaml has:\n  homeassistant:\n    packages: !include_dir_named packages\nRestart HA. The tray auto-picks up sensor.frigate_* entities.",
            }} />
        </div>

        {/* Occupancy table */}
        {roomNames.length > 0 && (
          <div style={{ marginBottom: 14, paddingTop: 12, borderTop: "1px solid var(--hg-border-soft)" }}>
            <div style={{
              fontFamily: "'Geist Mono', monospace",
              fontSize: 9.5, letterSpacing: "0.18em",
              textTransform: "uppercase",
              color: "var(--hg-fg-4)",
              marginBottom: 8,
            }}>occupancy</div>
            {roomNames.map((room) => {
              const occ = occupancy[room];
              const occupied = occ?.status === "present";
              const ageS = occ?.last_seen ? Math.max(0, nowS - occ.last_seen) : null;
              return (
                <HmRoomRow
                  key={room}
                  room={room}
                  occupant={occupied ? "occupied" : null}
                  ageS={ageS}
                />
              );
            })}
          </div>
        )}

        {/* Latest perceptions */}
        {recentPerceptions.length > 0 && (
          <div style={{ paddingTop: 12, borderTop: "1px solid var(--hg-border-soft)" }}>
            <div style={{
              fontFamily: "'Geist Mono', monospace",
              fontSize: 9.5, letterSpacing: "0.18em",
              textTransform: "uppercase",
              color: "var(--hg-fg-4)",
              marginBottom: 8,
            }}>recent perception</div>
            {recentPerceptions.slice(-3).reverse().map((p, i) => (
              <div key={i} style={{
                fontFamily: "'Geist', system-ui, sans-serif",
                fontSize: 11.5,
                color: "var(--hg-fg-2)",
                marginBottom: 4,
                fontStyle: "italic",
              }}>{p.text || p}</div>
            ))}
          </div>
        )}
      </>
    );
  };

  const renderNetwork = () => (
    <>
      {networkMetrics?.udm && (
        <div style={{ marginBottom: 18 }}>
          <div style={{
            display: "flex", alignItems: "baseline", justifyContent: "space-between",
            marginBottom: 8,
          }}>
            <span style={{
              fontFamily: "'Geist Mono', monospace",
              fontSize: 9.5, letterSpacing: "0.18em",
              textTransform: "uppercase",
              color: "var(--hg-fg-4)",
            }}>cloud gateway · udm</span>
            <span style={{
              fontFamily: "'Geist Mono', monospace",
              fontSize: 10, color: "var(--hg-fg-4)",
            }}>{networkMetrics.udm.state || "—"}</span>
          </div>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
            gap: 18,
          }}>
            <HmMetricCard label="cpu" value={networkMetrics.udm.cpu} unit="%" max={100} />
            <HmMetricCard label="ram" value={networkMetrics.udm.mem} unit="%" max={100} />
          </div>
        </div>
      )}
      {(networkMetrics?.switches || []).map((sw) => (
        <div key={sw.name} style={{ marginBottom: 18 }}>
          <div style={{
            display: "flex", alignItems: "baseline", justifyContent: "space-between",
            marginBottom: 8,
          }}>
            <span style={{
              fontFamily: "'Geist Mono', monospace",
              fontSize: 9.5, letterSpacing: "0.18em",
              textTransform: "uppercase",
              color: "var(--hg-fg-4)",
            }}>{sw.name}</span>
            <span style={{
              fontFamily: "'Geist Mono', monospace",
              fontSize: 10,
              color: sw.state === "connected" ? "var(--hg-fg-4)" : "var(--hg-warn)",
            }}>{sw.state || "—"}</span>
          </div>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
            gap: 18,
          }}>
            <HmMetricCard label="cpu" value={sw.cpu} unit="%" max={100} />
            <HmMetricCard label="ram" value={sw.mem} unit="%" max={100} />
          </div>
        </div>
      ))}
      {networkMetrics?.clientsKnown > 0 && (
        <div style={{ paddingTop: 12, borderTop: "1px solid var(--hg-border-soft)" }}>
          <div style={{
            fontFamily: "'Geist Mono', monospace",
            fontSize: 9.5, letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--hg-fg-4)",
            marginBottom: 6,
          }}>clients</div>
          <div style={{
            display: "inline-flex", gap: 4, marginBottom: 4,
          }}>
            {Array.from({ length: networkMetrics.clientsKnown }).map((_, i) => (
              <span key={i} style={{
                width: 6, height: 6, borderRadius: 999,
                background: i < networkMetrics.clientsOnline ? "var(--hg-fg-1)" : "var(--hg-border)",
              }} />
            ))}
          </div>
          <div style={{
            fontFamily: "'Geist Mono', monospace",
            fontSize: 11, color: "var(--hg-fg-2)",
          }}>
            <span style={{ color: "var(--hg-fg-1)" }}>{networkMetrics.clientsOnline}</span>
            <span style={{ color: "var(--hg-fg-4)" }}> / {networkMetrics.clientsKnown} online</span>
          </div>
        </div>
      )}
      {!networkMetrics?.udm && (!networkMetrics?.switches || networkMetrics.switches.length === 0) && (
        <HmEmptyState
          message="no unifi devices detected"
          action={{
            label: "how",
            tooltip: "HA → Settings → Devices & Services → + Add Integration → Unifi Network.\nGrant API access to your UDM / switches / APs.",
          }} />
      )}
    </>
  );

  /* ── Render ─────────────────────────────────────────────────────────── */
  return (
    <div style={{ borderTop: "1px solid var(--hg-border-soft)", background: "var(--hg-bg-0)" }}>
      {/* SUMMARY ROW — always visible, ~36px */}
      <button
        onClick={() => setExpanded((x) => !x)}
        className="hg-focusable"
        style={{
          width: "100%",
          padding: "9px 14px",
          background: "transparent",
          border: "none",
          textAlign: "left",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: 14,
          fontFamily: "'Geist Mono', monospace",
          fontSize: 10.5,
          color: "var(--hg-fg-3)",
          fontVariantNumeric: "tabular-nums",
          userSelect: "none",
          minHeight: 36,
        }}
      >
        <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
          <HmHealthDot tone={summaryTone} />
          <span style={{ color: "var(--hg-fg-1)", letterSpacing: "0.02em" }}>{aiPhrase}</span>
        </span>
        {lastActivity && (
          <span style={{
            color: "var(--hg-fg-3)",
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            maxWidth: 200,
          }}>
            {lastActivity}
            {lastActivityAge != null && lastActivityAge < 600 && (
              <span style={{ color: "var(--hg-fg-5)" }}>
                {" · "}{lastActivityAge < 60 ? `${lastActivityAge}s` : `${Math.round(lastActivityAge / 60)}m`}
              </span>
            )}
          </span>
        )}
        {warnChip && (
          <span style={{
            color: warnChip.tone === "crit" ? "var(--hg-crit)" : "var(--hg-warn)",
            fontSize: 9.5,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            border: `1px solid ${warnChip.tone === "crit" ? "var(--hg-crit)" : "var(--hg-warn)"}`,
            padding: "1px 6px",
          }}>{warnChip.text}</span>
        )}
        <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 12 }}>
          {ttfaP50 != null && (
            <span style={{ color: "var(--hg-fg-3)" }}>
              <span style={{ color: "var(--hg-fg-5)" }}>ttfa</span>{" "}
              <span style={{ color: "var(--hg-fg-1)" }}>{ttfaP50}ms</span>
            </span>
          )}
          <span style={{
            color: "var(--hg-fg-4)",
            transition: "transform 220ms cubic-bezier(.4,0,.2,1)",
            transform: expanded ? "rotate(180deg)" : "rotate(0deg)",
            display: "inline-flex", alignItems: "center", justifyContent: "center", width: 14, height: 14,
          }}>
            <svg width="9" height="6" viewBox="0 0 9 6" fill="none">
              <path d="M1 1L4.5 4.5L8 1" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
        </span>
      </button>

      {/* EXPANDED: tabs + scrollable body */}
      {expanded && (
        <div style={{ animation: "hg-fade-up 200ms cubic-bezier(.4,0,.2,1)" }}>
          <HmTabs
            value={activeTab}
            onChange={setActiveTab}
            tabs={tabs}
            extra={
              <button
                onClick={() => setDiagOpen(true)}
                className="hg-focusable"
                title="diagnostics"
                style={{
                  background: "transparent", border: "none",
                  padding: "6px 10px",
                  cursor: "pointer",
                  color: "var(--hg-fg-4)",
                  fontFamily: "'Geist Mono', monospace",
                  fontSize: 13,
                }}
              >⚙</button>
            }
          />
          <HmTrayBody maxHeight={320}>
            {activeTab === "now"     && renderNow()}
            {activeTab === "ai"      && renderAi()}
            {activeTab === "home"    && renderHome()}
            {activeTab === "network" && renderNetwork()}
          </HmTrayBody>
        </div>
      )}
      <HmDiagModal
        open={diagOpen}
        onClose={() => setDiagOpen(false)}
        bridgeHealth={bridgeHealth}
        visionHealth={visionHealth}
        traceSummary={traceSummary}
        lastTrace={lastTrace}
        networkMetrics={networkMetrics}
      />
    </div>
  );
}


function Sep() {
  return <span style={{ display: "inline-block", width: 14 }}> </span>;
}
function Num({ v, suffix }) {
  return <span style={{ color: "var(--hg-fg-1)" }}>{v}{suffix || ""}</span>;
}

/* ── First-run connection prompt ─────────────────────────────────────── */
function FirstRun({ connection, endpoint, token, onConnect, availableModels, onPickModel }) {
  const [url, setUrl] = useState(endpoint || "http://192.168.0.125:8123");
  const [tok, setTok] = useState(token || "");
  const isConnecting   = connection === "connecting";
  const isOffline      = connection === "offline";
  const isAuthInvalid  = connection === "auth_invalid";
  const isPicking      = connection === "picking-model";

  if (isPicking) {
    return (
      <div style={{
        minHeight: "100%", display: "flex", flexDirection: "column", justifyContent: "center",
        padding: "48px 28px 56px",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 28 }}>
          <StatusDot tone="live" size={5} />
          <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 10, letterSpacing: "0.16em", color: "var(--hg-fg-3)" }}>
            connected · {endpoint}
          </span>
        </div>
        <div style={{ fontFamily: "'Geist', system-ui, sans-serif", fontSize: 22, fontWeight: 500, color: "var(--hg-fg-0)", letterSpacing: "-0.02em", lineHeight: 1.2, marginBottom: 6 }}>
          Choose a model.
        </div>
        <div style={{ fontFamily: "'Geist', system-ui, sans-serif", fontSize: 13.5, color: "var(--hg-fg-3)", lineHeight: 1.55, marginBottom: 24 }}>
          {availableModels?.length ? `${availableModels.length} model${availableModels.length === 1 ? "" : "s"} found on your AI box.`
            : "no model list available — your home assistant will pick the configured agent's model"}
        </div>
        {availableModels?.length ? (
          <div style={{ display: "flex", flexDirection: "column", borderTop: "1px solid var(--hg-border)" }}>
            {availableModels.map((m) => (
              <button key={m.name} onClick={() => onPickModel(m.name)} className="hg-focusable" style={{
                display: "flex", alignItems: "baseline", gap: 14,
                padding: "12px 0",
                borderBottom: "1px solid var(--hg-border)",
                background: "transparent", border: "none", borderTop: 0, borderLeft: 0, borderRight: 0,
                borderBottomColor: "var(--hg-border)", borderBottomStyle: "solid", borderBottomWidth: 1,
                cursor: "pointer", textAlign: "left", width: "100%",
              }}
              onMouseEnter={(e) => e.currentTarget.style.background = "var(--hg-bg-1)"}
              onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}>
                <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 13, color: "var(--hg-fg-0)", flex: 1 }}>{m.name}</span>
                <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 11, color: "var(--hg-fg-4)", minWidth: 42, textAlign: "right" }}>{m.ctx}</span>
              </button>
            ))}
          </div>
        ) : (
          <button onClick={() => onPickModel("")} className="hg-focusable" style={{
            background: "transparent", border: "1px solid var(--hg-ice)",
            color: "var(--hg-ice-bright)", padding: "8px 14px", cursor: "pointer",
            fontFamily: "'Geist Mono', monospace", fontSize: 10.5, letterSpacing: "0.16em", textTransform: "uppercase",
            alignSelf: "flex-start",
          }}>continue ↵</button>
        )}
      </div>
    );
  }

  const headline =
    isAuthInvalid ? "that token wasn't accepted" :
    isOffline     ? "couldn't reach that home assistant" :
                    "connect to your home";
  const subhead =
    isAuthInvalid ? "double-check the long-lived access token, then try again" :
    isOffline     ? "check the home assistant url + that the supervisor is running" :
                    "point home at your home assistant, with a long-lived access token";

  const borderTone = (isOffline || isAuthInvalid) ? "var(--hg-warn)" : "var(--hg-border)";

  return (
    <div style={{
      minHeight: "100%", display: "flex", flexDirection: "column", justifyContent: "center",
      padding: "48px 28px 56px",
    }}>
      <div style={{ fontFamily: "'Geist', system-ui, sans-serif", fontSize: 22, fontWeight: 500, color: "var(--hg-fg-0)", letterSpacing: "-0.02em", lineHeight: 1.2, marginBottom: 6 }}>
        {headline}
      </div>
      <div style={{ fontFamily: "'Geist', system-ui, sans-serif", fontSize: 13.5, color: "var(--hg-fg-3)", lineHeight: 1.55, marginBottom: 28 }}>
        {subhead}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); onConnect(url, tok); }} style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        <div>
          <label style={{ fontFamily: "'Geist Mono', monospace", fontSize: 9.5, letterSpacing: "0.18em", color: "var(--hg-fg-4)" }}>home assistant url</label>
          <div style={{ display: "flex", alignItems: "center", gap: 8, borderBottom: `1px solid ${borderTone}`, paddingBottom: 6, marginTop: 6 }}>
            <input
              value={url} onChange={(e) => setUrl(e.target.value)}
              placeholder="http://192.168.0.125:8123"
              autoCapitalize="off" autoCorrect="off" spellCheck={false}
              style={{
                flex: 1, background: "transparent", border: "none", outline: "none",
                fontFamily: "'Geist Mono', monospace", fontSize: 13,
                color: "var(--hg-fg-0)", caretColor: "var(--hg-fg-0)",
              }}
            />
          </div>
        </div>
        <div>
          <label style={{ fontFamily: "'Geist Mono', monospace", fontSize: 9.5, letterSpacing: "0.18em", color: "var(--hg-fg-4)" }}>long-lived access token</label>
          <div style={{ display: "flex", alignItems: "center", gap: 8, borderBottom: `1px solid ${borderTone}`, paddingBottom: 6, marginTop: 6 }}>
            <input
              value={tok} onChange={(e) => setTok(e.target.value)}
              placeholder="eyJ…"
              type="password"
              autoCapitalize="off" autoCorrect="off" spellCheck={false}
              style={{
                flex: 1, background: "transparent", border: "none", outline: "none",
                fontFamily: "'Geist Mono', monospace", fontSize: 13,
                color: "var(--hg-fg-0)", caretColor: "var(--hg-fg-0)",
              }}
            />
            <button type="submit" disabled={isConnecting || !url || !tok} className="hg-focusable" style={{
              background: "transparent", border: "none", padding: 0,
              cursor: (isConnecting || !url || !tok) ? "default" : "pointer",
              color: (isConnecting || !url || !tok) ? "var(--hg-fg-4)" : "var(--hg-fg-1)",
              fontFamily: "'Geist Mono', monospace", fontSize: 9.5,
              letterSpacing: "0.16em",
            }}>{isConnecting ? "connecting…" : (isOffline || isAuthInvalid) ? "retry ↵" : "connect ↵"}</button>
          </div>
        </div>
      </form>
      <div style={{ marginTop: 36, fontFamily: "'Geist Mono', monospace", fontSize: 11, color: "var(--hg-fg-4)", lineHeight: 1.7 }}>
        get a token at <span style={{ color: "var(--hg-fg-2)" }}>profile → security → long-lived access tokens</span><br/>
        type <span style={{ color: "var(--hg-fg-2)" }}>/help</span> below to see all commands
      </div>
    </div>
  );
}

function BootBanner({ metrics }) {
  // Refined wordmark + EL signature + calm system signature.
  // Lives at the top of the feed; scrolls up as the conversation grows.
  // The ASCII art uses Geist Mono and a faint light-cone preamble — a quiet,
  // McCall-style projection of light onto the wordmark.
  const cone = [
    "               \u00b7               ",
    "             \u00b7   \u00b7             ",
    "           \u00b7       \u00b7           ",
    "         \u00b7           \u00b7         ",
    "       \u00b7               \u00b7       ",
    "     \u00b7                   \u00b7     ",
    "   \u00b7                       \u00b7   ",
    " \u00b7                           \u00b7 ",
  ];
  const art = [
    "    __                          ",
    "   / /_   ____   ____ ___   ___ ",
    "  / __ \\ / __ \\ / __ `__ \\ / _ \\",
    " / / / // /_/ // / / / / //  __/",
    "/_/ /_/ \\____//_/ /_/ /_/ \\___/ ",
  ];
  return (
    <div className="hg-boot" style={{
      padding: "56px 24px 36px",
      borderBottom: "1px solid var(--hg-border-soft)",
      fontFamily: "'Geist Mono', monospace",
    }}>
      {/* Cone of light */}
      <div style={{
        whiteSpace: "pre",
        fontSize: 9, lineHeight: 1.2,
        color: "var(--hg-fg-5)",
        textAlign: "center",
        marginBottom: 4,
      }}>{cone.map((l, i) => (
        <div key={i} style={{ opacity: 0.20 + i * 0.07 }}>{l}</div>
      ))}</div>

      {/* Wordmark */}
      <div style={{
        whiteSpace: "pre",
        fontSize: 11, lineHeight: 1.2,
        color: "var(--hg-fg-0)",
        textAlign: "center",
        fontWeight: 500,
      }}>{art.map((l, i) => <div key={i}>{l}</div>)}</div>

      {/* Brand signature */}
      <div style={{
        marginTop: 36,
        textAlign: "center",
        fontFamily: "'Geist Mono', monospace",
        fontSize: 10,
        letterSpacing: "0.28em",
        color: "var(--hg-fg-4)",
      }}>engineered lighting</div>
    </div>
  );
}

function SigRow({ label, value }) {
  return (
    <div style={{ display: "flex", alignItems: "center" }}>
      <span style={{ color: "var(--hg-ice)", marginRight: 10, fontSize: 7 }}>●</span>
      <span style={{ flex: 1, color: "var(--hg-fg-2)" }}>{label}</span>
      <span style={{ color: "var(--hg-fg-3)" }}>{value}</span>
    </div>
  );
}

/* ── Input row ───────────────────────────────────────────────────────── */
const SLASH_CMDS = [
  { cmd: "/connect",  hint: "<url> [<token>]", desc: "connect to a Home Assistant endpoint" },
  { cmd: "/endpoint", hint: "<url>",   desc: "change endpoint url" },
  { cmd: "/token",    hint: "<token>", desc: "update the HA long-lived access token" },
  { cmd: "/model",    hint: "<name>",  desc: "switch active model" },
  { cmd: "/metrics",  hint: "<url>",   desc: "set the metrics-sidecar base url" },
  { cmd: "/s2s",      hint: "<url> | token <hex> | voice <name>", desc: "configure s2s bridge — url, token, or per-session voice override" },
  { cmd: "/voice",    hint: "<name>",  desc: "swap Kokoro TTS voice (e.g. am_eric, af_heart)" },
  { cmd: "/voices",   hint: "",        desc: "list popular voice names" },
  { cmd: "/debug",    hint: "on|off",  desc: "show/hide internal diag events ([parakeet], [direct], etc.)" },
  { cmd: "/demo",     hint: "",        desc: "play the scripted demo conversation" },
  { cmd: "/clear",    hint: "",        desc: "clear the conversation" },
  { cmd: "/about",    hint: "",        desc: "show version + repo info" },
  { cmd: "/find",     hint: "<text>",  desc: "search past chat events for matching text" },
  { cmd: "/help",     hint: "",        desc: "list commands" },
];

function InputRow({ value, onChange, onSend, voice, onMicToggle, isStreaming, onStop }) {
  const inputRef = useRef(null);
  const [sel, setSel] = useState(0);
  const isSlash = value.startsWith("/");
  const firstTok = value.split(/\s+/)[0];
  const matches = isSlash ? SLASH_CMDS.filter((c) => c.cmd.startsWith(firstTok)) : [];
  const showMenu = isSlash && matches.length > 0 && !value.includes(" ");
  useEffect(() => { if (sel >= matches.length) setSel(0); }, [matches.length, sel]);

  const complete = (cmd) => {
    onChange(cmd + (SLASH_CMDS.find(c => c.cmd === cmd)?.hint ? " " : ""));
    inputRef.current?.focus();
  };
  const handleKey = (e) => {
    if (showMenu) {
      if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => (s + 1) % matches.length); return; }
      if (e.key === "ArrowUp")   { e.preventDefault(); setSel((s) => (s - 1 + matches.length) % matches.length); return; }
      if (e.key === "Tab")       { e.preventDefault(); complete(matches[sel].cmd); return; }
      if (e.key === "Enter" && matches[sel].cmd !== firstTok) { e.preventDefault(); complete(matches[sel].cmd); return; }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (value.trim().length > 0) onSend();
    }
  };

  return (
    <div style={{ position: "relative" }}>
      {showMenu && (
        <div style={{
          position: "absolute", bottom: "100%", left: 14, right: 14,
          background: "var(--hg-bg-1)", border: "1px solid var(--hg-border)",
          marginBottom: 6, padding: "4px 0",
          fontFamily: "'Geist Mono', monospace", fontSize: 12,
          boxShadow: "0 8px 24px -8px rgba(0,0,0,0.45)",
        }}>
          {matches.map((m, i) => (
            <div key={m.cmd}
              onMouseEnter={() => setSel(i)}
              onMouseDown={(e) => { e.preventDefault(); complete(m.cmd); }}
              style={{
                display: "flex", alignItems: "baseline", gap: 10,
                padding: "6px 12px",
                background: i === sel ? "var(--hg-bg-2)" : "transparent",
                cursor: "pointer",
              }}>
              <span style={{ color: i === sel ? "var(--hg-fg-0)" : "var(--hg-fg-1)", minWidth: 78 }}>{m.cmd}</span>
              {m.hint && <span style={{ color: "var(--hg-fg-4)" }}>{m.hint}</span>}
              <span style={{ color: "var(--hg-fg-3)", marginLeft: "auto", fontFamily: "'Geist', system-ui, sans-serif", fontSize: 11.5 }}>{m.desc}</span>
            </div>
          ))}
        </div>
      )}
      <div style={{
        padding: "10px 14px 12px",
        borderTop: "1px solid var(--hg-border)",
        background: "var(--hg-bg-0)",
        display: "flex", alignItems: "center", gap: 10,
      }}>
        <div style={{ flex: 1, display: "flex", alignItems: "center", gap: 8 }}>
          <input
            ref={inputRef}
            className="hg-focusable"
            placeholder={isSlash ? "" : "type or /command"}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKey}
            style={{
              fontFamily: isSlash ? "'Geist Mono', monospace" : "'Geist', system-ui, sans-serif",
              fontSize: isSlash ? 13 : 14,
              flex: 1, background: "transparent", border: "none", outline: "none",
              color: "var(--hg-fg-0)",
              caretColor: "var(--hg-fg-0)",
            }}
          />
          {isStreaming ? (
            <button
              aria-label="Stop generation"
              className="hg-focusable"
              onClick={onStop}
              style={{
                background: "transparent", border: "1px solid var(--hg-border)",
                padding: "3px 8px", cursor: "pointer",
                color: "var(--hg-fg-2)",
                display: "inline-flex", alignItems: "center", gap: 6,
                fontFamily: "'Geist Mono', ui-monospace, monospace",
                fontSize: 9.5, letterSpacing: "0.16em", textTransform: "uppercase",
              }}
              onMouseEnter={(e) => e.currentTarget.style.color = "var(--hg-fg-0)"}
              onMouseLeave={(e) => e.currentTarget.style.color = "var(--hg-fg-2)"}
            >
              <span style={{ display: "inline-block", width: 8, height: 8, background: "currentColor" }}></span>
              stop ⌘.
            </button>
          ) : value.trim().length > 0 && (
            <button
              aria-label="Send"
              className="hg-focusable"
              onClick={onSend}
              style={{
                background: "transparent", border: "none", padding: 0,
                color: "var(--hg-fg-2)", cursor: "pointer",
                display: "inline-flex", alignItems: "center", gap: 4,
                fontFamily: "'Geist Mono', ui-monospace, monospace",
                fontSize: 9.5, letterSpacing: "0.16em", textTransform: "uppercase",
              }}
              onMouseEnter={(e) => e.currentTarget.style.color = "var(--hg-fg-0)"}
              onMouseLeave={(e) => e.currentTarget.style.color = "var(--hg-fg-2)"}
            >{isSlash ? "run" : "send"} <IconSend size={11} /></button>
          )}
        </div>
        <MicButton state={voice.state} onClick={onMicToggle} />
      </div>
    </div>
  );
}

/* VoiceModeButton + the /s2s on|off slash command + the persistent
 * `s2sMode` toggle were retired in Phase 1.5. Voice mode is now
 * always-available; tap the mic icon to enter a voice session, tap
 * again to end it. Bridge default voice is Chatterbox-Gianna; swap
 * voices per-session with /voice <name>. */

function MicButton({ state, onClick }) {
  const isOff = state === "inactive";
  const isErr = state === "no-mic";
  const isListening = state === "listening";
  const isProcessing = state === "processing";
  const isSpeaking = state === "speaking";
  // Phase 1.5c: "active" = any non-idle/error state. The reactive
  // LiveWaveform replaces the static IconWaveLive so the icon pulses
  // with real audio whenever the session is open. Source flips to
  // "player" during the speaking state, "mic" during everything else
  // (listening picks up user voice, thinking/transcribing/ready show
  // a quiet idle pose since no audio is flowing).
  const isActive = !isOff && !isErr;

  const bg = (isListening || isSpeaking) ? "var(--hg-ice-glow)" : "transparent";
  const fg = isErr ? "var(--hg-warn)"
            : (isActive || isProcessing) ? "var(--hg-ice-bright)"
            : "var(--hg-fg-1)";
  const animation =
    isListening ? "hg-breathe 1.8s ease-in-out infinite"
    : isSpeaking ? "hg-speak 0.9s ease-in-out infinite"
    : "none";

  return (
    <button
      aria-label="Toggle voice mode"
      className="hg-focusable"
      onClick={onClick}
      style={{
        width: 36, height: 36,
        background: bg,
        border: `1px solid ${isErr ? "var(--hg-warn)" : (isListening || isSpeaking) ? "transparent" : "var(--hg-border)"}`,
        color: fg,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        cursor: "pointer", padding: 0, borderRadius: 999,
        position: "relative",
        transition: "background 240ms cubic-bezier(.4,0,.2,1), color 240ms, border-color 240ms",
        animation,
        flexShrink: 0,
      }}
    >
      {isProcessing ? (
        <svg width="16" height="16" viewBox="0 0 24 24" style={{ animation: "hg-rotate 1.1s linear infinite" }}>
          <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="14 60" strokeLinecap="round" />
        </svg>
      ) : isActive ? (
        <LiveWaveform source={isSpeaking ? "player" : "mic"} bars={5} height={11} />
      ) : (
        <IconMic size={15} />
      )}
    </button>
  );
}

/* ── Voice mode banner — quiet text strip above input ────────────────── */
/* VoiceBanner — visible status strip below the chat feed. Now supports
 * the expanded voice-mode state set (May 2026):
 *
 *   inactive     → banner hidden (voice mode off OR ready-idle and we
 *                  suppress the banner to reduce noise)
 *   ready        → "voice mode — tap mic to speak" (voice mode on, idle)
 *   listening    → mic capturing user speech (wave animation)
 *   transcribing → STT in flight (brief flash, ~200ms typically)
 *   thinking     → LLM is generating the response
 *   speaking     → TTS PCM streaming, audio playing back
 *   error        → mic denied / WS down / TTS down — tap to retry
 *
 * The bridge drives transcribing/thinking/speaking via
 * `{type: "state", state: "..."}` WS control messages on the s2s channel.
 * Listening/inactive are driven locally by the mic state. */
function VoiceBanner({ voice, onRetry }) {
  if (voice.state === "inactive") return null;
  const base = {
    padding: "6px 16px",
    borderTop: "1px solid var(--hg-border-soft)",
    display: "flex", alignItems: "center", gap: 8,
    fontFamily: "'Geist Mono', ui-monospace, monospace",
    fontSize: 11,
  };
  if (voice.state === "no-mic") {
    return (
      <div style={{ ...base, color: "var(--hg-warn)" }}>
        <StatusDot tone="error" size={5} />
        mic unavailable — using text
      </div>
    );
  }
  if (voice.state === "error") {
    return (
      <div style={{ ...base, color: "var(--hg-warn)" }}>
        <StatusDot tone="error" size={5} />
        <span>{voice.message || "voice error"}</span>
        <button
          onClick={onRetry}
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: "1px solid var(--hg-warn)",
            color: "var(--hg-warn)",
            fontFamily: "'Geist Mono', monospace",
            fontSize: 9,
            letterSpacing: "0.16em",
            textTransform: "uppercase",
            padding: "2px 6px",
            borderRadius: 2,
            cursor: "pointer",
          }}
        >retry</button>
      </div>
    );
  }
  if (voice.state === "ready") {
    return (
      <div style={{ ...base, color: "var(--hg-fg-3)" }}>
        <StatusDot tone="ok" size={5} />
        <span style={{ letterSpacing: "0.12em" }}>voice mode</span>
        <span style={{ marginLeft: "auto", color: "var(--hg-fg-4)" }}>tap mic to speak</span>
      </div>
    );
  }
  if (voice.state === "transcribing") {
    return (
      <div style={{ ...base, color: "var(--hg-ice)" }}>
        <StatusDot tone="info" size={5} />
        <span>transcribing…</span>
      </div>
    );
  }
  if (voice.state === "thinking") {
    return (
      <div style={{ ...base, color: "var(--hg-ice)" }}>
        <span style={{
          display: "inline-block",
          width: 14, height: 4,
          background: "linear-gradient(90deg, var(--hg-ice) 0%, transparent 100%)",
          animation: "hg-breathe 1.6s ease-in-out infinite",
        }}/>
        <span>thinking…</span>
      </div>
    );
  }
  // Active states with wave: listening, speaking. The wave is now
  // driven by a real AnalyserNode (mic input during listening, player
  // output during speaking) — see LiveWaveform below.
  return (
    <div style={{ ...base, color: "var(--hg-ice)" }}>
      <LiveWaveform
        source={voice.state === "speaking" ? "player" : "mic"}
        bars={18} height={9}
      />
      <span>{voice.state}…</span>
      <span style={{ marginLeft: "auto", color: "var(--hg-fg-4)" }}>tap mic to end</span>
    </div>
  );
}

/* LiveWaveform — Phase 1.5 item 6.
 *
 * Reads from window.s2sAnalysers.{mic|player} (set up in home-s2s.jsx)
 * each animation frame, and writes scaleY transforms to the bar spans.
 * Falls back to a neutral idle pose when the named analyser isn't
 * available yet (e.g. before the assistant has played any audio for
 * the speaking-state analyser to attach).
 *
 * Lightweight: 18 transforms per frame, no React re-render in the
 * loop (refs only). At 60fps that's ~1080 GPU-cheap composite-only
 * style updates per second. */
function LiveWaveform({ source = "mic", bars = 18, height = 9 }) {
  const refs = useRef([]);
  const heights = useRef(new Array(bars).fill(0.25));
  useEffect(() => {
    const buf = new Uint8Array(64);
    let raf = 0;
    const tick = () => {
      const a = (typeof window !== "undefined" && window.s2sAnalysers)
        ? window.s2sAnalysers[source] : null;
      if (a) {
        try { a.getByteFrequencyData(buf); }
        catch (e) { /* dead analyser */ }
        for (let i = 0; i < bars; i++) {
          // Sample lower frequency bins (speech sits below 4 kHz);
          // the upper bins are noise/silence on speech audio.
          const idx = Math.floor((i / bars) * 32);
          const v = buf[idx] / 255;
          heights.current[i] = heights.current[i] * 0.55 + v * 0.45;
          const el = refs.current[i];
          if (el) {
            el.style.transform = `scaleY(${0.2 + heights.current[i] * 1.1})`;
          }
        }
      } else {
        // Idle: settle bars towards a neutral resting pose so the
        // transition out of a session isn't jarring.
        for (let i = 0; i < bars; i++) {
          heights.current[i] = heights.current[i] * 0.85 + 0.25 * 0.15;
          const el = refs.current[i];
          if (el) {
            el.style.transform = `scaleY(${0.2 + heights.current[i] * 1.1})`;
          }
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [source, bars]);
  return (
    <span style={{
      display: "inline-flex", gap: 2, alignItems: "center",
      height, color: "var(--hg-ice)",
    }}>
      {Array.from({ length: bars }).map((_, i) => (
        <span key={i} ref={(el) => (refs.current[i] = el)} style={{
          width: 2, height,
          background: "var(--hg-ice-bright)",
          transformOrigin: "center",
          transform: "scaleY(0.25)",
          transition: "transform 60ms linear",
          borderRadius: 1,
        }}/>
      ))}
    </span>
  );
}

/* ── Event rendering switch ──────────────────────────────────────────── */
function EventRenderer({ e }) {
  switch (e.kind) {
    case "user":     return <UserMsg time={e.time} text={e.text} />;
    case "voice":    return <VoiceTranscript time={e.time} text={e.text} />;
    case "thinking": return <ThinkingLog time={e.time} text={e.text} />;
    case "tool":     return <ToolCall time={e.time} name={e.name} args={e.args} status={e.status} latency={e.latency} />;
    case "action":   return <ActionCard time={e.time} title={e.title} service={e.service} target={e.target} attrs={e.attrs} status={e.status} latency={e.latency} reason={e.reason} />;
    case "home":     return <HomeResponse time={e.time} text={e.text} streaming={e.streaming} />;
    case "system":   return <SystemEvent time={e.time} text={e.text} tone={e.tone} />;
    default: return null;
  }
}

/* ── The main App ────────────────────────────────────────────────────── */

const DEFAULT_METRICS = {
  model: "—", ttft: null, tps: null, e2e: null,
  gpu: 0, vram: 0, vramMax: 0, cpu: 0, ram: 0, ramMax: 0,
};

// Phase 1.5: the AI box always serves the metrics-sidecar (port 8092)
// and the personaplex-bridge (port 8094). They do NOT run on HAOS.
// Earlier versions derived these from the HA endpoint hostname, which
// pointed fresh installs at HAOS:8092/8094 where nothing was listening
// and silently broke voice mode + identity UX. Default to the AI-box
// LAN IP; /metrics <url> and /s2s <url> still override per-install.
function metricsBaseFromEndpoint(_endpoint) {
  if (typeof window !== "undefined" && window.HG_DEFAULT_METRICS_BASE) {
    return window.HG_DEFAULT_METRICS_BASE;
  }
  return "http://192.168.0.100:8092";
}

function s2sBaseFromEndpoint(_endpoint) {
  if (typeof window !== "undefined" && window.HG_DEFAULT_S2S_BASE) {
    return window.HG_DEFAULT_S2S_BASE;
  }
  return "http://192.168.0.100:8094";
}

function HomeApp({ density = "airy", metricsStyle = "ticker", initialEvents, voiceOverride, themeOverride, autoplay = true }) {
  const initialPrefs = useMemo(() => loadPrefs({
    endpoint: "",
    token: "",
    model: "",
    theme: "dark",
    metricsBase: "",
    // S2S experiment — full-duplex speech-to-speech via the
    // personaplex-bridge sidecar. Off by default; toggled per-window
    // with `/s2s on|off`. Existing HA voice pipeline keeps working
    // regardless of this flag.
    s2sMode: false,
    s2sBase: "",
    s2sToken: "",       // BRIDGE_TOKEN — set via /s2s token <hex>
    s2sVoice: "",       // default voice prompt; empty = bridge default (NATM2.pt)
    kokoroVoice: "",    // Kokoro TTS voice — set via /voice <name>; empty = bridge default (am_eric)
    debugMode: false,   // Show internal diag events ([parakeet], [direct], [kokoro], etc.) in feed
  }), []);
  const initialEventsFromStorage = useMemo(
    () => (initialEvents ? initialEvents : loadEvents()),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  const initialConvId = useMemo(() => loadConversationId(), []);

  const [theme, setTheme] = useState(themeOverride || initialPrefs.theme || "dark");
  useEffect(() => { if (themeOverride) setTheme(themeOverride); }, [themeOverride]);
  const [events, setEvents] = useState(initialEventsFromStorage);
  const [input, setInput] = useState("");
  const [voiceInternal, setVoiceInternal] = useState({ state: "inactive" });
  const voice = voiceOverride && voiceOverride !== "off"
    ? { state: voiceOverride }
    : voiceInternal;
  const setVoice = setVoiceInternal;
  const [connection, setConnection] = useState(
    initialEvents ? "online"
    : (initialPrefs.endpoint && initialPrefs.token ? "reconnecting" : "disconnected")
  );
  const [endpoint, setEndpoint] = useState(initialPrefs.endpoint);
  const [token, setToken] = useState(initialPrefs.token);
  const [model, setModel] = useState(initialPrefs.model);
  const [metricsBase, setMetricsBase] = useState(initialPrefs.metricsBase);
  const [s2sBase, setS2sBase] = useState(initialPrefs.s2sBase || "");
  const [s2sToken, setS2sToken] = useState(initialPrefs.s2sToken || "");
  const [s2sVoice, setS2sVoice] = useState(initialPrefs.s2sVoice || "");
  const [kokoroVoice, setKokoroVoice] = useState(initialPrefs.kokoroVoice || "");
  const [debugMode, setDebugMode] = useState(!!initialPrefs.debugMode);
  // Phase 1.5: `s2sMode` is transient session state, not persisted. The
  // VOICE pill + VoiceModeButton are retired; mic-tap is the single
  // entry point. Boots with s2sMode=false; flipped to true while a
  // voice session is active.
  const [s2sMode, setS2sMode] = useState(false);
  // Phase B F0-08: liveness of the metrics-sidecar (chat-tee SSE source
  // of truth) + the personaplex-bridge (voice + identity events).
  // null = unknown (first probe pending), true = healthy, false = down.
  // Polled every 15s; surfaces a warning pill in the header when down.
  const [sidecarOnline, setSidecarOnline] = useState(null);
  const [bridgeOnline, setBridgeOnline] = useState(null);
  // Master plan F.3: full bridge /healthz snapshot for the DebugPanel
  // (only populated when debugMode is on to avoid wasted polls).
  const [bridgeHealth, setBridgeHealth] = useState(null);
  // Master plan Phase 2: live network metrics (Unifi via HA WS).
  // Currently surfaces UDM Cloud Gateway + 2 USW Flex switches + count
  // of Unifi-attached device trackers in `home` state.
  const [networkMetrics, setNetworkMetrics] = useState({
    udm: null,           // {cpu, mem, uptime, state}
    switches: [],        // [{name, cpu, mem, state, uptime}]
    clientsOnline: 0,
    clientsKnown: 0,
  });
  // Vision-sidecar phash health.
  const [visionHealth, setVisionHealth] = useState(null);
  // Tray v2: HAOS host system metrics from HA's System Monitor integration.
  // Populated by a state_changed subscription that filters across the
  // multiple known entity_id patterns the integration uses.
  // Shape: { cpu, ram, disk, uptime } — all may be null until sensors exist.
  const [hostMetrics, setHostMetrics] = useState(null);
  // Tray v3: per-room occupancy snapshot from bridge /rooms endpoint.
  // Shape: { rooms: { living_room: { occupant, age_s, media }, ... } }
  // The bridge already tracks this via RoomContextStore.
  const [roomContext, setRoomContext] = useState(null);
  // Phase 1 identity (May 2026) — drives the "seen by" header pill,
  // vision-tile name chip, and WelcomeBanner. Latest face match from
  // either the s2s WS or the chat-tee SSE stream.
  //   {name, camera, score, confidence_band, ts, first_seen_today} | null
  const [identity, setIdentity] = useState(null);
  // arrival event from person.<X> not_home → home. Fires WelcomeBanner
  // regardless of face state. Reset to null after the banner displays.
  //   {display_name, person, ts} | null
  const [arrival, setArrival] = useState(null);
  // Phase 2: per-room media state. Keyed by room name. Each entry is
  // {entity_id, state, app_name, title, artist, category, ts}.
  // Cleared per-entity on "media event=cleared" messages.
  //   { living_room: {...}, kitchen: {...} }
  const [media, setMedia] = useState({});
  // Phase 1.5 item 7: responsive wide-mode. At >=700px the layout
  // switches from portrait (single camera frame + tabs) to landscape
  // (camera carousel + wide chat). Tracks window.innerWidth so the
  // user can drag the Tauri window between modes live.
  const [wideMode, setWideMode] = useState(
    typeof window !== "undefined" && window.innerWidth >= 700
  );
  useEffect(() => {
    const onResize = () => setWideMode(window.innerWidth >= 700);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const [availableModels, setAvailableModels] = useState(null);
  const [metrics, setMetrics] = useState(DEFAULT_METRICS);
  // Tray v3: rolling 40-sample history per metric for inline sparklines.
  // Pushed inside the same /metrics polling effect that updates `metrics`.
  const [metricsHistory, setMetricsHistory] = useState({
    gpu: [], vram: [], cpu: [], ram: [], ttft: [], tps: [],
  });
  const [conversationId, setConversationId] = useState(initialConvId);
  // Live Frigate-derived detection labels per camera, e.g.
  //   { kitchen: ["person"], driveway: ["car", "person"] }
  // Sourced from binary_sensor.{camera}_{label}_occupancy state_changed events.
  const [cameraLabels, setCameraLabels] = useState({});

  const feedRef = useRef(null);
  const timers = useRef([]);
  const rootRef = useRef(null);
  const streamingIds = useRef(new Set());
  const haClientRef = useRef(null);
  const activeRunRef = useRef(null); // { id, cancel }

  /* Theme → DOM */
  useEffect(() => {
    if (rootRef.current) rootRef.current.dataset.theme = theme;
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  /* Auto-scroll to bottom on new events */
  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight;
  }, [events]);

  /* Persist prefs whenever they change. Note: `s2sMode` is deliberately
   * NOT persisted — it's transient session state (Phase 1.5). The home
   * app always boots with voice mode inactive; mic-tap activates it. */
  useEffect(() => {
    savePrefs({ endpoint, token, model, theme, metricsBase, s2sBase, s2sToken, s2sVoice, kokoroVoice, debugMode });
  }, [endpoint, token, model, theme, metricsBase, s2sBase, s2sToken, s2sVoice, kokoroVoice, debugMode]);

  /* Persist events on change (debounced via rAF — cheap enough at our scale) */
  useEffect(() => {
    const id = requestAnimationFrame(() => saveEvents(events));
    return () => cancelAnimationFrame(id);
  }, [events]);

  /* Persist conversation_id whenever it changes */
  useEffect(() => { saveConversationId(conversationId); }, [conversationId]);

  /* Helpers to push events */
  const addEvent = useCallback((ev) => {
    setEvents((prev) => [...prev, { id: nextId(), time: fmtTime(), ...ev }]);
  }, []);

  const finishStream = useCallback((id, patch = {}) => {
    streamingIds.current.delete(id);
    setEvents((prev) => prev.map((e) => e.id === id ? { ...e, ...patch, streaming: false } : e));
  }, []);

  /* ── HA client lifecycle ──────────────────────────────────────────── */
  useEffect(() => {
    const client = new HAClient();
    haClientRef.current = client;
    // Expose to non-React modules (vision hook signs camera URLs through it).
    window.__hav_haClient = client;
    const off = client.onConnection(({ state, message }) => {
      // Map HAClient states to the UI's connection states.
      if (state === "connecting")          setConnection("connecting");
      else if (state === "online")         setConnection((c) => c === "picking-model" ? c : "online");
      else if (state === "auth_invalid")   setConnection("auth_invalid");
      else if (state === "offline")        setConnection((c) => c === "disconnected" ? c : "offline");
      if (message) addEvent({ kind: "system", text: message, tone: "warn" });
    });
    return () => { off(); client.disconnect(); haClientRef.current = null; };
  }, [addEvent]);

  /* Probe for the sidecar — tries a few common URLs and uses the first
   * one that returns /healthz. Helps when the AI box is on a different
   * host than HA (the typical case). */
  const probeMetricsBase = useCallback(async (haUrl) => {
    const candidates = [];
    try {
      const u = new URL(haUrl.replace(/^ws/, "http"));
      // 1. Same host as HA (works for single-box setups).
      candidates.push(`http://${u.hostname}:8092`);
      // 2. Same /24 with .100 (the typical AI-box address in this repo's
      //    reference setup).
      const m = u.hostname.match(/^(\d+\.\d+\.\d+)\.\d+$/);
      if (m) candidates.push(`http://${m[1]}.100:8092`);
      // 3. localhost.
      candidates.push("http://localhost:8092");
    } catch {
      candidates.push("http://localhost:8092");
    }
    for (const cand of candidates) {
      try {
        const r = await tauriFetch(`${cand}/healthz`);
        if (r.ok) {
          const j = await r.json();
          if (j?.ok) return cand;
        }
      } catch {}
    }
    return null;
  }, []);

  /* ── Connect (HA WS auth + sidecar discovery) ────────── */
  const connectTo = useCallback(async (haUrl, accessToken) => {
    if (!haUrl || !accessToken) return;
    setEndpoint(haUrl);
    setToken(accessToken);
    setAvailableModels(null);
    setConnection("connecting");
    addEvent({ kind: "system", text: `connecting to ${haUrl}`, tone: "info" });
    try {
      await haClientRef.current.connect(haUrl, accessToken);
    } catch (e) {
      addEvent({ kind: "system", text: `unreachable · ${e?.message || haUrl}`, tone: "error" });
      return;
    }
    // Always re-probe for the sidecar on connect — if a persisted
    // metricsBase is stale (e.g. wrong host saved in localStorage from a
    // prior session), the probe finds the correct one. User-set values via
    // /metrics still win because they save BEFORE this runs on reconnect.
    let mBase = await probeMetricsBase(haUrl);
    if (mBase) {
      if (mBase !== metricsBase) {
        setMetricsBase(mBase);
        addEvent({ kind: "system", text: `sidecar · ${mBase}`, tone: "ok" });
      }
    } else if (metricsBase) {
      // No probe candidate worked, but we have a saved value — try it.
      mBase = metricsBase;
      addEvent({
        kind: "system",
        tone: "warn",
        text: `sidecar probe failed. trying saved · ${mBase}`,
      });
    } else {
      addEvent({
        kind: "system",
        tone: "warn",
        text: "sidecar unreachable. run /metrics <ai-box-url:8092> to set it. without this you won't see voice pe conversations or live metrics.",
      });
    }
    // Auxiliary: discover the vLLM model name + ctx for the picker. Optional.
    if (mBase) {
      try {
        const aiHost = new URL(mBase).hostname;
        const r = await tauriFetch(`http://${aiHost}:8000/v1/models`);
        const j = await r.json();
        const data = j?.data || [];
        if (data.length > 0) {
          setAvailableModels(data.map((m) => ({
            name: m.id,
            ctx: m.max_model_len ? `${Math.round(m.max_model_len / 1024)}k` : "",
          })));
          setConnection("picking-model");
          addEvent({ kind: "system", text: `${data.length} model${data.length === 1 ? "" : "s"} on the ai box`, tone: "ok" });
          return;
        }
      } catch {
        // CORS or unreachable — non-fatal; HA's agent has its own model binding.
      }
    }
    setConnection("online");
    addEvent({ kind: "system", text: `connected · home assistant ${haClientRef.current.haVersion || ""}`, tone: "ok" });
  }, [addEvent, metricsBase, probeMetricsBase]);

  const confirmModel = useCallback((name) => {
    if (name) setModel(name);
    setConnection("online");
    setAvailableModels(null);
    if (name) addEvent({ kind: "system", text: `model · ${name}`, tone: "ok" });
  }, [addEvent]);

  /* Auto-reconnect on launch if we have stored credentials */
  useEffect(() => {
    if (connection === "reconnecting" && endpoint && token) {
      connectTo(endpoint, token);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ── Metrics polling ──────────────────────────────────────────────── */
  useEffect(() => {
    if (connection !== "online") return undefined;
    const base = metricsBase || metricsBaseFromEndpoint(endpoint);
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await tauriFetch(`${base}/metrics`);
        const m = await r.json();
        if (cancelled) return;
        // Phase 1 bugfix: NVML reports 103 GB total on the user's RTX 6000
        // Blackwell (96 GB spec) — includes ECC/fabric overhead the driver
        // doesn't expose as user-allocatable. Clamp to the spec-sheet value
        // so the UI shows what the user expects. Known Blackwell sizes:
        //   RTX 6000  96 GB
        //   B100/B200 192 GB
        // If NVML reports 96..104, treat as 96. Same idea for 192..200.
        const KNOWN_VRAM_SPECS = [96, 192];
        setMetrics((prev) => {
          const reportedMax = m.vram_total_gb ?? prev.vramMax;
          const specMax = KNOWN_VRAM_SPECS.find(
            (s) => reportedMax >= s && reportedMax <= s + 8
          ) ?? reportedMax;
          return {
            ...prev,
            model:   m.model    || prev.model,
            ttft:    m.ttft_ms ?? prev.ttft,
            tps:     m.tps     ?? prev.tps,
            gpu:     m.gpu_util_pct  ?? prev.gpu,
            vram:    m.vram_used_gb  ?? prev.vram,
            vramMax: specMax,
            cpu:     m.cpu_pct       ?? prev.cpu,
            ram:     m.ram_used_gb   ?? prev.ram,
            ramMax:  m.ram_total_gb  ?? prev.ramMax,
          };
        });
        // Tray v3: push to rolling history rings (40 samples each).
        // Snapshot all 6 keys so sparklines have time-series for each.
        setMetricsHistory((h) => {
          const next = { ...h };
          const keys = ["gpu", "vram", "cpu", "ram", "ttft", "tps"];
          const src = {
            gpu: m.gpu_util_pct, vram: m.vram_used_gb, cpu: m.cpu_pct,
            ram: m.ram_used_gb, ttft: m.ttft_ms, tps: m.tps,
          };
          for (const k of keys) {
            const v = src[k];
            const arr = [...(h[k] || []), (typeof v === "number" ? v : null)];
            next[k] = arr.slice(-40);
          }
          return next;
        });
      } catch {
        // sidecar down — keep last-known values
      }
    };
    tick();
    const t = setInterval(tick, 2000);
    return () => { cancelled = true; clearInterval(t); };
  }, [connection, endpoint, metricsBase]);

  /* ── HA conversation: send text via assist_pipeline/run ────────────── */
  const sendToHA = useCallback(async (text) => {
    if (!haClientRef.current || connection !== "online") {
      addEvent({ kind: "system", text: "not connected", tone: "warn" });
      return;
    }

    // Local instant feedback for the user's typed message. The matching
    // SSE event (when the sidecar tees the chat completion) will dedupe
    // against this by content and only add the assistant + tool_calls.
    addEvent({ kind: "user", text });
    const thinkingId = nextId();
    setEvents((prev) => [...prev, { id: thinkingId, kind: "thinking", time: fmtTime(), text: "calling assistant…" }]);

    const t0 = performance.now();
    const onEvent = (haEvent) => {
      if (!haEvent || !haEvent.type) return;
      console.log("[ha event]", haEvent.type, haEvent.data);
      if (haEvent.type === "intent-end") {
        const { convId } = extractIntentEnd(haEvent);
        if (convId) setConversationId(convId);
        return;
      }
      if (haEvent.type === "error") {
        const msg = haEvent.data?.message || "pipeline error";
        addEvent({ kind: "system", text: msg, tone: "error" });
      }
    };

    const run = haClientRef.current.runPipeline({
      text,
      conversationId,
      onEvent,
    });
    activeRunRef.current = run;

    try {
      const { elapsedMs } = await run.done;
      setMetrics((prev) => ({ ...prev, e2e: Math.round(elapsedMs) }));
    } catch (e) {
      addEvent({ kind: "system", text: e?.message || "pipeline failed", tone: "error" });
    } finally {
      setEvents((prev) => prev.filter((e) => e.id !== thinkingId));
      if (activeRunRef.current?.id === run.id) activeRunRef.current = null;
    }
  }, [connection, conversationId, addEvent]);

  /* ── Stop / cancel an in-flight run ────────────────────────────────── */
  const stopStreaming = useCallback(() => {
    if (activeRunRef.current) {
      activeRunRef.current.cancel?.();
      activeRunRef.current = null;
    }
    timers.current.forEach(clearTimeout);
    timers.current = [];
    streamingIds.current.forEach((id) => {
      setEvents((prev) => prev.map((e) => e.id === id ? { ...e, streaming: false, stopped: true } : e));
    });
    streamingIds.current.clear();
  }, []);

  /* ── Confirm / cancel a pending-confirm action ────────────────────── */
  const confirmAction = useCallback((id) => {
    // For Phase 1 the HA agent doesn't gate destructive ops yet, so this
    // just flips the status as if HA acknowledged it. Real wiring is Phase 2.
    setEvents((prev) => prev.map((e) => e.id === id ? { ...e, status: "pending" } : e));
    const t = setTimeout(() => {
      setEvents((prev) => prev.map((e) => e.id === id ? { ...e, status: "success", latency: "84ms" } : e));
    }, 700);
    timers.current.push(t);
  }, []);
  const cancelAction = useCallback((id) => {
    setEvents((prev) => prev.map((e) => e.id === id ? { ...e, status: "cancelled" } : e));
  }, []);

  // Master plan F.1: undo a successful action by firing its inverse
  // service against the original target.
  //
  // Phase 1 bugfix: multi-target actions store the literal label
  // "18 targets" in `target` and the actual entity_ids in `attrs.targets`.
  // The old code only checked `target.startsWith("area.")` etc. and
  // `attrs.entity_id` (singular), so undo on "all lights off" sent NO
  // selector and HA rejected with:
  //   "must contain at least one of entity_id, device_id, area_id, ..."
  // New precedence: attrs.targets[] (array) > attrs.entity_id (str|arr)
  //   > target "area.X" > target "entity.X".
  const undoAction = useCallback(async ({ id, originalService, inverseService, target, attrs }) => {
    const client = haClientRef.current;
    if (!client) throw new Error("HA not connected");
    const [domain, service] = inverseService.split(".");
    const serviceData = {};
    const callTarget = {};
    if (Array.isArray(attrs?.targets) && attrs.targets.length > 0) {
      // Multi-target case — the entity_ids that fired originally
      callTarget.entity_id = attrs.targets;
    } else if (attrs?.entity_id) {
      // Single or pre-arrayed entity_id stored in attrs
      callTarget.entity_id = attrs.entity_id;
    } else if (target?.startsWith?.("area.")) {
      callTarget.area_id = target.slice(5);
    } else if (target?.startsWith?.("entity.")) {
      callTarget.entity_id = target.slice(7);
    }
    if (!callTarget.entity_id && !callTarget.area_id) {
      const msg = `↺ undo unsupported — no resolvable target (target=${JSON.stringify(target)})`;
      console.warn("[undo]", msg);
      addEvent({ kind: "system", text: msg, tone: "error" });
      throw new Error("no resolvable target");
    }
    console.log("[undo]", originalService, "→", inverseService, "target", callTarget);
    try {
      await client.call({
        type: "call_service",
        domain,
        service,
        service_data: serviceData,
        target: callTarget,
      });
      addEvent({
        kind: "system",
        text: `↺ undone — fired ${inverseService}`,
        tone: "info",
      });
    } catch (e) {
      addEvent({
        kind: "system",
        text: `↺ undo failed — ${e.message || e}`,
        tone: "error",
      });
      throw e;
    }
  }, [addEvent]);

  /* ── Scripted demo player (for /demo) ──────────────────────────────── */
  const streamHomeLocal = useCallback((text, opts = {}) => {
    const id = nextId();
    streamingIds.current.add(id);
    setEvents((prev) => [...prev, { id, kind: "home", time: fmtTime(), text: "", streaming: true }]);
    const speed = opts.speed || 18;
    let i = 0;
    const tick = () => {
      if (!streamingIds.current.has(id)) return;
      i += 1;
      setEvents((prev) => prev.map((e) => e.id === id ? { ...e, text: text.slice(0, i) } : e));
      if (i < text.length) {
        const t = setTimeout(tick, speed + Math.random() * 12);
        timers.current.push(t);
      } else {
        streamingIds.current.delete(id);
        setEvents((prev) => prev.map((e) => e.id === id ? { ...e, streaming: false } : e));
      }
    };
    const t = setTimeout(tick, speed);
    timers.current.push(t);
    return id;
  }, []);

  const playScript = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    let accDt = 0;
    SCRIPT.forEach((step) => {
      accDt += step.dt;
      const t = setTimeout(() => {
        const ev = step.event;
        if (ev.kind === "home") { streamHomeLocal(ev.stream); return; }
        const id = ev.id || nextId();
        setEvents((prev) => [...prev, { id, time: fmtTime(), ...ev }]);
        if (step.becomes) {
          const t2 = setTimeout(() => {
            setEvents((prev) => prev.map((e) => e.id === id ? { ...e, ...step.becomes } : e));
          }, step.becomesAfter || 1000);
          timers.current.push(t2);
        }
      }, accDt);
      timers.current.push(t);
    });
  }, [streamHomeLocal]);

  /* ── Slash command parser ─────────────────────────────────────────── */
  const handleCommand = useCallback((raw) => {
    // Trim AGAIN after stripping the leading slash so "/ find office"
    // (with a space the user accidentally typed) becomes "find office"
    // not " find office" → empty cmd.
    const [cmd, ...rest] = raw.trim().slice(1).trim().split(/\s+/);
    const arg = rest.join(" ");
    switch (cmd) {
      case "connect":
      case "endpoint":
        if (arg) {
          const parts = arg.split(/\s+/);
          const url = parts[0];
          const tok = parts.slice(1).join(" ") || token;
          if (tok) connectTo(url, tok);
          else addEvent({ kind: "system", text: "missing token — use /token <token> first or paste both: /connect <url> <token>", tone: "warn" });
        } else {
          addEvent({ kind: "system", text: `usage: /${cmd} <url> [<token>]`, tone: "info" });
        }
        return true;
      case "token":
        if (arg) { setToken(arg); addEvent({ kind: "system", text: "token updated", tone: "ok" }); }
        else addEvent({ kind: "system", text: "usage: /token <ha long-lived access token>", tone: "info" });
        return true;
      case "model":
        if (arg) { setModel(arg); addEvent({ kind: "system", text: `model · ${arg}`, tone: "ok" }); }
        else addEvent({ kind: "system", text: "usage: /model <name>", tone: "info" });
        return true;
      case "metrics":
        if (arg) { setMetricsBase(arg); addEvent({ kind: "system", text: `metrics base · ${arg}`, tone: "ok" }); }
        else addEvent({ kind: "system", text: `metrics base · ${metricsBase || metricsBaseFromEndpoint(endpoint)}`, tone: "info" });
        return true;
      case "s2s": {
        // Configure the s2s bridge URL/token/voice. The voice-mode
        // on/off toggle moved to a dedicated Voice button in the
        // InputRow (May 2026) — `/s2s on|off` no longer works.
        //   /s2s                       → show current state
        //   /s2s token <hex>           → set BRIDGE_TOKEN for WS auth
        //   /s2s voice <name>          → set voice prompt (e.g. NATM2.pt)
        //   /s2s <url>                 → set the bridge URL
        const parts = arg.trim().split(/\s+/);
        const sub = parts[0] || "";
        if (!sub) {
          const url = s2sBase || s2sBaseFromEndpoint(endpoint);
          addEvent({ kind: "system",
            text: `s2s · ${url} · voice=${s2sVoice || "default"} · token=${s2sToken ? "<set>" : "<unset>"} · (tap the mic icon to enter voice mode)`,
            tone: "info" });
          return true;
        }
        if (sub === "on" || sub === "off") {
          addEvent({ kind: "system",
            text: "voice mode is now triggered by tapping the mic icon. `/s2s on|off` is retired.",
            tone: "warn" });
          return true;
        }
        if (sub === "token") {
          const t = parts.slice(1).join(" ");
          if (t) {
            setS2sToken(t);
            addEvent({ kind: "system", text: "s2s token updated", tone: "ok" });
          } else {
            addEvent({ kind: "system", text: "usage: /s2s token <hex>", tone: "info" });
          }
          return true;
        }
        if (sub === "voice") {
          const v = parts.slice(1).join(" ");
          if (v) {
            setS2sVoice(v);
            addEvent({ kind: "system", text: `s2s voice · ${v}`, tone: "ok" });
          } else {
            addEvent({ kind: "system", text: `s2s voice · ${s2sVoice || "(default — NATM2.pt)"}`, tone: "info" });
          }
          return true;
        }
        // Anything else → treat as a URL.
        setS2sBase(sub);
        addEvent({ kind: "system", text: `s2s bridge · ${sub}`, tone: "ok" });
        return true;
      }
      case "debug": {
        // Show/hide internal diag events ([parakeet], [direct],
        // [kokoro], [camera], [error], [moshi]) in the feed.
        //   /debug                → show current state
        //   /debug on | off       → set explicitly
        //   /debug toggle         → flip
        const v = (arg || "").trim().toLowerCase();
        let next = debugMode;
        if (v === "on" || v === "true" || v === "1") next = true;
        else if (v === "off" || v === "false" || v === "0") next = false;
        else if (v === "toggle" || v === "") next = !debugMode;
        setDebugMode(next);
        addEvent({ kind: "system",
          text: `debug · ${next ? "on — showing diag events (parakeet/direct/kokoro/camera/error)" : "off — clean feed"}`,
          tone: next ? "warn" : "ok" });
        return true;
      }
      case "voice":
      case "voices": {
        // Kokoro TTS voice swap (Stage 2 TM-shaped pipeline).
        //   /voice                    → show current + recommended list
        //   /voice <name>             → set voice for this and future sessions
        //   /voices                   → alias of `/voice` with no arg
        const POPULAR = [
          "am_eric (male, natural — default)",
          "am_onyx (male, deeper)",
          "am_michael (male, classic)",
          "am_liam (male, younger)",
          "bm_george (male, British)",
          "bm_fable (male, British narrator)",
          "af_heart (female, warm)",
          "af_bella (female, conversational)",
          "af_nicole (female, soft)",
          "af_nova (female, energetic)",
        ];
        const v = (arg || "").trim();
        if (cmd === "voices" || !v) {
          addEvent({ kind: "system",
            text: `voice · ${kokoroVoice || "(bridge default)"} · try: ${POPULAR.join(" · ")}`,
            tone: "info" });
          return true;
        }
        // Strip parenthetical descriptions if the user pasted one of
        // the suggestions verbatim ("am_eric (male, natural...)").
        const name = v.split(/\s+/)[0];
        setKokoroVoice(name);
        // Push a runtime set_voice message to any active S2S session
        // so the change applies immediately without remic-ing.
        try {
          const run = s2sRunRef.current;
          if (run && typeof run.setVoice === "function") {
            run.setVoice(name);
          }
        } catch (e) { /* noop */ }
        addEvent({ kind: "system", text: `voice · ${name}`, tone: "ok" });
        return true;
      }
      case "clear":
        stopStreaming();
        setEvents([]);
        setConversationId(null);
        return true;
      case "demo":
        playScript();
        return true;
      case "about":
      case "version":
        addEvent({ kind: "system", text: "home v0.1.0 · engineered-lighting/home · MIT", tone: "info" });
        return true;
      case "help": {
        // Phase 1.5: vertical list with descriptions. Builds from
        // SLASH_CMDS (which auto-complete already uses) so the help
        // output stays in sync as commands are added/renamed.
        const lines = SLASH_CMDS.map((c) => {
          const sig = c.hint ? `${c.cmd} ${c.hint}` : c.cmd;
          return `  ${sig}  —  ${c.desc}`;
        });
        addEvent({
          kind: "system",
          text: "commands:\n" + lines.join("\n"),
          tone: "info",
        });
        return true;
      }
      // Master plan F.4: search past events for matching text. Uses the
      // events array already in memory; doesn't hit the bridge.
      case "find": {
        const query = (arg || "").trim().toLowerCase();
        if (!query) {
          addEvent({ kind: "system", text: "usage: /find <text>", tone: "info" });
          return true;
        }
        const fields = (e) => [
          e.text || "",
          e.title || "",
          e.service || "",
          e.target || "",
          (e.args && JSON.stringify(e.args)) || "",
          (e.attrs && JSON.stringify(e.attrs)) || "",
        ].join(" ").toLowerCase();
        const hits = events.filter((e) => fields(e).includes(query));
        if (hits.length === 0) {
          addEvent({ kind: "system", text: `no matches for "${arg}"`, tone: "info" });
          return true;
        }
        const lines = hits.slice(-15).map((e) => {
          const t = e.time || "";
          const head = e.kind === "user" ? "you" :
                       e.kind === "voice" ? "voice" :
                       e.kind === "action" ? `action · ${e.service || ""} · ${e.target || ""}` :
                       e.kind === "perception" ? "perception" : e.kind;
          const body = (e.text || e.title || JSON.stringify(e.attrs || {}) || "").slice(0, 80);
          return `  ${t}  ${head}  ${body}`;
        });
        const more = hits.length > 15 ? `\n  …showing last 15 of ${hits.length} matches` : "";
        addEvent({
          kind: "system",
          text: `${hits.length} match${hits.length === 1 ? "" : "es"} for "${arg}":\n` + lines.join("\n") + more,
          tone: "info",
        });
        return true;
      }
      default:
        addEvent({ kind: "system", text: `unknown command: /${cmd}`, tone: "warn" });
        return true;
    }
  }, [addEvent, connectTo, endpoint, metricsBase, playScript, stopStreaming, token, s2sBase, s2sToken, s2sVoice, kokoroVoice, debugMode]);

  /* ── Free-form user input ─────────────────────────────────────────── */
  const sendInput = useCallback(() => {
    const text = input.trim();
    if (!text) return;
    if (text.startsWith("/")) {
      setInput("");
      handleCommand(text);
      return;
    }
    setInput("");
    sendToHA(text);
  }, [input, handleCommand, sendToHA]);

  /* ── Global keyboard shortcuts ─────────────────────────────────────── */
  useEffect(() => {
    const onKey = (e) => {
      const cmd = e.metaKey || e.ctrlKey;
      if (cmd && e.key === ".") {
        e.preventDefault();
        stopStreaming();
        return;
      }
      if (cmd && (e.key === "l" || e.key === "L")) {
        e.preventDefault();
        stopStreaming();
        setEvents([]);
        setConversationId(null);
        return;
      }
      if (cmd && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        // The InputRow's inner <input> is the only text field at top level.
        // Avoids needing a ref to thread through the component tree.
        document.querySelector('.hg-focusable input, input.hg-focusable, input[placeholder]')?.focus?.();
        return;
      }
      if (e.key === "Escape") {
        // Cancel any pending-confirm cards.
        setEvents((prev) => prev.map((ev) =>
          ev.kind === "action" && ev.status === "pending-confirm"
            ? { ...ev, status: "cancelled" }
            : ev
        ));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [stopStreaming]);

  /* ── Voice mode: mic → HA STT → pipeline → HA TTS → speakers ─────────── */
  const voiceCtxRef = useRef(null);
  const voiceStreamRef = useRef(null);
  const voiceRunRef = useRef(null);
  const voiceTtsAudioRef = useRef(null);

  const stopVoiceMode = useCallback((newState = "inactive") => {
    try { voiceRunRef.current?.cancel?.(); } catch {}
    voiceRunRef.current = null;
    try { voiceStreamRef.current?.getTracks?.().forEach((t) => t.stop()); } catch {}
    voiceStreamRef.current = null;
    try { voiceCtxRef.current?.close?.(); } catch {}
    voiceCtxRef.current = null;
    setVoice({ state: newState });
  }, []);

  const startVoiceMode = useCallback(async () => {
    if (!haClientRef.current || connection !== "online") {
      addEvent({ kind: "system", text: "connect first to use voice", tone: "warn" });
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
    } catch (e) {
      console.error("[mic] getUserMedia failed:", e);
      setVoice({ state: "no-mic" });
      addEvent({ kind: "system", text: `mic unavailable · ${e?.message || e}`, tone: "warn" });
      return;
    }
    voiceStreamRef.current = stream;
    setVoice({ state: "listening" });

    // Build AudioContext at 16k for HA (browser will resample from device rate).
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioCtx({ sampleRate: 16000 });
    voiceCtxRef.current = ctx;

    // Local feedback while the user is still talking. The transcript
    // updates on stt-end; the SSE feed adds the assistant reply +
    // tool_calls afterward (dedupes against this voice event by text).
    const voiceId = nextId();
    setEvents((prev) => [...prev, {
      id: voiceId, kind: "voice", time: fmtTime(), text: "listening…",
    }]);

    const t0 = performance.now();

    const onVoiceEvent = (haEvent) => {
      if (!haEvent || !haEvent.type) return;
      console.log("[ha voice]", haEvent.type, haEvent.data);
      if (haEvent.type === "stt-end") {
        const transcript = haEvent.data?.stt_output?.text || "";
        if (transcript) {
          setEvents((prev) => prev.map((e) =>
            e.id === voiceId ? { ...e, text: transcript } : e
          ));
        }
        setVoice({ state: "processing" });
        return;
      }
      if (haEvent.type === "intent-end") {
        const { convId } = extractIntentEnd(haEvent);
        if (convId) setConversationId(convId);
        return;
      }
      if (haEvent.type === "tts-end") {
        // Audio is reachable via HA at /api/tts_proxy/<url>. Play it.
        const u = haEvent.data?.tts_output?.url || haEvent.data?.tts_output?.media_id;
        if (u) {
          setVoice({ state: "speaking" });
          const audioUrl = u.startsWith("/") ? (endpoint.replace(/\/+$/, "") + u) : u;
          try {
            const a = new Audio(audioUrl);
            voiceTtsAudioRef.current = a;
            a.onended = () => { setVoice({ state: "inactive" }); };
            a.onerror = () => { setVoice({ state: "inactive" }); };
            // HA may serve audio with the long-lived token in cookies; if not,
            // we can pre-fetch with auth and play a blob.
            a.play().catch((err) => {
              console.error("[tts play]", err);
              addEvent({ kind: "system", text: `tts playback blocked · ${err.message}`, tone: "warn" });
              setVoice({ state: "inactive" });
            });
          } catch (e) {
            console.error("[tts]", e);
            setVoice({ state: "inactive" });
          }
        } else {
          setVoice({ state: "inactive" });
        }
        return;
      }
      if (haEvent.type === "error") {
        addEvent({ kind: "system", text: haEvent.data?.message || "voice pipeline error", tone: "error" });
      }
    };

    const run = haClientRef.current.runVoicePipeline({
      onEvent: onVoiceEvent,
      conversationId,
    });
    voiceRunRef.current = run;

    // Wait for the handler id, then start streaming PCM frames from mic.
    let handlerReady = false;
    run.audioHandlerReady.then(() => { handlerReady = true; }).catch((e) => {
      console.error("[voice handler]", e);
      stopVoiceMode("inactive");
    });

    // Stream audio via AudioWorklet if available, fall back to ScriptProcessor.
    // We capture, convert float32 → int16 LE, and ship as binary frames.
    const source = ctx.createMediaStreamSource(stream);
    const FRAME_SIZE = 4096;
    let processorNode;
    try {
      // ScriptProcessor is deprecated but ubiquitous; AudioWorklet would need
      // a separate module file. For v0.1 simplicity, ScriptProcessor is fine.
      processorNode = ctx.createScriptProcessor(FRAME_SIZE, 1, 1);
    } catch (e) {
      console.error("[audio] no processor", e);
      stopVoiceMode("no-mic");
      return;
    }
    processorNode.onaudioprocess = (e) => {
      if (!handlerReady || !run) return;
      const f32 = e.inputBuffer.getChannelData(0);
      const i16 = new Int16Array(f32.length);
      for (let i = 0; i < f32.length; i++) {
        const v = Math.max(-1, Math.min(1, f32[i]));
        i16[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
      }
      run.sendAudioChunk(new Uint8Array(i16.buffer));
    };
    source.connect(processorNode);
    // Route to a silent destination so the script-processor stays alive
    // without re-routing the mic to the speakers (would echo).
    const muteGain = ctx.createGain();
    muteGain.gain.value = 0;
    processorNode.connect(muteGain);
    muteGain.connect(ctx.destination);

    // When the run completes, clean up the mic.
    run.done.finally(() => {
      try { source.disconnect(); processorNode.disconnect(); muteGain.disconnect(); } catch {}
      try { stream.getTracks().forEach((t) => t.stop()); } catch {}
      voiceStreamRef.current = null;
      voiceRunRef.current = null;
      // Do NOT set inactive here — let tts-end's audio onended handler do it,
      // so the speaking state lasts through playback.
      setMetrics((prev) => ({
        ...prev,
        e2e: Math.round(performance.now() - t0),
      }));
    });
  }, [connection, conversationId, addEvent, endpoint, stopVoiceMode]);

  /* ── Experimental S2S voice mode (via personaplex-bridge) ─────────────
   * Runs in parallel to the HA voice pipeline — toggled by `/s2s on`.
   * Same MicButton/voice-state UI, but audio is routed through the
   * bridge WS and the bridge speaks back through Web Audio directly
   * (no HA TTS, no Voice PE). Chat-tee teeing keeps text turns in
   * the same conversation feed. */
  const s2sRunRef = useRef(null);
  const stopS2sMode = useCallback((newState = "inactive") => {
    try { s2sRunRef.current?.stop?.(); } catch (e) { /* noop */ }
    s2sRunRef.current = null;
    setVoice({ state: newState });
    setS2sMode(false);
  }, []);
  const startS2sMode = useCallback(async () => {
    if (!window.startS2SRun) {
      addEvent({ kind: "system", text: "s2s module not loaded", tone: "warn" });
      return;
    }
    const base = s2sBase || s2sBaseFromEndpoint(endpoint);
    if (!base) {
      addEvent({ kind: "system", text: "set bridge url with /s2s <url>", tone: "warn" });
      return;
    }
    setS2sMode(true);
    setVoice({ state: "listening" });
    const voiceId = nextId();
    setEvents((prev) => [...prev, {
      id: voiceId, kind: "voice", time: fmtTime(), text: "listening… (s2s)",
    }]);
    const t0 = performance.now();
    let lastUserText = "";
    const run = await window.startS2SRun({
      s2sBase: base,
      s2sToken: s2sToken || undefined,
      voicePrompt: s2sVoice || undefined,
      kokoroVoice: kokoroVoice || undefined,
      conversationId,
      onState: (state, message) => {
        // Forward states from the bridge. New states from May 2026:
        //   transcribing → Parakeet end-of-utterance, transcript in flight
        //   thinking     → LLM is generating the response
        //   ready        → voice mode active but idle (banner shows
        //                  "voice mode — tap mic to speak")
        //   error        → mic/WS/TTS failure — banner shows retry CTA
        // `processing` from older bridges maps to `thinking` for clarity.
        if (state === "listening") setVoice({ state: "listening" });
        else if (state === "transcribing") setVoice({ state: "transcribing" });
        else if (state === "thinking" || state === "processing") setVoice({ state: "thinking" });
        else if (state === "speaking") setVoice({ state: "speaking" });
        else if (state === "ready") setVoice({ state: "ready" });
        else if (state === "error") setVoice({ state: "error", message: message || "voice error" });
        else if (state === "idle" || state === "inactive") setVoice({ state: "inactive" });
      },
      onTranscript: (role, text, partial) => {
        if (!text) return;
        if (role === "user") {
          // User-side partials still ignored: the Parakeet transcript
          // arrives all at once at end-of-utterance, no streaming.
          if (partial) return;
          lastUserText = text;
          setEvents((prev) => prev.map((e) =>
            e.id === voiceId ? { ...e, text } : e
          ));
          return;
        }
        // Assistant transcript: stream partials so the UI updates as
        // PersonaPlex speaks instead of dumping the whole utterance at
        // end-of-speech (PersonaPlex can ramble for 60+ seconds).
        if (role === "assistant") {
          if (partial) {
            setEvents((prev) => {
              // Append/replace the live streaming home event for this turn.
              const last = prev[prev.length - 1];
              if (last && last.kind === "home" && last.streaming) {
                return [...prev.slice(0, -1), { ...last, text }];
              }
              return [...prev, {
                id: nextId(),
                kind: "home",
                time: fmtTime(),
                text,
                streaming: true,
              }];
            });
            return;
          }
          // Final: lock in the last streaming home event (or add a new
          // one if none exists yet).
          setEvents((prev) => {
            const last = prev[prev.length - 1];
            if (last && last.kind === "home" && last.streaming) {
              return [...prev.slice(0, -1), { ...last, text, streaming: false }];
            }
            return [...prev, {
              id: nextId(),
              kind: "home",
              time: fmtTime(),
              text,
            }];
          });
          setMetrics((prev) => ({
            ...prev,
            e2e: Math.round(performance.now() - t0),
          }));
        }
      },
      onError: (msg) => {
        addEvent({ kind: "system", text: `s2s · ${msg}`, tone: "error" });
        setVoice({ state: "inactive" });
      },
      onIdentity: (msg) => {
        // Phase 1: face-rec match (or identity_clear when name is null).
        if (!msg.name) {
          // identity_clear — drop the pill if it's for the current camera.
          setIdentity((cur) => (cur && cur.camera === msg.camera ? null : cur));
          return;
        }
        setIdentity({
          name: msg.name,
          camera: msg.camera,
          score: msg.score,
          confidence_band: msg.confidence_band,
          ts: msg.ts,
          first_seen_today: !!msg.first_seen_today,
        });
      },
      onPresence: (msg) => {
        // Phase 1: person.<X> arrival. Triggers WelcomeBanner with the
        // strong arrival path (always fires, ignores welcomedAt cooldown).
        if (msg.event === "arrived") {
          setArrival({
            display_name: msg.display_name,
            person: msg.person,
            ts: msg.ts,
          });
        }
      },
      onMedia: (msg) => {
        // Phase 2: media_player.* state change broadcast from bridge.
        if (msg.event === "active" && msg.room) {
          setMedia((cur) => ({
            ...cur,
            [msg.room]: {
              entity_id: msg.entity_id,
              state: msg.state,
              app_name: msg.app_name,
              title: msg.title,
              artist: msg.artist,
              category: msg.category,
              ts: msg.ts,
            },
          }));
        } else if (msg.event === "cleared" && msg.room) {
          setMedia((cur) => {
            if (cur[msg.room] && cur[msg.room].entity_id === msg.entity_id) {
              const { [msg.room]: _drop, ...rest } = cur;
              return rest;
            }
            return cur;
          });
        }
      },
    });
    s2sRunRef.current = run;
  }, [s2sBase, s2sToken, s2sVoice, kokoroVoice, endpoint, conversationId, addEvent]);

  /* Phase 1.5: every mic-tap goes through the s2s flow. The dedicated
   * "voice mode toggle" is retired — mic-tap IS the voice trigger. Tap
   * to enter (open WS, start listening), tap again to cancel mid-turn.
   * The bridge defaults voice_mode=true on attach so we don't need a
   * separate set_voice_mode message. */
  const toggleMic = useCallback(() => {
    const idle = voice.state === "inactive" || voice.state === "no-mic";
    if (idle) startS2sMode();
    else stopS2sMode("inactive");
  }, [voice.state, startS2sMode, stopS2sMode]);

  /* ── Single source of truth: SSE feed from the chat-tee sidecar ────────
   *
   * The sidecar proxies vLLM and broadcasts every chat completion — user
   * text, assistant text, tool_calls. With HA's prefer_local_intents set
   * to false, every turn (typed, voice mode, Voice PE) routes through the
   * LLM, so this stream carries the full conversation history regardless
   * of where it originated.
   *
   * Dedup with locally-rendered events: when the SSE event's user_msg
   * matches the most-recent user/voice event in the feed, we skip the
   * user side (keeps instant feedback for typed turns) and add the rest. */
  const seenSsEvents = useRef(new Set());
  useEffect(() => {
    if (connection !== "online") return undefined;
    const base = metricsBase || metricsBaseFromEndpoint(endpoint);
    // Phase 1.5 one-time warning: if the saved metricsBase points at the
    // HAOS host (where nothing serves :8092), the SSE feed will silently
    // fail. Most likely cause: a fresh install hit the old fallback
    // before we fixed it. Surface this in the console so users find it
    // when debugging "no perception events / no identity pill".
    try {
      const haHost = endpoint ? new URL(endpoint.replace(/^ws/, "http")).hostname : null;
      const baseHost = base ? new URL(base).hostname : null;
      if (metricsBase && haHost && baseHost === haHost && base.endsWith(":8092")) {
        console.warn(
          "[home] metricsBase points at the HA host (%s:8092). The sidecar runs on the AI box, not HAOS. Run /metrics http://<ai-box-ip>:8092 if SSE feed isn't working.",
          haHost,
        );
      }
    } catch {}
    let es;
    try {
      es = new EventSource(`${base}/conversations/stream?backfill_n=0`);
    } catch (e) {
      console.warn("[sse] not supported:", e);
      return undefined;
    }
    console.log("[sse] subscribing to", `${base}/conversations/stream`);
    es.onopen = () => console.log("[sse] open");
    es.onmessage = (ev) => {
      let entry;
      try { entry = JSON.parse(ev.data); }
      catch { return; }
      console.log("[sse] event", entry);
      // Phase 1: identity / presence events come through the SSE pipe
      // too (so text-only mode still gets the UX). Dispatch to the same
      // setters the WS handler uses and return early — these events
      // carry no chat text and shouldn't enter the events feed.
      if (entry.source === "s2s:identity" && entry.identity) {
        const i = entry.identity;
        if (i.type === "identity_clear") {
          setIdentity((cur) => (cur && cur.camera === i.camera ? null : cur));
        } else if (i.type === "identity" && i.name) {
          setIdentity({
            name: i.name, camera: i.camera, score: i.score,
            confidence_band: i.confidence_band, ts: i.ts,
            first_seen_today: !!i.first_seen_today,
          });
        }
        return;
      }
      if (entry.source === "s2s:presence" && entry.presence) {
        const p = entry.presence;
        if (p.event === "arrived") {
          setArrival({
            display_name: p.display_name, person: p.person, ts: p.ts,
          });
        }
        return;
      }
      if (entry.source === "s2s:media" && entry.media) {
        const m = entry.media;
        if (m.event === "active" && m.room) {
          setMedia((cur) => ({
            ...cur,
            [m.room]: {
              entity_id: m.entity_id,
              state: m.state,
              app_name: m.app_name,
              title: m.title,
              artist: m.artist,
              category: m.category,
              ts: m.ts,
            },
          }));
        } else if (m.event === "cleared" && m.room) {
          setMedia((cur) => {
            // Only clear if the room's current entry was THIS entity.
            // Another entity in the same room (e.g. living_room has
            // both Apple TV + Sonos) shouldn't be wiped.
            if (cur[m.room] && cur[m.room].entity_id === m.entity_id) {
              const { [m.room]: _drop, ...rest } = cur;
              return rest;
            }
            return cur;
          });
        }
        return;
      }
      const dedup = entry.id || `${entry.ts}-${(entry.user || "").slice(0, 30)}`;
      if (seenSsEvents.current.has(dedup)) return;
      seenSsEvents.current.add(dedup);
      if (seenSsEvents.current.size > 200) {
        seenSsEvents.current = new Set(Array.from(seenSsEvents.current).slice(-100));
      }

      // Flatten tool_calls into action cards. Extended OpenAI Conv's
      // `execute_services` wraps a list of HA service calls — split into one
      // card per unique {domain.service}, aggregating targets.
      const actionCards = [];
      for (const tc of entry.tool_calls || []) {
        const fn = tc.function || {};
        const name = fn.name || "tool";
        const parsed = fn.arguments_parsed
          || (() => { try { return JSON.parse(fn.arguments || "{}"); } catch { return {}; } })();

        if (name === "execute_services" && parsed && Array.isArray(parsed.list)) {
          // Group by service key to coalesce many entity actions into one card.
          const groups = new Map();
          for (const call of parsed.list) {
            const dom = call.domain || "";
            const svc = call.service || "";
            const key = `${dom}.${svc}`;
            if (!groups.has(key)) groups.set(key, { service: key, targets: new Set(), attrsList: [] });
            const g = groups.get(key);
            const sd = call.service_data || {};
            const tgt = sd.entity_id || sd.area_id || sd.device_id;
            (Array.isArray(tgt) ? tgt : [tgt]).filter(Boolean).forEach((t) => g.targets.add(t));
            const attrs = { ...sd };
            delete attrs.entity_id; delete attrs.area_id; delete attrs.device_id;
            if (Object.keys(attrs).length > 0) g.attrsList.push(attrs);
          }
          for (const g of groups.values()) {
            const targets = Array.from(g.targets);
            const targetStr = targets.length === 0 ? null
              : targets.length === 1 ? targets[0]
              : `${targets.length} targets`;
            const mergedAttrs = g.attrsList.length === 1
              ? { ...g.attrsList[0] }
              : g.attrsList.reduce((acc, a) => { Object.entries(a).forEach(([k,v]) => acc[k] = v); return acc; }, {});
            if (targets.length > 1) mergedAttrs.targets = targets;
            actionCards.push({
              id: nextId(), kind: "action", time: fmtTime(),
              title: `${g.service}${targetStr ? " · " + targetStr : ""}`,
              service: g.service,
              target: targetStr,
              attrs: mergedAttrs,
              status: "success",
            });
          }
        } else {
          // Non-execute_services tool call — render as a compact tool row.
          actionCards.push({
            id: nextId(), kind: "tool", time: fmtTime(),
            name, args: parsed, status: "success", latency: null,
          });
        }
      }

      setEvents((prev) => {
        // Look back ~8 events for a matching user/voice line.
        let matchIdx = -1;
        const lookbackStart = Math.max(0, prev.length - 8);
        for (let i = prev.length - 1; i >= lookbackStart; i--) {
          const e = prev[i];
          if ((e.kind === "user" || e.kind === "voice") &&
              (e.text || "").trim() === (entry.user || "").trim()) {
            matchIdx = i; break;
          }
        }
        const newUserEvents = [];
        if (matchIdx === -1 && entry.user) {
          // No local user event → originated outside the Home app (Voice PE).
          newUserEvents.push({
            id: nextId(), kind: "voice", time: fmtTime(),
            text: entry.user,
          });
        }
        // Tag bridge-emitted diag events ([parakeet] heard, [direct]
        // chitchat, [kokoro] speaking, [camera] driveway, etc.) so the
        // /debug toggle can show/hide them without losing them entirely.
        // Bridge posts these via post_diag_event() with
        // `source: "s2s:diag:<channel>"`. Real LLM-spoken responses
        // arrive with source "s2s:moshi-listener-parakeet" or vLLM
        // proxy sources — those stay kind="home".
        const isDiag = typeof entry.source === "string"
          && entry.source.startsWith("s2s:diag:");
        const diagChannel = isDiag
          ? entry.source.slice("s2s:diag:".length)
          : undefined;
        // The "perception" channel is the assistant's silent observation
        // of what the camera sees (vision-sidecar / Frigate detection).
        // Surface it inline as its own kind so the UI can render it
        // distinctly — italic, dimmed, no avatar — and so the /debug
        // toggle doesn't hide it (perception is the point, not noise).
        const isPerception = isDiag && diagChannel === "perception";
        // Strip the "[perception] " prefix the bridge prepends in
        // post_diag_event so the rendered line is just the body.
        const cleanedText = isPerception
          ? entry.assistant.replace(/^\[perception\]\s*/, "")
          : entry.assistant;
        const assistantEvent = entry.assistant
          ? [{
              id: nextId(),
              kind: isPerception ? "perception" : (isDiag ? "diag" : "home"),
              channel: diagChannel,
              time: fmtTime(),
              text: cleanedText,
            }]
          : [];
        // Defensive dedupe (May 2026): assistant text occasionally arrives
        // from two sources within a few seconds (chat-tee SSE + WS
        // transcript). The bridge-side fix removes the WS transcript for
        // assistant role, but this guard catches any legacy/stale source
        // we haven't tracked down. Scan back 20 events (action cards +
        // perceptions can interleave between duplicates, so a tighter
        // window misses).
        return [...prev, ...newUserEvents, ...actionCards, ...assistantEvent.filter((ev) => {
          if (ev.kind !== "home" && ev.kind !== "perception") return true;
          const target = (ev.text || "").trim();
          if (!target) return true;
          const lookback = prev.slice(-20);
          for (const e of lookback) {
            if (e.kind === ev.kind && (e.text || "").trim() === target) {
              return false;
            }
          }
          return true;
        })];
      });
    };
    es.onerror = (e) => {
      console.warn("[sse] error — will auto-reconnect");
    };
    return () => { try { es.close(); } catch {} };
  }, [connection, endpoint, metricsBase]);

  /* ── Phase B F0-08: sidecar + bridge healthz poll ─────────────────────
   *
   * Poll the metrics-sidecar (chat tee + telemetry) every 15s. If it
   * stops responding, the home app silently loses assistant replies via
   * SSE — surfacing a clear "sidecar offline" indicator lets the user
   * understand why their messages aren't getting answered.
   *
   * Bridge poll: the personaplex-bridge powers voice mode + identity +
   * media events. If it's down, voice mode breaks. Poll its /healthz too
   * — assumes bridge is at the same host as the sidecar on port 8094.
   */
  useEffect(() => {
    const base = metricsBase || metricsBaseFromEndpoint(endpoint);
    if (!base) return undefined;
    let cancelled = false;

    // Derive bridge URL: same host as sidecar, port 8094.
    let bridgeUrl = "";
    try {
      const u = new URL(base);
      bridgeUrl = `${u.protocol}//${u.hostname}:8094`;
    } catch {}

    const poll = async () => {
      // Phase 1 bugfix: native fetch() is CORS-blocked from the Tauri
      // origin against http://192.168.0.100:* — sets sidecarOnline /
      // bridgeOnline to false even when both are healthy, which raises
      // the "VOICE BRIDGE OFFLINE" pill incorrectly. tauriFetch routes
      // through Tauri's HTTP plugin (no webview CORS).
      // Sidecar
      try {
        const r = await tauriFetch(`${base}/healthz`, { cache: "no-store" });
        if (!cancelled) setSidecarOnline(r.ok);
      } catch (e) {
        if (!cancelled) setSidecarOnline(false);
        console.warn("[health] sidecar poll failed:", e?.message || e);
      }
      // Bridge — also capture full body for MetricsStrip
      if (bridgeUrl) {
        try {
          const r2 = await tauriFetch(`${bridgeUrl}/healthz`, { cache: "no-store" });
          if (!cancelled) {
            setBridgeOnline(r2.ok);
            if (r2.ok) {
              try { setBridgeHealth(await r2.json()); } catch {}
            }
          }
        } catch (e) {
          if (!cancelled) setBridgeOnline(false);
          console.warn("[health] bridge poll failed:", e?.message || e);
        }
      }
    };

    poll();  // immediate
    const id = setInterval(poll, 15000);
    return () => { cancelled = true; clearInterval(id); };
  }, [metricsBase, endpoint]);

  /* ── Voice PE activity indicator (header + voice state) ──────────────
   *
   * State_changed events for assist_satellite are still useful for
   * showing "Voice PE is listening" in the connection-dot area, even
   * though we no longer render placeholder turns here. The actual turn
   * content arrives via the SSE feed above. */
  useEffect(() => {
    if (connection !== "online" || !haClientRef.current) return undefined;
    const unsub = haClientRef.current.subscribeEvents("state_changed", (ev) => {
      const d = ev?.data;
      if (!d?.entity_id?.startsWith?.("assist_satellite.")) return;
      const newState = d.new_state?.state;
      if (newState !== d.old_state?.state) {
        console.log("[voicepe state]", d.old_state?.state, "→", newState);
      }
    });
    return unsub;
  }, [connection]);

  /* ── Frigate detection labels → vision drawer ────────────────────────
   *
   * Subscribes to binary_sensor.{camera}_{label}_occupancy state changes
   * and maintains a per-camera Set of currently-active labels (person,
   * cat, car, etc.). Hydrates from get_states so labels show immediately
   * on app open, not just after the next transition. The bridge has its
   * own occupancy tracking for the LLM side; this hook is UI-only. */
  useEffect(() => {
    if (connection !== "online" || !haClientRef.current) return undefined;
    const client = haClientRef.current;
    const cameraIds = (window.HG_CAMERAS || []).map((c) => c.id);
    if (cameraIds.length === 0) return undefined;
    const re = new RegExp(`^binary_sensor\\.(${cameraIds.join("|")})_([a-z0-9_]+)_occupancy$`);

    let cancelled = false;
    client.call({ type: "get_states" }).then((states) => {
      if (cancelled) return;
      const next = {};
      for (const s of states) {
        const m = s.entity_id.match(re);
        if (!m) continue;
        const [, cam, label] = m;
        if (label === "all") continue;          // aggregate sensor, skip
        if (s.state !== "on") continue;
        (next[cam] ||= new Set()).add(label.replace(/_/g, " "));
      }
      setCameraLabels(Object.fromEntries(
        Object.entries(next).map(([k, v]) => [k, [...v].sort()])
      ));
    }).catch(() => {});

    const unsub = client.subscribeEvents("state_changed", (ev) => {
      const d = ev?.data;
      if (!d?.entity_id) return;
      const m = d.entity_id.match(re);
      if (!m) return;
      const [, cam, label] = m;
      if (label === "all") return;
      const nice = label.replace(/_/g, " ");
      const isOn = d.new_state?.state === "on";
      setCameraLabels((prev) => {
        const cur = new Set(prev[cam] || []);
        if (isOn) cur.add(nice);
        else      cur.delete(nice);
        return { ...prev, [cam]: [...cur].sort() };
      });
    });

    return () => { cancelled = true; try { unsub(); } catch {} };
  }, [connection]);

  /* ── Phase 2: network metrics from HA Unifi entities ──────────────────
   *
   * Subscribes to state_changed for the Unifi-attached entities we care
   * about and rolls them up into one networkMetrics object for the
   * MetricsStrip drawer.
   *
   * Entities matched (per discover_metric_sensors probe):
   *   - sensor.cloud_gateway_fiber_(cpu|memory)_utilization
   *   - sensor.cloud_gateway_fiber_uptime
   *   - sensor.usw_flex_*_(cpu|memory)_utilization
   *   - sensor.usw_flex_*_state
   *   - sensor.usw_flex_*_uptime
   *   - device_tracker.unifi_* (count home/not_home)
   */
  useEffect(() => {
    if (connection !== "online" || !haClientRef.current) return undefined;
    const client = haClientRef.current;
    let cancelled = false;

    const isUdm = (eid) => eid.startsWith("sensor.cloud_gateway_fiber_") ||
                          eid.startsWith("sensor.cloud_gateway_");
    const isSwitch = (eid) => /^sensor\.usw_/.test(eid);
    const isUnifiClient = (eid) => /^device_tracker\.unifi_/.test(eid);

    const switchNameFromEid = (eid) => {
      // sensor.usw_flex_2_5g_8_poe_cpu_utilization → "usw flex 2.5G 8 PoE"
      const stripped = eid.replace(/^sensor\.usw_/, "")
        .replace(/_(cpu|memory)_utilization$/, "")
        .replace(/_state$/, "")
        .replace(/_uptime$/, "")
        .replace(/_uplink_mac$/, "");
      return "usw " + stripped.replace(/_/g, " ");
    };

    const rebuildFromStates = async () => {
      try {
        const states = await client.call({ type: "get_states" });
        if (cancelled) return;
        const udm = { cpu: null, mem: null, uptime: null, state: null };
        const switches = new Map(); // name → {cpu, mem, state, uptime}
        let clientsOnline = 0;
        let clientsKnown = 0;
        for (const s of states) {
          const eid = s.entity_id;
          const val = parseFloat(s.state);
          if (isUdm(eid)) {
            if (eid.endsWith("_cpu_utilization")) udm.cpu = isFinite(val) ? val : null;
            else if (eid.endsWith("_memory_utilization")) udm.mem = isFinite(val) ? val : null;
            else if (eid.endsWith("_uptime")) udm.uptime = s.state;
            else if (eid.endsWith("_state")) udm.state = s.state;
          } else if (isSwitch(eid)) {
            const name = switchNameFromEid(eid);
            const cur = switches.get(name) || { name };
            if (eid.endsWith("_cpu_utilization")) cur.cpu = isFinite(val) ? val : null;
            else if (eid.endsWith("_memory_utilization")) cur.mem = isFinite(val) ? val : null;
            else if (eid.endsWith("_state")) cur.state = s.state;
            else if (eid.endsWith("_uptime")) cur.uptime = s.state;
            switches.set(name, cur);
          } else if (isUnifiClient(eid)) {
            clientsKnown += 1;
            if (s.state === "home") clientsOnline += 1;
          }
        }
        // Only keep switches that have at least one numeric metric
        const switchArr = [...switches.values()].filter(
          (sw) => sw.cpu != null || sw.mem != null || sw.state
        );
        setNetworkMetrics({
          udm: (udm.cpu != null || udm.mem != null) ? udm : null,
          switches: switchArr,
          clientsOnline,
          clientsKnown,
        });
      } catch (e) {
        console.warn("[network] initial state load failed:", e?.message || e);
      }
    };
    rebuildFromStates();

    // Incremental updates via state_changed subscription
    const unsub = client.subscribeEvents("state_changed", (ev) => {
      const d = ev?.data;
      if (!d?.entity_id) return;
      const eid = d.entity_id;
      const newState = d.new_state?.state;
      const oldState = d.old_state?.state;
      // For client tracker home/not_home transitions, just rebuild
      // (cheap — single get_states call) so we don't race the counter.
      if (isUnifiClient(eid) && newState !== oldState) {
        rebuildFromStates();
        return;
      }
      // For UDM / switch metric updates, patch in place
      const val = parseFloat(newState);
      if (isUdm(eid) || isSwitch(eid)) {
        setNetworkMetrics((prev) => {
          if (isUdm(eid)) {
            const udm = { ...(prev.udm || {}) };
            if (eid.endsWith("_cpu_utilization")) udm.cpu = isFinite(val) ? val : null;
            else if (eid.endsWith("_memory_utilization")) udm.mem = isFinite(val) ? val : null;
            else if (eid.endsWith("_state")) udm.state = newState;
            else if (eid.endsWith("_uptime")) udm.uptime = newState;
            return { ...prev, udm };
          }
          const name = switchNameFromEid(eid);
          const switches = prev.switches.map((sw) => {
            if (sw.name !== name) return sw;
            const u = { ...sw };
            if (eid.endsWith("_cpu_utilization")) u.cpu = isFinite(val) ? val : null;
            else if (eid.endsWith("_memory_utilization")) u.mem = isFinite(val) ? val : null;
            else if (eid.endsWith("_state")) u.state = newState;
            else if (eid.endsWith("_uptime")) u.uptime = newState;
            return u;
          });
          // If this is a new switch we haven't seen, append
          if (!switches.some((sw) => sw.name === name)) {
            switches.push({ name,
              cpu: eid.endsWith("_cpu_utilization") ? val : null,
              mem: eid.endsWith("_memory_utilization") ? val : null,
              state: eid.endsWith("_state") ? newState : null,
            });
          }
          return { ...prev, switches };
        });
      }
    });

    return () => { cancelled = true; try { unsub(); } catch {} };
  }, [connection]);

  /* ── Tray v2: HAOS host system metrics from HA's System Monitor ─────
   *
   * Subscribes to whichever entity_id pattern HA's System Monitor
   * integration is actually using (varies between HA versions /
   * configurations). Tolerant of:
   *   sensor.processor_use_percent / sensor.processor_use
   *   sensor.system_monitor_processor_use(_percent)
   *   sensor.memory_use_percent / sensor.system_monitor_memory_use_percent
   *   sensor.disk_use_percent / sensor.system_monitor_disk_use_percent
   *   sensor.last_boot
   *
   * If NONE are present, hostMetrics stays null → drawer shows the
   * "host telemetry not enabled" empty-state row with a tooltip.
   */
  useEffect(() => {
    if (connection !== "online" || !haClientRef.current) return undefined;
    const client = haClientRef.current;
    let cancelled = false;

    const cpuRe  = /^sensor\.(system_monitor_)?(processor_use|cpu_use)/;
    const ramRe  = /^sensor\.(system_monitor_)?(memory_use_percent|ram_use)/;
    const diskRe = /^sensor\.(system_monitor_)?disk_use_percent/;
    const bootRe = /^sensor\.last_boot$/;

    const fmtUptime = (isoTs) => {
      if (!isoTs) return null;
      try {
        const boot = new Date(isoTs);
        const secs = Math.max(0, (Date.now() - boot.getTime()) / 1000);
        const d = Math.floor(secs / 86400);
        const h = Math.floor((secs % 86400) / 3600);
        const m = Math.floor((secs % 3600) / 60);
        if (d > 0) return `${d}d ${h}h`;
        if (h > 0) return `${h}h ${m}m`;
        return `${m}m`;
      } catch { return null; }
    };

    const rebuild = async () => {
      try {
        const states = await client.call({ type: "get_states" });
        if (cancelled) return;
        let cpu = null, ram = null, disk = null, uptime = null;
        for (const s of states) {
          const eid = s.entity_id;
          const val = parseFloat(s.state);
          if (cpuRe.test(eid) && isFinite(val)) cpu = val;
          else if (ramRe.test(eid) && isFinite(val)) ram = val;
          else if (diskRe.test(eid) && isFinite(val)) disk = val;
          else if (bootRe.test(eid)) uptime = fmtUptime(s.state);
        }
        const any = (cpu != null) || (ram != null) || (disk != null) || uptime;
        setHostMetrics(any ? { cpu, ram, disk, uptime } : null);
      } catch (e) {
        console.warn("[host] state load failed:", e?.message || e);
      }
    };
    rebuild();

    const unsub = client.subscribeEvents("state_changed", (ev) => {
      const d = ev?.data;
      const eid = d?.entity_id;
      if (!eid) return;
      const newState = d.new_state?.state;
      if (!(cpuRe.test(eid) || ramRe.test(eid) || diskRe.test(eid) || bootRe.test(eid))) {
        return;
      }
      const val = parseFloat(newState);
      setHostMetrics((prev) => {
        const cur = prev || { cpu: null, ram: null, disk: null, uptime: null };
        if (cpuRe.test(eid)) cur.cpu = isFinite(val) ? val : cur.cpu;
        else if (ramRe.test(eid)) cur.ram = isFinite(val) ? val : cur.ram;
        else if (diskRe.test(eid)) cur.disk = isFinite(val) ? val : cur.disk;
        else if (bootRe.test(eid)) cur.uptime = fmtUptime(newState);
        return { ...cur };
      });
    });

    return () => { cancelled = true; try { unsub(); } catch {} };
  }, [connection]);

  /* ── Tray v3: bridge /rooms endpoint poll (occupancy + visual age) ── */
  useEffect(() => {
    const base = metricsBase || metricsBaseFromEndpoint(endpoint);
    if (!base) return undefined;
    let bridgeUrl = "";
    try {
      const u = new URL(base);
      bridgeUrl = `${u.protocol}//${u.hostname}:8094`;
    } catch {}
    if (!bridgeUrl) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await tauriFetch(`${bridgeUrl}/rooms`, { cache: "no-store" });
        if (cancelled || !r.ok) return;
        setRoomContext(await r.json());
      } catch (e) {
        // Bridge may be restarting — keep last-known
      }
    };
    tick();
    const id = setInterval(tick, 8000);
    return () => { cancelled = true; clearInterval(id); };
  }, [metricsBase, endpoint]);

  /* ── Phase 2: vision-sidecar health (phash hit rate, cameras cached) ── */
  useEffect(() => {
    const base = metricsBase || metricsBaseFromEndpoint(endpoint);
    if (!base) return undefined;
    let cancelled = false;
    // Derive vision URL from sidecar host (sidecar on :8092, vision on :8091)
    let visionUrl = "";
    try {
      const u = new URL(base);
      visionUrl = `${u.protocol}//${u.hostname}:8091`;
    } catch {}
    const tick = async () => {
      if (!visionUrl) return;
      try {
        const r = await tauriFetch(`${visionUrl}/healthz`, { cache: "no-store" });
        if (!cancelled && r.ok) setVisionHealth(await r.json());
      } catch (e) {
        console.warn("[vision] healthz poll failed:", e?.message || e);
      }
    };
    tick();
    const id = setInterval(tick, 10000);
    return () => { cancelled = true; clearInterval(id); };
  }, [metricsBase, endpoint]);

  return (
    <div ref={rootRef} data-theme={theme} style={{
      "--hg-row-py": density === "condensed" ? `${8}px` : `${14}px`,
      display: "flex", flexDirection: "column",
      width: "100%", height: "100%",
      background: "var(--hg-bg-0)",
      color: "var(--hg-fg-0)",
      fontFamily: "'Geist', system-ui, sans-serif",
      overflow: "hidden",
    }}>
      <HomeHeader
        theme={theme}
        onToggleTheme={() => setTheme((t) => t === "dark" ? "light" : "dark")}
        voice={voice}
        connection={connection}
        sidecarOnline={sidecarOnline}
        bridgeOnline={bridgeOnline}
      />
      {/* F.3 revised: latency lives inside MetricsStrip now (expanded view)
          alongside GPU/VRAM. No separate floating panel. */}
      <WelcomeBanner
        identity={identity}
        arrival={arrival}
        onDismiss={() => setArrival(null)}
      />
      {connection === "online" && (
        <HomeVisionCard
          haUrl={endpoint}
          token={token}
          labels={cameraLabels}
          identity={identity}
          media={media}
          wideMode={wideMode}
        />
      )}
      <div
        ref={feedRef}
        className="hg-scroll"
        style={{
          flex: 1, overflowY: "auto",
          background: "var(--hg-bg-0)",
        }}>
        {(connection !== "online" && connection !== "reconnecting" && events.length === 0)
         || connection === "picking-model"
         || connection === "auth_invalid"
         || (connection === "offline" && events.length === 0)
         || (connection === "disconnected" && events.length === 0) ? (
          <FirstRun
            connection={connection}
            endpoint={endpoint}
            token={token}
            onConnect={connectTo}
            availableModels={availableModels}
            onPickModel={confirmModel}
          />
        ) : (
          <div style={{
            // Phase 1.5 item 7: chat widens with the window past 700px,
            // capped at 1200px so very wide displays still feel readable.
            maxWidth: wideMode ? "min(1200px, 95vw)" : 640,
            margin: "0 auto",
          }}>
            <BootBanner metrics={metrics} />
            {groupEventsBySpeaker(
              debugMode ? events : events.filter((e) => e.kind !== "diag")
            ).map((g, i) => (
              <TurnBlock key={i} group={g} density={density}
                onConfirmAction={confirmAction} onCancelAction={cancelAction}
                onUndoAction={undoAction} />
            ))}
          </div>
        )}
      </div>
      <MetricsStrip
        metrics={metrics}
        metricsHistory={metricsHistory}
        style={metricsStyle}
        metricsBase={metricsBase || metricsBaseFromEndpoint(endpoint)}
        bridgeHealth={bridgeHealth}
        networkMetrics={networkMetrics}
        visionHealth={visionHealth}
        hostMetrics={hostMetrics}
        roomContext={roomContext}
        voice={voice}
        identity={identity}
        media={media}
        recentPerceptions={events.filter((e) => e.kind === "perception").slice(-3)}
      />
      <VoiceBanner voice={voice} onRetry={toggleMic} />
      <InputRow
        value={input}
        onChange={setInput}
        onSend={sendInput}
        voice={voice}
        onMicToggle={toggleMic}
        isStreaming={streamingIds.current.size > 0 || events.some(e => e.streaming)}
        onStop={stopStreaming}
      />
    </div>
  );
}

function EmptyHint() { return <BootBanner />; }

Object.assign(window, { HomeApp, HomeHeader, MetricsStrip, InputRow, MicButton, VoiceBanner, EventRenderer, fmtTime });
