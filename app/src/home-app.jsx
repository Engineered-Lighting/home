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

/* ── Tauri-side ASR correction ───────────────────────────────────────
 * Mirrors HA-side PERSON_NAME_ALIASES (ha-config/extended_openai_conversation/
 * const.py). Applied to every event text rendered as a bubble (user echo,
 * AI response, perception caption) so the home app never visually shows
 * the forbidden spellings — even when the source skipped the HA-side
 * backstop (e.g., chat-tee SSE renders raw vLLM completions, parakeet
 * STT user-bubble echo bypasses the bridge correction, etc).
 * Whole-word case-insensitive; preserves possessives ('Marcello's' → 'Marcelo's')
 * because apostrophe is a non-word char that anchors \b.
 * Keep in sync with const.py PERSON_NAME_ALIASES + bridge ASR_CORRECTIONS. */
const ASR_ALIASES = {
  "marcello": "Marcelo",
  "marcella": "Marcelo",
  "marsello": "Marcelo",
  "marsella": "Marcelo",
  "marselo":  "Marcelo",
  "marsailo": "Marcelo",
  "marsaylo": "Marcelo",
  "marsailla":"Marcelo",
  "marsaila": "Marcelo",
};
const ASR_RE = new RegExp(
  "\\b(" + Object.keys(ASR_ALIASES).join("|") + ")\\b",
  "gi",
);
function applyAsrCorrection(text) {
  if (typeof text !== "string" || !text) return text;
  return text.replace(ASR_RE, (m) => ASR_ALIASES[m.toLowerCase()] || m);
}

function readViewportProfile() {
  if (typeof window === "undefined") {
    return { width: 1024, height: 768, mobile: false, phone: false };
  }
  const vv = window.visualViewport;
  const width = Math.round(vv?.width || window.innerWidth || 1024);
  const height = Math.round(vv?.height || window.innerHeight || 768);
  return {
    width,
    height,
    mobile: width < 700,
    phone: width < 480,
  };
}

function useViewportProfile() {
  const [viewport, setViewport] = useState(readViewportProfile);
  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    let raf = 0;
    const update = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => setViewport(readViewportProfile()));
    };
    window.addEventListener("resize", update);
    window.visualViewport?.addEventListener("resize", update);
    window.visualViewport?.addEventListener("scroll", update);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("scroll", update);
    };
  }, []);
  return viewport;
}

/* ── Header ──────────────────────────────────────────────────────────── */
function HomeHeader({
  theme, onToggleTheme, voice, connection, sidecarOnline, bridgeOnline,
  bridgeHealth, visionSidecarOnline, visionHealth, frigateMetrics, cameraLabels,
  sim, muteState, onUnmuteClick, onOpenPeople, onOpenIntelligence,
  onOpenVideoLabeler, onOpenSimulationControls, peopleButtonRef, aiStackState, metrics,
  serviceProfile, onOpenRemoteProfile, mobile = false,
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const isLive = voice.state !== "inactive" && voice.state !== "no-mic";
  // Phase B F0-08: surface sidecar/bridge offline as a warning pill.
  // sidecarOnline = false means SSE chat-tee is broken → assistant
  // replies won't reach the feed even if HA fires. bridgeOnline = false
  // means voice mode is broken (no identity/media events, no s2s WS).
  // Both null = unknown (first probe pending) — hide pill.
  const sidecarDown = sidecarOnline === false;
  const bridgeDown = bridgeOnline === false;
  // Tray v5: global header state is now layered. "online" means
  // core app + HA + bridge + sidecar all reachable. If HA is online
  // but a downstream is down, we show "degraded" (not "offline") so
  // the header doesn't contradict the drawer's live AI metrics.
  const isDegraded = connection === "online" && (sidecarDown || bridgeDown);
  const statusText =
    connection === "online" && !isDegraded  ? "online"     :
    isDegraded                              ? "degraded"   :
    connection === "connecting"             ? "connecting" :
    connection === "auth_invalid"           ? "bad token"  : "offline";
  const showWarn = isDegraded;
  const warnText = sidecarDown && bridgeDown ? "voice + chat offline"
                 : sidecarDown ? "chat-tee offline"
                 : bridgeDown ? "voice bridge offline" : "";
  const iconBtn = {
    background: "transparent", border: "none", padding: 4, cursor: "pointer",
    color: "var(--hg-fg-3)",
    display: "inline-flex", alignItems: "center", justifyContent: "center",
    lineHeight: 0,
    minWidth: 24,
    minHeight: 24,
    boxSizing: "border-box",
  };
  const atlasIconColor = theme === "light"
    ? "rgba(31, 79, 168, 0.86)"
    : "rgba(238,248,255,0.78)";
  const atlasIconBtn = {
    ...iconBtn,
    color: atlasIconColor,
  };
  const hoverBright = (e) => { e.currentTarget.style.color = "var(--hg-fg-0)"; };
  const hoverAtlas = (e) => {
    e.currentTarget.style.color = "var(--hg-fg-0)";
  };
  const hoverWarn   = (e) => { e.currentTarget.style.color = "var(--hg-warn)"; };
  const unhover     = (e) => { e.currentTarget.style.color = "var(--hg-fg-3)"; };
  const unhoverAtlas = (e) => {
    e.currentTarget.style.color = atlasIconColor;
  };
  const chipMax = mobile ? "min(142px, 36vw)" : "none";
  const mobileMenuItem = {
    all: "unset",
    minHeight: 42,
    padding: "0 12px",
    display: "flex",
    alignItems: "center",
    gap: 8,
    color: "var(--hg-fg-1)",
    borderBottom: "1px solid var(--hg-border-soft)",
    cursor: "pointer",
    fontFamily: "'Geist Mono', monospace",
    fontSize: 10.5,
    letterSpacing: "0.09em",
    textTransform: "uppercase",
  };
  const mobileMenuAction = (fn) => {
    setMenuOpen(false);
    fn?.();
  };
  return (
    <div
      data-tauri-drag-region
      style={{
        display: "flex", alignItems: mobile ? "center" : "baseline", gap: mobile ? 6 : 12,
        padding: mobile
          ? "calc(8px + env(safe-area-inset-top, 0px)) 10px 8px"
          : "16px 20px 14px",
        borderBottom: "1px solid var(--hg-border-soft)",
        background: "var(--hg-bg-0)",
        fontFamily: "'Geist Mono', ui-monospace, monospace",
        userSelect: "none",
        flexWrap: "nowrap",
        minWidth: 0,
      }}
    >
      <div data-tauri-drag-region style={{ display: "flex", flexDirection: "column", lineHeight: 1.05, pointerEvents: "none", minWidth: mobile ? 68 : "auto", flexShrink: 0 }}>
        <span style={{
          fontFamily: "'Geist', system-ui, sans-serif",
          fontSize: mobile ? 16 : 17, fontWeight: 500, color: "var(--hg-fg-0)",
          letterSpacing: "-0.02em",
        }}>home</span>
        {!mobile && <span style={{
          fontFamily: "'Geist Mono', monospace",
          fontSize: 8.5, letterSpacing: "0.24em",
          fontWeight: 500, color: "var(--hg-fg-4)", marginTop: 3,
        }}>engineered lighting</span>}
      </div>
      <span style={{
        marginLeft: "auto",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "flex-end",
        gap: mobile ? 6 : 10,
        fontSize: 11.5,
        minWidth: 0,
        flex: mobile ? "1 1 auto" : "0 1 auto",
        flexWrap: "nowrap",
        overflow: mobile && !menuOpen ? "hidden" : "visible",
      }}>
        {/* Phase B F0-08: backend liveness warning pill. Only renders
            when HA itself is online but a downstream service (sidecar
            chat-tee OR bridge for voice) is unreachable. */}
        {showWarn && (
          <span title={warnText} style={{
            display: "inline-flex", alignItems: "center", gap: 5,
            border: "1px solid var(--hg-warn)",
            color: "var(--hg-warn)",
            padding: mobile ? "6px 7px" : "2px 7px",
            borderRadius: 2,
            fontFamily: "'Geist Mono', monospace",
            fontSize: 9,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            maxWidth: chipMax,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            boxSizing: "border-box",
            minHeight: mobile ? 30 : "auto",
          }}>
            <span style={{
              width: 6, height: 6, borderRadius: 999,
              background: "var(--hg-warn)",
              animation: "hgPulse 2s ease-in-out infinite",
            }} />
            {warnText}
          </span>
        )}
        {/* A-1 + F-18 (Addendum 27) — capability-flag chip. Derives
            per-capability health from buildStackList(aiStackState,
            metrics) and renders a precise impact chip ("vision offline
            · face captions + describe_clip unavailable") instead of
            the existing vague pills. Hidden when everything is ok.
            Color tracks worst status: offline = crit, degraded = warn.
            Tooltip lists every degraded/offline capability with its
            impact text. Mounted AFTER the existing showWarn pill so
            during a major outage (e.g., sidecar + vllm both down)
            both render: upstream first, capability detail second. */}
        {(() => {
          if (sim?.active) return null;        // sim mode: skip — fixture services may be empty
          if (!window.buildStackList || !window.summarizeCapabilityHealth) return null;
          let summary;
          try {
            let services = window.buildStackList(aiStackState, metrics);
            if (window.deriveRuntimeServiceHealth) {
              services = window.deriveRuntimeServiceHealth(services, {
                connection,
                sidecarOnline,
                bridgeOnline,
                bridgeHealth,
                visionSidecarOnline,
                visionHealth,
                frigateMetrics,
                cameraLabels,
              });
            }
            summary = window.summarizeCapabilityHealth(services);
          } catch { return null; }
          if (!summary) return null;           // all ok — hide
          const isCrit = summary.worst_status === "offline";
          const color = isCrit ? "var(--hg-crit)" : "var(--hg-warn)";
          return (
            <span title={summary.tooltip_text} style={{
              display: mobile ? "none" : "inline-flex", alignItems: "center", gap: 5,
              border: `1px solid ${color}`,
              color,
              padding: "2px 7px",
              borderRadius: 2,
              fontFamily: "'Geist Mono', monospace",
              fontSize: 9,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              cursor: "help",
              maxWidth: chipMax,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}>
              <span style={{
                width: 6, height: 6, borderRadius: 999,
                background: color,
                animation: isCrit ? "hgPulse 1.4s ease-in-out infinite" : "none",
              }} />
              {summary.chip_text}
            </span>
          );
        })()}
        {muteState?.muted && (
          <button
            type="button"
            onClick={onUnmuteClick}
            title={`Jarvis muted (${muteState.reason || "active"}) — click to clear manual/timer mute`}
            style={{
              all: "unset",
              display: "inline-flex", alignItems: "center", gap: 5,
              border: "1px solid var(--hg-fg-3)",
              color: "var(--hg-fg-2)",
              padding: "2px 7px",
              borderRadius: 2,
              fontFamily: "'Geist Mono', monospace",
              fontSize: 9,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              cursor: "pointer",
              maxWidth: chipMax,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            <span style={{
              width: 6, height: 6, borderRadius: 999,
              background: "var(--hg-fg-2)",
            }} />
            muted · {muteState.reason || "active"}
          </button>
        )}
        {serviceProfile && onOpenRemoteProfile && (
          <button
            type="button"
            aria-label="Remote profile"
            onClick={onOpenRemoteProfile}
            title={`Connection profile: ${serviceProfile.label}`}
            className="hg-focusable hg-mobile-touch"
            style={{
              all: "unset",
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              border: "1px solid var(--hg-border)",
              color: "var(--hg-fg-3)",
              padding: "2px 7px",
              borderRadius: 2,
              fontFamily: "'Geist Mono', monospace",
              fontSize: 9,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              cursor: "pointer",
              minHeight: mobile ? 30 : 24,
              flexShrink: 0,
            }}
          >
            <span style={{
              width: 6,
              height: 6,
              borderRadius: 999,
              background: serviceProfile.id === "tailscale" ? "var(--hg-ice)" : "var(--hg-fg-4)",
            }} />
            {serviceProfile.shortLabel}
          </button>
        )}
        {/* Simulation Mode pill — unmissable amber chip so the designer
            (or anyone) instantly knows the data is mocked. */}
        {sim?.active && (
          <button
            type="button"
            onClick={onOpenSimulationControls}
            aria-label="Open simulation controls"
            title="Simulation Mode - open controls"
            className="hg-focusable hg-mobile-touch"
            style={{
              all: "unset",
              display: "inline-flex",
              alignItems: "center",
              cursor: "pointer",
              minHeight: mobile ? 30 : 24,
              flexShrink: 0,
            }}
          >
          <span title="Simulation Mode — everything you see is mocked. Type /simulation off to exit." style={{
            display: "inline-flex", alignItems: "center", gap: 5,
            border: "1px solid var(--hg-warn)",
            color: "var(--hg-warn)",
            padding: "2px 7px",
            borderRadius: 2,
            fontFamily: "'Geist Mono', monospace",
            fontSize: 9,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            maxWidth: chipMax,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}>
            <span style={{
              width: 6, height: 6, borderRadius: 999,
              background: "var(--hg-warn)",
            }} />
            sim · {sim.scenario || "—"}
          </span>
          </button>
        )}
        {/* Phase 1.5b: SEEN identity pill removed from header. The
            face-rec affordances live below now — name chip in the
            vision drawer, named perception line in the chat feed.
            The header stays clean. */}
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
          {/* Phase 1 bugfix: dot pulses ice-blue while a voice session is
              live (mic open OR speaking), but the text label only shows
              non-online states (connecting / bad token / offline). The
              voice state itself lives in the bottom VoiceBanner. */}
          <ConnectionDot state={isLive ? "live" : isDegraded ? "degraded" : connection} />
          {(connection !== "online" || isDegraded) && (
            <span title={isDegraded ? warnText : ""} style={{
              color: connection === "auth_invalid" ? "var(--hg-warn)"
                   : isDegraded ? "var(--hg-warn)"
                   : "var(--hg-fg-3)",
              fontFamily: "'Geist Mono', monospace",
              fontSize: mobile ? 9 : 10, letterSpacing: "0.12em",
            }}>{statusText}</span>
          )}
        </span>
        {mobile && (
          <span style={{
            position: "relative",
            display: "inline-flex",
            flexShrink: 0,
            zIndex: menuOpen ? 120 : "auto",
          }}>
            <button
              type="button"
              aria-label="Open mobile actions"
              aria-expanded={menuOpen ? "true" : "false"}
              className="hg-focusable hg-mobile-touch"
              onClick={() => setMenuOpen((open) => !open)}
              style={{
                ...iconBtn,
                width: 38,
                height: 38,
                border: "1px solid var(--hg-border)",
                borderRadius: 6,
                background: "rgba(255,255,255,0.025)",
              }}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="M3 4.5h10M3 8h10M3 11.5h10" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
              </svg>
            </button>
            {menuOpen && (
              <>
                <div
                  aria-hidden="true"
                  onClick={() => setMenuOpen(false)}
                  style={{
                    position: "fixed",
                    inset: 0,
                    zIndex: 79,
                    cursor: "default",
                    background: "transparent",
                  }}
                />
                <div
                  role="menu"
                  style={{
                    position: "absolute",
                    top: "calc(100% + 7px)",
                    right: 0,
                    zIndex: 80,
                    width: "min(230px, calc(100vw - 24px))",
                    maxHeight: "min(360px, calc(100dvh - 96px))",
                    overflowY: "auto",
                    background: "var(--hg-bg-1)",
                    border: "1px solid var(--hg-border)",
                    borderRadius: 7,
                    boxShadow: "0 18px 52px rgba(0,0,0,0.48)",
                  }}
                >
                  {onOpenPeople && <button type="button" role="menuitem" style={mobileMenuItem} onClick={() => mobileMenuAction(onOpenPeople)}>people</button>}
                  {onOpenIntelligence && <button type="button" role="menuitem" style={mobileMenuItem} onClick={() => mobileMenuAction(onOpenIntelligence)}>intelligence</button>}
                  {onOpenVideoLabeler && <button type="button" role="menuitem" style={mobileMenuItem} onClick={() => mobileMenuAction(onOpenVideoLabeler)}>video labeler</button>}
                  {onToggleTheme && <button type="button" role="menuitem" style={mobileMenuItem} onClick={() => mobileMenuAction(onToggleTheme)}>{theme === "dark" ? "light mode" : "dark mode"}</button>}
                  {sim?.active && onOpenSimulationControls && <button type="button" role="menuitem" style={mobileMenuItem} onClick={() => mobileMenuAction(onOpenSimulationControls)}>simulation</button>}
                </div>
              </>
            )}
          </span>
        )}
        {!mobile && onOpenPeople && (
          <button
            ref={peopleButtonRef}
            aria-label="Open people — relationships and identities"
            title="people · relationships and identities"
            className="hg-focusable"
            onClick={onOpenPeople}
            style={iconBtn}
            onMouseEnter={hoverBright} onMouseLeave={unhover}
          >
            {/* Two-figure glyph — recognizable without leaning on emoji. */}
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
              <circle cx="5" cy="4.2" r="1.6" stroke="currentColor" strokeWidth="1.1" />
              <path d="M2 11c0-1.7 1.3-3 3-3s3 1.3 3 3" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
              <circle cx="9.8" cy="5.2" r="1.3" stroke="currentColor" strokeWidth="1.1" />
              <path d="M8 11c0-1.4 0.9-2.4 2-2.7" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
            </svg>
          </button>
        )}
        {!mobile && onOpenIntelligence && (
          <button
            aria-label="Open intelligence atlas"
            title="intelligence atlas"
            className="hg-focusable"
            onClick={onOpenIntelligence}
            style={atlasIconBtn}
            onMouseEnter={hoverAtlas} onMouseLeave={unhoverAtlas}
          >
            {window.IconNeuralNetwork
              ? <window.IconNeuralNetwork size={17} />
              : (
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d="M6.2 7.2 12 5.4l5.8 2.7M6.2 7.2l2.1 7.2M17.8 8.1l-2.1 6.8M8.3 14.4l7.4.5M12 5.4l3.7 9.5M12 5.4 8.3 14.4" stroke="currentColor" strokeWidth="1.05" strokeLinecap="round" opacity="0.44" />
                  <circle cx="12" cy="5.4" r="2.05" fill="currentColor" />
                  <circle cx="6.2" cy="7.2" r="1.45" fill="currentColor" opacity="0.68" />
                  <circle cx="17.8" cy="8.1" r="1.45" fill="currentColor" opacity="0.68" />
                  <circle cx="8.3" cy="14.4" r="1.65" fill="currentColor" opacity="0.8" />
                  <circle cx="15.7" cy="14.9" r="1.65" fill="currentColor" opacity="0.8" />
                </svg>
              )}
          </button>
        )}
        {!mobile && onOpenVideoLabeler && (
          <button
            aria-label="Open video labeler"
            title="video labeler — review and label footage"
            className="hg-focusable"
            onClick={onOpenVideoLabeler}
            style={iconBtn}
            onMouseEnter={hoverBright} onMouseLeave={unhover}
          >
            {/* Film-strip glyph — frame + sprocket rows. */}
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
              <rect x="1.2" y="2.6" width="11.6" height="8.8" rx="1" stroke="currentColor" strokeWidth="1.1" />
              <path d="M4.4 2.6v8.8M9.6 2.6v8.8" stroke="currentColor" strokeWidth="1.1" />
              <path d="M1.2 5.5h3.2M1.2 8.4h3.2M9.6 5.5h3.2M9.6 8.4h3.2" stroke="currentColor" strokeWidth="0.9" opacity="0.6" />
            </svg>
          </button>
        )}
        {!mobile && onToggleTheme && (
          <button
            aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            className="hg-focusable"
            onClick={onToggleTheme}
            style={iconBtn}
            onMouseEnter={hoverBright} onMouseLeave={unhover}
          >{theme === "dark" ? <IconSun size={14} /> : <IconMoon size={14} />}</button>
        )}
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

const SPATIAL_CHAT_RAIL_WIDTH = "clamp(352px, 19vw, 388px)";

function SpatialModeRail({
  active,
  theme,
  opsVisible,
  onHome,
  onCameras,
  onPeople,
  onIntelligence,
  onLights,
  onOps,
  onToggleTheme,
}) {
  const items = [
    { id: "home", label: "home", title: "Spatial home", onClick: onHome, tone: "var(--hg-ice)" },
    { id: "cameras", label: "cams", title: "Camera review", onClick: onCameras, tone: "var(--hg-ice-bright)" },
    { id: "people", label: "people", title: "People tracking", onClick: onPeople, tone: "var(--hg-ice)" },
    { id: "intel", label: "atlas", title: "Intelligence Atlas", onClick: onIntelligence, tone: "var(--hg-ice)" },
    { id: "lights", label: "lights", title: "Lighting lab", onClick: onLights, tone: "var(--hg-warn)" },
    { id: "ops", label: "ops", title: opsVisible ? "Hide operational tray" : "Show operational tray", onClick: onOps, tone: "var(--hg-fg-2)" },
  ];
  const activeId = active || "home";
  const railBg = "rgba(2,4,7,0.34)";
  const railBorder = "rgba(184,216,255,0.065)";
  const itemBg = "rgba(255,255,255,0.018)";
  return (
    <nav
      aria-label="Spatial workspace"
      style={{
        position: "absolute",
        left: 16,
        top: 96,
        bottom: 96,
        width: 50,
        zIndex: 4,
        display: "flex",
        flexDirection: "column",
        alignItems: "stretch",
        gap: 4,
        padding: 4,
        border: `1px solid ${railBorder}`,
        borderRadius: 7,
        background: railBg,
        backdropFilter: "blur(18px)",
        boxShadow: "0 18px 52px rgba(0,0,0,0.30)",
      }}
    >
      {items.map((item) => {
        const isActive = item.id === activeId;
        return (
          <button
            key={item.id}
            type="button"
            className="hg-focusable"
            title={item.title}
            aria-pressed={isActive}
            onClick={item.onClick}
            style={{
              width: "100%",
              height: 37,
              border: `1px solid ${isActive ? item.tone : "transparent"}`,
              borderRadius: 5,
              background: isActive ? "rgba(138,190,255,0.09)" : itemBg,
              color: isActive ? "var(--hg-fg-0)" : "var(--hg-fg-3)",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 4,
              cursor: "pointer",
              fontFamily: "'Geist Mono', monospace",
              fontSize: 7.5,
              lineHeight: 1,
              letterSpacing: "0.04em",
              textTransform: "lowercase",
              userSelect: "none",
            }}
          >
            <span style={{
              width: 5,
              height: 5,
              borderRadius: 999,
              background: isActive ? item.tone : "var(--hg-fg-4)",
              boxShadow: isActive ? `0 0 12px ${item.tone}` : "none",
              opacity: isActive ? 1 : 0.56,
            }} />
            <span>{item.label}</span>
          </button>
        );
      })}
      <div style={{ flex: 1 }} />
      <button
        type="button"
        aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        className="hg-focusable"
        title={theme === "dark" ? "Light mode" : "Dark mode"}
        onClick={onToggleTheme}
        style={{
          width: "100%",
          height: 34,
          border: "1px solid transparent",
          borderRadius: 6,
          background: itemBg,
          color: "var(--hg-fg-2)",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          cursor: "pointer",
        }}
      >
        {theme === "dark" ? <IconSun size={14} /> : <IconMoon size={14} />}
      </button>
    </nav>
  );
}

function SimulationControlsDialog({ open, sim, onClose }) {
  const scenarios = useMemo(() => {
    if (!open) return [];
    try {
      const rows = window.SimScenarios?.list?.() || [];
      return rows
        .map((s) => ({ id: String(s?.id || "").trim(), description: String(s?.description || "").trim() }))
        .filter((s) => s.id);
    } catch {
      return [];
    }
  }, [open, sim?.scenario]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open || !sim?.active) return null;

  const current = sim.scenario || "healthy";
  const rows = scenarios.length ? scenarios : [{ id: current, description: "" }];
  const hasCurrent = rows.some((s) => s.id === current);
  const switchScenario = (id) => {
    if (!id) return;
    if (typeof sim.setScenario === "function") sim.setScenario(id);
    else sim.activate?.(id);
  };
  const scenarioButton = (active) => ({
    display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10,
    width: "100%",
    border: `1px solid ${active ? "var(--hg-warn)" : "var(--hg-border)"}`,
    background: active ? "rgba(245, 158, 11, 0.12)" : "transparent",
    color: active ? "var(--hg-warn)" : "var(--hg-fg-1)",
    padding: "7px 8px",
    borderRadius: 4,
    fontFamily: "'Geist Mono', monospace",
    fontSize: 10,
    letterSpacing: 0,
    cursor: "pointer",
    textAlign: "left",
  });
  const actionButton = {
    border: "1px solid var(--hg-border)",
    background: "transparent",
    color: "var(--hg-fg-1)",
    padding: "6px 9px",
    borderRadius: 4,
    fontFamily: "'Geist Mono', monospace",
    fontSize: 10,
    letterSpacing: 0,
    cursor: "pointer",
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Simulation controls"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose?.(); }}
      style={{ position: "fixed", inset: 0, zIndex: 90, pointerEvents: "auto" }}
    >
      <div
        className="hg-fade"
        onMouseDown={(e) => e.stopPropagation()}
        style={{
          position: "absolute",
          top: 48,
          right: 14,
          width: "min(380px, calc(100vw - 28px))",
          maxHeight: "min(620px, calc(100vh - 68px))",
          display: "flex",
          flexDirection: "column",
          gap: 10,
          overflow: "hidden",
          background: "var(--hg-bg-1)",
          border: "1px solid var(--hg-warn)",
          borderRadius: 6,
          boxShadow: "0 18px 50px rgba(0,0,0,0.45)",
          padding: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <div style={{
            color: "var(--hg-warn)",
            fontFamily: "'Geist Mono', monospace",
            fontSize: 11,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
          }}>simulation</div>
          <button
            type="button"
            aria-label="Close simulation controls"
            onClick={onClose}
            style={{ ...actionButton, padding: "4px 8px" }}
          >x</button>
        </div>

        <div style={{
          display: "grid",
          gridTemplateColumns: "1fr auto auto",
          gap: 6,
          alignItems: "center",
        }}>
          <select
            aria-label="Simulation scenario"
            value={hasCurrent ? current : ""}
            onChange={(e) => switchScenario(e.target.value)}
            style={{
              minWidth: 0,
              background: "var(--hg-bg-0)",
              color: "var(--hg-fg-0)",
              border: "1px solid var(--hg-border)",
              borderRadius: 4,
              padding: "6px 8px",
              fontFamily: "'Geist Mono', monospace",
              fontSize: 10,
            }}
          >
            {!hasCurrent && <option value="">{current}</option>}
            {rows.map((s) => <option key={s.id} value={s.id}>{s.id}</option>)}
          </select>
          <button type="button" onClick={() => sim.reset?.()} style={actionButton}>reset</button>
          <button type="button" onClick={() => { sim.deactivate?.(); onClose?.(); }} style={actionButton}>exit</button>
        </div>

        <div className="hg-scroll" style={{ overflowY: "auto", display: "grid", gap: 6, paddingRight: 2 }}>
          {rows.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => switchScenario(s.id)}
              style={scenarioButton(s.id === current)}
              title={s.description || s.id}
            >
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.id}</span>
              {s.id === current && <span aria-hidden="true">active</span>}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function ConnectionDot({ state }) {
  const toneMap = {
    online: "live", live: "live",
    connecting: "pending",
    offline: "error",
    degraded: "warn",
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

function _homeTone(hostMetrics, frigateMetrics) {
  // If neither HAOS host nor Frigate stats are wired, the section is idle.
  if (!hostMetrics && !frigateMetrics) return "idle";
  if (!hostMetrics) return "ok"; // Only Frigate present — healthy by default.
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

function _aiStackDepHealthItem(aiStackOnline, aiStackState, statusError = null) {
  if (aiStackOnline === false) {
    return { label: "ai stack", state: "unreachable", tone: "crit" };
  }
  if (aiStackOnline !== true) {
    return { label: "ai stack", state: "unknown", tone: "idle" };
  }
  const overall = aiStackState?.overall;
  if (!overall) {
    if (statusError === "auth") {
      return { label: "ai stack", state: "auth failed", tone: "crit" };
    }
    if (statusError === "rate_limited") {
      return { label: "ai stack", state: "rate limited", tone: "warn" };
    }
    if (statusError) {
      return { label: "ai stack", state: "status unavailable", tone: "warn" };
    }
    return { label: "ai stack", state: "no token", tone: "idle" };
  }
  const tone =
    overall === "ready" ? "ok" :
    overall === "warming" || overall === "partial" ||
    overall === "starting" || overall === "stopping" ? "warn" :
    overall === "offline" || overall === "down" || overall === "error" ? "crit" :
    "idle";
  return { label: "ai stack", state: overall, tone };
}

function _escapeRegexPart(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function _cameraOccupancyRegex(cameraIds = []) {
  const ids = [...new Set((cameraIds || []).map((id) => String(id || "").trim()).filter(Boolean))];
  if (!ids.length) return null;
  return new RegExp(`^binary_sensor\\.(${ids.map(_escapeRegexPart).join("|")})_([a-z0-9_]+)_occupancy$`);
}

function _matchCameraOccupancyEntity(cameraIds, entityId) {
  const re = _cameraOccupancyRegex(cameraIds);
  const m = entityId && re ? String(entityId).match(re) : null;
  if (!m) return null;
  const label = m[2];
  if (label === "all") return null;
  const nice = label.replace(/_/g, " ").trim();
  return nice ? { camera: m[1], label: nice } : null;
}

function cameraLabelsFromStates(cameraIds, states = []) {
  const next = {};
  for (const s of states || []) {
    const match = _matchCameraOccupancyEntity(cameraIds, s?.entity_id);
    if (!match || s.state !== "on") continue;
    (next[match.camera] ||= new Set()).add(match.label);
  }
  return Object.fromEntries(
    Object.entries(next).map(([k, v]) => [k, [...v].sort()])
  );
}

function updateCameraLabelsFromEvent(cameraIds, prev = {}, ev) {
  const d = ev?.data;
  const match = _matchCameraOccupancyEntity(cameraIds, d?.entity_id);
  if (!match) return prev;
  const cur = new Set(prev?.[match.camera] || []);
  if (d?.new_state?.state === "on") cur.add(match.label);
  else cur.delete(match.label);
  const next = { ...(prev || {}) };
  if (cur.size) next[match.camera] = [...cur].sort();
  else delete next[match.camera];
  return next;
}

/* Tiny presentational footer for the External Reasoning provider —
 * shown under the AI Stack card in the AI tab when a key is configured.
 * Reads from window.__EXTERNAL_STATS (rolling 20-call buffer) so it
 * updates whenever MetricsStrip re-renders (~15s poll cycle). */
function _ExternalProviderFooter() {
  const stats = window.__EXTERNAL_STATS || [];
  const provider = window.openaiProvider;
  if (!provider) return null;
  const recent = stats.slice(-1)[0];
  const okCalls = stats.filter((s) => s.ok).length;
  const failCalls = stats.length - okCalls;
  // p50 latency over the OK calls.
  const okLats = stats.filter((s) => s.ok && typeof s.latencyMs === "number").map((s) => s.latencyMs).sort((a, b) => a - b);
  const p50 = okLats.length ? okLats[Math.floor(okLats.length / 2)] : null;
  let tone = "idle";
  if (recent) {
    if (!recent.ok && (recent.error === "auth_failed" || recent.error === "http_error")) tone = "crit";
    else if (!recent.ok)                                                                   tone = "warn";
    else if (Date.now() - recent.ts < 30 * 60 * 1000)                                       tone = "ok";
  }
  const color =
    tone === "ok"   ? "var(--hg-ice-bright)" :
    tone === "warn" ? "var(--hg-warn)"       :
    tone === "crit" ? "var(--hg-crit)"       :
                       "var(--hg-fg-5)";
  return (
    <div style={{
      fontFamily: "'Geist Mono', ui-monospace, monospace",
      fontSize: 9.5,
      letterSpacing: "0.06em",
      color: "var(--hg-fg-5)",
      padding: "4px 12px",
      background: "var(--hg-bg-1, rgba(255,255,255,0.02))",
      border: "1px solid var(--hg-border-soft)",
      borderRadius: 4,
      display: "flex",
      alignItems: "center",
      gap: 8,
      flexWrap: "wrap",
    }}>
      <span style={{ color }}>●</span>
      <span style={{ fontWeight: 600, letterSpacing: "0.12em", textTransform: "uppercase" }}>external</span>
      <span>·</span>
      <span>{provider.name || "openai"}</span>
      {p50 != null && (<>
        <span>·</span>
        <span>p50 <span style={{ color: "var(--hg-fg-2)" }}>{p50}ms</span></span>
      </>)}
      <span>·</span>
      <span>{okCalls} ok{failCalls > 0 ? ` · ${failCalls} fail` : ""}</span>
      {recent && !recent.ok && (<>
        <span>·</span>
        <span style={{ color: "var(--hg-warn)" }}>last: {recent.error || "fail"}</span>
      </>)}
    </div>
  );
}

/* ── MetricsStrip v4 ────────────────────────────────────────────────────
 *
 * Designed product surface. Status strip always visible, tabs with
 * pill-style active state, content composed from Cards. Each tab
 * answers ONE question.
 *
 *   NOW:     "what's happening in my house right now?"
 *   AI:      "is the model healthy?"
 *   HOME:    "where are people / what does HA see?"
 *   NETWORK: "is the network healthy?"
 *
 * Last voice turn lives in AI (it's historical, not "now").
 */
function MetricsStrip({
  metrics, metricsHistory, metricsBase,
  bridgeHealth, networkMetrics, visionSidecarOnline, visionHealth, hostMetrics,
  frigateMetrics,
  // Tray v5: connection-state signals threaded through so the AI tab's
  // dependency-health strip can render plain-language status for the
  // AI Box (sidecarOnline) and Bridge (bridgeOnline) without re-polling.
  connection = "offline",
  sidecarOnline = null,
  bridgeOnline = null,
  // AI Stack Control (Phase 1): the supervisor-driven umbrella status.
  // Both polled in home-app.jsx and passed down so the AiStackCard
  // doesn't duplicate the 15-s heartbeat. See home-ai-stack.jsx.
  aiStackOnline = null,
  aiStackState = null,
  aiStackStatusError = null,
  roomContext,
  voice, identity, media,
  recentPerceptions = [],
  cameraLabels = {},
  // Sim Mode: trace state is now controlled by HomeApp (lifted from
  // internal state) so simulation scenarios can inject mock traces.
  // In live mode the trace-poll effect below writes to these via the
  // setters provided as props.
  traceSummary = null,
  lastTrace = null,
  setTraceSummary = () => {},
  setLastTrace = () => {},
  // Addendum 16 F-2: callback fired with the FULL trace array on each
  // poll so HomeApp's lab writer can backfill missed traces (was: only
  // the most recent was passed via setLastTrace, dropping any trace
  // that landed between the 5 s polls).
  onTracesFetched = () => {},
  // Addendum 17: routing-log entries fetched from HA, used by HomeApp
  // to backfill EXTERNAL turns into the lab history. External turns
  // bypass vLLM entirely (sidecar never sees them), so without this
  // hook the lab undercounts conversation activity.
  onRoutingLogFetched = () => {},
  // Proactive-assistant coordinator UI state (home-proactive.jsx) — a
  // single status row in the AI tab. { phase, away, reason, room, ... }
  proactive = null,
  onProactiveAction = () => {},
  simActive = false,
  // Lab tab (Addendum 10) — buffer refs lifted from HomeApp + a tick
  // to trigger re-render on sample/turn append.
  labTurnsRef = { current: [] },
  labSamplesRef = { current: [] },
  labTick = 0,
  // HA token + endpoint — passed for diag-pane log streaming (future)
  // AND routing-log poll (Addendum 17, external-turn visibility).
  token = "",
  endpoint = "",
  dockMode = false,
  mobile = false,
}) {
  const TRAY_EXPANDED_KEY = dockMode ? "hg-spatial-tray-expanded-v1" : "hg-tray-expanded-v1";
  const TRAY_TAB_KEY = dockMode ? "hg-spatial-tray-tab-v1" : "hg-tray-tab-v1";
  const TRAY_VALID_TABS = new Set(["ai", "infra", "trace"]);
  const [expanded, setExpanded] = useState(() => {
    if (dockMode) return false;
    try { return window.localStorage.getItem(TRAY_EXPANDED_KEY) === "1"; }
    catch { return false; }
  });
  // Tray v5: `ai` is the most important tab — it answers "is the AI ready,
  // responsive, healthy" in 3 seconds. `now` was removed because once
  // perception/occupancy left the drawer, it duplicated `ai`'s dependency
  // strip. `home` was renamed `infra` because "home" clashes with the
  // app brand. Tab order: ai · infra · network.
  const [activeTab, setActiveTab] = useState(() => {
    try {
      const stored = window.localStorage.getItem(TRAY_TAB_KEY);
      return TRAY_VALID_TABS.has(stored) ? stored : "ai";
    } catch { return "ai"; }
  });
  const [diagOpen, setDiagOpen] = useState(false);

  // Drag-resizable tray body. Persisted to localStorage so it survives
  // reloads. Initial value reads from storage; clamped at use site.
  const TRAY_HEIGHT_KEY = dockMode ? "hg-spatial-tray-height-v1" : "hg-tray-height-v1";
  const TRAY_MIN_HEIGHT = dockMode ? 118 : 160;
  const TRAY_MAX_HEIGHT_VH = 0.85; // 85% of viewport
  const [trayHeight, setTrayHeight] = useState(() => {
    try {
      const v = parseInt(window.localStorage.getItem(TRAY_HEIGHT_KEY) || "", 10);
      if (Number.isFinite(v) && v >= TRAY_MIN_HEIGHT) return v;
    } catch {}
    return dockMode ? 138 : 340;
  });
  const trayDragStateRef = useRef(null);
  const onTrayHandlePointerDown = useCallback((e) => {
    e.preventDefault();
    // Capture pointer so dragging works outside the handle's bounds.
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch {}
    trayDragStateRef.current = {
      startY: e.clientY,
      startHeight: trayHeight,
      pointerId: e.pointerId,
    };
  }, [trayHeight]);
  const onTrayHandlePointerMove = useCallback((e) => {
    const st = trayDragStateRef.current;
    if (!st || st.pointerId !== e.pointerId) return;
    // Drag UP increases height (tray grows upward), drag DOWN shrinks it.
    const delta = st.startY - e.clientY;
    const maxH = Math.floor(window.innerHeight * TRAY_MAX_HEIGHT_VH);
    const next = Math.max(TRAY_MIN_HEIGHT, Math.min(maxH, st.startHeight + delta));
    setTrayHeight(next);
  }, []);
  const onTrayHandlePointerUp = useCallback((e) => {
    const st = trayDragStateRef.current;
    if (!st || st.pointerId !== e.pointerId) return;
    trayDragStateRef.current = null;
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch {}
    try { window.localStorage.setItem(TRAY_HEIGHT_KEY, String(trayHeight)); } catch {}
  }, [trayHeight]);
  useEffect(() => {
    try { window.localStorage.setItem(TRAY_EXPANDED_KEY, expanded ? "1" : "0"); } catch {}
  }, [expanded]);
  useEffect(() => {
    if (!TRAY_VALID_TABS.has(activeTab)) return;
    try { window.localStorage.setItem(TRAY_TAB_KEY, activeTab); } catch {}
  }, [activeTab]);

  // Shared adapter: /conversations/recent OR /conversations/stream entry
  // → trace with pre-built 2-stage breakdown (prefill / gen) derived
  // from ttft_observed_ms. Used by BOTH the 5s poll (warm-start +
  // SSE fallback) AND the SSE subscriber (real-time per Addendum 29
  // Change 1).
  const mapSidecarEntryToTrace = useCallback((e) => {
    const totalMs = e.upstream_ms || 0;
    const ttft = e.ttft_observed_ms || 0;
    const prefillMs = Math.min(ttft, totalMs);
    const genMs = Math.max(0, totalMs - prefillMs);
    // CRITICAL: use the sidecar's REAL wall-clock timestamp (e.ts is unix
    // seconds, e.t_request_started is monotonic seconds — we want e.ts).
    // Old code used `Date.now() - totalMs` which assumed the turn just
    // ended; but entries from /conversations/recent can be 30+ seconds
    // old. The mismatch put the turn's [startedAt, endedAt] window in
    // the wrong time slice, so real GPU samples (which have correct
    // wall-clock t) fell outside it and never aggregated into the per-
    // turn anchors. Result: GPU/CPU lines showed flat for turns that
    // really did use the GPU heavily. Fall back to Date.now() only when
    // ts is missing (very old / non-conformant entries).
    let tEnd;
    if (typeof e.ts === "number" && e.ts > 0) {
      tEnd = e.ts * 1000;            // sidecar ts is seconds
    } else {
      tEnd = Date.now();
    }
    const tStart = tEnd - totalMs;
    return {
      turn_id: e.id,
      user_text: e.user || "",
      // Change 5: dual-line tooltip needs both sides.
      assistant_text: e.assistant || "",
      // Change 4: pass tool_calls through so multi-round grouping can
      // splice tool segments between consecutive rounds.
      tool_calls: Array.isArray(e.tool_calls) ? e.tool_calls : [],
      t_done: totalMs,
      stages: [
        { label: "prefill", ms: prefillMs, startedAt: tStart,             endedAt: tStart + prefillMs },
        { label: "gen",     ms: genMs,     startedAt: tStart + prefillMs, endedAt: tEnd                },
      ],
      cold: false,
    };
  }, []);

  useEffect(() => {
    // Sim Mode: skip the real trace polling — fixtures are injected
    // directly via the lifted setters.
    if (simActive) return undefined;
    if (!expanded || !metricsBase) return undefined;
    if (activeTab !== "now" && activeTab !== "ai" && activeTab !== "trace" && !diagOpen) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        // Sidecar-side: 2026-05-18 fix — sidecar exposes /conversations/recent
        // (not /traces/latest, which was a planned-but-never-shipped path).
        // /traces/summary also doesn't exist; we derive a minimal summary
        // from the entries client-side.
        // HA-side: routing log (every conversation, including external
        // which bypasses vLLM and produces no sidecar trace).
        const fetches = [
          tauriFetch(`${metricsBase}/conversations/recent?n=50`, { cache: "no-store" }),
        ];
        const wantRoutingLog = !!(endpoint && token);
        if (wantRoutingLog) {
          fetches.push(
            tauriFetch(
              `${endpoint.replace(/\/+$/, "")}/api/extended_openai_conversation/routing_log?tail=50`,
              { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" },
            ),
          );
        }
        const results = await Promise.all(fetches);
        if (cancelled) return;
        const [l, r] = results;
        if (l && l.ok) {
          const j = await l.json();
          const entries = Array.isArray(j.entries) ? j.entries : [];
          const traces = entries.map(mapSidecarEntryToTrace);
          // Derive a tiny summary so existing consumers of setTraceSummary
          // keep working (count + p50 of LLM ms is enough for the rail).
          if (traces.length > 0) {
            const llmMsList = traces.map((t) => t.t_done).sort((a, b) => a - b);
            const p50 = llmMsList[Math.floor(llmMsList.length / 2)] || 0;
            setTraceSummary({ count: traces.length, p50_llm_ms: p50, window_s: 3600 });
            setLastTrace(traces[traces.length - 1]);
            onTracesFetched(traces);
          }
        }
        if (r && r.ok) {
          const j = await r.json();
          const entries = Array.isArray(j.entries) ? j.entries : [];
          if (entries.length > 0) onRoutingLogFetched(entries);
        }
      } catch (e) {
        console.warn("[metrics] trace poll failed:", e?.message || e);
      }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [expanded, activeTab, diagOpen, metricsBase, simActive, endpoint, token, mapSidecarEntryToTrace]);

  // Addendum 29 Change 1: SSE subscription for real-time turn arrival.
  // The sidecar broadcasts every completion via /conversations/stream;
  // subscribing means new turns reach the lab tab within ~200ms of the
  // LLM call finishing instead of waiting up to 5s for the next poll.
  // The 5s poll above stays as warm-start (initial render) + SSE
  // fallback (reconnects on error with exponential backoff).
  useEffect(() => {
    if (simActive) return undefined;
    if (!expanded || !metricsBase) return undefined;
    if (activeTab !== "now" && activeTab !== "ai" && activeTab !== "trace" && !diagOpen) return undefined;
    // EventSource is available in WebView2; create once per (tab,base)
    // and tear down on cleanup. Don't use EventSource if it's missing
    // (older webviews, test envs): fall through to poll-only.
    if (typeof EventSource === "undefined") {
      console.warn("[trace-sse] EventSource not available — falling back to 5s poll");
      return undefined;
    }
    let cancelled = false;
    let es = null;
    let backoffMs = 1000;
    const maxBackoffMs = 30000;
    let reconnectTimer = null;
    const url = `${metricsBase.replace(/\/+$/, "")}/conversations/stream`;
    const connect = () => {
      if (cancelled) return;
      try {
        es = new EventSource(url);
      } catch (err) {
        console.warn("[trace-sse] EventSource ctor failed:", err?.message || err);
        return;
      }
      es.onopen = () => {
        backoffMs = 1000;
        if (typeof window !== "undefined") {
          window.__SSE_TRACE_CONNECTED = true;
          window.__SSE_TRACE_CONNECT_TS = Date.now();
        }
      };
      es.onmessage = (ev) => {
        if (cancelled) return;
        try {
          const entry = JSON.parse(ev.data);
          if (!entry || typeof entry !== "object") return;
          const trace = mapSidecarEntryToTrace(entry);
          // Track event-arrival timestamps so the autonomous probe
          // (AV29-1 cross-correlation) can detect missed turns.
          if (typeof window !== "undefined") {
            window.__SSE_TRACE_LAST_EVENT_TS = Date.now();
            window.__SSE_TRACE_EVENT_COUNT = (window.__SSE_TRACE_EVENT_COUNT || 0) + 1;
          }
          // Reuse the bulk handler so dedup + persistence are identical.
          onTracesFetched([trace]);
          // Also set lastTrace so consumers that watched the poll's
          // single-turn signal continue working.
          setLastTrace(trace);
        } catch (parseErr) {
          // Malformed event — ignore, but log once for diagnostics.
          console.warn("[trace-sse] parse fail:", parseErr?.message);
        }
      };
      es.onerror = () => {
        if (cancelled) return;
        if (typeof window !== "undefined") {
          window.__SSE_TRACE_CONNECTED = false;
          window.__SSE_TRACE_LAST_ERROR_TS = Date.now();
        }
        try { es && es.close(); } catch {}
        es = null;
        // Reconnect with exponential backoff. Poll path keeps running
        // in parallel so even sustained SSE outage doesn't block trace
        // updates entirely — they just slow back down to ~5s cadence.
        reconnectTimer = setTimeout(connect, backoffMs);
        backoffMs = Math.min(maxBackoffMs, backoffMs * 2);
      };
    };
    connect();
    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      try { es && es.close(); } catch {}
      if (typeof window !== "undefined") {
        window.__SSE_TRACE_CONNECTED = false;
      }
    };
  }, [expanded, activeTab, diagOpen, metricsBase, simActive, mapSidecarEntryToTrace, onTracesFetched]);

  /* ── Derived state ───────────────────────────────────────────────── */
  const aiTone = _aiBoxTone(metrics, bridgeHealth);
  const homeTone = _homeTone(hostMetrics, frigateMetrics);
  const netTone = _networkTone(networkMetrics);

  const voiceState = voice?.state || "inactive";
  const aiPhrase =
    voiceState === "listening"   ? "listening"
    : voiceState === "transcribing" ? "transcribing"
    : voiceState === "thinking"  ? "thinking"
    : voiceState === "speaking"  ? "speaking"
    : voiceState === "ready"     ? "ready"
    : bridgeHealth?.warmup_complete === false ? "warming"
    : "idle";
  const aiIsTransient = ["listening", "transcribing", "thinking", "speaking"].includes(voiceState);

  const lastActivity = identity?.name && identity?.camera
    ? { name: identity.name, room: identity.camera.replace(/_/g, " ") }
    : null;
  const lastActivityAge = identity?.ts
    ? Math.max(0, Math.floor(Date.now() / 1000 - identity.ts))
    : null;

  let warnChip = null;
  if (bridgeHealth && !bridgeHealth.ha_connected) {
    warnChip = { text: "ha down", tone: "crit" };
  } else if (networkMetrics?.switches?.some((sw) => (sw.mem || 0) > 90)) {
    warnChip = { text: "switch ram high", tone: "warn" };
  }

  const ttfaP50 = traceSummary?.ttfa_ms?.p50 != null ? Math.round(traceSummary.ttfa_ms.p50) : null;
  const ttfaP90 = traceSummary?.ttfa_ms?.p90 != null ? Math.round(traceSummary.ttfa_ms.p90) : null;

  // Tray v6: network tab dropped — without app↔HA / app↔AI-box latency
  // or throughput instrumentation, the network tab was just UniFi device
  // CPU/RAM (low signal). AI Box / HAOS / Bridge reachability lives in
  // the AI tab's dep-strip now. UniFi diagnostics retained in /diag.
  // Lab tab tier — derive once so we can set the tab warn flag.
  const _labBaseline = window.computeBaseline
    ? window.computeBaseline(labTurnsRef.current)
    : { p50: null, hasBaseline: false };
  const _labTier = window.deriveLabTier
    ? window.deriveLabTier(metrics, aiStackState, lastTrace, _labBaseline)
    : { tier: "ready" };
  const _labWarn = _labTier.tier !== "ready" && _labTier.tier !== "warming";

  const tabs = [
    { id: "ai",      label: "ai",    warn: _labWarn },
    { id: "infra",   label: "infra", warn: homeTone === "warn" || homeTone === "crit" },
    { id: "trace",   label: "trace" },
  ];

  useEffect(() => {
    if (activeTab === "intel") setActiveTab("ai");
  }, [activeTab]);

  // Active room derived from identity. Falls back to last-active per
  // RoomContextStore if available.
  const activeRoom = identity?.camera || null;
  const activeRoomMedia = activeRoom ? media?.[activeRoom] : null;
  const roomContextRows = (() => {
    const rooms = roomContext && typeof roomContext.rooms === "object" ? roomContext.rooms : null;
    if (!rooms) return [];
    return Object.entries(rooms).map(([room, data]) => {
      const d = data || {};
      const personNames = Array.isArray(d.persons)
        ? d.persons.map((p) => p?.identity?.name || p?.name || "").filter(Boolean)
        : [];
      const occupant = d.occupant || d.identity || d.person || personNames.join(", ");
      const occupied = d.occupied === true || !!occupant;
      const mediaLabel = typeof d.media === "string"
        ? d.media
        : (d.media?.title || d.media?.app_name || null);
      return {
        room,
        occupant: occupant || (occupied ? "occupied" : ""),
        ageS: d.age_s ?? d.age_seconds ?? d.perception_age_seconds ?? null,
        media: mediaLabel,
      };
    }).sort((a, b) => {
      const ao = !!a.occupant;
      const bo = !!b.occupant;
      if (ao !== bo) return ao ? -1 : 1;
      return a.room.localeCompare(b.room);
    });
  })();

  /* ── Tab renderers ───────────────────────────────────────────────── */

  // Tray v5 helpers — formatting for the v5-redesigned cards.
  const fmtAge = (s) => {
    if (s == null || !isFinite(s)) return null;
    if (s < 60) return `${Math.round(s)}s ago`;
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    return `${Math.round(s / 3600)}h ago`;
  };
  const fmtUptime = (s) => {
    if (!s || !isFinite(s)) return null;
    if (s < 60) return `${Math.round(s)}s`;
    if (s < 3600) return `${Math.round(s / 60)}m`;
    return `${Math.round(s / 3600)}h`;
  };

  // Dependency-health states for the strip atop the AI tab. Plain-language
  // labels; tone drives the dot color and the value color.
  //   Model            → metrics.model + warmup status (was MODEL card, v6 folded here)
  //   AI Stack         → aiStackState.overall (NEW — umbrella status; see home-ai-stack.jsx)
  //   metrics-sidecar  → sidecarOnline / unknown / offline (RENAMED from "ai box")
  //   HAOS             → connection + bridgeHealth.ha_connected, with a degraded fork
  //   Bridge           → bridgeOnline + warmup_complete + uptime
  //   TTS              → bridgeHealth.tts_engine name when bridge healthy; else unknown
  //   Frigate          → frigateMetrics non-null OR camera labels flowing → online
  const depStates = (() => {
    const items = [];
    // Model — folded in from the old MODEL card. The state value is the
    // model name itself; tone reflects ready/warming/degraded/offline.
    let modelTone = "idle", modelState = "unknown";
    if (hasLiveModelName(metrics.model)) {
      modelState = metrics.model;
      if (bridgeOnline === false) modelTone = "crit";
      else if (bridgeHealth?.ha_connected === false) modelTone = "warn";
      else if (bridgeHealth?.warmup_complete === false) modelTone = "warn";
      else modelTone = "ok";
    }
    items.push({ label: "model", state: modelState, tone: modelTone });
    // AI Stack — umbrella status across vLLM / STT / TTS / sidecars on the
    // Ubuntu AI box, as reported by the stack-supervisor on :8093.
    // Distinct from the "metrics-sidecar" chip below (which reports only
    // the chat-tee :8092 liveness) — this is the whole-stack rollup.
    items.push(_aiStackDepHealthItem(aiStackOnline, aiStackState, aiStackStatusError));
    // metrics-sidecar (was "ai box" — renamed so it no longer overlaps
    // with the new "ai stack" umbrella; this chip is just the :8092
    // /healthz reachability check).
    if (sidecarOnline === true) items.push({ label: "metrics-sidecar", state: "online", tone: "ok" });
    else if (sidecarOnline === false) items.push({ label: "metrics-sidecar", state: "offline", tone: "crit" });
    else items.push({ label: "metrics-sidecar", state: "unknown", tone: "idle" });
    // HAOS
    const haUp = connection === "online";
    const bridgeSeesHa = bridgeHealth?.ha_connected;
    if (haUp && bridgeSeesHa) items.push({ label: "haos", state: "connected", tone: "ok" });
    else if (haUp || bridgeSeesHa) items.push({ label: "haos", state: "degraded", tone: "warn" });
    else if (connection === "auth_invalid") items.push({ label: "haos", state: "bad token", tone: "crit" });
    else items.push({ label: "haos", state: "offline", tone: "crit" });
    // Bridge — value carries uptime when warm, plain state otherwise
    const upStr = fmtUptime(bridgeHealth?.uptime_s);
    if (bridgeOnline === false) items.push({ label: "bridge", state: "offline", tone: "crit" });
    else if (bridgeHealth?.warmup_complete === true) {
      items.push({ label: "bridge", state: upStr ? `warm · ${upStr}` : "warm", tone: "ok" });
    }
    else if (bridgeHealth?.warmup_complete === false) items.push({ label: "bridge", state: "warming", tone: "warn" });
    else items.push({ label: "bridge", state: "unknown", tone: "idle" });
    // TTS
    if (bridgeOnline === false) items.push({ label: "tts", state: "offline", tone: "crit" });
    else if (bridgeHealth?.tts_engine) items.push({ label: "tts", state: bridgeHealth.tts_engine, tone: "ok" });
    else items.push({ label: "tts", state: "unknown", tone: "idle" });
    // Frigate — proxy: stats sensors populated OR camera labels arriving in last 60s
    const labelsFlowing = cameraLabels && Object.keys(cameraLabels).length > 0;
    if (frigateMetrics || labelsFlowing) items.push({ label: "frigate", state: "online", tone: "ok" });
    else items.push({ label: "frigate", state: "unknown", tone: "idle" });
    return items;
  })();

  const renderAi = () => {
    // AI tab — migrated to display the 3 lab components: pressured
    // banner + 8-service grid + 4 resource pills. Composes the same
    // pieces HmLabTab used to render directly. The Lab tab now keeps
    // only the chart card + summary header + diag pane.
    if (!window.LabBanner || !window.LabStackList || !window.LabResPills
        || !window.deriveLabTier || !window.buildStackList) {
      return (
        <div style={{
          padding: "20px 0", fontFamily: "'Geist Mono', monospace",
          fontSize: 10.5, color: "var(--hg-fg-3)", textAlign: "center",
        }}>lab components not loaded</div>
      );
    }
    const _baseline = window.computeBaseline
      ? window.computeBaseline(labTurnsRef.current)
      : null;
    const _tier = window.deriveLabTier(metrics, aiStackState, lastTrace, _baseline);
    let _services = window.buildStackList(aiStackState, metrics);
    if (window.deriveRuntimeServiceHealth) {
      _services = window.deriveRuntimeServiceHealth(_services, {
        connection,
        sidecarOnline,
        bridgeOnline,
        bridgeHealth,
        visionSidecarOnline,
        visionHealth,
        frigateMetrics,
        cameraLabels,
      });
    }
    // Banner action dispatcher (e.g., 'stop ai models' on the pressured
    // banner) — delegates to HomeStackActions. Supervisor URL is the
    // metrics-sidecar host on port 8093.
    const _supervisorUrl = (() => {
      const base = metricsBase || metricsBaseFromEndpoint(endpoint);
      return supervisorBaseFromEndpoint(base, endpoint);
    })();
    const _stackToken =
      (typeof window !== "undefined" && window.__STACK_TOKEN) ||
      (typeof localStorage !== "undefined" && localStorage.getItem("hg-stack-token-DEV")) ||
      "";
    const _onBannerAction = async (verb) => {
      const HSA = window.HomeStackActions;
      if (!HSA || !_supervisorUrl || !_stackToken) {
        addEvent({
          kind: "system", tone: "warn",
          text: "stack supervisor not configured — set token via /stack-token",
        });
        return;
      }
      let result;
      if (verb === "stopStack")      result = await HSA.stop({ supervisorUrl: _supervisorUrl, stackToken: _stackToken });
      else if (verb === "start")     result = await HSA.start({ supervisorUrl: _supervisorUrl, stackToken: _stackToken });
      else if (verb === "restart")   result = await HSA.restart({ supervisorUrl: _supervisorUrl, stackToken: _stackToken });
      else if (verb === "freeGpu")   result = await HSA.freeGpu({ supervisorUrl: _supervisorUrl, stackToken: _stackToken });
      else return;
      if (result && !result.ok) {
        addEvent({
          kind: "system", tone: "warn",
          text: `${verb} failed: ${result.error || ("HTTP " + (result.status || "?"))}`,
        });
      } else if (result && result.ok) {
        addEvent({ kind: "system", text: `${verb}: dispatched`, tone: "ok" });
      }
    };
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {window.ProactiveStatusLine && (
          <window.ProactiveStatusLine proactive={proactive} onAction={onProactiveAction} />
        )}
        {window.AiStackCard && (
          <window.AiStackCard
            aiStackOnline={aiStackOnline}
            aiStackState={aiStackState}
            aiStackStatusError={aiStackStatusError}
            tokenConfigured={!!_stackToken}
            supervisorUrl={_supervisorUrl}
            stackToken={_stackToken}
          />
        )}
        <window.LabBanner banner={_tier.banner} onAction={_onBannerAction} />
        {window.LabStackPane
          ? <window.LabStackPane services={_services} metrics={metrics} />
          : <window.LabStackList services={_services} /> /* fallback if helpers missing */}
        <window.LabResPills metrics={metrics} />
      </div>
    );
  };


  // Tray v6: renderInfra cleanup pass.
  //   - Frigate CPU dropped: it's a per-thread sum (e.g. 341% = 3.41
  //     cores' worth) that overlaps the HAOS HOST CPU which already
  //     covers everything running on the LattePanda.
  //   - MEDIA INTEGRATIONS card dropped: low-signal under normal use.
  //     stale-media counts surface in the HA BRIDGE card as warn chips.
  //   - HAOS RAM falls back to absolute GB when System Monitor doesn't
  //     expose the `_percent` variant.
  //   - HAOS card spans two-up alongside FRIGATE; HA BRIDGE goes full
  //     width below so it doesn't leave dead space.
  const renderInfra = () => {
    const stale = bridgeHealth?.stale_media_integrations || [];
    const roomsLoaded = bridgeHealth?.rooms_loaded || 0;
    const mediaRegistered = bridgeHealth?.media_players_registered || 0;
    const heartbeat = bridgeHealth?.last_trace_age_s != null
      ? fmtAge(bridgeHealth.last_trace_age_s)
      : null;
    const uptime = fmtUptime(bridgeHealth?.uptime_s);

    // Bridge/API badge
    let bridgeBadge;
    if (!bridgeHealth) bridgeBadge = { text: "offline", tone: "crit" };
    else if (bridgeHealth.ha_connected === false) bridgeBadge = { text: "degraded", tone: "warn" };
    else if (bridgeHealth.warmup_complete === false) bridgeBadge = { text: "warming", tone: "warn" };
    else if (stale.length > 0) bridgeBadge = { text: `${stale.length} stale media`, tone: "warn" };
    else bridgeBadge = { text: "connected", tone: "ok" };

    // Non-default feature flags (chips, only when interesting)
    const flagChips = [];
    if (bridgeHealth?.dry_run === true) flagChips.push({ label: "DRY RUN", tone: "warn" });
    if (bridgeHealth?.voice_rewriter === true) flagChips.push({ label: "VOICE REWRITER", tone: "ice" });
    if (bridgeHealth?.direct_dispatch === true) flagChips.push({ label: "DIRECT DISPATCH", tone: "ice" });

    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {/* Row 1: HAOS HOST + FRIGATE side-by-side, equal-height */}
        <div style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 10,
          alignItems: "stretch",
        }}>
          <HmCard title="HAOS HOST"
                  badge={hostMetrics ? null : { text: "not set up", tone: "warn" }}>
            {hostMetrics ? (
              <div style={{
                display: "flex", flexDirection: "column", gap: 10,
                height: "100%",
              }}>
                {hostMetrics.cpu != null && (
                  <HmMetricCard label="cpu" value={hostMetrics.cpu} unit="%" max={100} viz="bar" />
                )}
                {/* RAM as bar (matches CPU). If only absolute GB is known
                    (the user's System Monitor exposes raw MB, not %),
                    fall back to a heuristic 32GB total — LattePanda
                    Sigma typically ships 16-32GB; if your box is 16GB
                    the bar will read ~75% (still legible). */}
                {hostMetrics.ram != null ? (
                  <HmMetricCard label="ram" value={hostMetrics.ram} unit="%" max={100} viz="bar" />
                ) : hostMetrics.ramGb != null ? (
                  <HmMetricCard
                    label="ram"
                    value={hostMetrics.ramGb}
                    unit={`/32 gb`}
                    max={32}
                    viz="bar"
                  />
                ) : null}
                {/* spacer pushes uptime to bottom so card matches Frigate height */}
                <div style={{ flex: 1 }} />
                {hostMetrics.uptime && (
                  <HmStatRow label="uptime" value={hostMetrics.uptime} />
                )}
              </div>
            ) : (
              <HmEmptyState
                message="enable system monitor"
                action={{
                  label: "how",
                  tooltip: "HA → Settings → Devices & Services → + Add Integration → System Monitor.\nSelect processor_use, memory_use_percent, disk_use_percent.\nRestart HA if entities don't appear within 60s.",
                }} />
            )}
          </HmCard>

          <HmCard title="FRIGATE"
                  badge={frigateMetrics
                    ? { text: `${Object.keys(frigateMetrics.fps || {}).length} cameras`, tone: "ok" }
                    : { text: "5 cameras", tone: "ice" }}>
            {frigateMetrics ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {/* Coral TPU = the only Frigate-specific compute metric;
                    overall CPU lives on the HAOS HOST card. */}
                {frigateMetrics.coralMs != null && (
                  <HmStatRow label="coral inference"
                             value={`${(Math.round(frigateMetrics.coralMs * 10) / 10).toFixed(1)} ms`}
                             tone={frigateMetrics.coralMs > 15 ? "warn" : "default"} />
                )}
                {frigateMetrics.fps && Object.keys(frigateMetrics.fps).length > 0 && (
                  <div style={{ marginTop: 4 }}>
                    {Object.entries(frigateMetrics.fps).sort(([a], [b]) => a.localeCompare(b)).map(([cam, fps]) => (
                      <HmStatRow key={cam}
                        label={cam.replace(/_/g, " ")}
                        value={`${Math.round(fps * 10) / 10} fps`} />
                    ))}
                  </div>
                )}
                {frigateMetrics.uptime && (
                  <HmStatRow label="uptime" value={frigateMetrics.uptime} />
                )}
              </div>
            ) : (
              <HmEmptyState
                message="enable stats sensors"
                action={{
                  label: "how",
                  tooltip: "Drop tools/ha_packages/frigate_stats.yaml from HomeAIVoice repo into\n/config/packages/frigate_stats.yaml on HAOS.\nEnsure configuration.yaml has:\n  homeassistant:\n    packages: !include_dir_named packages\nRestart HA. Tray auto-picks up sensor.frigate_*.",
                }} />
            )}
          </HmCard>
        </div>

        {/* HA BRIDGE / API — full width below */}
        <HmCard title="HA BRIDGE / API" badge={bridgeBadge}>
          {!bridgeHealth ? (
            <div style={{
              fontFamily: "'Geist Mono', monospace",
              fontSize: 11, color: "var(--hg-fg-5)",
            }}>bridge unreachable</div>
          ) : (
            <div style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
              gap: "6px 18px",
              alignItems: "baseline",
            }}>
              {uptime && <HmStatRow label="uptime" value={uptime} />}
              {heartbeat && <HmStatRow label="heartbeat" value={heartbeat} />}
              <HmStatRow label="rooms" value={roomsLoaded} />
              <HmStatRow label="media players" value={mediaRegistered} />
              {flagChips.length > 0 && (
                <div style={{
                  display: "flex", flexWrap: "wrap", gap: 4,
                  gridColumn: "1 / -1",
                  paddingTop: 6, marginTop: 2,
                  borderTop: "1px solid var(--hg-border-soft)",
                }}>
                  {flagChips.map((c) => (
                    <span key={c.label} style={{
                      fontFamily: "'Geist Mono', monospace",
                      fontSize: 9, letterSpacing: "0.1em",
                      padding: "2px 6px",
                      border: `1px solid var(--hg-${c.tone === "warn" ? "warn" : "ice"})`,
                      color: `var(--hg-${c.tone === "warn" ? "warn" : "ice-bright"})`,
                      borderRadius: 2,
                    }}>{c.label}</span>
                  ))}
                </div>
              )}
              {stale.length > 0 && (
                <div style={{
                  gridColumn: "1 / -1",
                  paddingTop: 8,
                  marginTop: 4,
                  borderTop: "1px solid var(--hg-border-soft)",
                  minWidth: 0,
                }}>
                  <div style={{
                    fontFamily: "'Geist Mono', monospace",
                    fontSize: 9,
                    letterSpacing: "0.14em",
                    textTransform: "uppercase",
                    color: "var(--hg-fg-5)",
                    marginBottom: 5,
                  }}>stale media</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                    {stale.slice(0, 6).map((m, i) => {
                      const entity = String(m?.entity_id || "media").replace(/^media_player\./, "").replace(/_/g, " ");
                      const age = Number.isFinite(m?.age_hours) ? `${Math.round(m.age_hours)}h` : "stale";
                      return (
                        <span key={`${entity}-${i}`} style={{
                          display: "inline-flex",
                          maxWidth: "100%",
                          gap: 5,
                          alignItems: "baseline",
                          fontFamily: "'Geist Mono', monospace",
                          fontSize: 10,
                          color: "var(--hg-warn)",
                          border: "1px solid color-mix(in srgb, var(--hg-warn) 42%, transparent)",
                          borderRadius: 2,
                          padding: "2px 6px",
                          background: "rgba(255,180,80,0.045)",
                        }}>
                          <span style={{
                            minWidth: 0,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}>{entity}</span>
                          <span style={{ color: "var(--hg-fg-5)", flex: "0 0 auto" }}>{age}</span>
                        </span>
                      );
                    })}
                    {stale.length > 6 && (
                      <span style={{
                        fontFamily: "'Geist Mono', monospace",
                        fontSize: 10,
                        color: "var(--hg-fg-5)",
                        padding: "2px 0",
                      }}>+{stale.length - 6} more</span>
                    )}
                  </div>
                </div>
              )}
              {window.HmRoomRow && roomContextRows.length > 0 && (
                <div style={{
                  gridColumn: "1 / -1",
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                  paddingTop: 8,
                  marginTop: 4,
                  borderTop: "1px solid var(--hg-border-soft)",
                }}>
                  <div style={{
                    fontFamily: "'Geist Mono', monospace",
                    fontSize: 9,
                    letterSpacing: "0.14em",
                    textTransform: "uppercase",
                    color: "var(--hg-fg-5)",
                    marginBottom: 2,
                  }}>room context</div>
                  {roomContextRows.slice(0, 8).map((r) => (
                    <window.HmRoomRow
                      key={r.room}
                      room={r.room}
                      occupant={r.occupant}
                      ageS={r.ageS}
                      media={r.media}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </HmCard>
      </div>
    );
  };

  // Lab tab (Addendum 10) — time-aligned chart with stage trace +
  // GPU/VRAM/CPU/RAM. Delegates to window.HmLabTab (declared in
  // home-metrics-lab.jsx). Internal diag-pane toggle is local.
  const renderLab = () => {
    if (!window.HmLabTab) {
      return (
        <div style={{
          padding: "20px 0", fontFamily: "'Geist Mono', monospace",
          fontSize: 10.5, color: "var(--hg-fg-3)", textAlign: "center",
        }}>HmLabTab not loaded</div>
      );
    }
    const _supervisorUrl = (() => {
      const base = metricsBase || metricsBaseFromEndpoint(endpoint);
      return supervisorBaseFromEndpoint(base, endpoint);
    })();
    const _stackToken =
      (typeof window !== "undefined" && window.__STACK_TOKEN) ||
      (typeof localStorage !== "undefined" && localStorage.getItem("hg-stack-token-DEV")) ||
      "";
    return (
      <window.HmLabTab
        metrics={metrics}
        metricsHistory={metricsHistory}
        aiStackOnline={aiStackOnline}
        aiStackState={aiStackState}
        lastTrace={lastTrace}
        traceSummary={traceSummary}
        labTurns={labTurnsRef.current}
        labSamples={labSamplesRef.current}
        labTick={labTick}
        supervisorUrl={_supervisorUrl}
        stackToken={_stackToken}
        tokenConfigured={!!_stackToken}
        simActive={simActive}
        diagOpen={false}
        setDiagOpen={() => {}}
      />
    );
  };

  // Tray v6: renderNetwork removed entirely (see tabs[] comment above).
  // UniFi data still flows in via `networkMetrics` and is available in
  // the diagnostics modal for power-users. The function below is kept
  // disabled-by-default purely for the diag modal's reuse, but its
  // output isn't wired to any tab. Safe to delete in a later pass.
  // eslint-disable-next-line no-unused-vars
  const _renderNetwork_unused = () => {
    const allDevices = [
      ...(networkMetrics?.udm ? [{ ...networkMetrics.udm, name: "cloud gateway · UDM", isUdm: true }] : []),
      ...(networkMetrics?.switches || []),
    ];
    const worstMem = allDevices.length > 0
      ? Math.max(...allDevices.map((d) => d.mem || 0))
      : null;
    const worstMemTone = worstMem == null ? "idle"
      : worstMem > 90 ? "crit"
      : worstMem > 70 ? "warn"
      : "ok";

    // Reachability items — same plain-language vocabulary as the AI tab's dep strip
    const reachItems = [];
    if (sidecarOnline === true) reachItems.push({ label: "ai box", state: "online", tone: "ok" });
    else if (sidecarOnline === false) reachItems.push({ label: "ai box", state: "offline", tone: "crit" });
    else reachItems.push({ label: "ai box", state: "unknown", tone: "idle" });
    const haUp = connection === "online";
    if (haUp && bridgeHealth?.ha_connected) reachItems.push({ label: "haos", state: "connected", tone: "ok" });
    else if (haUp || bridgeHealth?.ha_connected) reachItems.push({ label: "haos", state: "degraded", tone: "warn" });
    else reachItems.push({ label: "haos", state: "offline", tone: "crit" });
    if (bridgeOnline === true) reachItems.push({ label: "bridge", state: "online", tone: "ok" });
    else if (bridgeOnline === false) reachItems.push({ label: "bridge", state: "offline", tone: "crit" });
    else reachItems.push({ label: "bridge", state: "unknown", tone: "idle" });

    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {/* Network Health — single compact card */}
        <HmCard title="NETWORK HEALTH"
                badge={worstMemTone === "warn" || worstMemTone === "crit"
                  ? { text: "watch", tone: worstMemTone }
                  : { text: "healthy", tone: "ok" }}>
          <HmDepHealthStrip items={reachItems} />
          <div style={{ marginTop: 10 }}>
            {networkMetrics?.clientsKnown > 0 && (
              <HmStatRow
                label="clients"
                value={`${networkMetrics.clientsOnline} / ${networkMetrics.clientsKnown}`}
                tone={networkMetrics.clientsOnline < networkMetrics.clientsKnown ? "warn" : "default"}
              />
            )}
            {worstMem != null && (
              <HmStatRow
                label="worst device mem"
                value={`${Math.round(worstMem)}%`}
                tone={worstMemTone === "warn" || worstMemTone === "crit" ? worstMemTone : "default"}
              />
            )}
            {allDevices.length === 0 && networkMetrics?.clientsKnown == null && (
              <div style={{
                fontFamily: "'Geist Mono', monospace",
                fontSize: 10.5, color: "var(--hg-fg-5)",
                marginTop: 4,
              }}>no unifi telemetry</div>
            )}
          </div>
        </HmCard>

        {/* UniFi diagnostics — collapsed by default. Background data. */}
        {allDevices.length > 0 ? (
          <details style={{
            background: "var(--hg-bg-1)",
            border: "1px solid var(--hg-border-soft)",
            borderRadius: 4,
            padding: "10px 12px",
          }}>
            <summary style={{
              cursor: "pointer",
              fontFamily: "'Geist Mono', monospace",
              fontSize: 10, letterSpacing: "0.16em",
              textTransform: "uppercase",
              color: "var(--hg-fg-4)",
              outline: "none",
              fontWeight: 400,
            }}>unifi diagnostics · {allDevices.length} devices</summary>
            <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 10 }}>
              {allDevices.map((d) => (
                <HmCard
                  key={d.name}
                  title={d.name.toUpperCase()}
                  badge={d.state && d.state !== "connected"
                    ? { text: d.state, tone: "warn" }
                    : { text: "connected", tone: "ice" }}
                >
                  <div style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: 14,
                  }}>
                    <HmMetricCard label="cpu" value={d.cpu} unit="%" max={100} viz="bar" />
                    <HmMetricCard label="ram" value={d.mem} unit="%" max={100} viz="bar" />
                  </div>
                </HmCard>
              ))}
            </div>
          </details>
        ) : (
          <HmCard title="UNIFI">
            <HmEmptyState
              message="no unifi devices detected"
              action={{
                label: "how",
                tooltip: "HA → Settings → Devices & Services → + Add Integration → Unifi Network.\nGrant API access to your UDM / switches / APs.",
              }} />
          </HmCard>
        )}
      </div>
    );
  };

  const trayViewportHeight =
    typeof window !== "undefined"
      ? Math.round((window.visualViewport && window.visualViewport.height) || window.innerHeight || 760)
      : 760;
  const mobileTrayCap = Math.max(220, Math.min(320, Math.floor(trayViewportHeight * 0.36)));
  const trayBodyMaxHeight = dockMode
    ? Math.min(trayHeight, 124)
    : mobile
      ? Math.min(trayHeight, mobileTrayCap)
      : trayHeight;

  return (
    <div style={{
      borderTop: dockMode ? "0" : "1px solid var(--hg-border-soft)",
      background: dockMode ? "transparent" : "var(--hg-bg-0)",
      fontFeatureSettings: '"tnum"',
    }}>
      {/* DRAG HANDLE — only visible when tray expanded. Sits on the
          border between chat feed and tray; drag UP to grow the tray,
          DOWN to shrink. Width-100%, 6px tall, ns-resize cursor on
          hover so the affordance is discoverable. */}
      {expanded && (
        <div
          onPointerDown={onTrayHandlePointerDown}
          onPointerMove={onTrayHandlePointerMove}
          onPointerUp={onTrayHandlePointerUp}
          onPointerCancel={onTrayHandlePointerUp}
          title="Drag to resize"
          aria-label="Resize metrics tray"
          role="separator"
          style={{
            height: 6,
            width: "100%",
            cursor: "ns-resize",
            background: "transparent",
            position: "relative",
            touchAction: "none",
            zIndex: 2,
          }}
        >
          {/* Subtle 1px line that brightens on hover for visual feedback */}
          <div style={{
            position: "absolute", left: "50%", top: 2,
            width: 40, height: 2,
            transform: "translateX(-50%)",
            background: "var(--hg-fg-4)",
            borderRadius: 1,
            opacity: 0.5,
            pointerEvents: "none",
          }} />
        </div>
      )}
      {/* SUMMARY STRIP — always visible, ~34px */}
      <button
        onClick={() => setExpanded((x) => !x)}
        className="hg-focusable"
        style={{
          width: "100%",
          padding: mobile ? "6px 10px" : dockMode ? "7px 12px" : "8px 14px",
          background: "transparent",
          border: "none",
          textAlign: "left",
          cursor: "pointer",
          display: "flex", alignItems: "center", gap: mobile ? 8 : dockMode ? 10 : 14,
          fontFamily: "'Geist Mono', monospace",
          fontSize: mobile ? 10 : 10.5,
          color: "var(--hg-fg-3)",
          userSelect: "none",
          minHeight: mobile ? 32 : dockMode ? 32 : 34,
          overflow: "hidden",
        }}
      >
        {/* AI status group — v6.2: during transient voice states (listening,
            speaking, transcribing, thinking) we render the LiveWaveform +
            state word INLINE here. The standalone VoiceBanner is hidden in
            that case so there's just one persistent strip above the input
            row. Idle / ready / warming still show the plain phrase. */}
        <span style={{
          display: "inline-flex",
          alignItems: "center",
          gap: mobile ? 6 : 8,
          minWidth: 0,
          flex: mobile ? "1 1 auto" : "0 1 auto",
          overflow: "hidden",
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: 999,
            background: aiIsTransient ? "var(--hg-ice-bright)" : "var(--hg-fg-3)",
            animation: aiIsTransient ? "hg-pulse-dot 1.4s ease-in-out infinite" : "none",
          }} />
          {aiIsTransient && (voiceState === "listening" || voiceState === "speaking") ? (
            <>
              <LiveWaveform
                source={voiceState === "speaking" ? "player" : "mic"}
                bars={14} height={8}
              />
              <span style={{ color: "var(--hg-ice)", fontWeight: 600 }}>{voiceState}…</span>
            </>
          ) : aiIsTransient ? (
            <span style={{ color: "var(--hg-ice)", fontWeight: 600 }}>{voiceState}…</span>
          ) : (
            <span style={{ color: "var(--hg-fg-1)", fontWeight: 600 }}>{aiPhrase}</span>
          )}
        </span>

        {/* Last activity */}
        {lastActivity && lastActivityAge != null && lastActivityAge < 600 && (
          <span style={{ display: mobile ? "none" : "inline-flex", gap: 4, color: "var(--hg-fg-3)", alignItems: "baseline" }}>
            <span style={{ color: "var(--hg-fg-4)" }}>·</span>
            <span style={{ color: "var(--hg-fg-2)" }}>{lastActivity.name}</span>
            <span style={{ color: "var(--hg-fg-4)" }}>in</span>
            <span style={{ color: "var(--hg-fg-2)" }}>{lastActivity.room}</span>
            <span style={{ color: "var(--hg-fg-5)" }}>
              · {lastActivityAge < 60 ? `${lastActivityAge}s` : `${Math.round(lastActivityAge / 60)}m`}
            </span>
          </span>
        )}

        {/* Warn chip */}
        {warnChip && (
          <span style={{
            color: warnChip.tone === "crit" ? "var(--hg-crit)" : "var(--hg-warn)",
            fontSize: mobile ? 9 : 9.5,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            border: `1px solid ${warnChip.tone === "crit" ? "var(--hg-crit)" : "var(--hg-warn)"}`,
            padding: "1px 6px",
            borderRadius: 3,
            fontWeight: 400,
          }}>{warnChip.text}</span>
        )}

        {/* Right cluster — minimalist headline metrics visible WITHOUT
            expanding the tray. Replaces the redundant `ha` dot (HA health
            already surfaces in the header warn pill).
            Shows whichever of these are available right now:
              · last voice turn total ms — most-recent latency
              · ttft (live LLM warmth signal)
              · vram % — memory pressure
              · gpu %  — only when actively spiking (>5%)
            All three feed into the chevron for a glanceable status row. */}
        <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: dockMode ? 10 : 14 }}>
          {!mobile && lastTrace?.t_done > 0 && (() => {
            const ms = Math.round(lastTrace.t_done);
            const sec = (ms / 1000).toFixed(ms >= 10000 ? 0 : 1);
            const tone = ms > 2500 ? "var(--hg-warn)" : "var(--hg-fg-1)";
            return (
              <span style={{ color: "var(--hg-fg-5)", display: "inline-flex", gap: 4 }}>
                <span>last</span>
                <span style={{ color: tone, fontWeight: 600 }}>{sec}s</span>
              </span>
            );
          })()}
          {!mobile && metrics.ttft != null && metrics.ttft > 0 && (
            <span style={{ color: "var(--hg-fg-5)", display: "inline-flex", gap: 4 }}>
              <span>ttft</span>
              <span style={{
                color: metrics.ttft > 200 ? "var(--hg-warn)" : "var(--hg-fg-1)",
                fontWeight: 600,
              }}>{Math.round(metrics.ttft)}</span>
              <span>ms</span>
            </span>
          )}
          {metrics.vram != null && metrics.vramMax && (() => {
            const pct = (metrics.vram / metrics.vramMax) * 100;
            const tone = pct > 90 ? "var(--hg-crit)" : pct > 75 ? "var(--hg-warn)" : "var(--hg-fg-1)";
            return (
              <span style={{ color: "var(--hg-fg-5)", display: "inline-flex", gap: 4 }}>
                {!mobile && <span>vram</span>}
                <span style={{ color: tone, fontWeight: 600 }}>{Math.round(pct)}%</span>
              </span>
            );
          })()}
          {!mobile && metrics.gpu != null && metrics.gpu >= 5 && (
            <span style={{ color: "var(--hg-fg-5)", display: "inline-flex", gap: 4 }}>
              <span>gpu</span>
              <span style={{
                color: metrics.gpu > 90 ? "var(--hg-crit)"
                     : metrics.gpu > 70 ? "var(--hg-warn)"
                     : "var(--hg-fg-1)",
                fontWeight: 600,
              }}>{Math.round(metrics.gpu)}%</span>
            </span>
          )}
          {/* F-19 (Addendum 27 P0): GPU temp pill. NVML temp from sidecar
              /metrics. Thresholds match the Lab tab's THRESHOLDS.temp
              (warn 78°C, crit 90°C — Addendum 12 calibration). Rendered
              only when the sidecar exposes gpu_temp_c. */}
          {!mobile && metrics.tempC != null && (
            <span style={{ color: "var(--hg-fg-5)", display: "inline-flex", gap: 4 }}>
              <span>temp</span>
              <span style={{
                color: metrics.tempC >= 90 ? "var(--hg-crit)"
                     : metrics.tempC >= 78 ? "var(--hg-warn)"
                     : "var(--hg-fg-1)",
                fontWeight: 600,
              }}>{Math.round(metrics.tempC)}°c</span>
            </span>
          )}
          <span style={{
            color: "var(--hg-fg-4)",
            transition: "transform 220ms cubic-bezier(.4,0,.2,1)",
            transform: expanded ? "rotate(180deg)" : "rotate(0deg)",
            display: "inline-flex", alignItems: "center", justifyContent: "center", width: 14, height: 14,
            marginLeft: 4,
          }}>
            <svg width="9" height="6" viewBox="0 0 9 6" fill="none">
              <path d="M1 1L4.5 4.5L8 1" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
        </span>
      </button>

      {expanded && (
        <div style={{ animation: "hg-fade-up 220ms cubic-bezier(.4,0,.2,1)" }}>
          <HmTabs
            value={activeTab}
            onChange={setActiveTab}
            tabs={tabs}
            compact={dockMode}
            extra={
              <button
                onClick={() => setDiagOpen(true)}
                className="hg-focusable"
                title="diagnostics"
                style={{
                  background: "transparent", border: "1px solid var(--hg-border-soft)",
                  padding: "4px 8px",
                  cursor: "pointer",
                  color: "var(--hg-fg-3)",
                  fontFamily: "'Geist Mono', monospace",
                  fontSize: 11,
                  borderRadius: 4,
                  transition: "border-color 200ms, color 200ms",
                }}
              >diagnostics</button>
            }
          />
          <HmTrayBody maxHeight={trayBodyMaxHeight} compact={dockMode}>
            <div key={activeTab} style={{ animation: "hg-fade-up 160ms ease-out" }}>
              {activeTab === "ai"      && renderAi()}
              {activeTab === "infra"   && renderInfra()}
              {activeTab === "trace"   && renderLab()}
            </div>
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

function ServiceProfileInline({
  profile,
  onProfileChange,
  onOpenRemotePanel,
  onRemoteCheck,
  probeResults,
  running = false,
  compact = false,
}) {
  if (!window.HomeServices) return null;
  const profiles = window.HomeServices.profiles();
  const active = profile || window.HomeServices.getProfile();
  const results = Array.isArray(probeResults) ? probeResults : [];
  const failures = results.filter((r) => !r.ok).length;
  const summary = results.length
    ? `${results.length - failures}/${results.length} reachable`
    : "not checked";
  const btnBase = {
    border: "1px solid var(--hg-border)",
    background: "transparent",
    borderRadius: 4,
    padding: compact ? "5px 8px" : "6px 10px",
    minHeight: compact ? 40 : "auto",
    cursor: "pointer",
    fontFamily: "'Geist Mono', monospace",
    fontSize: compact ? 9 : 10,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    flex: compact ? "1 1 96px" : "0 0 auto",
  };
  return (
    <div style={{
      marginBottom: compact ? 18 : 24,
      borderTop: "1px solid var(--hg-border-soft)",
      borderBottom: "1px solid var(--hg-border-soft)",
      padding: compact ? "12px 0" : "14px 0",
      display: "flex",
      flexDirection: "column",
      gap: 10,
    }}>
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 10,
        fontFamily: "'Geist Mono', monospace",
        fontSize: compact ? 9.5 : 10.5,
        color: "var(--hg-fg-4)",
        letterSpacing: "0.12em",
        textTransform: "uppercase",
      }}>
        <span>connection profile</span>
        <span style={{ color: failures ? "var(--hg-warn)" : "var(--hg-fg-3)" }}>
          {running ? "checking" : summary}
        </span>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {profiles.map((p) => {
          const selected = active.id === p.id;
          return (
            <button
              key={p.id}
              type="button"
              className="hg-focusable"
              aria-label={p.label}
              title={p.label}
              onClick={() => onProfileChange?.(p.id)}
              style={{
                ...btnBase,
                borderColor: selected ? "var(--hg-ice)" : "var(--hg-border)",
                background: selected ? "rgba(138,190,255,0.08)" : "transparent",
                color: selected ? "var(--hg-fg-0)" : "var(--hg-fg-3)",
              }}
            >
              {p.shortLabel}
            </button>
          );
        })}
        <button
          type="button"
          className="hg-focusable"
          onClick={() => onRemoteCheck?.()}
          disabled={running}
          style={{ ...btnBase, marginLeft: compact ? 0 : "auto", color: running ? "var(--hg-fg-5)" : "var(--hg-fg-2)", cursor: running ? "default" : "pointer" }}
        >
          test
        </button>
        <button
          type="button"
          className="hg-focusable"
          onClick={() => onOpenRemotePanel?.()}
          style={{ ...btnBase, color: "var(--hg-fg-2)" }}
        >
          details
        </button>
      </div>
    </div>
  );
}

function RemoteProfileDialog({
  open,
  profile,
  probeResults,
  readiness,
  running,
  onClose,
  onProfileChange,
  onRemoteCheck,
  onDebugBundle,
  onCopyRecovery,
  onCopyServiceUrls,
  mobile = false,
}) {
  const [customValues, setCustomValues] = useState({});
  useEffect(() => {
    if (!open || !window.HomeServices) return;
    setCustomValues(window.HomeServices.getAll());
  }, [open, profile?.id]);
  if (!open || !window.HomeServices) return null;
  const profiles = window.HomeServices.profiles();
  const active = profile || window.HomeServices.getProfile();
  const services = window.HomeServices.services;
  const meta = window.HomeServices.meta;
  const urls = window.HomeServices.getAll();
  const resultsByService = new Map((probeResults || []).map((r) => [r.service, r]));
  const build = window.HomeServices.buildInfo();
  const travel = readiness?.profile?.id === active.id
    ? readiness
    : window.HomeServices.buildTravelReadiness(probeResults);
  const hostEntries = Object.values(travel.hosts || {})
    .filter((host) => host.total > 0)
    .sort((a, b) => {
      const order = { windows: 10, "ubuntu-ai": 20, "home-assistant": 30, "browser-gateway": 40 };
      return (order[a.id] || 99) - (order[b.id] || 99);
    });
  const statusColor = travel.status === "blocked" ? "var(--hg-crit)"
    : travel.status === "ready" ? "var(--hg-ice-bright)"
    : travel.status === "unknown" ? "var(--hg-fg-4)" : "var(--hg-warn)";
  const saveCustom = () => {
    try {
      window.HomeServices.setCustomServices(customValues);
      onProfileChange?.("custom");
    } catch (e) {
      console.warn("[services] custom save failed", e);
    }
  };
  const actionBtn = {
    border: "1px solid var(--hg-border)",
    background: "transparent",
    color: "var(--hg-fg-2)",
    borderRadius: 4,
    padding: mobile ? "8px 9px" : "7px 10px",
    cursor: "pointer",
    fontFamily: "'Geist Mono', monospace",
    fontSize: mobile ? 9.5 : 10,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    minHeight: mobile ? 36 : "auto",
    boxSizing: "border-box",
  };
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Remote profile"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 120,
        background: "rgba(0,0,0,0.54)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: mobile ? 0 : 18,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose?.(); }}
    >
      <div style={{
        width: mobile ? "100vw" : "min(760px, 96vw)",
        height: mobile ? "100dvh" : "auto",
        maxHeight: mobile ? "100dvh" : "min(760px, 92vh)",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        background: "var(--hg-bg-0)",
        border: "1px solid var(--hg-border)",
        borderRadius: mobile ? 0 : 7,
        boxShadow: "0 22px 80px rgba(0,0,0,0.48)",
      }}>
        <div style={{
          padding: mobile
            ? "calc(11px + env(safe-area-inset-top, 0px)) 12px 10px"
            : "18px 20px 14px",
          borderBottom: "1px solid var(--hg-border-soft)",
          display: "flex",
          alignItems: "flex-start",
          gap: 14,
        }}>
          <div style={{ flex: 1 }}>
            <div style={{
              fontFamily: "'Geist', system-ui, sans-serif",
              fontSize: mobile ? 16 : 19,
              fontWeight: 500,
              color: "var(--hg-fg-0)",
              marginBottom: 4,
            }}>Remote access / Travel readiness</div>
            <div style={{
              fontFamily: "'Geist Mono', monospace",
              fontSize: mobile ? 9.5 : 10,
              color: "var(--hg-fg-4)",
              letterSpacing: "0.08em",
            }}>
              {active.label} - v{build.version} - {build.commit} - travel {travel.status}
            </div>
          </div>
          <button type="button" onClick={onClose} className="hg-focusable" style={{
            background: "transparent",
            border: "none",
            color: "var(--hg-fg-3)",
            cursor: "pointer",
            fontFamily: "'Geist Mono', monospace",
            fontSize: 18,
            minWidth: mobile ? 44 : "auto",
            minHeight: mobile ? 44 : "auto",
            lineHeight: 1,
          }}>x</button>
        </div>

        <div style={{
          padding: mobile ? "9px 10px" : "14px 20px",
          borderBottom: "1px solid var(--hg-border-soft)",
          display: "flex",
          gap: mobile ? 6 : 8,
          alignItems: "center",
          flexWrap: "wrap",
        }}>
          {profiles.map((p) => (
            <button
              key={p.id}
              type="button"
              className="hg-focusable"
              onClick={() => onProfileChange?.(p.id)}
              style={{
                ...actionBtn,
                borderColor: active.id === p.id ? "var(--hg-ice)" : "var(--hg-border)",
                background: active.id === p.id ? "rgba(138,190,255,0.08)" : "transparent",
                color: active.id === p.id ? "var(--hg-fg-0)" : "var(--hg-fg-3)",
                flex: mobile ? "1 1 92px" : "0 0 auto",
              }}
            >
              {mobile ? (p.id === "tailscale" ? "tailscale" : p.label.toLowerCase()) : p.label}
            </button>
          ))}
          <button type="button" className="hg-focusable" onClick={() => onRemoteCheck?.()} disabled={running} style={{
            ...actionBtn,
            marginLeft: mobile ? 0 : "auto",
            flex: mobile ? "1 1 88px" : "0 0 auto",
            color: running ? "var(--hg-fg-5)" : "var(--hg-fg-2)",
            cursor: running ? "default" : "pointer",
          }}>{running ? "checking" : "test all"}</button>
          <button type="button" className="hg-focusable" onClick={() => onDebugBundle?.()} style={{ ...actionBtn, flex: mobile ? "1 1 88px" : "0 0 auto" }}>debug</button>
          <button type="button" className="hg-focusable" onClick={() => onCopyRecovery?.()} style={{ ...actionBtn, flex: mobile ? "1 1 88px" : "0 0 auto" }}>recovery</button>
          <button type="button" className="hg-focusable" onClick={() => onCopyServiceUrls?.()} style={{ ...actionBtn, flex: mobile ? "1 1 88px" : "0 0 auto" }}>urls</button>
        </div>

        <div className="hg-scroll hg-mobile-scroll" style={{
          overflowY: "auto",
          padding: mobile ? "10px 12px calc(18px + env(safe-area-inset-bottom, 0px))" : "12px 20px 18px",
        }}>
          <div style={{
            border: `1px solid ${statusColor}`,
            background: travel.status === "ready" ? "rgba(138,190,255,0.06)" : "rgba(255,184,77,0.05)",
            borderRadius: 6,
            padding: "10px 12px",
            marginBottom: 14,
            display: "grid",
            gridTemplateColumns: mobile ? "1fr" : "minmax(120px, 1fr) minmax(120px, 1fr) minmax(120px, 1fr)",
            gap: 10,
            fontFamily: "'Geist Mono', monospace",
            fontSize: 10,
            color: "var(--hg-fg-3)",
          }}>
            <div><span style={{ color: statusColor, textTransform: "uppercase" }}>{travel.status}</span></div>
            <div>{travel.counts.reachable}/{travel.counts.total} reachable</div>
            <div>{travel.checkedAt || "not checked"}</div>
          </div>

          {hostEntries.map((host) => (
            <div key={host.id} style={{ marginBottom: 16 }}>
              <div style={{
                display: "flex",
                alignItems: "baseline",
                justifyContent: "space-between",
                gap: 12,
                marginBottom: 6,
                fontFamily: "'Geist Mono', monospace",
                fontSize: 10,
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                color: "var(--hg-fg-4)",
              }}>
                <span>{host.label}</span>
                <span style={{
                  color: host.status === "blocked" ? "var(--hg-crit)"
                    : host.status === "ready" ? "var(--hg-ice-bright)"
                    : host.status === "unknown" ? "var(--hg-fg-5)" : "var(--hg-warn)",
                }}>{host.status} - {host.ok}/{host.total}</span>
              </div>
              <div style={{
                display: "grid",
                gridTemplateColumns: mobile ? "minmax(0, 1fr)" : "128px minmax(260px, 1fr) 92px",
                columnGap: 10,
                rowGap: mobile ? 5 : 1,
                fontFamily: "'Geist Mono', monospace",
                fontSize: 10,
              }}>
                {(host.services || []).map((item) => {
                  const service = item.service;
                  const result = resultsByService.get(service);
                  const ok = result?.ok || item.ok;
                  const statusText = result
                    ? ok ? `${result.ms}ms` : (result.error || `HTTP ${result.status || "?"}`)
                    : (item.status === "ready" ? "ready" : "not checked");
                  const failed = result && !ok;
                  return (
                    <React.Fragment key={`${host.id}-${service}`}>
                      <div style={{
                        padding: mobile ? "10px 0 0" : "9px 0",
                        color: "var(--hg-fg-3)",
                        borderBottom: mobile ? "none" : "1px solid var(--hg-border-soft)",
                      }}>{meta[service]?.label || item.label || service}</div>
                      <div style={{
                        padding: mobile ? "2px 0 0" : "7px 0",
                        borderBottom: "1px solid var(--hg-border-soft)",
                        minWidth: 0,
                      }}>
                        {active.id === "custom" && services.includes(service) ? (
                          <input
                            value={customValues[service] || ""}
                            onChange={(e) => setCustomValues((cur) => ({ ...cur, [service]: e.target.value }))}
                            style={{
                              width: "100%",
                              boxSizing: "border-box",
                              background: "transparent",
                              border: "1px solid var(--hg-border-soft)",
                              borderRadius: 4,
                              color: "var(--hg-fg-1)",
                              padding: "5px 7px",
                              fontFamily: "'Geist Mono', monospace",
                              fontSize: mobile ? 16 : 10,
                            }}
                          />
                        ) : (
                          <span style={{
                            display: "block",
                            color: "var(--hg-fg-1)",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}>{urls[service] || item.url}</span>
                        )}
                        {failed && (
                          <div style={{
                            marginTop: 5,
                            color: "var(--hg-fg-4)",
                            whiteSpace: "normal",
                            lineHeight: 1.4,
                          }}>
                            {item.recoveryHint}
                            {item.diagnosticCommand && (
                              <div style={{ color: "var(--hg-fg-5)", marginTop: 3 }}>{item.diagnosticCommand}</div>
                            )}
                          </div>
                        )}
                      </div>
                      <div style={{
                        padding: mobile ? "0 0 10px" : "9px 0",
                        textAlign: mobile ? "left" : "right",
                        color: result ? (ok ? "var(--hg-ice-bright)" : "var(--hg-warn)") : "var(--hg-fg-5)",
                        borderBottom: "1px solid var(--hg-border-soft)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}>{statusText}</div>
                    </React.Fragment>
                  );
                })}
              </div>
            </div>
          ))}

          {(travel.risks || []).length > 0 && (
            <div style={{
              borderTop: "1px solid var(--hg-border-soft)",
              paddingTop: 10,
              marginTop: 2,
              fontFamily: "'Geist Mono', monospace",
              fontSize: 10,
              color: "var(--hg-fg-4)",
              lineHeight: 1.45,
            }}>
              {(travel.risks || []).map((item, idx) => (
                <div key={`${item.title}-${idx}`} style={{ marginBottom: 6 }}>
                  <span style={{ color: item.severity === "blocker" ? "var(--hg-crit)" : "var(--hg-warn)" }}>{item.title}</span>
                  <span> - {item.detail}</span>
                </div>
              ))}
            </div>
          )}

          {active.id === "custom" && (
            <button type="button" className="hg-focusable" onClick={saveCustom} style={{
              marginTop: 14,
              border: "1px solid var(--hg-ice)",
              background: "rgba(138,190,255,0.08)",
              color: "var(--hg-fg-0)",
              borderRadius: 4,
              padding: "8px 11px",
              cursor: "pointer",
              fontFamily: "'Geist Mono', monospace",
              fontSize: 10,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
            }}>save custom profile</button>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── First-run connection prompt ─────────────────────────────────────── */
function FirstRun({
  connection,
  endpoint,
  token,
  onConnect,
  availableModels,
  onPickModel,
  onSimulation = null,
  compact = false,
  serviceProfile = null,
  onProfileChange = null,
  onOpenRemotePanel = null,
  onRemoteCheck = null,
  serviceProbeResults = null,
  serviceProbeRunning = false,
}) {
  const [url, setUrl] = useState(endpoint || webDefaultBase("HG_DEFAULT_HA_BASE") || "http://192.168.0.125:8123");
  // Start the token field empty when the last connection failed — a rejected
  // token shouldn't be pre-filled, or the user just retries the dead token.
  const [tok, setTok] = useState(
    (connection === "offline" || connection === "auth_invalid") ? "" : (token || "")
  );
  const isConnecting   = connection === "connecting";
  const isOffline      = connection === "offline";
  const isAuthInvalid  = connection === "auth_invalid";
  const isPicking      = connection === "picking-model";

  useEffect(() => {
    const next = endpoint || webDefaultBase("HG_DEFAULT_HA_BASE") || "http://192.168.0.125:8123";
    setUrl(next);
  }, [endpoint, serviceProfile?.id]);

  if (isPicking) {
    return (
      <div style={{
        minHeight: "100%", display: "flex", flexDirection: "column",
        justifyContent: compact ? "flex-start" : "center",
        padding: compact ? "24px 22px 36px" : "48px 28px 56px",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 28 }}>
          <StatusDot tone="live" size={5} />
          <span style={{ fontFamily: "'Geist Mono', monospace", fontSize: 10, letterSpacing: "0.16em", color: "var(--hg-fg-3)" }}>
            connected · {endpoint}
          </span>
        </div>
        <div style={{ fontFamily: "'Geist', system-ui, sans-serif", fontSize: compact ? 18 : 22, fontWeight: 500, color: "var(--hg-fg-0)", letterSpacing: "-0.02em", lineHeight: 1.2, marginBottom: 6 }}>
          Choose a model.
        </div>
        <div style={{ fontFamily: "'Geist', system-ui, sans-serif", fontSize: compact ? 12.5 : 13.5, color: "var(--hg-fg-3)", lineHeight: 1.55, marginBottom: 24 }}>
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
    isOffline     ? "check the url and token. in browser mode, also make sure home assistant allows this local origin" :
                    "use a home assistant long-lived access token. desktop mode can use the same token without browser cors";

  const borderTone = (isOffline || isAuthInvalid) ? "var(--hg-warn)" : "var(--hg-border)";

  return (
    <div style={{
      minHeight: "100%", display: "flex", flexDirection: "column",
      justifyContent: compact ? "flex-start" : "center",
      padding: compact ? "40px 22px 34px" : "48px 28px 56px",
    }}>
      {compact && (
        <div style={{
          display: "flex", alignItems: "center", gap: 8,
          marginBottom: 18,
          fontFamily: "'Geist Mono', monospace",
          fontSize: 9.5,
          letterSpacing: "0.16em",
          color: "var(--hg-fg-4)",
          textTransform: "uppercase",
        }}>
          <StatusDot tone={(isOffline || isAuthInvalid) ? "warn" : "idle"} size={5} />
          setup
        </div>
      )}
      <div style={{ fontFamily: "'Geist', system-ui, sans-serif", fontSize: compact ? 18 : 22, fontWeight: 500, color: "var(--hg-fg-0)", letterSpacing: "-0.02em", lineHeight: 1.2, marginBottom: 6 }}>
        {headline}
      </div>
      <div style={{ fontFamily: "'Geist', system-ui, sans-serif", fontSize: compact ? 12.5 : 13.5, color: "var(--hg-fg-3)", lineHeight: 1.55, marginBottom: compact ? 22 : 28 }}>
        {subhead}
      </div>
      <ServiceProfileInline
        profile={serviceProfile}
        onProfileChange={onProfileChange}
        onOpenRemotePanel={onOpenRemotePanel}
        onRemoteCheck={onRemoteCheck}
        probeResults={serviceProbeResults}
        running={serviceProbeRunning}
        compact={compact}
      />
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
              placeholder="paste token"
              type="password"
              autoCapitalize="off" autoCorrect="off" spellCheck={false}
              style={{
                flex: 1, background: "transparent", border: "none", outline: "none",
                fontFamily: "'Geist Mono', monospace", fontSize: 13,
                color: "var(--hg-fg-0)", caretColor: "var(--hg-fg-0)",
              }}
            />
            <button type="submit" disabled={isConnecting || !url || !tok} className="hg-focusable" style={{
              background: "transparent", border: "none", padding: "7px 8px",
              minHeight: compact ? 34 : 32,
              boxSizing: "border-box",
              cursor: (isConnecting || !url || !tok) ? "default" : "pointer",
              color: (isConnecting || !url || !tok) ? "var(--hg-fg-4)" : "var(--hg-fg-1)",
              fontFamily: "'Geist Mono', monospace", fontSize: 9.5,
              letterSpacing: "0.16em",
            }}>{isConnecting ? "connecting…" : (isOffline || isAuthInvalid) ? "retry ↵" : "connect ↵"}</button>
          </div>
        </div>
      </form>
      <div style={{
        marginTop: compact ? 20 : 28,
        paddingTop: compact ? 14 : 16,
        borderTop: "1px solid var(--hg-border-soft)",
        display: "flex",
        flexDirection: "column",
        gap: compact ? 7 : 9,
        fontFamily: "'Geist Mono', monospace",
        fontSize: compact ? 9.5 : 10.5,
        color: "var(--hg-fg-4)",
        lineHeight: 1.55,
      }}>
        <div><span style={{ color: "var(--hg-fg-2)" }}>token</span> profile / security / long-lived access tokens</div>
        <div><span style={{ color: "var(--hg-fg-2)" }}>browser</span> allow http://127.0.0.1:5180 in HA cors if requests fail</div>
        <div><span style={{ color: "var(--hg-fg-2)" }}>desktop</span> tauri avoids browser cors and keeps the token in app storage</div>
        {onSimulation && (
          <button
            type="button"
            onClick={onSimulation}
            className="hg-focusable"
            style={{
              alignSelf: "flex-start",
              marginTop: 3,
              background: "transparent",
              border: "1px solid var(--hg-border)",
              borderRadius: 4,
              color: "var(--hg-fg-2)",
              cursor: "pointer",
              fontFamily: "'Geist Mono', monospace",
              fontSize: compact ? 9.5 : 10.5,
              letterSpacing: "0.12em",
              padding: compact ? "9px 10px" : "6px 9px",
              minHeight: compact ? 40 : "auto",
              textTransform: "uppercase",
            }}
          >
            try simulation
          </button>
        )}
      </div>
    </div>
  );
}

/* Boot wordmark + light-cone — hoisted to module scope so both the static
 * in-feed BootBanner and the animated BootSequence render the exact same art.
 * Verbatim from the original BootBanner; the escaping is load-bearing. */
const BOOT_CONE = [
    "               \u00b7               ",
    "             \u00b7   \u00b7             ",
    "           \u00b7       \u00b7           ",
    "         \u00b7           \u00b7         ",
    "       \u00b7               \u00b7       ",
    "     \u00b7                   \u00b7     ",
    "   \u00b7                       \u00b7   ",
    " \u00b7                           \u00b7 ",
  ];
const BOOT_ART = [
    "    __                          ",
    "   / /_   ____   ____ ___   ___ ",
    "  / __ \\ / __ \\ / __ `__ \\ / _ \\",
    " / / / // /_/ // / / / / //  __/",
    "/_/ /_/ \\____//_/ /_/ /_/ \\___/ ",
];

/* The exact condition under which FirstRun (vs the chat feed) is the visible
 * surface — extracted verbatim from HomeApp's render gate so the gate,
 * routeOnboarding, and the boot focus effect can never disagree. */
function isFirstRunVisible(connection, events) {
  return connection !== "online" && connection !== "reconnecting";
}

function BootBanner({ metrics }) {
  // Refined wordmark + EL signature + calm system signature.
  // Lives at the top of the feed; scrolls up as the conversation grows.
  // The ASCII art uses Geist Mono and a faint light-cone preamble — a quiet,
  // McCall-style projection of light onto the wordmark.
  const cone = BOOT_CONE;
  const art = BOOT_ART;
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

/* BootSequence — the animated boot logo. Plays on every launch — a calm,
 * unhurried type-in (~4s, skippable). Reuses BootBanner's exact styling so the
 * boot logo and the in-feed BootBanner are visually identical — this one just
 * types itself in. HomeApp owns the mode decision; this component is purely
 * presentational.
 *
 * Props: { mode, onComplete }
 *  - "animate"  — slow char-by-char reveal → sig fade → calm hold → onComplete()
 *  - "instant"  — full art immediately + a short calm hold → onComplete()
 *                 (the prefers-reduced-motion fallback)
 *  - "settling" — full art + a faint pulsing "connecting…" line; no onComplete
 *                 (HomeApp's settling effect owns the exit)
 */
function BootSequence({ mode, onComplete }) {
  // Boot timing — deliberately slow + relaxed. It plays every launch and it's
  // skippable (any key/click), so there's no rush. Tunable here.
  const STEP_CHARS = 2;          // chars revealed per tick
  const STEP_MS = 40;            // tick interval → ~3.3s to type the wordmark
  const ANIMATE_HOLD_MS = 900;   // calm hold after the sig fades in
  const INSTANT_HOLD_MS = 650;   // reduced-motion hold (no typing)
  const TOTAL = BOOT_ART.join("\n").length;
  const [revealed, setRevealed] = useState(mode === "animate" ? 0 : TOTAL);
  const [showSig, setShowSig] = useState(mode !== "animate");
  const doneRef = useRef(false);

  // Keep `onComplete` in a ref so `finish` is a STABLE callback. Without this,
  // if the parent passes an inline-arrow `onComplete` (new ref every render),
  // `finish` would change every parent render — the hold-timeout effects below
  // would re-run and clear+re-arm their setTimeout, so on a frequently
  // re-rendering parent the timeout never fires and the boot sequence hangs.
  const onCompleteRef = useRef(onComplete);
  useEffect(() => { onCompleteRef.current = onComplete; }, [onComplete]);

  const finish = useCallback(() => {
    if (doneRef.current) return;
    doneRef.current = true;
    const cb = onCompleteRef.current;
    if (cb) cb();
  }, []);

  // Char reveal — animate only. Cleared on unmount and on any mode change.
  useEffect(() => {
    if (mode !== "animate") return;
    const iv = setInterval(() => {
      setRevealed((r) => {
        if (r >= TOTAL) { clearInterval(iv); return r; }
        return Math.min(r + STEP_CHARS, TOTAL);
      });
    }, STEP_MS);
    return () => clearInterval(iv);
  }, [mode, TOTAL]);

  // Skip — any key/click jumps the reveal straight to the end (animate only).
  useEffect(() => {
    if (mode !== "animate") return;
    const skip = () => setRevealed(TOTAL);
    document.addEventListener("keydown", skip);
    document.addEventListener("click", skip);
    return () => {
      document.removeEventListener("keydown", skip);
      document.removeEventListener("click", skip);
    };
  }, [mode, TOTAL]);

  // Animate: once fully revealed → sig fades in, calm hold → onComplete().
  useEffect(() => {
    if (mode !== "animate" || revealed < TOTAL) return;
    setShowSig(true);
    const t = setTimeout(finish, ANIMATE_HOLD_MS);
    return () => clearTimeout(t);
  }, [mode, revealed, TOTAL, finish]);

  // Instant: a calm hold, then onComplete().
  useEffect(() => {
    if (mode !== "instant") return;
    const t = setTimeout(finish, INSTANT_HOLD_MS);
    return () => clearTimeout(t);
  }, [mode, finish]);

  // Map the global `revealed` count onto per-line reveal offsets. Each line is
  // always rendered full-width (revealed span + transparent unrevealed span)
  // so there's zero layout shift; the block cursor sits at the frontier.
  let offset = 0;
  const artLines = BOOT_ART.map((line) => {
    const start = offset;
    offset += line.length + 1; // +1 for the joining "\n"
    const n = Math.max(0, Math.min(line.length, revealed - start));
    const isCurrent = mode === "animate" && revealed >= start && n < line.length;
    return { line, n, isCurrent };
  });

  return (
    <div style={{
      flex: 1,
      display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center",
      background: "var(--hg-bg-0)",
      fontFamily: "'Geist Mono', monospace",
      animation: "hg-fade-up 600ms ease-out",
    }}>
      {/* Cone of light */}
      <div style={{
        whiteSpace: "pre",
        fontSize: 9, lineHeight: 1.2,
        color: "var(--hg-fg-5)",
        textAlign: "center",
        marginBottom: 4,
      }}>{BOOT_CONE.map((l, i) => (
        <div key={i} style={{ opacity: 0.20 + i * 0.07 }}>{l}</div>
      ))}</div>

      {/* Wordmark — typed in, zero layout shift */}
      <div style={{
        whiteSpace: "pre",
        fontSize: 11, lineHeight: 1.2,
        color: "var(--hg-fg-0)",
        textAlign: "center",
        fontWeight: 500,
      }}>{artLines.map(({ line, n, isCurrent }, i) => (
        <div key={i}>
          <span>{line.slice(0, n)}</span>
          {isCurrent && (
            <span style={{
              background: "var(--hg-fg-0)", color: "transparent",
              animation: "hg-caret-blink 1.05s steps(1) infinite",
            }}>{line.slice(n, n + 1) || " "}</span>
          )}
          <span style={{ color: "transparent" }}>
            {isCurrent ? line.slice(n + 1) : line.slice(n)}
          </span>
        </div>
      ))}</div>

      {/* Brand signature — fades in once the wordmark finishes */}
      <div style={{
        marginTop: 36,
        textAlign: "center",
        fontFamily: "'Geist Mono', monospace",
        fontSize: 10,
        letterSpacing: "0.28em",
        color: "var(--hg-fg-4)",
        opacity: showSig ? 1 : 0,
        transition: "opacity 450ms ease-out",
      }}>engineered lighting</div>

      {/* Settling — a faint, slowly-pulsing "connecting…" line */}
      {mode === "settling" && (
        <div style={{
          marginTop: 18,
          fontFamily: "'Geist Mono', monospace",
          fontSize: 9.5, letterSpacing: "0.18em",
          color: "var(--hg-fg-5)",
          animation: "hg-pulse-dot 1.6s ease-in-out infinite",
        }}>connecting…</div>
      )}
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
// Categories drive /help grouping + ordering. Each SLASH_CMDS entry
// carries a `category` field; the help renderer collects by category
// in the order defined here. Adding a new command? Pick the best-fit
// category from this list (or add a new one — keep the list small).
const SLASH_CMD_CATEGORIES = [
  { id: "connection", label: "connection",        desc: "set up + maintain the link to HA + the AI stack" },
  { id: "agent",      label: "ask the agent",      desc: "speak to the agent + steer routing" },
  { id: "vision",     label: "vision + cameras",   desc: "frigate, multi-frame describe, snapshots" },
  { id: "world",      label: "world + recap",      desc: "live state, recap, explainability" },
  { id: "voice",      label: "voice + audio",      desc: "TTS swap, mute, voice modes" },
  { id: "debug",      label: "debug + observability", desc: "search chat, see internals, route logs" },
  { id: "mode",       label: "modes + lifecycle",  desc: "simulation, demo, clear, proactive" },
  { id: "meta",       label: "meta",               desc: "list this list" },
];

const SLASH_CMDS = [
  // ── connection ────────────────────────────────────────────────
  { cmd: "/connect",  hint: "<url> [<token>]", desc: "connect to a Home Assistant endpoint", category: "connection" },
  { cmd: "/endpoint", hint: "<url>",   desc: "change endpoint url", category: "connection" },
  { cmd: "/token",    hint: "<token>", desc: "update the HA long-lived access token", category: "connection" },
  { cmd: "/stack-token", hint: "<token>", desc: "save STACK_TOKEN for AI stack supervisor (from /opt/home-ai-voice/.env)", category: "connection" },
  { cmd: "/metrics",  hint: "<url>",   desc: "set the metrics-sidecar base url", category: "connection" },
  { cmd: "/s2s",      hint: "<url> | token <hex> | voice <name>", desc: "configure s2s bridge — url, token, or per-session voice override", category: "connection" },
  { cmd: "/profile",  hint: "status|lan|tailscale|custom", desc: "switch or inspect the active service profile", category: "connection" },
  { cmd: "/remote",   hint: "check", desc: "test every configured Home service endpoint", category: "connection" },
  { cmd: "/travel",   hint: "check|status|recovery|bundle", desc: "check travel readiness and copy recovery/debug details", category: "connection" },
  // ── ask the agent ─────────────────────────────────────────────
  { cmd: "/ask",        hint: "<text>",     desc: "force external/general provider for this question (bypasses classifier)", category: "agent" },
  { cmd: "/local",      hint: "<text>",     desc: "force local home agent for this question (bypasses classifier)", category: "agent" },
  { cmd: "/route",      hint: "<text>",     desc: "show how the classifier would route this text (no dispatch)", category: "agent" },
  { cmd: "/external",   hint: "on|off|status|set-key", desc: "external provider config + auto-routing toggle", category: "agent" },
  { cmd: "/agent-tools", hint: "",            desc: "list every LLM tool the agent can call (grouped by capability)", category: "agent" },
  { cmd: "/model",    hint: "<name>",  desc: "switch active model", category: "agent" },
  // ── vision + cameras ──────────────────────────────────────────
  { cmd: "/home2",      hint: "on|off",     desc: "toggle the Home 2 spatial layout: real apartment primary, chat docked on the right", category: "vision" },
  { cmd: "/cameras",    hint: "",            desc: "show a thumbnail snapshot of every configured camera (from the M1 cache or live)", category: "vision" },
  { cmd: "/describe-clip", hint: "<camera> [frames] [interval_s]", desc: "watch a multi-frame clip from a camera and describe motion. e.g. /describe-clip kitchen, /describe-clip driveway 6 1.5", category: "vision" },
  { cmd: "/find-clips", hint: "<query>",    desc: "search Frigate clips via semantic-search CLIP (e.g. 'package on porch'). Returns recent matches with thumbnails.", category: "vision" },
  { cmd: "/spatial",    hint: "",           desc: "open the light-footprint map — Addendum 38 Phase 1 calibration review: which lights illuminate which space", category: "vision" },
  { cmd: "/apartment",  hint: "prewarm|status", desc: "open the 3d apartment, or warm/check its cached scan assets before opening. alias: /3d", category: "vision" },
  { cmd: "/labeler",    hint: "[base <url>]", desc: "open the video timeline labeler — review footage, edit segment labels. alias: /vl", category: "vision" },
  { cmd: "/look",       hint: "<camera> <question>", desc: "ask the vision model a spatial question about a camera — it reasons by 'pointing' (boxing what it sees), rendered as the paper's two-panel figure. e.g. /look kitchen what is on the counter", category: "vision" },
  // ── world + recap ─────────────────────────────────────────────
  { cmd: "/world-state",hint: "[<room>|--raw]", desc: "open world-state drawer. <room> pre-filters to one room (click × to clear). '--raw' dumps full JSON inline instead.", category: "world" },
  { cmd: "/recap",      hint: "[hours]",    desc: "summarize recent home activity. e.g. /recap (last 6h) · /recap 24 (today) · /recap 168 (past week)", category: "world" },
  { cmd: "/why",        hint: "[conv-id]",  desc: "open explainability drawer — routing decision + tool calls + final text for the most recent (or specified) conv_id", category: "world" },
  { cmd: "/why-light",  hint: "<zone>",     desc: "explain the last lighting decision for a zone — current classifier state, recent manual overrides (last 24h), and the last few state transitions from the shadow log. Pass no zone to list available zones.", category: "world" },
  { cmd: "/lights",     hint: "",           desc: "open the Living Lights tuning drawer — 'right now' status, quick warmth/brightness bias sliders, and grouped knobs for the cascade (ToD, working hours, asleep, gaming, movie, defaults). No long chat required.", category: "world" },
  // ── voice + audio ─────────────────────────────────────────────
  { cmd: "/mute",     hint: "[2h|30m|movie]", desc: "mute Jarvis (manual, or for a duration, or for a movie). 'Hey Jarvis, stop' works by voice.", category: "voice" },
  { cmd: "/unmute",   hint: "",        desc: "clear manual mute (TV-active + movie_mode auto-signals still apply)", category: "voice" },
  { cmd: "/voice",    hint: "<name>",  desc: "swap Kokoro TTS voice (e.g. am_eric, af_heart)", category: "voice" },
  { cmd: "/voices",   hint: "",        desc: "list popular voice names", category: "voice" },
  // ── debug + observability ─────────────────────────────────────
  { cmd: "/find",       hint: "<text>",     desc: "search past chat events for matching text", category: "debug" },
  { cmd: "/debug",    hint: "on|off|bundle",  desc: "show/hide diag events or export profile diagnostics", category: "debug" },
  { cmd: "/test",       hint: "classifier|external-privacy|external-suite", desc: "run built-in test suites and print a pass/fail summary", category: "debug" },
  { cmd: "/route-log",  hint: "[tail]",     desc: "dump recent external-routing decisions as JSONL. alias: /routes", category: "debug" },
  { cmd: "/lab-dump",       hint: "",       desc: "dump labSamplesRef + labTurnsRef + anchor stats as JSON (Addendum 32 evidence-gathering for line-graph flatness bug)", category: "debug" },
  { cmd: "/lab-dump-watch", hint: "<sec>|stop", desc: "auto-dump every N seconds for 12 iterations (timing-window evidence)", category: "debug" },
  // ── modes + lifecycle ─────────────────────────────────────────
  { cmd: "/simulation", hint: "[scenario | off]", desc: "enter design-review mode (no real services — mocked everything)", category: "mode" },
  { cmd: "/sim",        hint: "<scenario>", desc: "switch simulation scenario · /sim scenarios to list", category: "mode" },
  { cmd: "/proactive",  hint: "[mode on|off | test … | reset]", desc: "proactive-assistant status, mode overrides, and no-leave event tests", category: "mode" },
  { cmd: "/demo",     hint: "",        desc: "play the scripted demo conversation", category: "mode" },
  { cmd: "/clear",    hint: "",        desc: "clear the conversation", category: "mode" },
  // ── meta ──────────────────────────────────────────────────────
  { cmd: "/about",    hint: "",        desc: "show version + repo info", category: "meta" },
  { cmd: "/help",       hint: "",           desc: "list commands grouped by category (click any entry to fill the input)", category: "meta" },
];

function InputRow({ value, onChange, onSend, voice, onMicToggle, isStreaming, onStop, focusToken, mobile = false, theme = "dark" }) {
  const inputRef = useRef(null);
  const [sel, setSel] = useState(0);
  // HomeApp bumps `focusToken` to pull focus here (e.g. when the chat feed
  // becomes the live surface after boot). The `if (focusToken)` guard makes
  // the mount run (initial focusToken === 0) a no-op — InputRow is always
  // mounted, including behind the FirstRun form, so an unguarded focus()
  // would steal focus from FirstRun's endpoint field on every boot.
  useEffect(() => { if (focusToken) inputRef.current?.focus(); }, [focusToken]);
  const isSlash = value.startsWith("/");
  const firstTok = value.split(/\s+/)[0];
  const matches = isSlash ? SLASH_CMDS.filter((c) => c.cmd.startsWith(firstTok)) : [];
  const showMenu = isSlash && matches.length > 0 && !value.includes(" ");
  const spatialMode = !!(typeof window !== "undefined" && window.__HOME_SPATIAL_MODE);
  const lightTheme = theme === "light";
  const menuSurface = lightTheme
    ? "var(--hg-bg-3)"
    : spatialMode ? "rgba(5,7,11,0.98)" : "rgba(8,10,14,0.985)";
  const menuSelected = lightTheme
    ? "rgba(31,79,168,0.11)"
    : "rgba(184,216,255,0.08)";
  const menuShadow = lightTheme
    ? "0 18px 42px rgba(60,50,30,0.20), 0 0 0 1px rgba(26,24,18,0.04)"
    : spatialMode ? "0 14px 34px rgba(0,0,0,0.42)" : "0 18px 48px rgba(0,0,0,0.44)";
  const inputSurface = "var(--hg-bg-0)";
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
    <div style={{ position: "relative", flexShrink: 0 }}>
      {showMenu && (
        <div style={{
          position: "absolute", bottom: "100%", left: mobile ? 8 : spatialMode ? 10 : 12, right: mobile ? 8 : spatialMode ? 10 : 12,
          maxHeight: mobile ? "min(320px, 44dvh)" : spatialMode ? "min(260px, 34vh)" : "min(420px, 46vh)",
          overflowY: "auto",
          background: menuSurface,
          border: "1px solid var(--hg-border)",
          borderRadius: spatialMode ? 7 : 8,
          marginBottom: spatialMode ? 7 : 8,
          padding: spatialMode ? "4px 0" : "6px 0",
          fontFamily: "'Geist Mono', monospace", fontSize: mobile ? 12.5 : spatialMode ? 11 : 12,
          boxShadow: menuShadow,
          backdropFilter: lightTheme ? "none" : spatialMode ? "blur(14px)" : "blur(18px)",
          zIndex: 20,
          WebkitOverflowScrolling: "touch",
        }}>
          <div style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: spatialMode ? "4px 10px 6px" : "4px 12px 8px",
            color: "var(--hg-fg-4)",
            fontSize: 9,
            letterSpacing: "0.14em",
            textTransform: "uppercase",
          }}>
            <span>commands</span>
            <span>{matches.length}</span>
          </div>
          {matches.map((m, i) => (
            <div key={m.cmd}
              onMouseEnter={() => setSel(i)}
              onMouseDown={(e) => { e.preventDefault(); complete(m.cmd); }}
              style={{
                display: "flex", alignItems: "baseline", gap: 10,
                minHeight: mobile ? 44 : "auto",
                padding: mobile ? "9px 11px" : spatialMode ? "6px 10px" : "7px 12px",
                background: i === sel ? menuSelected : "transparent",
                cursor: "pointer",
              }}>
              <span style={{ color: i === sel ? "var(--hg-fg-0)" : "var(--hg-fg-1)", minWidth: spatialMode ? 74 : 88 }}>{m.cmd}</span>
              {m.hint && <span style={{ color: "var(--hg-fg-4)" }}>{m.hint}</span>}
              <span style={{
                color: "var(--hg-fg-3)",
                marginLeft: "auto",
                fontFamily: "'Geist', system-ui, sans-serif",
                fontSize: spatialMode ? 10.5 : 11.5,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                maxWidth: spatialMode ? 150 : "none",
              }}>{m.desc}</span>
            </div>
          ))}
        </div>
      )}
      <div style={{
          padding: mobile
            ? "7px 9px calc(8px + env(safe-area-inset-bottom, 0px))"
            : spatialMode ? "9px 12px 11px" : "10px 12px 12px",
        borderTop: "1px solid var(--hg-border)",
        background: inputSurface,
        display: "flex", alignItems: "center", gap: mobile ? 8 : 10,
        minHeight: mobile ? 54 : "auto",
      }}>
        <div style={{ flex: 1, display: "flex", alignItems: "center", gap: 8 }}>
          <input
            ref={inputRef}
            className="hg-focusable"
            aria-label="Command input"
            placeholder={isSlash ? "" : "type or /command"}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKey}
            style={{
              fontFamily: isSlash ? "'Geist Mono', monospace" : "'Geist', system-ui, sans-serif",
              fontSize: mobile ? 16 : isSlash ? 13 : 14,
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
                background: lightTheme ? "rgba(26,24,18,0.03)" : "transparent", border: "1px solid var(--hg-border)",
                padding: mobile ? "8px 10px" : "3px 8px", cursor: "pointer",
                color: "var(--hg-fg-2)",
                display: "inline-flex", alignItems: "center", gap: 6,
                fontFamily: "'Geist Mono', ui-monospace, monospace",
                fontSize: mobile ? 10 : 9.5, letterSpacing: "0.16em", textTransform: "uppercase",
                minHeight: mobile ? 40 : "auto",
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
                background: lightTheme ? "rgba(26,24,18,0.03)" : "transparent", border: mobile ? "1px solid var(--hg-border)" : "none", padding: mobile ? "8px 10px" : 0,
                color: "var(--hg-fg-2)", cursor: "pointer",
                display: "inline-flex", alignItems: "center", gap: 4,
                fontFamily: "'Geist Mono', ui-monospace, monospace",
                fontSize: mobile ? 10 : 9.5, letterSpacing: "0.16em", textTransform: "uppercase",
                minHeight: mobile ? 40 : "auto",
                borderRadius: mobile ? 6 : 0,
              }}
              onMouseEnter={(e) => e.currentTarget.style.color = "var(--hg-fg-0)"}
              onMouseLeave={(e) => e.currentTarget.style.color = "var(--hg-fg-2)"}
            >{isSlash ? "run" : "send"} <IconSend size={11} /></button>
          )}
        </div>
        {/* v6: inline "tap mic to end" hint next to the mic button while
            a voice session is active. The VoiceBanner used to carry this
            hint on the right edge, but moving it here collapses the two
            persistent strips into one. */}
        {(voice.state === "listening" || voice.state === "speaking") && (
          <span style={{
            fontFamily: "'Geist Mono', ui-monospace, monospace",
            fontSize: mobile ? 9 : 9.5,
            letterSpacing: "0.12em",
            color: "var(--hg-fg-4)",
            whiteSpace: "nowrap",
            display: mobile ? "none" : "inline",
          }}>tap mic to end</span>
        )}
        <MicButton state={voice.state} onClick={onMicToggle} mobile={mobile} />
      </div>
    </div>
  );
}

/* VoiceModeButton + the /s2s on|off slash command + the persistent
 * `s2sMode` toggle were retired in Phase 1.5. Voice mode is now
 * always-available; tap the mic icon to enter a voice session, tap
 * again to end it. Bridge default voice is Chatterbox-Gianna; swap
 * voices per-session with /voice <name>. */

function MicButton({ state, onClick, mobile = false }) {
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
        width: mobile ? 44 : 36, height: mobile ? 44 : 36,
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
  // v6.2: transient states (listening / speaking / transcribing /
  // thinking) are now displayed inline in the MetricsStrip summary so
  // there's only one persistent strip above the input row. VoiceBanner
  // only renders for error / no-mic / ready states that genuinely need
  // a dedicated callout.
  if (voice.state === "listening" || voice.state === "speaking"
      || voice.state === "transcribing" || voice.state === "thinking") {
    return null;
  }
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
  // v6: "tap mic to end" hint moved to InputRow next to the mic button.
  return (
    <div style={{ ...base, color: "var(--hg-ice)" }}>
      <LiveWaveform
        source={voice.state === "speaking" ? "player" : "mic"}
        bars={18} height={9}
      />
      <span>{voice.state}…</span>
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

const CLEARED_METRICS = { ...DEFAULT_METRICS, model: null };

function emptyMetricsHistory() {
  return { gpu: [], vram: [], cpu: [], ram: [], ttft: [], tps: [] };
}

function emptyNetworkMetrics() {
  return { udm: null, switches: [], clientsOnline: 0, clientsKnown: 0 };
}

function hasLiveModelName(modelName) {
  const s = typeof modelName === "string" ? modelName.trim() : "";
  return !!s && s !== "\u2014" && s !== "\u00e2\u20ac\u201d";
}

// Phase 1.5: the AI box always serves the metrics-sidecar (port 8092)
// and the personaplex-bridge (port 8094). They do NOT run on HAOS.
// Earlier versions derived these from the HA endpoint hostname, which
// pointed fresh installs at HAOS:8092/8094 where nothing was listening
// and silently broke voice mode + identity UX. Default to the AI-box
// LAN IP; /metrics <url> and /s2s <url> still override per-install.
function metricsBaseFromEndpoint(_endpoint) {
  if (typeof window !== "undefined" && window.HomeServices) {
    const configured = window.HomeServices.get("metrics");
    if (configured) return configured;
  }
  if (typeof window !== "undefined" && window.HG_DEFAULT_METRICS_BASE) {
    return window.HG_DEFAULT_METRICS_BASE;
  }
  return "http://192.168.0.100:8092";
}

const HOME_SERVICE_BY_RUNTIME_KEY = {
  HG_DEFAULT_HA_BASE: "ha",
  HG_DEFAULT_METRICS_BASE: "metrics",
  HG_DEFAULT_VLLM_BASE: "vllm",
  HG_DEFAULT_VISION_BASE: "vision",
  HG_DEFAULT_INTELLIGENCE_BASE: "intelligence",
  HG_DEFAULT_SUPERVISOR_BASE: "supervisor",
  HG_DEFAULT_S2S_BASE: "s2s",
  HG_DEFAULT_TRACKER_BASE: "tracker",
  HG_DEFAULT_VIDEO_LABELER_BASE: "videoLabeler",
  HG_DEFAULT_FRIGATE_BASE: "frigate",
  HG_DEFAULT_APARTMENT_ASSET_BASE: "apartmentAssets",
};

function webDefaultBase(key) {
  const service = HOME_SERVICE_BY_RUNTIME_KEY[key];
  if (typeof window !== "undefined" && window.HomeServices && service) {
    const configured = window.HomeServices.get(service);
    if (configured) return String(configured).replace(/\/+$/, "");
  }
  if (typeof window !== "undefined" && window[key]) {
    return String(window[key]).replace(/\/+$/, "");
  }
  return "";
}

function normalizeServiceUrlForCompare(value) {
  return String(value || "").trim().replace(/\/+$/, "").toLowerCase();
}

function siblingServiceBase(metricsBase, endpoint, key, port, fallback) {
  const configured = webDefaultBase(key);
  if (configured) return configured;
  const source = metricsBase || metricsBaseFromEndpoint(endpoint);
  try {
    const u = new URL(source);
    return `${u.protocol}//${u.hostname}:${port}`;
  } catch {
    return fallback;
  }
}

function intelligenceBaseFromEndpoint(metricsBase, _endpoint) {
  const configured = webDefaultBase("HG_DEFAULT_INTELLIGENCE_BASE");
  if (configured) return configured;
  try {
    const saved = localStorage.getItem("hg-intelligence-base");
    if (saved && !window.HomeServices) return saved.replace(/\/+$/, "");
  } catch {}
  return siblingServiceBase(metricsBase, _endpoint, "HG_DEFAULT_INTELLIGENCE_BASE", 8095, "http://192.168.0.100:8095");
}

function s2sBaseFromEndpoint(_endpoint) {
  const configured = webDefaultBase("HG_DEFAULT_S2S_BASE");
  if (configured) return configured;
  return "http://192.168.0.100:8094";
}

function visionBaseFromEndpoint(metricsBase, endpoint) {
  return siblingServiceBase(metricsBase, endpoint, "HG_DEFAULT_VISION_BASE", 8091, "http://192.168.0.100:8091");
}

function supervisorBaseFromEndpoint(metricsBase, endpoint) {
  return siblingServiceBase(metricsBase, endpoint, "HG_DEFAULT_SUPERVISOR_BASE", 8093, "http://192.168.0.100:8093");
}

// ─────────────────────────────────────────────────────────────────────
// Dedup-lookup helpers — pure predicates shared by:
//   1. SSE chat-tee handler (renders LOCAL turns via metrics-sidecar
//      broadcast of vLLM completions).
//   2. extended_openai_conversation.conversation.finished subscriber
//      (renders EXTERNAL turns + any other HA-routed turn the SSE feed
//      doesn't see).
// Both sources can fire for the same conversation_id with the same
// text, in either order, depending on race timing. These predicates
// catch the duplicate by exact text match within a bounded lookback
// window — keeping the dedup symmetric across fire orders.
// ─────────────────────────────────────────────────────────────────────
// Dedup helpers — used by every source that adds chat bubbles. Match
// by EXACT text + recent time window. The time window matters because
// the same question/answer can recur turn-to-turn (e.g., user asks
// "do you see me?" 5 times, agent returns the same canned phrasing
// each time — we MUST render all 5). The historical 20-event lookback
// without a time gate caused the 2nd+ identical answers to be eaten.
//
// 8s window catches the cross-source race (SSE + WS event firing for
// the same turn within ~1s) without folding distinct turns. Same
// window for user + assistant since the racing-source problem is
// symmetric.
function _withinWindow(eventTimeStr, windowMs = 8000, nowDate = new Date()) {
  // ev.time is fmtTime() output: "HH:MM:SS" 24h. We only care about
  // very recent (< windowMs) so reconstruct a Date for "today" and
  // compare. If parsing fails, conservatively return true (treat as
  // recent — preserves old dedup-by-count behavior as fallback).
  if (typeof eventTimeStr !== "string") return true;
  const m = eventTimeStr.match(/^(\d{2}):(\d{2}):(\d{2})$/);
  if (!m) return true;
  const d = new Date(nowDate);
  d.setHours(+m[1], +m[2], +m[3], 0);
  let diff = nowDate.getTime() - d.getTime();
  // Handle midnight wrap: if event "looks" like it's in the future,
  // assume it was yesterday and add 24h.
  if (diff < -windowMs) diff += 86400000;
  return diff >= 0 && diff <= windowMs;
}

function findRecentUserIdx(prev, text, lookback = 8, windowMs = 8000) {
  const target = (text || "").trim();
  if (!target) return -1;
  const now = new Date();
  const start = Math.max(0, prev.length - lookback);
  for (let i = prev.length - 1; i >= start; i--) {
    const e = prev[i];
    if ((e.kind === "user" || e.kind === "voice") &&
        (e.text || "").trim() === target &&
        _withinWindow(e.time, windowMs, now)) {
      return i;
    }
  }
  return -1;
}

function findRecentAssistantIdx(prev, text, kind = "home", lookback = 20, windowMs = 8000) {
  const target = (text || "").trim();
  if (!target) return -1;
  const now = new Date();
  const start = Math.max(0, prev.length - lookback);
  for (let i = prev.length - 1; i >= start; i--) {
    const e = prev[i];
    if (e.kind === kind &&
        (e.text || "").trim() === target &&
        _withinWindow(e.time, windowMs, now)) {
      return i;
    }
  }
  return -1;
}

function isDirectLightStateQuestion(text) {
  const t = String(text || "").toLowerCase().replace(/[^\w\s]/g, " ").replace(/\s+/g, " ").trim();
  if (!t) return false;
  if (!/\b(lights?|lamps?)\b/.test(t)) return false;
  if (!/\bon\b/.test(t)) return false;
  if (/\b(turn|switch|toggle|set|make|dim|brighten)\b/.test(t)) return false;
  return /\b(which|what|list|show|tell|are|currently)\b/.test(t);
}

function humanizeEntityId(entityId) {
  return String(entityId || "")
    .replace(/^[^.]+\./, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (m) => m.toUpperCase());
}

function friendlyStateName(state, modelNames) {
  const entityId = state?.entity_id || "";
  const modelName = modelNames?.get?.(entityId);
  if (modelName) return modelName;
  return state?.attributes?.friendly_name || humanizeEntityId(entityId);
}

function isLightOrLampState(state, apartmentSwitchIds) {
  const entityId = state?.entity_id || "";
  const domain = entityId.split(".")[0];
  if (domain === "light") return true;
  if (domain !== "switch") return false;
  if (apartmentSwitchIds?.has?.(entityId)) return true;
  const haystack = `${state?.attributes?.friendly_name || ""} ${humanizeEntityId(entityId)}`.toLowerCase();
  return /\b(light|lamp|lamps|ambient)\b/.test(haystack);
}

function HomeApp({ density = "airy", metricsStyle = "ticker", initialEvents, voiceOverride, themeOverride, autoplay = true }) {
  const viewport = useViewportProfile();
  const mobile = viewport.mobile;
  const initialPrefs = useMemo(() => loadPrefs({
    endpoint: webDefaultBase("HG_DEFAULT_HA_BASE"),
    token: "",
    model: "",
    theme: "dark",
    metricsBase: webDefaultBase("HG_DEFAULT_METRICS_BASE"),
    // S2S experiment — full-duplex speech-to-speech via the
    // personaplex-bridge sidecar. Off by default; toggled per-window
    // with `/s2s on|off`. Existing HA voice pipeline keeps working
    // regardless of this flag.
    s2sMode: false,
    s2sBase: webDefaultBase("HG_DEFAULT_S2S_BASE"),
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
  // Surface voice.state on window so non-React readers (e.g., the metrics
  // poll's adaptive-cadence adapter) can sample it without prop-drilling.
  useEffect(() => {
    if (typeof window !== "undefined") window.__hav_voiceState = voice.state;
  }, [voice.state]);
  useEffect(() => {
    if (typeof window !== "undefined") window.__hav_viewport = viewport;
  }, [viewport]);
  const hasStoredHaCredentials = !!(initialPrefs.endpoint && initialPrefs.token);
  const [connection, setConnection] = useState(
    hasStoredHaCredentials ? "reconnecting" : "disconnected"
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
  const [serviceProfile, setServiceProfile] = useState(() => (
    window.HomeServices ? window.HomeServices.getProfile() : null
  ));
  const [remotePanelOpen, setRemotePanelOpen] = useState(false);
  const [serviceProbeResults, setServiceProbeResults] = useState(null);
  const [serviceProbeRunning, setServiceProbeRunning] = useState(false);
  const [serviceReadiness, setServiceReadiness] = useState(() => (
    window.HomeServices ? window.HomeServices.buildTravelReadiness() : null
  ));
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
  // Phase 4A.9 Jarvis mute — composite state read from
  // binary_sensor.jarvis_muted_effective_2. Header pill renders when on.
  const [muteState, setMuteState] = useState({ muted: false, reason: "", until: "" });
  // Addendum 14 / Slice 3 — people overlay open state. Header button opens;
  // Escape or close button closes. Voice replies continue to flow into the
  // underlying chat feed while the overlay is open.
  const [peopleOpen, setPeopleOpen] = useState(false);
  const [intelligenceOpen, setIntelligenceOpen] = useState(false);
  // Addendum 27 / M5 — explainability drawer state. Stores the conv_id
  // whose routing-log entries the drawer renders, or null when closed.
  // Open via /why slash command or by clicking any kind:"home"/"action"/
  // "external" bubble while debugMode is on.
  const [explainConvId, setExplainConvId] = useState(null);
  // Addendum 27 / F-32 — world-state debug drawer open state.
  // Toggled via /world-state slash command. Reads from the existing
  // /api/extended_openai_conversation/world_state endpoint.
  const [worldStateDrawerOpen, setWorldStateDrawerOpen] = useState(false);
  // /lights drawer — Living Lights tuning surface. One panel with
  // "Right now" status + global bias knobs + cascade-priority sliders
  // for the 15 promoted constants (vacant baselines, ToD CT buckets,
  // movie/gaming/asleep ceilings). See home-lights.jsx.
  const [lightsOpen, setLightsOpen] = useState(false);
  // Per-room filter applied when /world-state <room> opens the drawer.
  // Cleared via the × chip in the drawer header.
  const [worldStateInitialRoom, setWorldStateInitialRoom] = useState(null);
  // Addendum 38 Phase 1 — /spatial light-footprint drawer open state.
  const [spatialDrawerOpen, setSpatialDrawerOpen] = useState(false);
  // /apartment — full-screen 3D apartment takeover (home-apartment.jsx).
  const [apartmentOpen, setApartmentOpen] = useState(false);
  // /labeler — full-screen video timeline labeler (home-video-labeler.jsx).
  // Mutually exclusive with the other full-screen surfaces (people /
  // intelligence / apartment) — opening one closes the rest, which kills
  // Escape-stacking and z-fights between fixed inset-0 overlays.
  const [videoLabelerOpen, setVideoLabelerOpen] = useState(false);
  // S79: the active Simulation Mode header pill opens this local controls
  // dialog for switching scenarios, resetting fixtures, or returning live.
  const [simulationControlsOpen, setSimulationControlsOpen] = useState(false);
  // Phase 0.5 "Thinking with Visual Primitives" — /look drawer state.
  // lookInitial seeds the drawer from the command args; its `nonce`
  // re-keys the drawer so a repeated /look re-runs cleanly.
  const [lookDrawerOpen, setLookDrawerOpen] = useState(false);
  const [lookInitial, setLookInitial] = useState({ camera: null, question: "", nonce: 0 });
  // Master plan F.3: full bridge /healthz snapshot for the DebugPanel
  // (only populated when debugMode is on to avoid wasted polls).
  const [bridgeHealth, setBridgeHealth] = useState(null);
  // AI Stack Control (Phase 1, read-only): liveness of the stack-supervisor
  // (:8093 — separate from the docker-compose stack so it survives `stack.sh
  // down`) and its aggregate status payload. Polled every 15s alongside
  // sidecar/bridge below. See home-ai-stack.jsx.
  const [aiStackOnline, setAiStackOnline] = useState(null);
  const [aiStackState, setAiStackState] = useState(null);
  const [aiStackStatusError, setAiStackStatusError] = useState(null);
  const aiStackHealthWarnRef = useRef("");
  // External Reasoning Fallback (see home-external.jsx + the plan at
  // ~/.claude/plans/keen-doodling-parasol.md): modal visibility for
  // `/external set-key`, and an AbortController ref shared across the
  // dispatch path so a new send/Esc cancels any in-flight external
  // stream. Stored in a ref (not state) because cancelling shouldn't
  // re-render — it just aborts the fetch.
  const [externalKeyModalOpen, setExternalKeyModalOpen] = useState(false);
  const currentExternalCtrlRef = useRef(null);
  // ID of the in-flight external assistant event we're streaming
  // chunks into. Carried so onChunk/onDone can target the right event
  // (rather than always mutating the last appended event, which
  // breaks if anything else appends mid-stream).
  const currentExternalEventIdRef = useRef(null);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const api = {
      closeOverlays: () => {
        setRemotePanelOpen(false);
        setPeopleOpen(false);
        setIntelligenceOpen(false);
        setExplainConvId(null);
        setWorldStateDrawerOpen(false);
        setLightsOpen(false);
        setSpatialDrawerOpen(false);
        setApartmentOpen(false);
        setVideoLabelerOpen(false);
        setSimulationControlsOpen(false);
        setLookDrawerOpen(false);
        setExternalKeyModalOpen(false);
        return true;
      },
      openApartmentForAudit: () => {
        const haBase = webDefaultBase("HG_DEFAULT_HA_BASE");
        if (haBase) setEndpoint(haBase);
        // Keep FirstRun out of the way without claiming HA is connected. The
        // live Apartment audit only needs tracker/assets/Frigate; setting
        // "online" here would start HA subscription effects with no socket.
        setConnection("reconnecting");
        setVideoLabelerOpen(false);
        setPeopleOpen(false);
        setIntelligenceOpen(false);
        setApartmentOpen(true);
        return true;
      },
    };
    window.__havUiDebug = api;
    return () => {
      if (window.__havUiDebug === api) delete window.__havUiDebug;
    };
  }, []);
  // Master plan Phase 2: live network metrics (Unifi via HA WS).
  // Currently surfaces UDM Cloud Gateway + 2 USW Flex switches + count
  // of Unifi-attached device trackers in `home` state.
  const [networkMetrics, setNetworkMetrics] = useState(emptyNetworkMetrics);
  // Vision-sidecar phash health.
  const [visionSidecarOnline, setVisionSidecarOnline] = useState(null);
  const [visionHealth, setVisionHealth] = useState(null);
  // Tray v2: HAOS host system metrics from HA's System Monitor integration.
  // Populated by a state_changed subscription that filters across the
  // multiple known entity_id patterns the integration uses.
  // Shape: { cpu, ram, disk, uptime } — all may be null until sensors exist.
  const [hostMetrics, setHostMetrics] = useState(null);
  // Frigate stats from /config/packages/frigate_stats.yaml REST scrape +
  // template sensors. Populated by a state_changed subscription on the
  // sensor.frigate_* family. Stays null when the package isn't installed
  // so the FRIGATE card shows the "enable stats sensors" empty-state.
  // Shape: { totalCpu, coralMs, uptime, fps: { living_room, kitchen, ... } }
  const [frigateMetrics, setFrigateMetrics] = useState(null);
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
  const [spatialLayout, setSpatialLayout] = useState(() => {
    try {
      if (typeof window === "undefined") return false;
      const params = new URLSearchParams(window.location.search || "");
      const raw = String(params.get("layout") || params.get("view") || params.get("home2") || "").toLowerCase();
      if (["spatial", "home2", "2"].includes(raw)) return true;
      if (["classic", "chat", "off", "1"].includes(raw)) return false;
      if (params.has("spatial2") || params.has("spatial-home") || params.get("home2") === "") return true;
      return window.localStorage?.getItem("hg-layout-v2") === "spatial";
    } catch (e) { return false; }
  });
  const [spatialOpsDockOpen, setSpatialOpsDockOpen] = useState(true);
  useEffect(() => {
    if (typeof window !== "undefined") {
      window.__hav_setSpatialLayout = (next) => setSpatialLayout(!!next);
    }
    return () => {
      if (typeof window !== "undefined" && window.__hav_setSpatialLayout) {
        delete window.__hav_setSpatialLayout;
      }
    };
  }, []);
  const [availableModels, setAvailableModels] = useState(null);
  const [metrics, setMetrics] = useState(CLEARED_METRICS);
  // Tray v3: rolling 40-sample history per metric for inline sparklines.
  // Pushed inside the same /metrics polling effect that updates `metrics`.
  const [metricsHistory, setMetricsHistory] = useState(emptyMetricsHistory);
  const [conversationId, setConversationId] = useState(initialConvId);
  // Live Frigate-derived detection labels per camera, e.g.
  //   { kitchen: ["person"], driveway: ["car", "person"] }
  // Sourced from binary_sensor.{camera}_{label}_occupancy state_changed events.
  const [cameraLabels, setCameraLabels] = useState({});
  // Lifted from MetricsStrip so sim scenarios can inject mock traces.
  // In live mode the MetricsStrip's poll effect writes via these
  // setters (passed in as props).
  const [traceSummary, setTraceSummary] = useState(null);
  const [lastTrace, setLastTrace] = useState(null);

  // Lab tab (Addendum 10): rolling buffers for time-aligned chart.
  // refs (not state) to avoid re-render storms; labTick triggers
  // refresh when relevant. Per-turn samples on each LabTurn means
  // the 21-call history survives even when turns are minutes apart.
  //
  // Addendum 13 fix C.2: seed labTurnsRef from localStorage on mount
  // so the chart survives app restarts (until TTL = 6h). Opt-out by
  // setting localStorage.setItem('hg-lab-persist','off'). Persistence
  // logic lives in window.HomeMetricsLabHelpers.{loadLabTurnsFromStorage,
  // persistLabTurns}; pure-function tested in tools/run-lab-tests.js.
  const LAB_STORAGE_KEY = "hg-lab-turns-v1";
  const LAB_STORAGE_TTL_MS = 6 * 60 * 60 * 1000;
  const labTurnsRef = useRef((function () {
    try {
      if (typeof window === "undefined") return [];
      if (window.localStorage && window.localStorage.getItem("hg-lab-persist") === "off") return [];
      const H = window.HomeMetricsLabHelpers;
      if (!H || typeof H.loadLabTurnsFromStorage !== "function") return [];
      return H.loadLabTurnsFromStorage(window.localStorage, LAB_STORAGE_KEY, LAB_STORAGE_TTL_MS);
    } catch (e) { return []; }
  })());
  const labSamplesRef = useRef([]);
  const [labTick, setLabTick] = useState(0);

  // Addendum 32 Phase A: expose lab buffers + tick on window for
  // /lab-dump inspection. We expose the REF OBJECTS (not .current) so
  // external reads always see the live latest state without
  // triggering React re-renders. Mirrors the existing
  // window.__INFLIGHT_LAST_STUB pattern (home-app.jsx:3796).
  // window.__hav_lastAnchorStats is populated from inside
  // buildDenseStageAnchors (helpers.js) — the AR32-7 H9 probe.
  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    window.__hav_labSamplesRef = labSamplesRef;
    window.__hav_labTurnsRef = labTurnsRef;
    return () => {
      try { delete window.__hav_labSamplesRef; } catch {}
      try { delete window.__hav_labTurnsRef; } catch {}
    };
  }, []);
  useEffect(() => {
    if (typeof window !== "undefined") window.__hav_renderTick = labTick;
  }, [labTick]);

  // Addendum 29 Change 2 / AV29-4: periodic sweep evicts in-flight
  // stubs older than 60s (orphaned by silently-failed dispatches).
  // 5s cadence balances responsiveness against wakeup cost. Helper is
  // pure (tested via Node) so this useEffect is just a thin scheduler.
  useEffect(() => {
    const H = window.HomeMetricsLabHelpers;
    if (!H || typeof H.evictStaleInFlight !== "function") return undefined;
    const id = setInterval(() => {
      try {
        const before = labTurnsRef.current.length;
        const after = H.evictStaleInFlight(labTurnsRef.current, Date.now(), 60000);
        if (after.length !== before) {
          labTurnsRef.current = after;
          setLabTick((t) => t + 1);
        }
      } catch (e) {
        // Silent — eviction is best-effort.
      }
    }, 5000);
    return () => clearInterval(id);
  }, []);

  // Debounced persistence: after each turn write, schedule a save in 2s.
  // Multiple rapid turns collapse to one write. No write in sim mode.
  const labPersistTimer = useRef(null);
  const schedulePersistLabTurns = useCallback(() => {
    try {
      if (window.__SIM_ACTIVE) return;
      if (window.localStorage && window.localStorage.getItem("hg-lab-persist") === "off") return;
      const H = window.HomeMetricsLabHelpers;
      if (!H || typeof H.persistLabTurns !== "function") return;
      if (labPersistTimer.current) clearTimeout(labPersistTimer.current);
      labPersistTimer.current = setTimeout(() => {
        try { H.persistLabTurns(window.localStorage, LAB_STORAGE_KEY, labTurnsRef.current); }
        catch (e) { /* quota / serialization error — silent */ }
      }, 2000);
    } catch (e) {}
  }, []);

  // Lab turn writer: convert a single trace object → LabTurn + push to
  // labTurnsRef. Dedupes by turn_id (with a defensive secondary key on
  // t_done+user_text in case bridge omits turn_id). Returns true if a
  // new turn was appended, false if it was a duplicate. Pure-ish: the
  // only side-effect is the ref push + cap.
  const processTrace = useCallback((trace) => {
    if (!trace || !trace.t_done) return false;
    // Primary dedup: turn_id. Secondary (defense vs AR16-7): t_done +
    // user_text hash — collision window is microseconds-wide but worth
    // guarding against the rare same-ms / same-text edge.
    const turnId = trace.turn_id || `t-${trace.t_done}-${(trace.user_text || "").slice(0,16)}`;
    if (labTurnsRef.current.find((t) => t.id === turnId)) return false;
    // Idempotency under SSE/poll race: also check sidecar_ids on
    // already-grouped turns (AV29-18).
    if (labTurnsRef.current.find(
      (t) => Array.isArray(t.sidecar_ids) && t.sidecar_ids.indexOf(String(turnId)) !== -1
    )) return false;
    // Addendum 29 Change 3: multi-round grouping. Before creating a NEW
    // turn, see if this trace belongs to an in-flight turn (same
    // user_text within a 2.5s window). Multi-round tool-using turns
    // produce N sidecar entries with the same user text; the grouping
    // helper collapses them into one bar with N×(prefill+gen) stages
    // + tool segments spliced in by Change 4. Helper tests:
    // tools/run-lab-tests.js → "Addendum 29 Change 3" suite.
    const H = (typeof window !== "undefined" && window.HomeMetricsLabHelpers) || null;
    if (H && typeof H.findTurnToGroupInto === "function") {
      // Use the real wall-clock startedAt from the adapter's stages when
      // present (same fix as the new-turn path above — fixes off-by-up-to-
      // 30-seconds when /conversations/recent returns aged entries).
      let groupStartedAt;
      if (Array.isArray(trace.stages) && trace.stages.length > 0
          && Number.isFinite(trace.stages[0].startedAt)) {
        groupStartedAt = trace.stages[0].startedAt;
      } else {
        groupStartedAt = Date.now() - (trace.t_done || 0);
      }
      const traceForGroup = {
        turn_id: turnId,
        user_text: trace.user_text,
        assistant_text: trace.assistant_text || "",
        startedAt: groupStartedAt,
        t_done: trace.t_done,
        stages: trace.stages || null,
      };
      const groupTarget = H.findTurnToGroupInto(labTurnsRef.current, traceForGroup, 2500);
      if (groupTarget && typeof H.mergeTraceIntoTurn === "function") {
        // Addendum 29 Change 2: real trace arrived for an in-flight stub
        // → clear the flag so the renderer stops showing the pulsing
        // band and renders the real stages (which the merge below
        // appends).
        if (groupTarget.inFlight) {
          groupTarget.inFlight = false;
          if (typeof window !== "undefined") {
            window.__INFLIGHT_LAST_CLEARED_TS = Date.now();
          }
        }
        // Build the stages for the new round (same logic as the new-turn
        // path below, but only the stages portion).
        let newStages;
        if (Array.isArray(trace.stages) && trace.stages.length > 0) {
          newStages = trace.stages;
        } else {
          const startNew = Date.now() - (trace.t_done || 0);
          const sttN   = trace.t_parakeet_done || 0;
          const llmN   = Math.max(0, (trace.t_pipeline_intent_end || 0) - sttN);
          const synthN = Math.max(0, (trace.t_synth_done || 0) - (trace.t_pipeline_intent_end || 0));
          const audioN = Math.max(0, (trace.t_done || 0) - (trace.t_first_audio_sent || 0));
          newStages = [
            { label: "stt",   ms: sttN,   startedAt: startNew,                                    endedAt: startNew + sttN },
            { label: "llm",   ms: llmN,   startedAt: startNew + sttN,                              endedAt: startNew + sttN + llmN },
            { label: "synth", ms: synthN, startedAt: startNew + sttN + llmN,                       endedAt: startNew + sttN + llmN + synthN },
            { label: "audio", ms: audioN, startedAt: startNew + sttN + llmN + synthN,              endedAt: startNew + (trace.t_done || 0) },
          ];
        }
        H.mergeTraceIntoTurn(groupTarget, {
          turn_id: turnId,
          user_text: trace.user_text,
          assistant_text: trace.assistant_text || "",
          stages: newStages,
          // Change 4: pass tool_calls so the helper updates pending_tools
          // for the NEXT merge to splice them into the bar.
          tool_calls: Array.isArray(trace.tool_calls) ? trace.tool_calls : [],
        });
        return true; // counts as appended
      }
    }
    const total = trace.t_done || 0;
    // CRITICAL: prefer the adapter-supplied stages' real wall-clock
    // timestamps (sidecar wall-clock ts) over Date.now(). Without this,
    // turns that arrived 10+ seconds ago via /conversations/recent
    // poll get assigned a startedAt of "now-totalMs", which doesn't
    // overlap with the labSamplesRef wall-clock window — so GPU/CPU
    // samples DURING the real turn get filtered out and the per-turn
    // anchors render flat. Take startedAt/endedAt from the first/last
    // stage when present; only fall back to Date.now() when no stages.
    let startedAt, endedAt;
    if (Array.isArray(trace.stages) && trace.stages.length > 0) {
      const fs = trace.stages[0];
      const ls = trace.stages[trace.stages.length - 1];
      startedAt = Number.isFinite(fs.startedAt) ? fs.startedAt : (Date.now() - total);
      endedAt = Number.isFinite(ls.endedAt) ? ls.endedAt : Date.now();
    } else {
      startedAt = Date.now() - total;
      endedAt = Date.now();
    }
    // 2026-05-18: allow the adapter to pre-supply a stages array (typed
    // turns from /conversations/recent use this path with prefill/gen
    // stages derived from ttft_observed_ms). Voice turns coming from
    // the bridge with t_* fields take the legacy 4-stage build below.
    let stages;
    if (Array.isArray(trace.stages) && trace.stages.length > 0) {
      stages = trace.stages;
    } else {
      const stt   = trace.t_parakeet_done || 0;
      const llm   = Math.max(0, (trace.t_pipeline_intent_end || 0) - stt);
      const synth = Math.max(0, (trace.t_synth_done || 0) - (trace.t_pipeline_intent_end || 0));
      const audio = Math.max(0, (trace.t_done || 0) - (trace.t_first_audio_sent || 0));
      stages = [
        { label: "stt",   ms: stt,   startedAt: startedAt,                          endedAt: startedAt + stt },
        { label: "llm",   ms: llm,   startedAt: startedAt + stt,                    endedAt: startedAt + stt + llm },
        { label: "synth", ms: synth, startedAt: startedAt + stt + llm,              endedAt: startedAt + stt + llm + synth },
        { label: "audio", ms: audio, startedAt: startedAt + stt + llm + synth,      endedAt },
      ];
    }
    // Sample window: include samples ±3 seconds around the turn for
    // resource-line aggregation. The ±3s is "samples that surround
    // this turn", NOT "this turn was 6 seconds longer" — stage
    // durations remain honest. With the metrics poll at 750ms idle
    // / 250ms active, a 1-second typed turn at strict ±250ms window
    // catches 0-2 samples, leaving most stage sub-anchors to fall
    // back to synthetic baselines (the flat-line bug). ±3s catches
    // 6-12 samples, enough for stage-wide-fallback (see
    // buildDenseStageAnchors) to populate real anchors across most
    // stages.
    //
    // Previous behavior extended ±30s AND prepended an "ambient"
    // stage that lied about turn duration (made every bar look ~30s
    // wide). The lookback-only widening keeps bars honest while
    // resource lines get sufficient samples.
    const samples = labSamplesRef.current
      .filter((s) => s.t >= startedAt - 3000 && s.t <= endedAt + 3000)
      .map((s) => ({ ...s }));
    let effectiveTotal = total;
    let effectiveStartedAt = startedAt;
    // Change 4: seed pending_tools from this trace's tool_calls so the
    // NEXT round's merge can splice them as labeled segments between
    // this round's gen and the next round's prefill.
    const pendingTools = Array.isArray(trace.tool_calls)
      ? trace.tool_calls
          .map((tc) => tc && tc.function && tc.function.name)
          .filter((n) => typeof n === "string" && n.length > 0)
      : [];
    labTurnsRef.current.push({
      id: turnId,
      startedAt: effectiveStartedAt,
      endedAt,
      totalMs: effectiveTotal,
      stages, samples,
      transcript: trace.user_text,
      // Addendum 29 Change 5: dual-line tooltip needs both sides of the
      // conversation. assistant_text is empty for in-flight stubs +
      // tool-call-only rounds; tooltip renderer hides the line in those
      // cases (AV29-12).
      assistant_text: trace.assistant_text || "",
      // Change 3: track which sidecar IDs have been folded into this
      // turn so future SSE/poll deliveries with the same id don't
      // double-append (AV29-18). Seeded with the current trace's id.
      sidecar_ids: [String(turnId)],
      // Change 4: pending tool names awaiting their next-round signal.
      pending_tools: pendingTools,
      cold: !!trace.cold,
      slow: false, crit: false,
    });
    if (labTurnsRef.current.length > 32) {
      labTurnsRef.current = labTurnsRef.current.slice(-32);
    }
    return true;
  }, [labTurnsRef, labSamplesRef]);

  // Live-mode trace appender — called by both:
  //   (a) the lastTrace useEffect (back-compat, single trace per poll)
  //   (b) MetricsStrip.onTracesFetched (F-2: backfill from n=50 array)
  useEffect(() => {
    if (window.__SIM_ACTIVE) return;
    if (processTrace(lastTrace)) {
      setLabTick((t) => t + 1);
      schedulePersistLabTurns();
    }
  }, [lastTrace, processTrace, schedulePersistLabTurns]);

  // Addendum 16 F-2: bulk-traces handler. Fired by MetricsStrip with the
  // full sidecar buffer (cap 50) on each poll. Dedupes via processTrace
  // so re-seeing existing turns is a no-op. Persists once at the end.
  const handleTracesFetched = useCallback((traces) => {
    if (window.__SIM_ACTIVE) return;
    if (!Array.isArray(traces) || traces.length === 0) return;
    let appended = 0;
    for (const t of traces) {
      if (processTrace(t)) appended++;
    }
    if (appended > 0) {
      setLabTick((t) => t + 1);
      schedulePersistLabTurns();
    }
  }, [processTrace, schedulePersistLabTurns]);

  // Addendum 17: routing-log entries → slim LabTurns for EXTERNAL turns.
  // The sidecar only sees vLLM completions; external turns route to OpenAI
  // directly and produce no sidecar trace. Without this synthesis the lab
  // history undercounts conversation activity (visible as gaps where the
  // user asked general-knowledge questions). Dedup by conv_id against
  // labTurnsRef so we don't double-render turns the sidecar also captured.
  const processRoutingLogEntry = useCallback((entry) => {
    if (!entry || entry.dispatched !== "external") return false;
    const id = entry.conv_id || `r-${entry.ts}-${(entry.text || "").slice(0, 16)}`;
    if (labTurnsRef.current.find((t) => t.id === id)) return false;
    const tsMs = entry.ts ? Date.parse(entry.ts) : Date.now();
    if (!Number.isFinite(tsMs)) return false;
    const total = Math.max(0, entry.ext_latency_ms || 0);
    const endedAt = tsMs;
    const startedAt = endedAt - total;
    // Pseudo-stages for visual rhythm — the real data is one number
    // (round-trip latency). The split (5% / 90% / 5%) matches the
    // typical shape of an OpenAI call: brief prompt assembly, dominant
    // server-side processing, brief response decode. Purple shading
    // varies per stage so the bar reads as segmented like local turns.
    const reqMs = Math.round(total * 0.05);
    const recvMs = Math.round(total * 0.05);
    const apiMs = Math.max(0, total - reqMs - recvMs);
    const stages = [
      { label: "ext_req",  ms: reqMs,  startedAt,                            endedAt: startedAt + reqMs },
      { label: "ext_api",  ms: apiMs,  startedAt: startedAt + reqMs,         endedAt: startedAt + reqMs + apiMs },
      { label: "ext_recv", ms: recvMs, startedAt: startedAt + reqMs + apiMs, endedAt },
    ];
    labTurnsRef.current.push({
      id,
      startedAt, endedAt, totalMs: total,
      stages,
      samples: labSamplesRef.current
        .filter((s) => s.t >= startedAt && s.t <= endedAt + 250)
        .map((s) => ({ ...s })),
      transcript: entry.text,
      cold: false,
      slow: false, crit: false,
      route: "external",
    });
    if (labTurnsRef.current.length > 32) {
      labTurnsRef.current = labTurnsRef.current.slice(-32);
    }
    return true;
  }, []);

  const handleRoutingLogFetched = useCallback((entries) => {
    if (window.__SIM_ACTIVE) return;
    if (!Array.isArray(entries) || entries.length === 0) return;
    let appended = 0;
    for (const e of entries) {
      if (processRoutingLogEntry(e)) appended++;
    }
    if (appended > 0) {
      // Re-sort labTurnsRef by startedAt to keep history chronological
      // (routing-log entries can interleave with sidecar traces).
      labTurnsRef.current.sort((a, b) => (a.startedAt || 0) - (b.startedAt || 0));
      setLabTick((t) => t + 1);
      schedulePersistLabTurns();
    }
  }, [processRoutingLogEntry, schedulePersistLabTurns]);

  // (Sim injection useEffect lives below — needs `sim` after useSimulation
  // call; deferred until after that hook initializes.)

  // Simulation Mode: per-room camera-state map ("online-empty",
  // "offline", etc.) consumed by HomeVisionFrame's simulationSrc prop.
  // Stays null in live mode. See simulation-cameras.jsx.
  const [simCameraStates, setSimCameraStates] = useState(null);

  // Proactive-assistant coordinator UI state (see home-proactive.jsx).
  // The coordinator hook (called below) drives this; sim scenarios drive
  // it directly via the sim setters. ONE useState — render-stability.
  //   { phase, reason, room, person, away, sightingLevel, sinceTs }
  const proactiveIdleState = window.PROACTIVE_IDLE_STATE || {
    phase: "idle", away: false, reason: null, room: null, person: null,
    sightingLevel: "none", sinceTs: 0,
  };
  const [proactive, setProactive] = useState(() => (
    typeof window.loadProactivePending === "function"
      ? window.loadProactivePending(proactiveIdleState)
      : proactiveIdleState
  ));

  // Simulation Mode hook (see simulation.jsx). Reads URL params /
  // localStorage on boot; provides activate/deactivate/setScenario.
  // The hook writes synthetic data into the real setters below.
  const sim = window.useSimulation({
    setMetrics, setMetricsHistory,
    setBridgeHealth, setHostMetrics, setFrigateMetrics,
    setNetworkMetrics, setRoomContext, setVisionHealth, setVisionSidecarOnline,
    setTraceSummary, setLastTrace,
    setIdentity, setMedia, setCameraLabels, setSimCameraStates,
    setConnection, setSidecarOnline, setBridgeOnline,
    setAiStackOnline, setAiStackState,
    setVoice: setVoiceInternal, setEvents,
    // proactive deltas merge (a timeline step may patch just `phase`)
    setProactive: (val) => setProactive((prev) => ({ ...prev, ...val })),
  });
  useEffect(() => {
    if (!sim.active) setSimulationControlsOpen(false);
  }, [sim.active]);
  useEffect(() => {
    if (sim.active) return;
    if (typeof window.saveProactivePending === "function") {
      window.saveProactivePending(proactive);
    }
  }, [sim.active, proactive]);
  useEffect(() => {
    if (sim.active || connection === "online") return;
    setCameraLabels({});
    setNetworkMetrics(emptyNetworkMetrics());
    setHostMetrics(null);
    setFrigateMetrics(null);
    setRoomContext(null);
  }, [connection, sim.active]);

  const feedRef = useRef(null);
  const timers = useRef([]);
  const rootRef = useRef(null);
  const peopleButtonRef = useRef(null);
  const streamingIds = useRef(new Set());
  const haClientRef = useRef(null);
  const activeRunRef = useRef(null); // { id, cancel }
  const resetFeedScrollTop = useCallback(() => {
    if (typeof window === "undefined") return;
    window.requestAnimationFrame(() => {
      if (feedRef.current) feedRef.current.scrollTop = 0;
    });
  }, []);
  const closePeopleOverlay = useCallback(() => {
    setPeopleOpen(false);
    const focusPeopleButton = () => {
      try { peopleButtonRef.current?.focus?.(); } catch {}
    };
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(focusPeopleButton);
    else setTimeout(focusPeopleButton, 0);
  }, []);

  /* ── First-run boot sequence ──────────────────────────────────────────
   * bootPhase: "logo" → "settling" → "ready". The animated BootSequence
   * plays on every launch (fast + skippable); routeOnboarding then teaches
   * the right next step without nagging returning users. */
  const [onboarding] = useState(() => loadOnboarding({ seenHelpHint: false }));
  const [bootMode] = useState(() =>
    (typeof window !== "undefined" && window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches)
      ? "instant" : "animate"
  );
  const [bootPhase, setBootPhase] = useState("logo");
  const [focusToken, setFocusToken] = useState(0);
  const onboardedRef = useRef(false);
  const focusedRef = useRef(false);

  useEffect(() => {
    if (!window.HomeServices) return undefined;
    return window.HomeServices.onChange(({ profile }) => {
      setServiceProfile(profile);
      if (!window.HG_WEB_MODE) {
        const ha = window.HomeServices.get("ha");
        const metrics = window.HomeServices.get("metrics");
        const s2s = window.HomeServices.get("s2s");
        if (ha) setEndpoint(ha);
        if (metrics) setMetricsBase(metrics);
        if (s2s) setS2sBase(s2s);
      }
    });
  }, []);

  // hg-fill-input listener — any component (currently HelpContent) can
  // dispatch a CustomEvent("hg-fill-input", {detail: "<command>"}) to
  // populate the chat input + focus it. Avoids prop-drilling setInput
  // through every event renderer. The user just clicks → command lands
  // in the input → they hit Enter.
  useEffect(() => {
    const onFill = (ev) => {
      const cmd = ev?.detail;
      if (typeof cmd !== "string" || !cmd) return;
      setInput(cmd);
      setFocusToken((t) => t + 1);  // bump focus token → InputRow useEffect focuses
    };
    window.addEventListener("hg-fill-input", onFill);
    return () => window.removeEventListener("hg-fill-input", onFill);
  }, []);
  // Stable callback — passed to BootSequence's onComplete. Must NOT be an
  // inline arrow: BootSequence keys effects off it, and an unstable ref would
  // churn its hold-timeout (see BootSequence). setBootPhase is stable.
  const handleBootComplete = useCallback(() => setBootPhase("settling"), []);

  /* Theme → DOM */
  useEffect(() => {
    if (rootRef.current) rootRef.current.dataset.theme = theme;
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  /* Auto-scroll to bottom on new events. `bootPhase` is in the deps because
   * the feed div is unmounted during boot — without it the scroll-to-bottom
   * never fires on the → "ready" transition and returning users would land
   * at the top of their history. */
  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight;
  }, [events, bootPhase]);

  /* Persist prefs whenever they change. Note: `s2sMode` is deliberately
   * NOT persisted — it's transient session state (Phase 1.5). The home
   * app always boots with voice mode inactive; mic-tap activates it. */
  useEffect(() => {
    savePrefs({ endpoint, token, model, theme, metricsBase, s2sBase, s2sToken, s2sVoice, kokoroVoice, debugMode });
  }, [endpoint, token, model, theme, metricsBase, s2sBase, s2sToken, s2sVoice, kokoroVoice, debugMode]);

  /* Persist events on change (debounced via rAF — cheap enough at our scale).
   * Onboarding-injected hints (`e.onboarding`) are session-scoped UI, not
   * chat history — filtered out so they never persist or stack across launches. */
  useEffect(() => {
    const id = requestAnimationFrame(() => saveEvents(events.filter((e) => !e.onboarding)));
    return () => cancelAnimationFrame(id);
  }, [events]);

  /* Persist conversation_id whenever it changes */
  useEffect(() => { saveConversationId(conversationId); }, [conversationId]);

  /* Helpers to push events */
  const addEvent = useCallback((ev) => {
    // Apply ASR correction to bubble text so the rendered text never
    // shows forbidden spellings, regardless of source (SSE raw vLLM,
    // HA WS event, parakeet STT echo, etc).
    const corrected = ev?.text
      ? { ...ev, text: applyAsrCorrection(ev.text) }
      : ev;
    setEvents((prev) => [...prev, { id: nextId(), time: fmtTime(), ...corrected }]);
  }, []);

  // Lab sim-mode injection (Addendum 10). Runs after `sim` is available
  // from useSimulation. When a lab fixture is in the snapshot, overwrite
  // the refs + bump tick.
  useEffect(() => {
    window.__SIM_ACTIVE = !!sim?.active;
    const labFixture = sim?.snapshot?.lab;
    if (sim?.active && labFixture) {
      labTurnsRef.current = [...(labFixture.turns || [])];
      labSamplesRef.current = [...(labFixture.samples || [])];
      setLabTick((t) => t + 1);
    }
  }, [sim?.active, sim?.snapshot?.lab]);

  /* ── Proactive-assistant coordinator (home-proactive.jsx) ───────────────
   * Owns the two-stage arrival confirmation + room-entry conversational
   * policy. Fed by three transports: the HA-WS `homeai_proactive`
   * subscription it opens itself, the SSE `s2s:proactive` branch, and
   * `observeIdentity` off the existing `s2s:identity` events. Drives the
   * `proactive` state above. Every returned callback is render-stable. */
  const frigateOnline = frigateMetrics != null;
  const proactiveCoord = window.useProactiveCoordinator({
    connection, haClientRef, simActive: sim.active,
    debugMode, media, frigateOnline, addEvent, setProactiveState: setProactive,
  });
  const handleProactiveStatusAction = useCallback((action) => {
    if (action === "acknowledge" && typeof proactiveCoord.acknowledgePending === "function") {
      proactiveCoord.acknowledgePending();
      return;
    }
    if (typeof proactiveCoord.dismissPending === "function") {
      proactiveCoord.dismissPending();
    }
  }, [proactiveCoord]);

  const finishStream = useCallback((id, patch = {}) => {
    streamingIds.current.delete(id);
    setEvents((prev) => prev.map((e) => e.id === id ? { ...e, ...patch, streaming: false } : e));
  }, []);

  /* ── Boot phase driver: "settling" → "ready" ──────────────────────────
   * "logo" → "settling" is owned by BootSequence's onComplete (in render).
   * Two effects own the settling exit: (a) flip to "ready" the instant
   * `connection` is terminal; (b) a 3s safety timeout armed once on entering
   * "settling" — NOT reset by interim connection changes — so a genuinely
   * hung connect still releases the boot screen. */
  useEffect(() => {
    if (bootPhase !== "settling") return;
    if (connection !== "reconnecting" && connection !== "connecting") setBootPhase("ready");
  }, [bootPhase, connection]);

  useEffect(() => {
    if (bootPhase !== "settling") return;
    const t = setTimeout(() => setBootPhase("ready"), 3000);
    return () => clearTimeout(t);
  }, [bootPhase]);

  /* routeOnboarding — latched, runs exactly once per launch, the first time
   * `connection` is terminal at/after bootPhase "ready". Posts ≤1 onboarding
   * hint; hints carry an `onboarding` marker and are session-scoped (the
   * events-persist effect filters them, so they never stack across launches). */
  useEffect(() => {
    if (bootPhase !== "ready" || onboardedRef.current) return;
    if (connection === "reconnecting" || connection === "connecting") return; // not terminal yet
    onboardedRef.current = true;
    if (sim.active) return; // sim entry posts its own catalog — stay silent
    const hasChatted = events.some((e) => e.kind === "user" || e.kind === "voice");
    if (connection === "online") {
      if (!hasChatted && !onboarding.seenHelpHint) {
        addEvent({ kind: "system", tone: "info", onboarding: "help-hint",
          text: "Connected. Type /help to learn what you can do." });
        saveOnboarding({ seenHelpHint: true });
      }
      // hasChatted → returning user, straight to chat, no message.
    } else if (!isFirstRunVisible(connection, events)) {
      // Chat feed is the visible surface (returning user). FirstRun's helper
      // text already carries this copy for the form case.
      addEvent({ kind: "system", tone: "info", onboarding: "disconnected-hint",
        text: "Not connected. Type /connect to connect to your local home stack, or /simulation to explore the app with mock data." });
    }
  }, [bootPhase, connection]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Input focus — a separate latched effect. Focuses the chat input once, the
   * moment the feed is the live surface at bootPhase "ready". Kept out of
   * routeOnboarding because focus shouldn't wait for a *terminal* connection —
   * a returning user whose connect hangs still has a visible feed. Gated on
   * !isFirstRunVisible so it never strands focus behind the FirstRun form. */
  useEffect(() => {
    if (bootPhase !== "ready" || focusedRef.current) return;
    if (isFirstRunVisible(connection, events)) return;
    focusedRef.current = true;
    setFocusToken((t) => t + 1);
  }, [bootPhase, connection, events]);

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
    const webMetricsBase = window.HG_WEB_MODE ? webDefaultBase("HG_DEFAULT_METRICS_BASE") : "";
    if (webMetricsBase) return webMetricsBase;
    const candidates = [];
    try {
      if (window.HomeServices) {
        const resolved = window.HomeServices.get("metrics");
        if (resolved) candidates.push(resolved);
        for (const c of window.HomeServices.candidates("metrics") || []) candidates.push(c);
      }
    } catch {}
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
    for (const cand of Array.from(new Set(candidates.filter(Boolean)))) {
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
  const connectTo = useCallback(async (haUrl, accessToken, options = {}) => {
    if (!haUrl || !accessToken) return;
    if (!options.profileManaged && window.HomeServices && !window.HG_WEB_MODE) {
      try {
        const currentHa = window.HomeServices.get("ha");
        if (currentHa && normalizeServiceUrlForCompare(haUrl) !== normalizeServiceUrlForCompare(currentHa)) {
          window.HomeServices.setOverride("ha", haUrl);
        }
      } catch {}
    }
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
        let vllmBase = webDefaultBase("HG_DEFAULT_VLLM_BASE");
        if (!vllmBase) {
          const aiHost = new URL(mBase).hostname;
          vllmBase = `http://${aiHost}:8000`;
        }
        const r = await tauriFetch(`${vllmBase}/v1/models`);
        const j = await r.json();
        const data = j?.data || [];
        if (data.length > 0) {
          const models = data.map((m) => ({
            name: m.id,
            ctx: m.max_model_len ? `${Math.round(m.max_model_len / 1024)}k` : "",
          }));
          // Skip the model picker when there's nothing to decide: a single
          // model on the box, or the model the user already picked on a prior
          // connect is still available. Only a genuine multi-model choice with
          // no prior pick drops into the picker.
          const autoPick = models.length === 1
            ? models[0].name
            : (model && models.some((m) => m.name === model) ? model : null);
          if (autoPick) {
            setModel(autoPick);
            setConnection("online");
            addEvent({ kind: "system", text: `connected · model ${autoPick}`, tone: "ok" });
            return;
          }
          setAvailableModels(models);
          setConnection("picking-model");
          addEvent({ kind: "system", text: `${data.length} models on the ai box — choose one`, tone: "ok" });
          return;
        }
      } catch (e) {
        // CORS or unreachable — non-fatal; HA's agent has its own model binding.
      }
    }
    setConnection("online");
    addEvent({ kind: "system", text: `connected · home assistant ${haClientRef.current.haVersion || ""}`, tone: "ok" });
  }, [addEvent, metricsBase, probeMetricsBase, model]);

  const confirmModel = useCallback((name) => {
    if (name) setModel(name);
    setConnection("online");
    setAvailableModels(null);
    if (name) addEvent({ kind: "system", text: `model · ${name}`, tone: "ok" });
  }, [addEvent]);

  const syncServiceStateFromResolver = useCallback(() => {
    if (!window.HomeServices) return;
    const ha = window.HomeServices.get("ha");
    const metrics = window.HomeServices.get("metrics");
    const s2s = window.HomeServices.get("s2s");
    if (ha) setEndpoint(ha);
    if (metrics) setMetricsBase(metrics);
    if (s2s) setS2sBase(s2s);
    setServiceProfile(window.HomeServices.getProfile());
  }, []);

  const applyServiceProfile = useCallback((profileId) => {
    if (!window.HomeServices) return;
    const profile = window.HomeServices.setProfile(profileId);
    const nextEndpoint = window.HomeServices.get("ha");
    const nextMetrics = window.HomeServices.get("metrics");
    const nextS2s = window.HomeServices.get("s2s");
    try { haClientRef.current?.disconnect?.(); } catch (e) { /* best effort */ }
    try { s2sRunRef.current?.stop?.(); } catch (e) { /* best effort */ }
    setServiceProfile(profile);
    if (nextEndpoint) setEndpoint(nextEndpoint);
    if (nextMetrics) setMetricsBase(nextMetrics);
    if (nextS2s) setS2sBase(nextS2s);
    addEvent({ kind: "system", text: `profile · ${profile.label}`, tone: "info" });
    if (token && nextEndpoint) {
      setConnection("reconnecting");
      setTimeout(() => connectTo(nextEndpoint, token, { profileManaged: true }), 0);
    } else {
      setConnection("offline");
      resetFeedScrollTop();
    }
  }, [addEvent, connectTo, resetFeedScrollTop, token]);

  const runRemoteCheck = useCallback(async (announce = true) => {
    if (!window.HomeServices) return [];
    setServiceProbeRunning(true);
    try {
      const results = await window.HomeServices.probeAll();
      setServiceProbeResults(results);
      const readiness = window.HomeServices.buildTravelReadiness(results);
      setServiceReadiness(readiness);
      const ok = results.filter((r) => r.ok).length;
      const bad = results.length - ok;
      if (announce) {
        addEvent({
          kind: "system",
          text: bad ? `remote check · ${ok}/${results.length} reachable · ${bad} failing` : `remote check · ${ok}/${results.length} reachable`,
          tone: bad ? "warn" : "ok",
        });
      }
      syncServiceStateFromResolver();
      return results;
    } catch (e) {
      if (announce) addEvent({ kind: "system", text: `remote check failed · ${e?.message || e}`, tone: "error" });
      return [];
    } finally {
      setServiceProbeRunning(false);
    }
  }, [addEvent, syncServiceStateFromResolver]);

  const copyDebugBundle = useCallback(async () => {
    if (!window.HomeServices) return;
    const bundle = window.HomeServices.debugBundle({
      connection,
      endpoint,
      metricsBase,
      s2sBase,
      lastProbe: serviceProbeResults,
      viewport,
      webMode: !!window.HG_WEB_MODE,
      userAgent: navigator.userAgent,
    });
    const text = JSON.stringify(bundle, null, 2);
    try {
      await navigator.clipboard?.writeText?.(text);
      addEvent({ kind: "system", text: "debug bundle copied to clipboard", tone: "ok" });
    } catch (e) {
      addEvent({ kind: "system", text: `debug bundle:\n${text.slice(0, 1800)}`, tone: "info" });
    }
  }, [addEvent, connection, endpoint, metricsBase, s2sBase, serviceProbeResults, viewport]);

  const currentTravelReadiness = useCallback(() => {
    if (!window.HomeServices) return null;
    return window.HomeServices.buildTravelReadiness(serviceProbeResults);
  }, [serviceProbeResults]);

  const announceTravelReadiness = useCallback((readiness) => {
    if (!window.HomeServices || !readiness) return;
    const tone = readiness.status === "blocked" ? "error"
      : readiness.status === "ready" ? "ok"
      : readiness.status === "unknown" ? "info" : "warn";
    addEvent({ kind: "system", text: window.HomeServices.formatReadiness(readiness), tone });
  }, [addEvent]);

  const runTravelCheck = useCallback(async () => {
    if (!window.HomeServices) return null;
    const results = await runRemoteCheck(false);
    const readiness = window.HomeServices.buildTravelReadiness(results);
    setServiceReadiness(readiness);
    announceTravelReadiness(readiness);
    return readiness;
  }, [announceTravelReadiness, runRemoteCheck]);

  const copyTravelBundle = useCallback(async () => {
    if (!window.HomeServices) return;
    const bundle = window.HomeServices.debugBundle({
      connection,
      endpoint,
      metricsBase,
      s2sBase,
      lastProbe: serviceProbeResults,
      viewport,
      webMode: !!window.HG_WEB_MODE,
      userAgent: navigator.userAgent,
    });
    const text = JSON.stringify(bundle, null, 2);
    try {
      await navigator.clipboard?.writeText?.(text);
      addEvent({ kind: "system", text: "travel debug bundle copied to clipboard", tone: "ok" });
    } catch (e) {
      addEvent({ kind: "system", text: `travel debug bundle:\n${text.slice(0, 1800)}`, tone: "info" });
    }
  }, [addEvent, connection, endpoint, metricsBase, s2sBase, serviceProbeResults, viewport]);

  const copyRecoveryCommands = useCallback(async () => {
    if (!window.HomeServices) return;
    const readiness = currentTravelReadiness();
    const text = window.HomeServices.formatRecoveryCommands(readiness);
    try {
      await navigator.clipboard?.writeText?.(text);
      addEvent({ kind: "system", text: "travel recovery commands copied", tone: "ok" });
    } catch (e) {
      addEvent({ kind: "system", text: `travel recovery:\n${text.slice(0, 1800)}`, tone: "info" });
    }
  }, [addEvent, currentTravelReadiness]);

  const copyServiceUrls = useCallback(async () => {
    if (!window.HomeServices) return;
    const text = window.HomeServices.formatServiceUrls();
    try {
      await navigator.clipboard?.writeText?.(text);
      addEvent({ kind: "system", text: "service URL table copied", tone: "ok" });
    } catch (e) {
      addEvent({ kind: "system", text: `service URLs:\n${text}`, tone: "info" });
    }
  }, [addEvent]);

  /* Auto-reconnect on launch if we have stored credentials.
   * Sim Mode: skip — sim scenarios provide their own connection state
   * via the synthesis effect. */
  useEffect(() => {
    if (sim.active) return;
    if (connection === "reconnecting" && endpoint && token) {
      connectTo(endpoint, token);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* Sim Mode transitions — tear down on activate, reconnect on deactivate.
   *
   * ON ACTIVATE (sim → true):
   *   Close any open HA WebSocket so real state_changed events stop
   *   leaking into the mocked view. Voice WS is already blocked at
   *   construction site; s2s ref stop is no-op if not active.
   *
   * ON DEACTIVATE (sim → false):
   *   The user typed /simulation off. We need to actively re-open the
   *   HA WS so live mode works again — useEffect polling resumes via
   *   dependency-array reruns, but WebSockets need an explicit connect
   *   call. If creds are present, reconnect; otherwise hint the user. */
  const prevSimActiveRef = useRef(sim.active);
  useEffect(() => {
    const wasActive = prevSimActiveRef.current;
    const isActive = sim.active;
    prevSimActiveRef.current = isActive;
    if (wasActive === isActive) return; // no-op on first render

    if (isActive) {
      // sim → ON: tear down real connections
      try { haClientRef.current?.disconnect?.(); } catch (e) { /* ignore */ }
      try { s2sRunRef.current?.stop?.(); } catch (e) { /* ignore */ }
      // streamKey bump is handled by the camera frame remount via sim prop
    } else {
      // sim → OFF: re-establish HA connection if we have creds.
      // The polling useEffects already re-run because they depend on
      // sim.active; only the WS needs an explicit reconnect.
      if (endpoint && token) {
        addEvent({ kind: "system", text: "exiting simulation — reconnecting…", tone: "info" });
        connectTo(endpoint, token);
      } else {
        addEvent({
          kind: "system",
          text: "simulation off · type /connect <url> <token> to reconnect",
          tone: "info",
        });
        // Reset connection state so the FirstRun panel shows up again
        setConnection("offline");
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sim.active]);

  /* ── Metrics polling ──────────────────────────────────────────────── */
  useEffect(() => {
    if (sim.active) return undefined; // Sim Mode: fixtures injected directly
    if (connection !== "online") return undefined;
    const base = metricsBase || metricsBaseFromEndpoint(endpoint);
    let cancelled = false;
    const tick = async () => {
      try {
        // cache: "no-store" — the WebView2 HTTP stack can otherwise
        // serve a cached body and the metrics would look frozen even
        // when the sidecar is returning fresh samples. The bridge
        // /healthz poll already does this; the metrics poll must too.
        const r = await tauriFetch(`${base}/metrics`, { cache: "no-store" });
        if (!r.ok) {
          if (!cancelled) {
            setSidecarOnline(false);
            setMetrics(CLEARED_METRICS);
            setMetricsHistory(emptyMetricsHistory());
            if (typeof window !== "undefined") window.__hav_metricsLatest = null;
          }
          return;
        }
        const m = await r.json();
        if (cancelled) return;
        setSidecarOnline(true);
        // Phase 1 bugfix: NVML reports 103 GB total on the user's RTX 6000
        // Blackwell (96 GB spec) — includes ECC/fabric overhead the driver
        // doesn't expose as user-allocatable. Clamp to the spec-sheet value
        // so the UI shows what the user expects. Known Blackwell sizes:
        //   RTX 6000  96 GB
        //   B100/B200 192 GB
        // If NVML reports 96..104, treat as 96. Same idea for 192..200.
        const KNOWN_VRAM_SPECS = [96, 192];
        setMetrics((prev) => {
          const hasMetric = (key) => Object.prototype.hasOwnProperty.call(m, key);
          const reportedMax = hasMetric("vram_total_gb") ? m.vram_total_gb : prev.vramMax;
          const specMax = typeof reportedMax === "number" ? KNOWN_VRAM_SPECS.find(
            (s) => reportedMax >= s && reportedMax <= s + 8
          ) ?? reportedMax : reportedMax;
          return {
            ...prev,
            model:   hasMetric("model") ? (m.model || null) : prev.model,
            ttft:    hasMetric("ttft_ms") ? m.ttft_ms : prev.ttft,
            tps:     hasMetric("tps") ? m.tps : prev.tps,
            gpu:     hasMetric("gpu_util_pct") ? m.gpu_util_pct : prev.gpu,
            vram:    hasMetric("vram_used_gb") ? m.vram_used_gb : prev.vram,
            vramMax: specMax,
            cpu:     hasMetric("cpu_pct") ? m.cpu_pct : prev.cpu,
            ram:     hasMetric("ram_used_gb") ? m.ram_used_gb : prev.ram,
            ramMax:  hasMetric("ram_total_gb") ? m.ram_total_gb : prev.ramMax,
            // F-19 (Addendum 27 P0): GPU temperature in °C from NVML via
            // the sidecar /metrics endpoint. null when sidecar hasn't
            // been patched OR NVML can't read temp (unsupported GPU).
            // Consumed by the rail's temp pill — render only when non-null.
            tempC:   hasMetric("gpu_temp_c") ? m.gpu_temp_c : prev.tempC,
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
            // v6: 200 samples × 750ms poll = ~2.5 min trend window for
            // each metric. Memory cost: 6 metrics × 200 × 8 bytes ≈ 10 KB.
            next[k] = arr.slice(-200);
          }
          return next;
        });
        // Lab tab (Addendum 10): timestamped sample for time-aligned chart.
        // Pushed to a rolling 600-cap ref so the lab chart can render
        // resource lines aligned to per-turn windows. Capture happens
        // regardless of active tab so the buffer fills in background.
        const reportedMaxLab = m.vram_total_gb ?? 0;
        const specMaxLab = [96, 192].find(
          (s) => reportedMaxLab >= s && reportedMaxLab <= s + 8
        ) ?? reportedMaxLab;
        const buildLabSample = (row, clientNow) => {
          const reportedMax = row?.vram_total_gb ?? reportedMaxLab;
          const specMax = [96, 192].find(
            (s) => reportedMax >= s && reportedMax <= s + 8
          ) ?? reportedMax;
          const tempCRaw = typeof row?.gpu_temp_c === "number"
            ? row.gpu_temp_c
            : (typeof m.gpu_temp_c === "number" ? m.gpu_temp_c : null);
          const ramTotal = row?.ram_total_gb ?? m.ram_total_gb ?? 0;
          const ramUsed = row?.ram_used_gb ?? m.ram_used_gb ?? 0;
          const ageMs = typeof row?.age_ms === "number" ? row.age_ms : null;
          return {
            t: ageMs != null ? clientNow - ageMs : clientNow,
            resourceSeq: row?.seq ?? null,
            gpuPct: ((row?.gpu_util_pct ?? m.gpu_util_pct) ?? 0) / 100,
            vramPct: specMax > 0 ? Math.min(1, ((row?.vram_used_gb ?? m.vram_used_gb) ?? 0) / specMax) : 0,
            vramUsedGb: (row?.vram_used_gb ?? m.vram_used_gb) ?? 0,
            vramTotalGb: specMax,
            cpuPct: ((row?.cpu_pct ?? m.cpu_pct) ?? 0) / 100,
            ramPct: ramTotal > 0 ? Math.min(1, ramUsed / ramTotal) : 0,
            ramUsedGb: ramUsed,
            ramTotalGb: ramTotal,
            tempC: tempCRaw,
            tempPct: tempCRaw != null ? Math.max(0, Math.min(1, tempCRaw / 100)) : null,
            tps: m.tps,
            ttftMs: m.ttft_ms,
          };
        };
        const clientNow = Date.now();
        const highResRows = Array.isArray(m.resource_samples) ? m.resource_samples : [];
        const labSamples = [];
        if (highResRows.length > 0 && typeof window !== "undefined") {
          const boot = m.resource_boot_id || "default";
          const prior = window.__hav_resourceSampleSeqSeen;
          if (!prior || prior.boot !== boot || !(prior.set instanceof Set)) {
            window.__hav_resourceSampleSeqSeen = { boot, set: new Set(), order: [] };
          }
          const seen = window.__hav_resourceSampleSeqSeen;
          for (const row of highResRows) {
            if (row?.seq == null) continue;
            const key = `${boot}:${row.seq}`;
            if (seen.set.has(key)) continue;
            seen.set.add(key);
            seen.order.push(key);
            labSamples.push(buildLabSample(row, clientNow));
          }
          while (seen.order.length > 1200) {
            const old = seen.order.shift();
            seen.set.delete(old);
          }
        } else {
          labSamples.push(buildLabSample(null, clientNow));
        }
        // Lab tab feature-detect: home-metrics-lab.jsx reads
        // window.__hav_metricsLatest.gpu_temp_c to decide whether to render
        // the 4th (TEMP) resource line. Falls back to 3 rows (gpu/cpu/ram)
        // when the sidecar hasn't been patched with NVML temperature.
        if (typeof window !== "undefined") window.__hav_metricsLatest = m;
        for (const labSample of labSamples) {
          labSamplesRef.current.push(labSample);
          try {
            const H = window.HomeMetricsLabHelpers;
            if (H && typeof H.attachSampleToRecentTurns === "function") {
              H.attachSampleToRecentTurns(labTurnsRef.current, labSample);
            }
          } catch (e) { /* swallow - never break the poll on a helper miss */ }
        }
        if (labSamplesRef.current.length > 1800) {
          labSamplesRef.current = labSamplesRef.current.slice(-1800);
        }
        // Addendum 32 Phase C fix (H3 — snapshot timing):
        // turn.samples is the IMMUTABLE snapshot taken at turn-creation
        // time. Samples arriving AFTER turn creation never got attached.
        // /lab-dump confirmed: `live_in_window_count: 15`,
        // `persisted_samples_count: 0` for fresh in-flight stub.
        // Fix: attachSampleToRecentTurns (helpers.js) appends the new
        // sample to any of the last 5 turns whose ±3s window contains
        // the sample's t. Pure-function helper; tested in run-lab-tests.js.
        // Trigger lab re-render only every 4 samples (~3s) to keep
        // SVG repaints out of the hot path.
        if (labSamples.length > 0) {
          setLabTick((t) => t + 1);
        }
      } catch (e) {
        if (!cancelled) {
          setSidecarOnline(false);
          setMetrics(CLEARED_METRICS);
          setMetricsHistory(emptyMetricsHistory());
          if (typeof window !== "undefined") window.__hav_metricsLatest = null;
        }
      }
    };
    tick();
    // Addendum 12 step 7: adaptive cadence. 250 ms during an active turn
    // (last trace fired within 3 s OR voice.state !== "inactive") so the
    // lab chart's per-stage anchors get real data; back to 750 ms when
    // idle to keep idle cost the same. The interval-id is recomputed
    // every second by the adapter timer below; when a new turn arrives
    // the next tick downshifts within ~1 s. Pure-function tested in
    // tools/run-lab-tests.js → "adaptive poll cadence" suite.
    let intervalMs = 750;
    let tHandle = setInterval(tick, intervalMs);
    const adapter = setInterval(() => {
      const H = window.HomeMetricsLabHelpers;
      if (!H) return;
      const latest = labTurnsRef.current[labTurnsRef.current.length - 1];
      const lastTurnEndedAt = latest ? latest.endedAt : 0;
      const nextMs = H.pickPollInterval({
        nowMs: Date.now(),
        lastTurnEndedAt: lastTurnEndedAt,
        voiceState: (window.__hav_voiceState || "inactive"),
      });
      if (nextMs !== intervalMs) {
        clearInterval(tHandle);
        intervalMs = nextMs;
        tHandle = setInterval(tick, intervalMs);
      }
    }, 1000);
    return () => { cancelled = true; clearInterval(tHandle); clearInterval(adapter); };
  }, [connection, endpoint, metricsBase, sim.active]);

  /* ── HA conversation: send text via assist_pipeline/run ────────────── */
  const sendToHA = useCallback(async (text) => {
    // Simulation Mode: no real HA pipeline available. Echo the message
    // back as a system note so the designer sees obvious feedback and
    // suggest the story scenarios for scripted demos.
    if (sim.active) {
      addEvent({ kind: "user", text });
      addEvent({
        kind: "system",
        text: "[sim] live HA pipeline is mocked off. Try `/sim action-success`, `/sim action-failed`, or `/sim movie-mode` to see a scripted demo turn.",
        tone: "info",
      });
      return;
    }
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

    // Addendum 29 Change 2: in-flight stub for the trace bar. Pushes a
    // placeholder LabTurn that the renderer shows as a pulsing band at
    // the right edge. When the real trace arrives via SSE/poll, the
    // grouping helper (Change 3) matches by user_text and merges into
    // this stub — the inFlight flag is cleared in processTrace. If the
    // request silently errors, evictStaleInFlight (60s sweep below)
    // cleans it up.
    const inFlightStub = {
      id: `inflight-${Date.now()}-${(text || "").slice(0, 16)}`,
      transcript: text,
      assistant_text: "",
      startedAt: Date.now(),
      endedAt: Date.now(),
      totalMs: 0,
      stages: [],
      samples: [],
      sidecar_ids: [],
      pending_tools: [],
      inFlight: true,
      cold: false, slow: false, crit: false,
    };
    // Addendum 32 Phase C follow-up: back-fill samples from the buffer
    // that ALREADY landed in this stub's ±3s window. The poll's
    // attachSampleToRecentTurns only catches FUTURE samples; pre-existing
    // ones (typically 3-6 samples from the last few seconds before the
    // user typed) would otherwise be lost. /lab-dump showed 30 samples
    // in window but only 11 attached — this closes that gap.
    {
      const stubStart = inFlightStub.startedAt;
      const stubEnd = inFlightStub.endedAt;
      const bufSamples = labSamplesRef.current || [];
      for (let i = 0; i < bufSamples.length; i++) {
        const s = bufSamples[i];
        if (typeof s.t !== "number") continue;
        if (s.t < stubStart - 3000) continue;
        if (s.t > stubEnd + 3000) continue;
        inFlightStub.samples.push(s);
      }
      // Per-turn cap (same as the helper)
      if (inFlightStub.samples.length > 50) {
        inFlightStub.samples = inFlightStub.samples.slice(-50);
      }
    }
    labTurnsRef.current.push(inFlightStub);
    setLabTick((t) => t + 1);
    if (typeof window !== "undefined") {
      window.__INFLIGHT_LAST_STUB = inFlightStub;
    }

    const t0 = performance.now();
    // Per-turn state: which tools the LLM called this turn, and the id of
    // the live streaming bubble (so intent-end can lock it). Both live in
    // closure variables, not refs, because they reset every turn.
    const turnToolCalls = [];
    let streamingBubbleId = null;
    const finalizeStreamingBubble = () => {
      const id = streamingBubbleId;
      if (!id) return false;
      streamingBubbleId = null;
      streamingIds.current.delete(id);
      setEvents((prev) => prev.map((e) =>
        e.id === id ? { ...e, streaming: false } : e));
      return true;
    };

    const onEvent = (haEvent) => {
      if (!haEvent || !haEvent.type) return;
      console.log("[ha event]", haEvent.type, haEvent.data);

      // ── Streaming partials (Phase 0.5+ progressive reveal) ─────────────
      // HA 2026.5.1's extended_openai_conversation emits chat_log_delta as
      // separate events: one role marker (no content), then content-only
      // deltas, then optionally a tool_calls delta. extractIntentProgress
      // (home-ha.jsx:478) parses each shape and returns either {textDelta}
      // or {toolCalls} — never both. Tool *arguments* arrive ~600ms in,
      // before any answer text streams; tool *results* never appear in any
      // event (see Phase A findings in the plan), which is why we fetch
      // /reason/latest after intent-end for the perception card.
      if (haEvent.type === "intent-progress") {
        const prog = (typeof extractIntentProgress === "function")
          ? extractIntentProgress(haEvent) : null;
        if (!prog) return;

        if (prog.toolCalls && prog.toolCalls.length) {
          for (const tc of prog.toolCalls) {
            turnToolCalls.push(tc);
            // Small "looking…" beat so the user sees the LLM commit to a
            // tool ~600ms in. describe_camera carries an explicit camera
            // in args; look auto-routes (camera not in args).
            const friendly = tc.name === "look" ? "looking"
              : tc.name === "describe_camera" ? "looking quickly"
              : tc.name === "refresh_perception" ? "looking freshly"
              : tc.name === "look_zoom" ? "looking closer"
              : tc.name;
            const camHint = (tc.args && (tc.args.camera || tc.args.room))
              ? ` (${tc.args.camera || tc.args.room})` : "";
            addEvent({ kind: "system", tone: "info",
                       text: `${friendly}${camHint}…` });
          }
          return;
        }

        if (prog.textDelta) {
          setEvents((prev) => {
            const last = prev[prev.length - 1];
            if (last && last.kind === "home" && last.streaming && last.id === streamingBubbleId) {
              // Append the delta. HA sends deltas (not accumulated text),
              // unlike the s2s personaplex bridge which sends snapshots.
              return [...prev.slice(0, -1),
                      { ...last, text: (last.text || "") + prog.textDelta }];
            }
            // First content delta of the turn — replace the thinking stub.
            const next = prev.filter((e) => e.id !== thinkingId);
            const newId = nextId();
            streamingBubbleId = newId;
            streamingIds.current.add(newId);
            return [...next, {
              id: newId, kind: "home", time: fmtTime(),
              text: prog.textDelta, streaming: true,
            }];
          });
          return;
        }
      }

      if (haEvent.type === "intent-end") {
        const { convId } = extractIntentEnd(haEvent);
        if (convId) setConversationId(convId);
        // Lock the streaming bubble (if one exists) so the trailing stop-
        // button / visual cue settles.
        finalizeStreamingBubble();
        // Surface a perception card per vision tool the LLM called this
        // turn. Tool results are NEVER in HA events, so we hit the
        // sidecar's ring-buffer endpoints (/reason/latest, /describe/latest)
        // as a side-channel. Each tool gets a separate fetch — the LLM may
        // call look only, describe_camera/refresh_perception only, or both. The text format
        // uses "<camera>: <answer>" so PerceptionContent's room-extractor
        // (home-events.jsx:423) parses the room out and labels the card.
        const visionTools = turnToolCalls.filter((t) =>
          t.name === "look" || t.name === "look_zoom" ||
          t.name === "describe_camera" || t.name === "refresh_perception");
        if (visionTools.length) {
          try {
            const base = metricsBase || metricsBaseFromEndpoint(endpoint);
            if (base) {
              const visionOrigin = visionBaseFromEndpoint(base, endpoint);
              const tauriFetch = window.tauriFetch || fetch;
              const seen = new Set();
              for (const tc of visionTools) {
                if (seen.has(tc.name)) continue;   // dedupe within turn
                seen.add(tc.name);
                const isDescribe = tc.name === "describe_camera" || tc.name === "refresh_perception";
                const latestPath = isDescribe ? "/describe/latest" : "/reason/latest";
                tauriFetch(visionOrigin + latestPath, { cache: "no-store" })
                  .then((r) => r.ok ? r.json() : null)
                  .then((data) => {
                    if (!data) return;
                    let snapshotUrl = null;
                    let cardText = "";
                    const cam = (data.camera || "").replace(/[^a-z_]/gi, "_");
                    if (isDescribe) {
                      const desc = data.description || "(no description)";
                      if (data.snapshot_url) {
                        snapshotUrl = visionOrigin + data.snapshot_url + "?cb=" + Date.now();
                      }
                      cardText = `${cam}: ${desc}`;
                    } else {
                      if (data.annotated_url) {
                        snapshotUrl = visionOrigin + data.annotated_url + "?cb=" + Date.now();
                      }
                      cardText = `${cam}: ${data.answer || "(grounded look)"}`;
                    }
                    addEvent({
                      kind: "perception",
                      text: cardText,
                      snapshotUrl,
                    });
                  })
                  .catch((err) => {
                    console.error(`[perception] ${tc.name} fetch err`, err);
                  });
              }
            }
          } catch (e) {
            console.error("[perception] outer", e);
          }
        }
        return;
      }
      if (haEvent.type === "error") {
        const msg = haEvent.data?.message || "pipeline error";
        addEvent({ kind: "system", text: msg, tone: "error" });
      }
    };

    const pipelineText = muteState?.muted
      ? `just this once, ${text}`
      : text;
    if (muteState?.muted) {
      addEvent({
        kind: "system",
        text: "muted · running typed command once; voice stays quiet",
        tone: "info",
      });
    }
    const run = haClientRef.current.runPipeline({
      text: pipelineText,
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
      // Defensive: if streaming never started (thinking stub never replaced
      // by a streaming bubble), the existing filter removes the stub. If it
      // did start but HA never emitted intent-end, settle that bubble too so
      // the STOP affordance never remains wedged after run.done resolves.
      const pendingStreamingId = streamingBubbleId;
      if (pendingStreamingId) {
        streamingBubbleId = null;
        streamingIds.current.delete(pendingStreamingId);
      }
      setEvents((prev) => prev
        .filter((e) => e.id !== thinkingId)
        .map((e) => pendingStreamingId && e.id === pendingStreamingId
          ? { ...e, streaming: false }
          : e));
      if (activeRunRef.current?.id === run.id) activeRunRef.current = null;
    }
  }, [connection, conversationId, addEvent, metricsBase, endpoint, muteState?.muted]);

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
  }, [addEvent, sim.active]);

  /* ── Inline control cards (light / media) ─────────────────────────────
   * onControlAction is the thin HA executor the cards call to fire a
   * service. STABLE useCallback (render-stability discipline — the cards
   * key effects off it; an unstable ref would churn their debounce/reconcile
   * timers, the class of bug that hung the boot sequence). `target` is the
   * explicit HA target object the card built for THIS control — a group
   * target for transport, a single-entity target for per-zone volume /
   * Onkyo routing. In sim mode the card routes to SimControlStore itself
   * and never calls this. */
  const onControlAction = useCallback(async ({ domain, service, service_data, target }) => {
    const client = haClientRef.current;
    if (!client) throw new Error("HA not connected");
    if (!target || (!target.entity_id && !target.area_id && !target.device_id)) {
      throw new Error("no resolvable target");
    }
    await client.call({
      type: "call_service",
      domain, service,
      service_data: service_data || {},
      target,
    });
  }, []);

  /* Drives time-based control-card expiry. Only ticks while a controllable
   * card is in the feed; otherwise idle. (During an active conversation,
   * events change anyway → re-render → expiry re-derived; the ticker only
   * matters for idle stretches.) */
  const [_lifecycleTick, setLifecycleTick] = useState(0);
  useEffect(() => {
    const hasControlCards = events.some((e) => e.kind === "action" && e.controllable);
    if (!hasControlCards) return undefined;
    const iv = setInterval(() => setLifecycleTick((t) => t + 1), 60000);
    return () => clearInterval(iv);
  }, [events]);

  /* Per-event lifecycle map for control cards — memoized so its identity is
   * stable between renders (render-stability discipline). */
  const controlLifecycles = useMemo(
    () => (window.deriveControlLifecycles ? window.deriveControlLifecycles(events) : new Map()),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [events, _lifecycleTick],
  );

  /* Expose the HA base URL so control cards can build album-art URLs from
   * HA's relative (signed) entity_picture paths. */
  useEffect(() => { window.__hav_haEndpoint = endpoint || ""; }, [endpoint]);

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
      case "stack-token":
      case "stacktoken":
        // Saves the STACK_TOKEN to localStorage so AiStackCard reads
        // it on next render. See docs/RUNBOOK.md → "Workstation token
        // storage" → DevTools quick path. Phase 4 Tauri-bootstrap
        // will replace this with a 0600 config file.
        if (arg) {
          try {
            localStorage.setItem("hg-stack-token-DEV", arg.trim());
            addEvent({ kind: "system", text: "stack token saved · reload to pick up (Ctrl+R)", tone: "ok" });
          } catch (e) {
            addEvent({ kind: "system", text: `stack token save failed · ${e?.message || "localStorage error"}`, tone: "error" });
          }
        } else {
          if (typeof window !== "undefined" && window.__HOME_WEB_STACK_TOKEN_PROXY) {
            addEvent({ kind: "system", text: "stack token is proxied by the web gateway; no browser token needed", tone: "ok" });
            return true;
          }
          const existing = (typeof localStorage !== "undefined" && localStorage.getItem("hg-stack-token-DEV")) || "";
          if (existing) {
            addEvent({ kind: "system", text: `stack token · <set, ${existing.length} chars>`, tone: "info" });
          } else {
            addEvent({ kind: "system", text: "usage: /stack-token <STACK_TOKEN from /opt/home-ai-voice/.env>", tone: "info" });
          }
        }
        return true;
      case "mute": {
        // /mute              -> set explicit boolean (manual mute)
        // /mute 2h | 30m     -> set timer
        // /mute movie        -> turn on input_boolean.homeai_movie (composite kicks in)
        const a = arg.trim();
        const client = haClientRef.current;
        if (!client) { addEvent({ kind: "system", text: "not connected to HA", tone: "warn" }); return true; }
        const dur = a.match(/^(\d+)\s*(h|hour|hours|m|min|minute|minutes)\b/i);
        if (dur) {
          const n = Number(dur[1]);
          const mins = n * (/^h/i.test(dur[2]) ? 60 : 1);
          const until = new Date(Date.now() + mins * 60000)
            .toISOString().replace(/\.\d+Z$/, "");
          client.callService("input_datetime", "set_datetime", {
            entity_id: "input_datetime.jarvis_mute_until", datetime: until,
          }).catch((e) => addEvent({ kind: "system", text: `mute timer set failed: ${e?.message}`, tone: "error" }));
          addEvent({ kind: "system", text: `muted for ${mins} min · auto-resumes`, tone: "ok" });
        } else if (/^movie/i.test(a)) {
          client.callService("input_boolean", "turn_on", { entity_id: "input_boolean.homeai_movie" })
            .catch((e) => addEvent({ kind: "system", text: `movie mode failed: ${e?.message}`, tone: "error" }));
          addEvent({ kind: "system", text: "movie mode on · Jarvis quiet until movie ends", tone: "ok" });
        } else {
          client.callService("input_boolean", "turn_on", { entity_id: "input_boolean.jarvis_muted_explicit" })
            .catch((e) => addEvent({ kind: "system", text: `mute failed: ${e?.message}`, tone: "error" }));
          client.callService("input_text", "set_value", {
            entity_id: "input_text.jarvis_mute_reason", value: "typed command",
          }).catch(() => {});
          addEvent({ kind: "system", text: "muted · say 'Hey Jarvis, wake up' or /unmute to resume", tone: "ok" });
        }
        return true;
      }
      case "unmute": {
        const client = haClientRef.current;
        if (!client) { addEvent({ kind: "system", text: "not connected to HA", tone: "warn" }); return true; }
        client.callService("input_boolean", "turn_off", { entity_id: "input_boolean.jarvis_muted_explicit" })
          .catch((e) => addEvent({ kind: "system", text: `unmute failed: ${e?.message}`, tone: "error" }));
        client.callService("input_datetime", "set_datetime", {
          entity_id: "input_datetime.jarvis_mute_until", datetime: "2000-01-01T00:00:00",
        }).catch(() => {});
        addEvent({ kind: "system", text: "unmuted · movie_mode + TV-active auto-signals still apply", tone: "ok" });
        return true;
      }
      case "model":
        if (arg) { setModel(arg); addEvent({ kind: "system", text: `model · ${arg}`, tone: "ok" }); }
        else addEvent({ kind: "system", text: "usage: /model <name>", tone: "info" });
        return true;
      case "metrics":
        if (arg) {
          try { window.HomeServices?.setOverride?.("metrics", arg); } catch {}
          syncServiceStateFromResolver();
          setMetricsBase(arg);
          addEvent({ kind: "system", text: `metrics base · ${arg}`, tone: "ok" });
        }
        else addEvent({ kind: "system", text: `metrics base · ${metricsBase || metricsBaseFromEndpoint(endpoint)}`, tone: "info" });
        return true;
      case "profile": {
        const sub = arg.trim().toLowerCase();
        if (!window.HomeServices) {
          addEvent({ kind: "system", text: "service profiles are not available in this runtime", tone: "warn" });
          return true;
        }
        if (!sub || sub === "status") {
          const profile = window.HomeServices.getProfile();
          const services = window.HomeServices.getAll();
          const lines = [
            `profile · ${profile.label}`,
            `  ha: ${services.ha}`,
            `  metrics: ${services.metrics}`,
            `  s2s: ${services.s2s}`,
            `  frigate: ${services.frigate}`,
          ];
          addEvent({ kind: "system", text: lines.join("\n"), tone: "info" });
          return true;
        }
        if (sub === "lan" || sub === "home-lan") {
          applyServiceProfile("home-lan");
          return true;
        }
        if (sub === "tailscale" || sub === "tail" || sub === "remote") {
          applyServiceProfile("tailscale");
          return true;
        }
        if (sub === "custom") {
          applyServiceProfile("custom");
          setRemotePanelOpen(true);
          return true;
        }
        addEvent({ kind: "system", text: "usage: /profile status|lan|tailscale|custom", tone: "info" });
        return true;
      }
      case "remote": {
        const sub = arg.trim().toLowerCase();
        if (!sub || sub === "check" || sub === "status") {
          runRemoteCheck(true);
        } else {
          addEvent({ kind: "system", text: "usage: /remote check", tone: "info" });
        }
        return true;
      }
      case "travel": {
        const sub = arg.trim().toLowerCase();
        if (!window.HomeServices) {
          addEvent({ kind: "system", text: "travel readiness is not available in this runtime", tone: "warn" });
          return true;
        }
        if (!sub || sub === "check") {
          runTravelCheck();
          return true;
        }
        if (sub === "status") {
          const readiness = currentTravelReadiness();
          setServiceReadiness(readiness);
          announceTravelReadiness(readiness);
          return true;
        }
        if (sub === "recovery") {
          copyRecoveryCommands();
          return true;
        }
        if (sub === "bundle" || sub === "debug") {
          copyTravelBundle();
          return true;
        }
        addEvent({ kind: "system", text: "usage: /travel check|status|recovery|bundle", tone: "info" });
        return true;
      }
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
        try { window.HomeServices?.setOverride?.("s2s", sub); } catch {}
        syncServiceStateFromResolver();
        setS2sBase(sub);
        addEvent({ kind: "system", text: `s2s bridge · ${sub}`, tone: "ok" });
        return true;
      }
      case "lab-dump": {
        // Addendum 32 Phase A: evidence-gathering for the
        // "resource lines flatten to synthetic baselines" bug.
        // Emits a structured snapshot of labSamplesRef +
        // labTurnsRef + last-anchor stats. User pastes the
        // resulting chat-system event back so we can diagnose
        // against the H1-H9 hypothesis table.
        const samples = labSamplesRef.current || [];
        const turns = labTurnsRef.current || [];
        const anchorStats = (typeof window !== "undefined" && window.__hav_lastAnchorStats) || {};
        const valueSummary = (() => {
          if (samples.length === 0) return null;
          const metrics = ["gpu", "cpu", "ram", "temp"];
          const out = {};
          for (const m of metrics) {
            const key = `${m}Pct`;
            const vals = samples.map((s) => s[key]).filter((v) => typeof v === "number" && !isNaN(v));
            if (vals.length === 0) { out[m] = null; continue; }
            let mn = vals[0], mx = vals[0], sum = 0;
            for (let i = 0; i < vals.length; i++) {
              if (vals[i] < mn) mn = vals[i];
              if (vals[i] > mx) mx = vals[i];
              sum += vals[i];
            }
            out[m] = {
              min: +mn.toFixed(3),
              max: +mx.toFixed(3),
              mean: +(sum / vals.length).toFixed(3),
              last: +vals[vals.length - 1].toFixed(3),
              count: vals.length,
            };
          }
          return out;
        })();
        // Density distribution across all turns
        const sampleCounts = turns.map((t) => (t.samples || []).length).sort((a, b) => a - b);
        const pct = (arr, p) => arr.length === 0 ? 0 : arr[Math.floor((arr.length - 1) * p)];
        const recent = turns.slice(-5).map((t) => {
          const stageTotalMs = (t.stages || []).reduce((acc, s) => acc + (s.ms || 0), 0);
          const liveInWindow = samples.filter(
            (s) => s.t >= (t.startedAt || 0) - 3000 && s.t <= (t.endedAt || 0) + 3000
          ).length;
          // Clock skew: first sample in turn vs turn startedAt
          const inTurnSamples = (t.samples || []);
          const firstInTurnT = inTurnSamples.length > 0 ? inTurnSamples[0].t : null;
          const clockSkewMs = (firstInTurnT && t.startedAt) ? firstInTurnT - t.startedAt : null;
          // Anchor stats per metric for this turn
          const stats = {};
          for (const m of ["gpu", "cpu", "ram", "temp"]) {
            const k = `${t.id}_${m}`;
            if (anchorStats[k]) stats[m] = anchorStats[k];
          }
          return {
            id: (t.id || "").slice(0, 40),
            inFlight: !!t.inFlight,
            startedAt: t.startedAt,
            endedAt: t.endedAt,
            totalMs: t.totalMs,
            stage_total_ms: stageTotalMs,
            stage_total_eq_totalMs: stageTotalMs === t.totalMs,
            persisted_samples_count: (t.samples || []).length,
            live_in_window_count: liveInWindow,
            clock_skew_first_sample_ms: clockSkewMs,
            stage_labels: (t.stages || []).map((s) => s.label),
            anchor_stats: stats,
          };
        });
        const dump = {
          now_ms: Date.now(),
          render_tick: (typeof window !== "undefined" && window.__hav_renderTick) || null,
          samples_total: samples.length,
          samples_first_t: samples[0]?.t,
          samples_last_t: samples.slice(-1)[0]?.t,
          samples_age_seconds: samples.length > 0 ? Math.round((Date.now() - samples.slice(-1)[0].t) / 1000) : null,
          samples_first_keys: samples[0] ? Object.keys(samples[0]) : [],
          samples_value_summary: valueSummary,
          turns_total: turns.length,
          turn_density_distribution: {
            min: sampleCounts.length > 0 ? sampleCounts[0] : 0,
            p10: pct(sampleCounts, 0.1),
            p50: pct(sampleCounts, 0.5),
            p90: pct(sampleCounts, 0.9),
            max: sampleCounts.length > 0 ? sampleCounts[sampleCounts.length - 1] : 0,
            zero_count: sampleCounts.filter((c) => c === 0).length,
          },
          recent_turns: recent,
        };
        if (typeof window !== "undefined") {
          window.__hav_lastDump = dump;
          try { console.log("LAB-DUMP", dump); } catch {}
        }
        addEvent({
          kind: "system",
          text: "lab-dump:\n```json\n" + JSON.stringify(dump, null, 2) + "\n```",
          tone: "info",
        });
        return true;
      }
      case "lab-dump-watch": {
        // AR32-6: cadence dumps for timing-window bugs.
        // /lab-dump-watch 5 → dump every 5s for 60s (12 dumps).
        // /lab-dump-watch stop → cancel an active watch.
        const sub = (arg || "").trim().toLowerCase();
        if (sub === "stop") {
          if (window.__hav_labDumpWatchId) {
            clearInterval(window.__hav_labDumpWatchId);
            window.__hav_labDumpWatchId = null;
            addEvent({ kind: "system", text: "lab-dump-watch · stopped", tone: "ok" });
          } else {
            addEvent({ kind: "system", text: "lab-dump-watch · not running", tone: "info" });
          }
          return true;
        }
        const interval = Math.max(1, parseInt(sub || "5", 10) || 5);
        if (window.__hav_labDumpWatchId) {
          clearInterval(window.__hav_labDumpWatchId);
        }
        let n = 0;
        const maxDumps = 12;
        const fire = () => handleCommand("/lab-dump");
        fire();   // immediate first dump
        window.__hav_labDumpWatchId = setInterval(() => {
          n++;
          if (n >= maxDumps) {
            clearInterval(window.__hav_labDumpWatchId);
            window.__hav_labDumpWatchId = null;
            addEvent({ kind: "system", text: `lab-dump-watch · complete (${maxDumps} dumps)`, tone: "ok" });
            return;
          }
          fire();
        }, interval * 1000);
        addEvent({
          kind: "system",
          text: `lab-dump-watch · cadence ${interval}s × ${maxDumps} dumps`,
          tone: "ok",
        });
        return true;
      }
      case "debug": {
        // Show/hide internal diag events ([parakeet], [direct],
        // [kokoro], [camera], [error], [moshi], [route]) in the feed.
        //   /debug                → show current state
        //   /debug on | off       → set explicitly
        //   /debug toggle         → flip
        const v = (arg || "").trim().toLowerCase();
        if (v === "bundle") {
          copyDebugBundle();
          return true;
        }
        let next = debugMode;
        if (v === "on" || v === "true" || v === "1") next = true;
        else if (v === "off" || v === "false" || v === "0") next = false;
        else if (v === "toggle" || v === "") next = !debugMode;
        setDebugMode(next);
        addEvent({ kind: "system",
          text: `debug · ${next ? "on — showing diag events (parakeet/direct/kokoro/camera/error/routing)" : "off — clean feed"}`,
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
        // /clear is a fresh start — re-show the /help hint, and reset the
        // persisted flag so onboarding teaches again next launch too.
        saveOnboarding({ seenHelpHint: false });
        if (connection === "online") {
          addEvent({ kind: "system", tone: "info", onboarding: "help-hint",
            text: "Connected. Type /help to learn what you can do." });
        }
        return true;
      case "demo":
        playScript();
        return true;
      case "about":
      case "version":
        addEvent({ kind: "system", text: "home v0.1.0 · engineered-lighting/home · MIT", tone: "info" });
        return true;
      case "help": {
        // Categorized + interactive help. Builds from SLASH_CMDS
        // (auto-complete uses the same source so help stays in sync).
        // Emits a kind:"help" event carrying the structured payload
        // (groups + commands) so HelpContent in home-events.jsx can
        // render hover-brighten + click-to-fill-input UX. The old
        // flat text rendering is no longer reachable from here.
        const groups = SLASH_CMD_CATEGORIES.map((cat) => {
          const cmds = SLASH_CMDS.filter((c) => c.category === cat.id);
          return { ...cat, commands: cmds };
        }).filter((g) => g.commands.length > 0);
        // Uncategorized fallback — surfaces drift the moment a command
        // is added without a category (instead of silently disappearing
        // from /help).
        const uncategorized = SLASH_CMDS.filter((c) => !c.category);
        if (uncategorized.length > 0) {
          groups.push({
            id: "_uncategorized",
            label: "uncategorized (add a category field!)",
            desc: "",
            commands: uncategorized,
          });
        }
        // Sim mode: emit the legacy sim-help text first so the sim
        // scenario catalog stays prominent in design-review sessions.
        if (sim.active && window.SimulationCommand) {
          const simHelp = window.SimulationCommand.formatSimHelp(sim.active, sim.scenario);
          if (simHelp) addEvent({ kind: "system", text: simHelp, tone: "info" });
        }
        addEvent({
          kind: "help",
          groups,
          totalCount: SLASH_CMDS.length,
          tip: "hover any command to highlight · click to paste into the input box · then hit Enter",
        });
        return true;
      }
      case "simulation":
      case "sim": {
        if (window.SimulationCommand) {
          window.SimulationCommand.handle(cmd, arg, { sim, addEvent });
        } else {
          addEvent({ kind: "system", text: "simulation module not loaded", tone: "warn" });
        }
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
      // Proactive-assistant coordinator — status, mode overrides, and the
      // "test from HA without leaving the house" event injectors. The test
      // injectors drive the REAL coordinator (→ real evaluateProactive →
      // real Voice PE announce); they no-op in Simulation Mode.
      case "proactive": {
        const coord = proactiveCoord;
        const parts = (arg || "").trim().split(/\s+/).filter(Boolean);
        const sub = (parts[0] || "").toLowerCase();
        if (!sub) {
          const st = coord.getStatus();
          const modes = Object.keys(st.modes).filter((k) => st.modes[k]);
          addEvent({ kind: "system", tone: "info", text:
            `proactive · phase: ${st.phase}${st.away ? " · away" : ""}\n` +
            `modes: ${modes.length ? modes.join(", ") : "none"}\n` +
            `greeted rooms: ${st.ledger.greetedRooms.length ? st.ledger.greetedRooms.join(", ") : "none"}\n` +
            "usage:\n" +
            "  /proactive <sleep|dnd|focus|movie> <on|off>\n" +
            "  /proactive test <arrived|left|room|confirm> [name|room]\n" +
            "  /proactive reset",
          });
          return true;
        }
        if (sub === "reset") {
          coord.resetLedger();
          addEvent({ kind: "system", text: "proactive: ledger cleared, coordinator reset to idle", tone: "ok" });
          return true;
        }
        if (sub === "sleep" || sub === "dnd" || sub === "focus" || sub === "movie") {
          const on = (parts[1] || "").toLowerCase() === "on";
          coord.setMode(sub, on);
          addEvent({ kind: "system", text: `proactive: ${sub} mode ${on ? "ON" : "off"}`, tone: "info" });
          return true;
        }
        if (sub === "test") {
          const kind = (parts[1] || "").toLowerCase();
          const a = parts.slice(2).join(" ");
          let evt = null;
          // Default name is "Marcelo" everywhere so `/proactive test arrived`
          // and `/proactive test confirm` (both with no arg) MATCH — the
          // coordinator only confirms an arrival when the identity's
          // display_name equals the arriving person's.
          if (kind === "arrived")      evt = { type: "person_arrived_home", person: "person.test", display_name: a || "Marcelo", confidence: 1.0, test: true };
          else if (kind === "left")    evt = { type: "person_left_home",    person: "person.test", display_name: a || "Marcelo", confidence: 1.0, test: true };
          else if (kind === "room")    evt = { type: "room_entered",        room: a || "kitchen", confidence: 0.9, test: true };
          else if (kind === "confirm") evt = { type: "identity_confirmed",  display_name: a || "Marcelo", room: "living_room", confidence: 0.97, test: true };
          if (!evt) {
            addEvent({ kind: "system", text: "usage: /proactive test <arrived|left|room|confirm> [name|room]", tone: "info" });
            return true;
          }
          coord.ingestEvent(window.normalizeProactiveEvent(evt, "ha_ws"));
          addEvent({ kind: "system", text: `proactive: injected test ${kind} event` + (sim.active ? " (no-op — Simulation Mode)" : ""), tone: "info" });
          return true;
        }
        addEvent({ kind: "system", text: `unknown /proactive subcommand: ${sub}`, tone: "warn" });
        return true;
      }
      // ── External reasoning fallback ─────────────────────────────────
      case "ask": {
        if (!arg) {
          addEvent({ kind: "system", text: "usage: /ask <text> — force external/general provider", tone: "info" });
          return true;
        }
        const configured = window.isExternalConfigured
          ? window.isExternalConfigured()
          : !!(window.openaiProvider && window.openaiProvider.isConfigured());
        if (!configured) {
          addEvent({ kind: "system", text: "external reasoning is not configured — use /external set-key first", tone: "warn" });
          return true;
        }
        dispatchExternal(arg);
        return true;
      }
      case "local": {
        if (!arg) {
          addEvent({ kind: "system", text: "usage: /local <text> — force local home agent", tone: "info" });
          return true;
        }
        addEvent({ kind: "user", text: arg });
        sendToHA(arg);
        return true;
      }
      case "route": {
        if (!arg) {
          addEvent({ kind: "system", text: "usage: /route <text> — classify without dispatching", tone: "info" });
          return true;
        }
        const explanation = window.explainIntent ? window.explainIntent(arg) : "classifier unavailable";
        addEvent({ kind: "system", text: `route: ${explanation}`, tone: "info" });
        return true;
      }
      case "external": {
        const [sub, ...subRest] = arg.split(/\s+/).filter(Boolean);
        const subArg = subRest.join(" ");
        if (!sub || sub === "status") {
          const provider = window.getExternalProvider ? window.getExternalProvider() : window.openaiProvider;
          const configured = window.isExternalConfigured
            ? window.isExternalConfigured(provider)
            : !!(provider && provider.isConfigured && provider.isConfigured());
          const enabled = (() => {
            try { return localStorage.getItem("hg-external-enabled") === "on"; }
            catch { return false; }
          })();
          const stats = window.__EXTERNAL_STATS || [];
          const text = window.formatExternalStatus
            ? window.formatExternalStatus({ provider, configured, enabled, stats })
            : [
                `external Â· provider: ${provider?.name || "(none)"}`,
                `  configured: ${configured ? "yes" : "no"} Â· auto-routing: ${enabled ? "on" : "off"}`,
                `  calls tracked: ${stats.length}`,
              ].join("\n");
          const recent = stats.slice(-1)[0];
          const lines = [
            `external · provider: ${window.openaiProvider?.name || "(none)"}`,
            `  configured: ${configured ? "yes" : "no"} · auto-routing: ${enabled ? "on" : "off"}`,
            `  calls tracked: ${stats.length}` + (recent ? ` · last: ${recent.ok ? "ok" : recent.error || "fail"} (${recent.latencyMs || "?"}ms)` : ""),
          ];
          addEvent({ kind: "system", text, tone: configured ? "ok" : "warn" });
          return true;
        }
        if (sub === "on") {
          try { localStorage.setItem("hg-external-enabled", "on"); } catch {}
          addEvent({ kind: "system", text: "external auto-routing: on", tone: "ok" });
          return true;
        }
        if (sub === "off") {
          try { localStorage.setItem("hg-external-enabled", "off"); } catch {}
          addEvent({ kind: "system", text: "external auto-routing: off (use /ask to force)", tone: "info" });
          return true;
        }
        if (sub === "set-key") {
          setExternalKeyModalOpen(true);
          return true;
        }
        addEvent({ kind: "system", text: `unknown /external subcommand: ${sub}`, tone: "warn" });
        return true;
      }
      case "test": {
        const [sub] = arg.split(/\s+/).filter(Boolean);
        if (!sub) {
          addEvent({ kind: "system", text: "usage: /test classifier | external-privacy | external-suite", tone: "info" });
          return true;
        }
        if (sub === "classifier") {
          if (!window.runClassifierTest) { addEvent({ kind: "system", text: "classifier test module not loaded", tone: "error" }); return true; }
          const r = window.runClassifierTest();
          addEvent({ kind: "system", text: `[classifier] ✓ ${r.passed} · ✗ ${r.failed}`, tone: r.failed === 0 ? "ok" : "error" });
          r.results.filter((x) => !x.ok).slice(0, 6).forEach((x) => {
            addEvent({ kind: "system", text: `  fail: "${x.input}" → got ${x.got}, expected ${x.expected}`, tone: "error" });
          });
          return true;
        }
        if (sub === "external-privacy") {
          if (!window.runPrivacyTest) { addEvent({ kind: "system", text: "privacy test module not loaded", tone: "error" }); return true; }
          window.runPrivacyTest().then((r) => {
            addEvent({ kind: "system", text: `[privacy] ✓ ${r.passed} · ✗ ${r.failed}`, tone: r.failed === 0 ? "ok" : "error" });
            r.results.forEach((x) => {
              if (x.violations.length > 0) addEvent({ kind: "system", text: `  leak: "${x.input}" → ${x.violations.join(", ")}`, tone: "error" });
              if (!x.shapeOk)              addEvent({ kind: "system", text: `  shape: "${x.input}" → messages array malformed`, tone: "error" });
            });
          });
          return true;
        }
        if (sub === "external-suite") {
          if (!window.runExternalSuite) { addEvent({ kind: "system", text: "test suite module not loaded", tone: "error" }); return true; }
          window.runExternalSuite(addEvent);
          return true;
        }
        addEvent({ kind: "system", text: `unknown /test subcommand: ${sub}`, tone: "warn" });
        return true;
      }
      case "spatial": {
        // Addendum 38 Phase 1 — open the light-footprint Map drawer.
        setSpatialDrawerOpen(true);
        return true;
      }
      case "home2":
      case "spatial-home": {
        const next = !/^off|classic|chat|0|false$/i.test(arg.trim());
        setSpatialLayout(next);
        try { localStorage.setItem("hg-layout-v2", next ? "spatial" : "classic"); } catch (e) { /* ignore */ }
        addEvent({
          kind: "system",
          text: next
            ? "home 2 spatial layout on - apartment is primary, chat is docked right"
            : "home 2 spatial layout off - classic chat layout restored",
          tone: "ok",
        });
        return true;
      }
      case "apartment":
      case "3d": {
        const sub = arg.trim().toLowerCase();
        if (sub === "prewarm" || sub === "warm" || sub === "prewarm status" || sub === "warm status" || sub === "status") {
          const prewarm = window.HomeApartmentPrewarm;
          if (!prewarm) {
            addEvent({ kind: "system", text: "apartment prewarm module not loaded", tone: "error" });
            return true;
          }
          if (sub === "prewarm" || sub === "warm") {
            prewarm.start({ mode: "full", reason: "slash", force: true });
          }
          const s = prewarm.status();
          const fmt = (bytes) => bytes ? `${(bytes / 1024 / 1024).toFixed(bytes > 1024 * 1024 ? 1 : 3)} MB` : "0 MB";
          const warmed = (s.warmed || []).map((item) => `${item.label}: ${fmt(item.bytes)} in ${item.ms}ms`).join("\n");
          const failed = (s.failed || []).map((item) => `${item.label}: ${item.error}`).join("\n");
          addEvent({
            kind: "system",
            tone: s.state === "ready" || s.state === "running" ? "ok" : "warn",
            text:
              `apartment prewarm ${s.state}` +
              (s.mode ? ` (${s.mode})` : "") +
              (s.current ? `\ncurrent: ${s.current}` : "") +
              (s.modules ? `\nmodules: ${s.modules.ok ? "ready" : "degraded"} (${s.modules.loaded} loaded)` : "") +
              (warmed ? `\nwarmed:\n${warmed}` : "") +
              (failed ? `\nfailed:\n${failed}` : ""),
          });
          return true;
        }
        // Full-screen 3D apartment takeover (white point cloud, P0).
        if (!window.HomeApartmentView) {
          addEvent({ kind: "system", text: "apartment module not loaded", tone: "error" });
          return true;
        }
        if (spatialLayout) {
          addEvent({ kind: "system", text: "apartment is already primary in Home 2 layout", tone: "info" });
          return true;
        }
        setVideoLabelerOpen(false);
        setApartmentOpen(true);
        return true;
      }
      case "labeler":
      case "vl": {
        // Video timeline labeler (M0). `/labeler base <url>` rewires the
        // service base; bare /labeler
        // opens the full-screen overlay, closing the other takeovers.
        const a = arg.trim();
        if (/^base\s+\S/.test(a)) {
          const url = a.replace(/^base\s+/, "").trim().replace(/\/+$/, "");
          try {
            if (window.HomeServices) window.HomeServices.setOverride("videoLabeler", url);
            else localStorage.setItem("videoLabeler.base", url);
            syncServiceStateFromResolver();
            addEvent({ kind: "system", text: `labeler base → ${url}`, tone: "ok" });
          } catch (e) {
            addEvent({ kind: "system", text: `labeler base save failed · ${e?.message || "localStorage error"}`, tone: "error" });
          }
          return true;
        }
        if (!window.HomeVideoLabelerOverlay) {
          addEvent({ kind: "system", text: "video labeler module not loaded", tone: "error" });
          return true;
        }
        setPeopleOpen(false);
        setIntelligenceOpen(false);
        setApartmentOpen(false);
        setVideoLabelerOpen(true);
        return true;
      }
      case "look": {
        // Phase 0.5 "Thinking with Visual Primitives" — open the /look
        // drawer: ask the vision model a spatial question about a camera
        // and render the boxes it reasons with. `/look <camera> <question>`
        // pre-fills + auto-runs; bare `/look` opens an empty drawer.
        const parsed = window.HomeLookParseArg
          ? window.HomeLookParseArg(arg)
          : { camera: null, question: "" };
        setLookInitial({
          camera: parsed.camera,
          question: parsed.question,
          nonce: Date.now(),
        });
        setLookDrawerOpen(true);
        return true;
      }
      case "world-state":
      case "world": {
        // F-32 (Addendum 27): default to opening the world-state
        // drawer for a navigable view. Flags fall back to the
        // legacy raw-JSON dump for paste-back use cases.
        //   /world-state              → open drawer
        //   /world-state --raw        → dump full JSON inline
        //   /world-state kitchen      → dump one room inline
        //   /world-state --raw kitchen → same
        const argTrimmed = arg.trim();
        const wantsRaw = /(^|\s)--raw(\s|$)/.test(argTrimmed);
        const argSansFlags = argTrimmed.replace(/--raw\b/g, "").trim();
        if (!wantsRaw && !argSansFlags) {
          // No room arg, no --raw → open the drawer with full view
          setWorldStateInitialRoom(null);
          setWorldStateDrawerOpen(true);
          return true;
        }
        if (!wantsRaw && argSansFlags) {
          // Room arg without --raw → open the drawer pre-filtered to
          // that room. User can ×-clear the filter to widen.
          setWorldStateInitialRoom(argSansFlags);
          setWorldStateDrawerOpen(true);
          return true;
        }
        // Fall through to legacy raw dump (--raw flag explicitly given).
        const room = argSansFlags || null;
        if (!endpoint || !token) {
          addEvent({ kind: "system", text: "world-state · need /connect <url> <token> first", tone: "warn" });
          return true;
        }
        (async () => {
          try {
            const base = endpoint.replace(/\/+$/, "");
            const qs = room ? `?room=${encodeURIComponent(room)}` : "";
            const url = `${base}/api/extended_openai_conversation/world_state${qs}`;
            const r = await tauriFetch(url, {
              headers: { Authorization: `Bearer ${token}` },
              cache: "no-store",
            });
            if (!r.ok) {
              addEvent({ kind: "system", text: `world-state · http ${r.status}`, tone: "error" });
              return;
            }
            const data = await r.json();
            if (data && data.enabled === false) {
              addEvent({ kind: "system", text: `world-state · disabled (${data.reason || "off"})`, tone: "warn" });
              return;
            }
            const label = room ? `world-state · ${room}` : "world-state · full snapshot";
            addEvent({ kind: "system", text: `${label} (select-all + copy):`, tone: "info" });
            // Pretty-printed JSON (paste readability > one-line compactness here).
            addEvent({ kind: "system", text: JSON.stringify(data, null, 2) });
          } catch (err) {
            addEvent({ kind: "system", text: `world-state · fetch failed: ${err?.message || err}`, tone: "error" });
          }
        })();
        return true;
      }
      case "recap": {
        // M2 (Addendum 27): trigger the recap() tool via HA conversation
        // API with a structured prompt. Default 6h; numeric arg overrides
        // (e.g. /recap 24 = today, /recap 168 = past week).
        let hours = 6;
        const a = arg.trim();
        if (a) {
          const n = parseInt(a, 10);
          if (Number.isFinite(n) && n >= 1 && n <= 168) {
            hours = n;
          } else {
            addEvent({ kind: "system", text: `recap · '${a}' isn't a valid hours value (1-168); using default 6`, tone: "warn" });
          }
        }
        if (!endpoint || !token) {
          addEvent({ kind: "system", text: "recap · need /connect <url> <token> first", tone: "warn" });
          return true;
        }
        addEvent({ kind: "system", text: `recap · gathering last ${hours}h…`, tone: "info" });
        (async () => {
          try {
            const base = endpoint.replace(/\/+$/, "");
            const url = `${base}/api/conversation/process`;
            const r = await tauriFetch(url, {
              method: "POST",
              headers: {
                Authorization: `Bearer ${token}`,
                "Content-Type": "application/json",
              },
              body: JSON.stringify({
                text: `Summarize what happened in our home over the last ${hours} hours in 2-3 conversational sentences. Use the recap tool to gather the data — don't read raw fields aloud.`,
                language: "en",
                // 2026-05-18 fix: without agent_id, HA routes to its
                // default agent (often the built-in intent matcher),
                // which parsed "Use the recap tool..." as an area
                // lookup → returned "Sorry, I am not aware of any
                // area called Use". Explicit agent_id ensures the
                // request reaches our LLM agent that knows the tool.
                agent_id: "conversation.extended_openai_conversation",
              }),
              cache: "no-store",
            });
            if (!r.ok) {
              addEvent({ kind: "system", text: `recap · http ${r.status}`, tone: "error" });
              return;
            }
            const data = await r.json();
            const speech = data?.response?.speech?.plain?.speech || "(no response)";
            // Render the recap as a regular assistant bubble for natural feel
            addEvent({ kind: "home", text: speech });
          } catch (err) {
            addEvent({ kind: "system", text: `recap · fetch failed: ${err?.message || err}`, tone: "error" });
          }
        })();
        return true;
      }
      case "cameras":
      case "cams": {
        // Visual sweep: emit one perception event per configured
        // camera with its thumbnail URL pointing at the M1 snapshot
        // cache. PerceptionContent already handles snapshotUrl render
        // + onError fallback to text, so a stale/404 thumbnail
        // degrades cleanly to a text chip without spinning.
        const cameras = window.HG_CAMERAS || [];
        if (cameras.length === 0) {
          addEvent({ kind: "system", text: "cameras · no cameras configured (window.HG_CAMERAS empty)", tone: "warn" });
          return true;
        }
        // Derive vision-sidecar URL (same pattern as /describe-clip).
        const baseM = metricsBase || (() => {
          try { return metricsBaseFromEndpoint(endpoint); } catch { return ""; }
        })();
        const visionUrl = visionBaseFromEndpoint(baseM, endpoint);
        const isSim = sim?.active;
        if (!visionUrl && !isSim) {
          addEvent({ kind: "system", text: "cameras · vision-sidecar URL not derivable — set /metrics first", tone: "warn" });
          return true;
        }
        // In sim mode the fixture provides per-camera placeholder URLs
        // (data URLs) so the UI exercises the perception render path
        // without hitting the network.
        const simFixture = isSim && typeof window.__SIM_CAMERAS_FIXTURE === "function"
          ? window.__SIM_CAMERAS_FIXTURE()
          : null;
        addEvent({ kind: "system", text: `cameras · ${cameras.length} configured:`, tone: "info" });
        for (const cam of cameras) {
          let snapshotUrl;
          if (simFixture && simFixture[cam.id]) {
            snapshotUrl = simFixture[cam.id];
          } else {
            snapshotUrl = `${visionUrl}/snapshot/${cam.id}/latest.jpg`;
          }
          addEvent({
            kind: "perception",
            text: `${cam.name} · cached snapshot${isSim ? " (sim)" : ""}`,
            snapshotUrl,
          });
        }
        return true;
      }
      case "agent-tools":
      case "tools": {
        // List every LLM tool the agent can call, grouped by capability.
        // Source of truth: ha-config/extended_openai_conversation/const.py
        // (DEFAULT_CONF_FUNCTION_TOOLS). This list is maintained in
        // parallel — when adding a tool to const.py, update this list too.
        // The pairing is enforced by the developer adding both at once;
        // there's no live-sync because the agent's prompt may override
        // per-subentry anyway.
        const TOOL_CATALOG = [
          { group: "device", tools: [
            ["execute_services", "fire one or more HA service calls (light, switch, media_player, lock, etc.) — supports area_id or entity_id targeting"],
            ["get_attributes", "read current state + attributes of one entity"],
            ["invoke_script", "run a user-defined HA script by entity_id with optional variables — F-9"],
          ] },
          { group: "discovery", tools: [
            ["areas_in_home", "list every area with floor + sample entities"],
            ["entities_in_area", "entities in one area, optionally filtered by domain"],
            ["entities_with_label", "entities tagged with a label (HA 2024.8+)"],
            ["entities_by_domain", "every entity of one domain (light, lock, etc.), optionally within an area"],
            ["find_entity", "fuzzy search by name / alias / friendly name"],
          ] },
          { group: "presence + perception (world_state)", tools: [
            ["get_all_rooms_state", "snapshot of every room — occupancy + identified persons"],
            ["get_room_state", "one room — persons, perception summary, freshness"],
            ["find_person", "locate a person across cameras + HA presence"],
            ["who_is_in", "list persons currently in a specific room"],
            ["refresh_perception", "force a fresh vision-sidecar caption (1-3s blocking)"],
          ] },
          { group: "vision (multi-frame + clips)", tools: [
            ["describe_clip", "watch a short clip (N frames over Xs) and describe motion — F-3"],
            ["find_clips", "Frigate semantic search via CLIP — '<query>' or by camera/label/face"],
          ] },
          { group: "history + recap", tools: [
            ["get_history", "HA Recorder — state history for entities over a time window"],
            ["recap", "composition tool — bundles find_clips + recorder + identity for 'what happened today'"],
          ] },
          { group: "living lights", tools: [
            ["set_presence_override", "pin occupancy-managed lights to the user's requested brightness/color for a duration"],
            ["clear_presence_override", "clear an active Living Lights override and return to occupancy automation"],
            ["get_recent_overrides", "summarize recent manual/automation lighting adjustments for a zone"],
          ] },
          { group: "gesture training", tools: [
            ["start_gesture_training_capture", "start a guided gesture-training capture in kitchen, dining room, or living room"],
          ] },
          { group: "advanced", tools: [
            ["load_skill", "load a custom skill on demand"],
            ["bash", "execute a shell command (gated; rare)"],
          ] },
        ];
        // Render compact: one group header per group, one line per tool
        // "<name> — <desc>" so the user can scan + paste back the whole
        // list for inclusion in a prompt or doc.
        const totalCount = TOOL_CATALOG.reduce((a, g) => a + g.tools.length, 0);
        addEvent({ kind: "system", text: `agent-tools · ${totalCount} tools registered (see ha-config/extended_openai_conversation/const.py for live spec):`, tone: "info" });
        for (const group of TOOL_CATALOG) {
          addEvent({ kind: "system", text: `── ${group.group} (${group.tools.length}) ──` });
          for (const [name, desc] of group.tools) {
            addEvent({ kind: "system", text: `  ${name} — ${desc}` });
          }
        }
        return true;
      }
      case "describe-clip":
      case "clip": {
        // F-3 (Addendum 27): direct invocation of vision-sidecar
        // /describe_clip, bypassing HA's conversation API entirely.
        // Faster than going through the LLM agent (~3-8s vs ~15s)
        // and gives the user a quick way to test multi-frame motion
        // queries from the chat surface. Result renders as a
        // perception bubble with a thumbnail of the LAST frame from
        // the M1 snapshot cache (/snapshot/<camera>/latest.jpg).
        //
        // Args: <camera> [frames] [interval_s]
        //   /describe-clip kitchen           → defaults (4 frames, 1.0s)
        //   /describe-clip driveway 6 1.5    → 6 frames over 7.5s
        // Addendum 30B: tolerant parser via shared pure helper. Old positional
        // split broke on two-word cameras ("living room" → frames="room").
        // Walks tokens, accumulates non-numeric as camera name, normalizes to
        // entity_id form (lowercased, underscored, strip "camera." prefix).
        const parserFn = (window.HomeMetricsLabHelpers && window.HomeMetricsLabHelpers.parseDescribeClipArg) || null;
        const parsed = parserFn ? parserFn(arg) : { error: "parser helper unavailable" };
        if (parsed.error) {
          if (/missing camera/i.test(parsed.error)) {
            addEvent({ kind: "system", text: "describe-clip · usage: /describe-clip <camera> [frames] [interval_s]", tone: "warn" });
          } else {
            addEvent({ kind: "system", text: `describe-clip · ${parsed.error}`, tone: "warn" });
          }
          return true;
        }
        const camera = parsed.camera;
        const frames = parsed.frames;
        const interval_s = parsed.interval_s;
        // Derive vision URL from metricsBase (same pattern as the
        // visionHealth poll above).
        const baseM = metricsBase || (() => {
          try { return metricsBaseFromEndpoint(endpoint); } catch { return ""; }
        })();
        const visionUrl = visionBaseFromEndpoint(baseM, endpoint);
        // Sim mode: short-circuit with the canned fixture so the
        // command is testable without a real vision-sidecar reachable.
        const isSim = sim?.active;
        if (!visionUrl && !isSim) {
          addEvent({ kind: "system", text: "describe-clip · vision-sidecar URL not derivable — set /metrics first", tone: "warn" });
          return true;
        }
        const framesEff = frames || 4;
        const intervalEff = interval_s || 1.0;
        addEvent({ kind: "system", text: `describe-clip · watching ${camera} (${framesEff} frames over ${(framesEff - 1) * intervalEff}s)…`, tone: "info" });
        (async () => {
          let body;
          let snapshotUrl = null;
          try {
            if (isSim && typeof window.__SIM_DESCRIBE_CLIP_FIXTURE === "function") {
              // Sim path: skip the network, return canned data
              body = window.__SIM_DESCRIBE_CLIP_FIXTURE(camera, framesEff, intervalEff);
            } else {
              const payload = { camera, frames: framesEff, interval_s: intervalEff };
              const r = await tauriFetch(`${visionUrl}/describe_clip`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
                cache: "no-store",
              });
              if (!r.ok) {
                addEvent({ kind: "system", text: `describe-clip · http ${r.status}`, tone: "error" });
                return;
              }
              body = await r.json();
              // M1 snapshot URL — the sidecar caches the last frame
              // from every /describe* call. Use the resolved entity_id
              // if the sidecar echoed one, else fall back to the user-
              // typed camera.
              const cam = (body.entity_id || `camera.${camera.replace(/[\s]/g, "_")}`).replace(/^camera\./, "");
              snapshotUrl = `${visionUrl}/snapshot/${cam}/latest.jpg`;
            }
            const description = (body.description || "").trim() || "(no description)";
            const meta = [
              body.frames_captured != null ? `${body.frames_captured} frames` : null,
              body.span_s != null ? `${body.span_s}s span` : null,
              body.latency_ms != null ? `${body.latency_ms}ms` : null,
            ].filter(Boolean).join(" · ");
            addEvent({
              kind: "perception",
              text: `${camera} — ${description}${meta ? `  (${meta})` : ""}`,
              snapshotUrl,
            });
          } catch (err) {
            addEvent({ kind: "system", text: `describe-clip · fetch failed: ${err?.message || err}`, tone: "error" });
          }
        })();
        return true;
      }
      case "find-clips":
      case "clips": {
        // F-2-tier (Addendum 27): direct invocation of the Frigate
        // find_clips tool, bypassing the LLM. Hits the integration
        // endpoint that proxies to Frigate's /api/events?search=…
        // Renders each clip as a system event with the thumbnail URL
        // so the user can copy/paste back for analysis.
        const query = arg.trim();
        if (!query) {
          addEvent({ kind: "system", text: "find-clips · usage: /find-clips <query> (e.g. 'package on porch')", tone: "warn" });
          return true;
        }
        if (!endpoint || !token) {
          addEvent({ kind: "system", text: "find-clips · need /connect <url> <token> first", tone: "warn" });
          return true;
        }
        (async () => {
          try {
            // No dedicated REST view exists for this — instead invoke
            // the HA conversation API with a structured prompt that
            // calls the tool, OR hit Frigate directly via a proxy.
            // Cleanest: have a tiny new endpoint OR call HA's
            // /api/conversation/process with a wrapped query.
            // For now: ask HA conversation to run find_clips. The
            // agent recognizes the tool from const.py.
            const base = endpoint.replace(/\/+$/, "");
            const url = `${base}/api/conversation/process`;
            const r = await tauriFetch(url, {
              method: "POST",
              headers: {
                Authorization: `Bearer ${token}`,
                "Content-Type": "application/json",
              },
              body: JSON.stringify({
                text: `Use find_clips to search for: ${query}. Return only the raw tool result.`,
                language: "en",
                // 2026-05-18: same agent_id fix as /recap. Without
                // this, HA's built-in intent matcher catches the
                // request and treats "Use" / "Run" / etc. as an
                // area name lookup.
                agent_id: "conversation.extended_openai_conversation",
              }),
              cache: "no-store",
            });
            if (!r.ok) {
              addEvent({ kind: "system", text: `find-clips · http ${r.status}`, tone: "error" });
              return;
            }
            const data = await r.json();
            const speech = data?.response?.speech?.plain?.speech || "(no response)";
            addEvent({ kind: "system", text: `find-clips · ${speech}`, tone: "info" });
          } catch (err) {
            addEvent({ kind: "system", text: `find-clips · fetch failed: ${err?.message || err}`, tone: "error" });
          }
        })();
        return true;
      }
      case "why": {
        // M5 (Addendum 27): open the explainability drawer for a
        // specific conv_id. With no arg, opens for the most-recent
        // bubble carrying a convId. With an arg, opens for that exact
        // conv_id (no validation — drawer renders "no entries" if
        // unknown).
        const id = arg.trim();
        if (id) {
          setExplainConvId(id);
          return true;
        }
        // No arg: walk the events list right-to-left for the most
        // recent bubble with a convId.
        const recent = [...events].reverse().find((e) => e.convId);
        if (!recent) {
          addEvent({ kind: "system", text: "why · no recent turns with conv_id (try after a voice/typed turn)", tone: "warn" });
          return true;
        }
        setExplainConvId(recent.convId);
        return true;
      }
      case "lights": {
        // Open the Living Lights tuning drawer. One unified surface with
        // 'right now' status, bias knobs (warmth + brightness), and
        // cascade-priority sliders for the 15 promoted constants. The
        // drawer reads + writes input_numbers directly via the WebSocket
        // client — no LLM in the loop for everyday tweaks.
        if (!haClientRef.current) {
          addEvent({ kind: "system", text: "lights · not connected to HA (run /connect first)", tone: "warn" });
          return true;
        }
        setLightsOpen(true);
        return true;
      }
      case "why-light": {
        // First-PR introspection: explain the last lighting decision for
        // a zone. Pulls (a) the live classifier sensor, (b) recent manual
        // overrides via the RecentOverridesView REST endpoint, (c) last
        // few shadow-log transitions via the LightingDecisionsView REST
        // endpoint. Composes a single chat-feed system entry.
        //
        // Graceful: if no recent overrides, still shows classifier state +
        // shadow-log transitions ("no manual overrides in last 24h").
        const LIGHT_ZONES = [
          "sofa", "front_left", "front_door", "weights", "office",
          "dining_left", "dining_right", "sink", "island_left", "island_right",
        ];
        const ZONE_CAMERA_MAP = {
          dining_left: "dining_room", dining_right: "dining_room",
          sink: "kitchen", island_left: "kitchen", island_right: "kitchen",
          sofa: "living_room", front_left: "living_room", weights: "living_room",
          office: "living_room", front_door: "living_room",
        };
        const zone = arg.trim().toLowerCase().replace(/\s+/g, "_");
        if (!zone) {
          addEvent({
            kind: "system",
            text: `why-light · pass a zone, e.g. /why-light office. Zones: ${LIGHT_ZONES.join(", ")}`,
            tone: "info",
          });
          return true;
        }
        if (!LIGHT_ZONES.includes(zone)) {
          addEvent({
            kind: "system",
            text: `why-light · unknown zone '${zone}'. Available: ${LIGHT_ZONES.join(", ")}`,
            tone: "warn",
          });
          return true;
        }
        if (!endpoint || !token) {
          addEvent({ kind: "system", text: "why-light · need /connect <url> <token> first", tone: "warn" });
          return true;
        }
        (async () => {
          const camera = ZONE_CAMERA_MAP[zone];
          const sensorId = `sensor.${camera}_${zone}_lighting_state`;
          const base = endpoint.replace(/\/+$/, "");
          const headers = { Authorization: `Bearer ${token}` };
          let classifier = null;
          let overridesRes = null;
          let decisionsRes = null;
          let pendingRes = null;
          let activityRes = null;
          try {
            const r = await tauriFetch(`${base}/api/states/${sensorId}`, {
              headers, cache: "no-store",
            });
            if (r.ok) classifier = await r.json();
          } catch (_) { /* tolerate */ }
          try {
            const r = await tauriFetch(
              `${base}/api/extended_openai_conversation/recent_overrides?zone=${encodeURIComponent(zone)}&hours=24`,
              { headers, cache: "no-store" },
            );
            if (r.ok) overridesRes = await r.json();
          } catch (_) { /* tolerate */ }
          try {
            const r = await tauriFetch(
              `${base}/api/extended_openai_conversation/lighting_decisions?zone=${encodeURIComponent(zone)}&tail=10&hours=24`,
              { headers, cache: "no-store" },
            );
            if (r.ok) decisionsRes = await r.json();
          } catch (_) { /* tolerate */ }
          // M2.3 — latest pending_preference for this zone (drilldown surface).
          try {
            const r = await tauriFetch(
              `${base}/api/extended_openai_conversation/recent_pending_preferences?zone=${encodeURIComponent(zone)}&tail=1&hours=72`,
              { headers, cache: "no-store" },
            );
            if (r.ok) pendingRes = await r.json();
          } catch (_) { /* tolerate */ }
          try {
            const intelligenceBase = intelligenceBaseFromEndpoint(metricsBase, endpoint);
            const r = await tauriFetch(
              `${intelligenceBase}/api/intelligence/lighting-activity?zone=${encodeURIComponent(zone)}&hours=24&limit=5`,
              { cache: "no-store" },
            );
            if (r.ok) activityRes = await r.json();
          } catch (_) { /* tolerate */ }
          // Compose the chat-feed entry.
          const friendlyZone = zone.replace(/_/g, " ");
          const lines = [`**${friendlyZone}** — /why-light`];
          const formatLightValue = (state) => {
            if (!state) return "?";
            if (state.state === "off") return "off";
            const parts = [];
            if (state.color_temp_kelvin != null && isFinite(Number(state.color_temp_kelvin))) {
              parts.push(`${Math.round(Number(state.color_temp_kelvin))}K`);
            }
            if (state.brightness_pct != null && isFinite(Number(state.brightness_pct))) {
              parts.push(`${Math.round(Number(state.brightness_pct))}%`);
            }
            return parts.length ? parts.join(" ") : (state.state || "?");
          };
          if (classifier) {
            const stateNow = classifier.state || "unknown";
            const a = classifier.attributes || {};
            const predBri = a.predicted_brightness_pct;
            const dwellMs = a.dwell_ms;
            const lastManualAt = a.last_manual_at;
            lines.push(
              `current state: ${stateNow}` +
              (predBri != null ? ` (predicted ${predBri}%)` : "") +
              (dwellMs != null ? ` · dwell ${dwellMs}ms` : "") +
              (lastManualAt ? ` · last manual: ${lastManualAt}` : ""),
            );
          } else {
            lines.push(`classifier sensor unavailable (${sensorId})`);
          }
          const overrides = (overridesRes && overridesRes.data) || [];
          if (overrides.length > 0) {
            lines.push(`last 24h: ${overrides.length} manual override${overrides.length === 1 ? "" : "s"} (showing last 3)`);
            const recent = overrides.slice(-3);
            for (const ov of recent) {
              const ts = ov.ts || "?";
              const ap = ov.actual_pct != null ? `${ov.actual_pct}%` : "?";
              const pp = ov.predicted_pct != null ? `${ov.predicted_pct}%` : "?";
              const shadow = ov.shadow_mode ? " (shadow)" : "";
              const reason = ov.debug_match_reason && ov.debug_match_reason !== "none"
                ? ` · ${ov.debug_match_reason}` : "";
              lines.push(`  • ${ts} — actual ${ap} vs predicted ${pp}${shadow}${reason}`);
            }
          } else {
            lines.push("no manual overrides in the last 24h");
          }
          // M2.3 — latest pending_preference for the zone. Concise: 1-3 lines.
          // Surfaces provenance (natural vs forced trigger) and prediction
          // mismatch warning when present. Full record stays in meta.whyLight.
          const pending = (pendingRes && pendingRes.entries) || [];
          const latestPending = pending.length > 0 ? pending[pending.length - 1] : null;
          if (latestPending) {
            const pts = latestPending.ts || "?";
            const cap = (latestPending.captured_state && latestPending.captured_state.brightness_pct);
            const prov = (latestPending.capture_provenance && latestPending.capture_provenance.capture_trigger) || "?";
            const provTag = prov === "vacancy_settle" ? "natural" : (prov === "manual_automation_trigger" ? "manually triggered" : prov);
            lines.push(`latest preference capture: ${pts} @ ${cap != null ? cap + "%" : "?"} (${provTag})`);
            const consistency = latestPending.prediction_consistency || {};
            const mismatch = consistency.prediction_mismatch_pct;
            if (mismatch != null && Math.abs(mismatch) > 10) {
              const primaryPct = consistency.primary_prediction_pct;
              const wouldPct = consistency.would_have_done_brightness_pct;
              lines.push(`  ⚠️ prediction mismatch ${mismatch}% — classifier predicted ${primaryPct}% but deviation cache had ${wouldPct}%`);
            }
            const occ = latestPending.occupancy || {};
            if (occ.state != null && occ.age_s != null) {
              const flickerParts = [];
              if (occ.age_s < 10) flickerParts.push("⚠ unstable at capture");
              if (occ.flicker_count_5min != null && occ.flicker_count_5min >= 3) flickerParts.push(`flicker x${occ.flicker_count_5min}/5min`);
              const flickerNote = flickerParts.length ? " " + flickerParts.join(", ") : "";
              lines.push(`  occupancy at capture: ${occ.state} for ${occ.age_s}s${flickerNote}`);
            }
            // M4: ONE debug-mode-only line surfacing dedupe + provenance metadata.
            // Keeps normal output uncluttered; appears only when /debug is on.
            if (typeof debugMode !== "undefined" && debugMode) {
              const evid = latestPending.evidence_id || "<absent>";
              const dkey = latestPending.dedupe_key || "<absent>";
              const dwin = latestPending.dedupe_window_s != null ? latestPending.dedupe_window_s + "s" : "?";
              const sup = latestPending.capture_suppressed_count != null ? latestPending.capture_suppressed_count : "?";
              const relOv = latestPending.related_override_context_id || "<none>";
              lines.push(`  [debug] evid=${evid} dedupe_key=${dkey} window=${dwin} suppressed_before_emit=${sup} related_override=${relOv}`);
            }
          } else {
            lines.push("no pending preferences captured for this zone yet");
          }
          const activity = (activityRes && activityRes.episodes) || [];
          if (activity.length > 0) {
            const summary = activityRes.summary || {};
            const totalEpisodes = summary.episode_count || activity.length;
            lines.push(`recent light changes: ${totalEpisodes} episode${totalEpisodes === 1 ? "" : "s"} (showing last 3)`);
            for (const ep of activity.slice(0, 3)) {
              const from = formatLightValue(ep.first_state);
              const to = formatLightValue(ep.last_state);
              const source = String(ep.source_class || "unknown_source").replace(/_/g, " ");
              const interp = String(ep.interpretation || "not_used_for_learning").replace(/_/g, " ");
              const folded = ep.event_count > 1 ? ` Â· ${ep.event_count} raw events` : "";
              lines.push(`  â€¢ ${ep.end_ts || "?"} â€” ${from} â†’ ${to} Â· ${source} Â· ${interp}${folded}`);
            }
          } else {
            lines.push("no universal lighting activity episodes ingested yet");
          }
          const decisions = (decisionsRes && decisionsRes.entries) || [];
          if (decisions.length > 0) {
            lines.push(`last classifier transitions (newest first, up to 5):`);
            const recent = decisions.slice(-5).reverse();
            for (const d of recent) {
              const ts = d.ts || "?";
              const from = d.from_state || "?";
              const to = d.to_state || "?";
              const bri = d.predicted && d.predicted.brightness_pct;
              lines.push(`  • ${ts} ${from} → ${to}${bri != null ? ` @ ${bri}%` : ""}`);
            }
          } else {
            lines.push("no shadow-log transitions in the last 24h");
          }
          addEvent({
            kind: "system",
            text: lines.join("\n"),
            tone: "info",
            meta: { whyLight: { zone, classifier, overrides, decisions, latestPending, activity } },
          });
        })();
        return true;
      }
      case "route-log":
      case "routes": {
        // Bulk-dump recent routing decisions from /config/external_routing.log
        // as RAW JSONL system events — select-all + copy + paste back to me
        // for offline analysis. Companion to /debug on (live one-liners).
        //   /route-log        → last 20 entries
        //   /route-log <N>    → last N (clamped 1..200 server-side)
        const n = Math.max(1, Math.min(parseInt(arg, 10) || 20, 200));
        if (!endpoint || !token) {
          addEvent({ kind: "system", text: "route-log · need /connect <url> <token> first", tone: "warn" });
          return true;
        }
        (async () => {
          try {
            const base = endpoint.replace(/\/+$/, "");
            const url = `${base}/api/extended_openai_conversation/routing_log?tail=${n}`;
            const r = await tauriFetch(url, {
              headers: { Authorization: `Bearer ${token}` },
              cache: "no-store",
            });
            if (!r.ok) {
              addEvent({ kind: "system", text: `route-log · http ${r.status}`, tone: "error" });
              return;
            }
            const data = await r.json();
            const entries = Array.isArray(data?.entries) ? data.entries : [];
            if (entries.length === 0) {
              addEvent({ kind: "system", text: "route-log · no routing entries yet", tone: "info" });
              return;
            }
            addEvent({ kind: "system", text: `route-log · ${entries.length} entries (raw JSONL — select-all + copy):`, tone: "info" });
            for (const e of entries) {
              // Raw single-line JSON, intentionally NOT pretty-printed
              // so the pasted text is parseable JSONL (one entry per line).
              addEvent({ kind: "system", text: JSON.stringify(e) });
            }
          } catch (err) {
            addEvent({ kind: "system", text: `route-log · fetch failed: ${err?.message || err}`, tone: "error" });
          }
        })();
        return true;
      }
      default:
        addEvent({ kind: "system", text: `unknown command: /${cmd}`, tone: "warn" });
        return true;
    }
    // NOTE: `dispatchExternal` is intentionally NOT in this deps array.
    // It's declared with `const` further down (after handleCommand) — a
    // direct reference here would hit the TDZ at render-eval time. The
    // function body resolves dispatchExternal via closure lookup AT
    // CALL time, by which point it's defined. Its identity is stable
    // (its own deps are [addEvent], itself stable), so omitting it
    // doesn't cause a memoization correctness issue.
  }, [addEvent, announceTravelReadiness, applyServiceProfile, connectTo, copyDebugBundle, copyRecoveryCommands, copyTravelBundle, currentTravelReadiness, debugMode, endpoint, events, kokoroVoice, metricsBase, playScript, runRemoteCheck, runTravelCheck, s2sBase, s2sToken, s2sVoice, sendToHA, spatialLayout, stopStreaming, syncServiceStateFromResolver, token]);

  /* ── External Reasoning dispatch (see home-external.jsx) ─────────────
   *
   * Single entry point for any external-routed message. Responsibilities:
   *   1. Cancel any in-flight external stream via the shared AbortController.
   *   2. Echo the user's text as a "user" event (same as the local path).
   *   3. Inject a "thinking" placeholder while waiting for first chunk.
   *   4. Append an empty external event and stream chunks into it by id.
   *   5. On done/error, finalize the streaming flag and surface state.
   *
   * Private home context is NEVER added here — the function passes only
   * `text` to window.askExternal. The "no leakage by construction"
   * invariant lives in this signature. */
  const dispatchExternal = useCallback((text) => {
    if (!text || !text.trim()) return;
    // 1. Cancel any prior stream.
    if (currentExternalCtrlRef.current) {
      try { currentExternalCtrlRef.current.abort(); } catch {}
      currentExternalCtrlRef.current = null;
    }
    // 2. User echo.
    addEvent({ kind: "user", text });
    // 3. Thinking placeholder.
    const thinkingId = `e-thinking-${Date.now()}`;
    setEvents((prev) => [
      ...prev,
      { id: thinkingId, time: fmtTime(), kind: "thinking", text: "asking external model…" },
    ]);
    // 4. External event (created empty; chunks fill it).
    const externalId = `e-external-${Date.now()}`;
    currentExternalEventIdRef.current = externalId;
    setEvents((prev) => [
      ...prev,
      { id: externalId, time: fmtTime(), kind: "external", text: "", streaming: true },
    ]);
    // Helpers to mutate the in-flight event by id (avoids accidentally
    // mutating events appended by other paths during the stream).
    const appendDelta = (delta) => {
      setEvents((prev) => prev.map((e) =>
        e.id === externalId ? { ...e, text: applyAsrCorrection((e.text || "") + delta) } : e,
      ));
    };
    const finalize = (extra) => {
      const fixed = extra && typeof extra.text === "string"
        ? { ...extra, text: applyAsrCorrection(extra.text) }
        : extra;
      setEvents((prev) => prev
        .filter((e) => e.id !== thinkingId)
        .map((e) => e.id === externalId ? { ...e, ...(fixed || {}), streaming: false } : e),
      );
    };
    const replaceWithError = (msg, tone) => {
      setEvents((prev) => prev
        .filter((e) => e.id !== thinkingId && e.id !== externalId)
        .concat([{ id: nextId(), time: fmtTime(), kind: "system", text: msg, tone: tone || "warn" }]),
      );
    };
    // 5. Dispatch.
    const ctrl = new AbortController();
    currentExternalCtrlRef.current = ctrl;
    if (!window.askExternal) {
      replaceWithError("external module not loaded — rebuild required", "error");
      return;
    }
    window.askExternal(
      text,
      {
        onChunk: ({ delta }) => appendDelta(delta || ""),
        onDone: (_res) => {
          finalize({});
          if (currentExternalCtrlRef.current === ctrl) currentExternalCtrlRef.current = null;
        },
        onError: (err) => {
          const code = (err && err.code) || (err && err.message) || "unknown";
          const friendly =
            code === "not_configured" ? "external reasoning is not configured — use /external set-key" :
            code === "auth_failed"    ? "external provider auth failed — rotate key with /external set-key" :
            code === "rate_limited"   ? "external provider rate-limited — try again shortly" :
            code === "unreachable"    ? "external provider unreachable — check connection or use /local" :
            code === "aborted"        ? null /* user cancelled — silent */ :
            code === "transient"      ? "external provider transient error" :
                                         `external request failed (${code})`;
          if (friendly === null) {
            // Aborted: keep partial text but mark non-streaming with a hint.
            setEvents((prev) => prev
              .filter((e) => e.id !== thinkingId)
              .map((e) => e.id === externalId ? { ...e, streaming: false, text: (e.text || "") + " (cancelled)" } : e),
            );
          } else {
            replaceWithError(friendly, "error");
          }
          if (currentExternalCtrlRef.current === ctrl) currentExternalCtrlRef.current = null;
        },
      },
      ctrl.signal,
    ).catch(() => { /* onError above already handled */ });
  }, [addEvent]);

  const answerDirectLightStateQuestion = useCallback(async (text) => {
    if (!isDirectLightStateQuestion(text)) return false;
    addEvent({ kind: "user", text });
    if (sim.active) {
      addEvent({ kind: "system", text: "[sim] live Home Assistant state is mocked off", tone: "info" });
      return true;
    }
    const client = haClientRef.current;
    if (!client || connection !== "online" || typeof client.call !== "function") {
      addEvent({ kind: "system", text: "not connected - cannot read live light state", tone: "warn" });
      return true;
    }

    try {
      const modelNames = new Map();
      const apartmentSwitchIds = new Set();
      try {
        if (window.HomeApartmentData?.getModel && endpoint && token) {
          const aptModel = await window.HomeApartmentData.getModel({ endpoint, token, sim: false });
          for (const dev of aptModel?.devices || []) {
            const entityId = dev?.ha_entity_id;
            if (!entityId) continue;
            const domain = entityId.split(".")[0];
            if (domain !== "light" && domain !== "switch") continue;
            if (dev.name || dev.id) modelNames.set(entityId, dev.name || dev.id);
            if (domain === "switch") apartmentSwitchIds.add(entityId);
          }
        }
      } catch (e) {
        console.warn("[home] apartment light model lookup failed", e);
      }

      const states = await client.call({ type: "get_states" });
      const onStates = (states || [])
        .filter((state) => isLightOrLampState(state, apartmentSwitchIds))
        .filter((state) => state?.state === "on");
      const names = onStates
        .map((state) => friendlyStateName(state, modelNames))
        .filter(Boolean)
        .sort((a, b) => a.localeCompare(b));
      const switchBacked = onStates.some((state) => String(state?.entity_id || "").startsWith("switch."));
      const textOut = names.length
        ? `I checked Home Assistant just now. ${switchBacked ? "Lights and switch-backed lamps" : "Lights"} currently on: ${names.join(", ")}.`
        : "I checked Home Assistant just now. No light entities or apartment switch-backed lamps are on.";
      addEvent({ kind: "home", text: textOut });
    } catch (e) {
      console.warn("[home] live light state lookup failed", e);
      addEvent({ kind: "system", text: `could not read live light state - ${e?.message || "Home Assistant query failed"}`, tone: "warn" });
    }
    return true;
  }, [addEvent, connection, endpoint, sim.active, token]);

  /* ── Free-form user input ─────────────────────────────────────────── */
  const sendInput = useCallback(async () => {
    const text = input.trim();
    if (!text) return;
    if (text.startsWith("/")) {
      setInput("");
      handleCommand(text);
      return;
    }
    setInput("");
    if (await answerDirectLightStateQuestion(text)) return;
    // External Reasoning router: classify the text + dispatch externally
    // ONLY if (a) classifier returned "external" AND (b) the user has
    // enabled auto-routing AND (c) provider is configured. Otherwise
    // the local home agent handles it via sendToHA (unchanged path).
    let route = "local";
    try {
      const intent = window.classifyIntent ? window.classifyIntent(text) : "local";
      const enabled = (() => {
        try { return localStorage.getItem("hg-external-enabled") === "on"; }
        catch { return false; }
      })();
      const configured = window.isExternalConfigured
        ? window.isExternalConfigured()
        : !!(window.openaiProvider && window.openaiProvider.isConfigured());
      if (intent === "external" && enabled && configured) route = "external";
    } catch {}
    if (route === "external") {
      dispatchExternal(text);
    } else {
      sendToHA(text);
    }
  }, [input, handleCommand, answerDirectLightStateQuestion, sendToHA, dispatchExternal]);

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
        const transcript = applyAsrCorrection(haEvent.data?.stt_output?.text || "");
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
    const placeholderId = nextId();
    setEvents((prev) => [...prev, {
      id: placeholderId, kind: "voice", time: fmtTime(), text: "listening… (s2s)",
    }]);
    const t0 = performance.now();
    let lastUserText = "";
    // One s2s session can produce MANY utterances. The placeholder above
    // gets claimed by the FIRST user transcript; every subsequent utterance
    // appends a fresh voice bubble. Without this, the same id was reused
    // for every transcript and each new utterance overwrote the last —
    // so the chat appeared to "replace what I said with my next question".
    let placeholderClaimed = false;
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
      onTranscript: (role, rawText, partial) => {
        if (!rawText) return;
        // Apply Tauri-side ASR correction at the s2s bridge transcript
        // entry point so the same canonical text reaches the dedup
        // helpers regardless of which source fires first (s2s bridge
        // vs HA WS conversation.finished). Without this, the bridge's
        // raw Parakeet output ("Marcella") and HA's corrected output
        // ("Marcelo") render as two different bubbles for one utterance.
        const text = applyAsrCorrection(rawText);
        if (role === "user") {
          // User-side partials still ignored: the Parakeet transcript
          // arrives all at once at end-of-utterance, no streaming.
          if (partial) return;
          lastUserText = text;
          if (!placeholderClaimed) {
            // First utterance — replace the "listening…" placeholder.
            placeholderClaimed = true;
            setEvents((prev) => prev.map((e) =>
              e.id === placeholderId ? { ...e, text } : e
            ));
          } else {
            // Subsequent utterance — append a fresh voice bubble so the
            // previous transcript stays in the chat. Dedup against
            // recent user bubbles to avoid double-render when both the
            // bridge SSE and conversation.finished WS fire.
            setEvents((prev) => {
              if (findRecentUserIdx(prev, text, 8) !== -1) return prev;
              return [...prev, {
                id: nextId(), kind: "voice", time: fmtTime(), text,
              }];
            });
          }
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
            // Dedup against recent assistant bubbles: HA WS event and
            // s2s bridge can both fire for the same turn.
            if (findRecentAssistantIdx(prev, text, "home", 20) !== -1) return prev;
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
    if (sim.active) return undefined; // Sim Mode: no SSE subscription
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
          // Frigate face-rec → the proactive coordinator. This is the
          // PRIMARY stage-2 arrival-confirmation transport (it self-gates:
          // only acted on while an arrival is pending).
          proactiveCoord.observeIdentity(i);
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
      // Proactive presence / room-entry events — the bridge SSE FALLBACK
      // transport (the primary is the homeai_proactive HA-WS subscription
      // the coordinator opens itself). Dispatch + early-return like the
      // identity/presence/media branches above — these carry no chat text.
      if (entry.source === "s2s:proactive" && entry.proactive) {
        proactiveCoord.ingestEvent(
          window.normalizeProactiveEvent(entry.proactive, "sse")
        );
        return;
      }
      const dedup = entry.id || `${entry.ts}-${(entry.user || "").slice(0, 30)}`;
      if (seenSsEvents.current.has(dedup)) return;
      seenSsEvents.current.add(dedup);
      if (seenSsEvents.current.size > 200) {
        seenSsEvents.current = new Set(Array.from(seenSsEvents.current).slice(-100));
      }
      // If a proactive follow-up window is open, this user turn is (probably)
      // the reply — hand the text to the coordinator. It self-gates on phase,
      // so this is a no-op unless a follow-up is actually being awaited.
      if (entry.user) proactiveCoord.observeUserTurn(entry.user, { ts: entry.ts });

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
            if (!groups.has(key)) groups.set(key, {
              service: key, targets: new Set(), attrsList: [],
              // Per-kind target sets — kept SEPARATE so the control-card
              // ActionContext knows entity vs area vs device (the flat
              // `targets` set discards that). See plan Adversarial #2.
              entityTargets: new Set(), areaTargets: new Set(), deviceTargets: new Set(),
            });
            const g = groups.get(key);
            const sd = call.service_data || {};
            const addAll = (val, set) => (Array.isArray(val) ? val : [val]).filter(Boolean).forEach((t) => set.add(t));
            addAll(sd.entity_id, g.entityTargets);
            addAll(sd.area_id, g.areaTargets);
            addAll(sd.device_id, g.deviceTargets);
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
            const id = nextId();
            // Inline control-card context — derived from the resolved targets
            // already in hand. window.buildActionContext lives in home-control.jsx.
            const actionCtx = window.buildActionContext ? window.buildActionContext({
              id, service: g.service,
              entityTargets: Array.from(g.entityTargets),
              areaTargets: Array.from(g.areaTargets),
              deviceTargets: Array.from(g.deviceTargets),
              attrs: mergedAttrs, status: "success",
            }) : null;
            actionCards.push({
              id, kind: "action", time: fmtTime(),
              title: `${g.service}${targetStr ? " · " + targetStr : ""}`,
              service: g.service,
              target: targetStr,
              attrs: mergedAttrs,
              status: "success",
              controllable: actionCtx ? actionCtx.controllable : false,
              actionCtx,
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
        // Apply Tauri-side ASR correction up front: dedup compares
        // bubble text, so for symmetry against addEvent (which corrects
        // on insert) we must correct here too — otherwise the same
        // logical text shows as two bubbles when one source emits
        // "Marcello" and the other "Marcelo".
        const correctedUser = applyAsrCorrection(entry.user);
        // Dedup user/voice bubble against the last ~8 events.
        const userIdx = findRecentUserIdx(prev, correctedUser, 8);
        const newUserEvents = [];
        if (userIdx === -1 && correctedUser) {
          // No local user event → originated outside the Home app (Voice PE).
          newUserEvents.push({
            id: nextId(), kind: "voice", time: fmtTime(),
            text: correctedUser,
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
        // Parakeet-heard transcripts are the user's spoken question.
        // The bridge sends them as DIAG events (small italic), but the
        // user expects them rendered as a proper user bubble — same
        // treatment as typed input. Detect "[parakeet] heard: ..." and
        // emit an extra kind:"voice" event with the inner text. The diag
        // line stays as-is for /debug on, but the voice bubble gives the
        // user normal chat treatment for their own question.
        //
        // Bridge format is `f"[parakeet] heard: {text!r}"` so quotes vary
        // (Python repr picks single/double to avoid escapes). Use a loose
        // match + manual quote-stripping rather than a strict regex.
        if (isDiag && diagChannel === "parakeet" && entry.assistant) {
          const PREFIX = "[parakeet] heard:";
          let heardText = "";
          const raw = String(entry.assistant).trim();
          if (raw.startsWith(PREFIX)) {
            heardText = raw.slice(PREFIX.length).trim();
            // Strip a single matched pair of surrounding ASCII or smart quotes.
            const opens = ["'", '"', "‘", "“"];
            const closes = ["'", '"', "’", "”"];
            const oi = opens.indexOf(heardText[0]);
            if (oi >= 0 && heardText[heardText.length - 1] === closes[oi]) {
              heardText = heardText.slice(1, -1);
            }
          }
          if (heardText) {
            const correctedHeard = applyAsrCorrection(heardText);
            if (findRecentUserIdx(prev, correctedHeard, 8) === -1) {
              newUserEvents.push({
                id: nextId(), kind: "voice", time: fmtTime(),
                text: correctedHeard,
              });
            }
          }
        }
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
        // Same Tauri-side ASR correction: catches the SSE-feed path
        // (raw vLLM completions that bypassed HA's output backstop).
        const correctedAssistant = applyAsrCorrection(cleanedText);
        const assistantEvent = entry.assistant
          ? [{
              id: nextId(),
              kind: isPerception ? "perception" : (isDiag ? "diag" : "home"),
              channel: diagChannel,
              time: fmtTime(),
              text: correctedAssistant,
            }]
          : [];
        // Defensive dedupe (May 2026): assistant text occasionally arrives
        // from two sources within a few seconds (chat-tee SSE + WS
        // transcript / conversation.finished). Both sources are now
        // wired up — the bridge-side fix removes the WS transcript for
        // assistant role, and the new conv.finished subscriber uses the
        // same lookup helper. Scan back 20 events (action cards +
        // perceptions can interleave between duplicates).
        return [...prev, ...newUserEvents, ...actionCards, ...assistantEvent.filter((ev) => {
          if (ev.kind !== "home" && ev.kind !== "perception") return true;
          return findRecentAssistantIdx(prev, ev.text, ev.kind, 20) === -1;
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
    if (sim.active) return undefined; // Sim Mode: no real health polls
    const base = metricsBase || metricsBaseFromEndpoint(endpoint);
    if (!base) return undefined;
    let cancelled = false;

    // Derive bridge URL: same host as sidecar, port 8094.
    // Derive supervisor URL: same host as sidecar, port 8093 (AI Stack Control).
    const bridgeUrl = s2sBaseFromEndpoint(endpoint);
    const supervisorUrl = supervisorBaseFromEndpoint(base, endpoint);

    // STACK_TOKEN comes from one of (in priority order):
    //   1. window.__STACK_TOKEN  (set by the Tauri stack_token() command at
    //      app startup — Phase 4 hardening). MVP doesn't wire this yet.
    //   2. localStorage "hg-stack-token-DEV" (developer-set via DevTools).
    // Tokens are never hardcoded in this file. If neither is set, we skip
    // the supervisor poll and AiStackCard renders a "not configured" stub.
    // In web mode this may be a non-secret gateway marker. The gateway
    // replaces it with the real server-side token before forwarding.
    const stackToken =
      (typeof window !== "undefined" && window.__STACK_TOKEN) ||
      (typeof localStorage !== "undefined" && localStorage.getItem("hg-stack-token-DEV")) ||
      "";

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
              try { setBridgeHealth(await r2.json()); }
              catch { setBridgeHealth(null); }
            } else {
              setBridgeHealth(null);
            }
          }
        } catch (e) {
          if (!cancelled) {
            setBridgeOnline(false);
            setBridgeHealth(null);
          }
          console.warn("[health] bridge poll failed:", e?.message || e);
        }
      }
      // Stack supervisor (port 8093) — runs OUTSIDE docker-compose as a
      // systemd unit, so it stays reachable when the AI stack itself is
      // down. /healthz needs no auth (used here as a liveness probe);
      // /api/stack/status needs the bearer token. If no token is
      // configured we still probe /healthz so the UI can show "supervisor
      // is up, but token not configured" instead of "supervisor offline".
      const warnSupervisorHealth = (key, message, detail) => {
        if (aiStackHealthWarnRef.current === key) return;
        aiStackHealthWarnRef.current = key;
        if (detail != null) console.warn(message, detail);
        else console.warn(message);
      };
      const clearSupervisorHealthWarn = () => {
        aiStackHealthWarnRef.current = "";
      };
      if (supervisorUrl) {
        try {
          const rh = await tauriFetch(`${supervisorUrl}/healthz`, { cache: "no-store" });
          if (!cancelled) {
            setAiStackOnline(rh.ok);
            if (!rh.ok) {
              setAiStackStatusError(null);
              setAiStackState(null);
            }
          }
          if (rh.ok && stackToken) {
            try {
              const rs = await tauriFetch(`${supervisorUrl}/api/stack/status`, {
                cache: "no-store",
                headers: { Authorization: `Bearer ${stackToken}` },
              });
              if (rs.ok) {
                try {
                  const body = await rs.json();
                  if (!cancelled) {
                    setAiStackStatusError(null);
                    setAiStackState(body);
                    clearSupervisorHealthWarn();
                  }
                } catch (e) {
                  if (!cancelled) {
                    setAiStackStatusError("bad_json");
                    setAiStackState(null);
                  }
                  warnSupervisorHealth("bad_json", "[health] supervisor status JSON failed:", e?.message || e);
                }
              } else if (rs.status === 401) {
                // Reachable supervisor, rejected token. Keep this distinct
                // from offline so recovery UI can point at /stack-token.
                if (!cancelled) {
                  setAiStackStatusError("auth");
                  setAiStackState(null);
                }
                warnSupervisorHealth("auth", "[health] supervisor 401 - STACK_TOKEN rejected");
              } else if (rs.status === 429) {
                // rate-limited — back off; next 15s tick will retry.
                if (!cancelled) {
                  setAiStackStatusError("rate_limited");
                  setAiStackState(null);
                }
                warnSupervisorHealth("rate_limited", "[health] supervisor 429 - auth rate-limited");
              } else {
                if (!cancelled) {
                  setAiStackStatusError(`http_${rs.status || "unknown"}`);
                  setAiStackState(null);
                }
                warnSupervisorHealth(`http_${rs.status || "unknown"}`, `[health] supervisor status HTTP ${rs.status || "unknown"}`);
              }
            } catch (e) {
              if (!cancelled) {
                setAiStackStatusError("fetch_failed");
                setAiStackState(null);
              }
              warnSupervisorHealth("fetch_failed", "[health] supervisor status fetch failed:", e?.message || e);
            }
          } else if (rh.ok && !stackToken && !cancelled) {
            setAiStackStatusError(null);
            setAiStackState(null);
            clearSupervisorHealthWarn();
          }
        } catch (e) {
          if (!cancelled) {
            setAiStackOnline(false);
            setAiStackStatusError(null);
            setAiStackState(null);
          }
          // Quiet — supervisor down is a normal degraded state, not a bug.
        }
      }
    };

    poll();  // immediate
    const id = setInterval(poll, 15000);
    return () => { cancelled = true; clearInterval(id); };
  }, [metricsBase, endpoint, sim.active]);

  /* ── Voice PE activity indicator (header + voice state) ──────────────
   *
   * State_changed events for assist_satellite are still useful for
   * showing "Voice PE is listening" in the connection-dot area, even
   * though we no longer render placeholder turns here. The actual turn
   * content arrives via the SSE feed above. */
  useEffect(() => {
    if (sim.active) return undefined;
    if (connection !== "online" || !haClientRef.current) return undefined;
    const VOICE_PE = (window.PROACTIVE_CONFIG && window.PROACTIVE_CONFIG.VOICE_PE_SATELLITE) || null;
    const unsub = haClientRef.current.subscribeEvents("state_changed", (ev) => {
      const d = ev?.data;
      if (!d?.entity_id?.startsWith?.("assist_satellite.")) return;
      const newState = d.new_state?.state;
      if (newState !== d.old_state?.state) {
        console.log("[voicepe state]", d.old_state?.state, "→", newState);
      }
      // Drive the proactive coordinator's speak → follow-up → idle lifecycle
      // off the wall puck's REAL state transitions (it self-gates on phase,
      // so normal Voice PE use is ignored).
      if (VOICE_PE && d.entity_id === VOICE_PE && newState) {
        proactiveCoord.observeSatelliteState(newState);
      }
    });
    return unsub;
  }, [connection]);

  /* ── Routing decisions → live [route] diag stream ────────────────────
   *
   * Subscribes to the extended_openai_conversation.routing_decision HA
   * event (fired inside log_decision() AFTER redaction — so the payload
   * here matches the JSONL line on disk exactly). Renders each as one
   * compact diag line in the chat feed; the existing `/debug on` filter
   * shows/hides them.
   *
   * `EXTERNAL_ROUTING_LOG_MODE=off` on the HAOS side disables both the
   * file write AND the event fire, so this hook silently goes idle when
   * logging is turned off — no client-side toggle needed.
   *
   * Field shape mirrors external_routing.log_decision():
   *   ts, conv_id, src, text, text_len, intent, matched, key_present,
   *   dispatched, ext_status, ext_latency_ms, ext_reply_chars,
   *   ext_reply_snippet, local_reply_chars, local_reply_snippet
   */
  useEffect(() => {
    if (sim.active) return undefined;
    if (connection !== "online" || !haClientRef.current) return undefined;
    const unsub = haClientRef.current.subscribeEvents(
      "extended_openai_conversation.routing_decision",
      (ev) => {
        const d = ev?.data || {};
        const src = (d.src || "?").padEnd(5);
        // Show dispatched route, falling back to intent if the
        // completion-side log line isn't in yet (decision-only writes
        // omit `dispatched`).
        const route = d.dispatched || d.intent || "?";
        const matched = d.matched || "-";
        const text = (d.text || "").replace(/\s+/g, " ").slice(0, 80);
        let tail;
        if (d.dispatched === "external") {
          const lat = d.ext_latency_ms != null ? `${(d.ext_latency_ms / 1000).toFixed(1)}s` : "?";
          const chars = d.ext_reply_chars != null ? `${d.ext_reply_chars}ch` : "?";
          tail = `${d.ext_status || "?"} ${lat} ${chars}`;
        } else if (d.dispatched === "external→fallback") {
          tail = `fail:${d.ext_status || "?"}, local: "${(d.local_reply_snippet || "").slice(0, 60)}"`;
        } else if (d.dispatched === "local") {
          const snippet = (d.local_reply_snippet || "").slice(0, 80);
          tail = snippet ? `local: "${snippet}"` : "local";
        } else {
          tail = "(pending)";
        }
        const text_part = text ? `"${text}"` : "(no text)";
        const line = `[route] ${src} • ${route} • matched=${matched} • ${text_part} → ${tail}`;
        addEvent({ kind: "diag", channel: "routing", text: line });
      },
    );
    return unsub;
  }, [connection]);

  /* ── Living Lights override + articulation events → chat feed ────────
   *
   * Subscribes to `living_lights_override_detected` AND
   * `living_lights_articulation` HA bus events via the dedicated
   * home-lighting-events.jsx module. Two separate subscriptions; no
   * routing through home-proactive.jsx.
   *
   * Override events render as a kind:"system" line (non-verbose by
   * default; verbose under /debug on). Articulation events render as
   * kind:"assistant" lines, capped at 200 chars by the module.
   *
   * R5/converged-plan First-PR observation layer.
   */
  useEffect(() => {
    if (sim.active) return undefined;
    if (connection !== "online" || !haClientRef.current) return undefined;
    const lightingApi = (typeof window !== "undefined") && window.HomeLightingEvents;
    if (!lightingApi || typeof lightingApi.subscribe !== "function") {
      return undefined;
    }
    const unsub = lightingApi.subscribe(
      haClientRef.current,
      addEvent,
      { debugMode: () => debugMode },
    );
    return () => { try { unsub?.(); } catch (_) { /* ignore */ } };
  }, [connection, debugMode]);

  /* ── M1 perception caption events → perception chip + thumbnail ──────
   *
   * Subscribes to extended_openai_conversation.perception_caption which
   * fires from world_state.py's auto-trigger scheduler (and from manual
   * refresh_perception) whenever a fresh vision-sidecar caption lands.
   * Payload: {room, caption, source, snapshot_url?, ts, latency_ms,
   * camera_entity_id}.
   *
   * Emits a kind:"perception" event so PerceptionContent (home-events.jsx)
   * renders the compact chip with an optional thumbnail. We dedupe
   * against any chip the bridge SSE may have already produced for the
   * same room+caption text within the last 8 events — both sources
   * happen to be wired simultaneously today.
   */
  useEffect(() => {
    if (sim.active) return undefined;
    if (connection !== "online" || !haClientRef.current) return undefined;
    const unsub = haClientRef.current.subscribeEvents(
      "extended_openai_conversation.perception_caption",
      (ev) => {
        const d = ev?.data || {};
        const room = d.room || "";
        const caption = applyAsrCorrection(d.caption || "");
        if (!caption) return;
        const text = room ? `${room}: ${caption}` : caption;
        // 8-event lookback dedupe — matches the SSE handler's window.
        // PerceptionContent renders the chip; snapshotUrl is consumed
        // by the same component for thumbnail rendering when present.
        setEvents((prev) => {
          for (let i = prev.length - 1; i >= Math.max(0, prev.length - 8); i--) {
            if (prev[i].kind === "perception" && prev[i].text === text) {
              return prev;
            }
          }
          return [...prev, {
            id: nextId(),
            kind: "perception",
            channel: "perception",
            time: fmtTime(),
            text,
            snapshotUrl: d.snapshot_url || null,
            source: d.source || null,
            latencyMs: d.latency_ms != null ? d.latency_ms : null,
          }];
        });
      },
    );
    return unsub;
  }, [connection]);

  /* ── Jarvis mute (Addendum 9) — header pill subscription ───────────
   *
   * binary_sensor.jarvis_muted_effective_2 is a composite OR of:
   *   • input_boolean.jarvis_muted_explicit  ("Hey Jarvis, stop" or /mute)
   *   • input_datetime.jarvis_mute_until > now  (timer)
   *   • input_boolean.homeai_movie  (movie mode)
   *   • media_player.lg_tv active > 5min (auto-detect) — opt-out via
   *     input_boolean.jarvis_auto_mute_tv
   *
   * Initial fetch via REST + live updates via state_changed subscription.
   * Mirrors the routing_decision pattern from Addendum 1.
   */
  useEffect(() => {
    if (sim.active) return undefined;
    if (connection !== "online" || !haClientRef.current) return undefined;
    const client = haClientRef.current;
    let cancelled = false;
    // Initial state fetch.
    (async () => {
      try {
        const s = await client.callApi("GET", "states/binary_sensor.jarvis_muted_effective_2");
        if (cancelled) return;
        setMuteState({
          muted: s?.state === "on",
          reason: s?.attributes?.reason || "",
          until: s?.attributes?.mute_until_iso || "",
        });
      } catch (e) {
        // Helper not deployed yet — silently leave default (false).
      }
    })();
    // Live updates.
    const unsub = client.subscribeEvents("state_changed", (ev) => {
      if (ev?.data?.entity_id !== "binary_sensor.jarvis_muted_effective_2") return;
      const ns = ev.data.new_state;
      setMuteState({
        muted: ns?.state === "on",
        reason: ns?.attributes?.reason || "",
        until: ns?.attributes?.mute_until_iso || "",
      });
    });
    return () => { cancelled = true; if (unsub) try { unsub(); } catch {} };
  }, [connection]);

  // Click handler for header pill — fires /unmute equivalent.
  const handleUnmuteClick = useCallback(() => {
    const client = haClientRef.current;
    if (!client) return;
    client.callService("input_boolean", "turn_off", { entity_id: "input_boolean.jarvis_muted_explicit" })
      .catch(() => {});
    client.callService("input_datetime", "set_datetime", {
      entity_id: "input_datetime.jarvis_mute_until", datetime: "2000-01-01T00:00:00",
    }).catch(() => {});
    addEvent({ kind: "system", text: "unmuted via header pill click", tone: "ok" });
  }, [addEvent]);

  /* ── conversation.finished → fallback transcript renderer ────────────
   *
   * The metrics-sidecar SSE feed at /conversations/stream broadcasts
   * vLLM completions only — so for HA-routed turns that DON'T touch
   * vLLM (every external-route turn, since ask_external() calls OpenAI
   * directly), neither the user-input bubble nor the assistant response
   * has a rendering source. The [route] diag line lands but the actual
   * transcript is invisible.
   *
   * HA's extended_openai_conversation integration fires
   * `extended_openai_conversation.conversation.finished` on the success
   * path of BOTH routes with a SLIM payload:
   *   {conversation_id, agent_id, user_input_text, assistant_text, route}
   *
   * (The earlier shape included the full chat_log via messages[]; that
   * routinely exceeded 32 KB on tool-using turns, exceeding HA's
   * recorder + WS delivery limits and breaking transcript rendering.
   * Slim payload fixes both — see plan Addendum 4 fix-up.)
   *
   * Dedup against bubbles the SSE feed may have already rendered for
   * local turns. findRecentUserIdx / findRecentAssistantIdx are shared
   * with the SSE handler — whichever source fires second sees the
   * first source's bubbles and skips.
   */
  useEffect(() => {
    if (sim.active) return undefined;
    if (connection !== "online" || !haClientRef.current) return undefined;
    const unsub = haClientRef.current.subscribeEvents(
      "extended_openai_conversation.conversation.finished",
      (ev) => {
        const d = ev?.data || {};
        // Slim shape (current): direct fields. Backward compat: also
        // read the old messages[] shape if it shows up (a stale HA
        // instance still has the old code).
        let userText = d.user_input_text || "";
        let assistantText = d.assistant_text || "";
        if (!userText && Array.isArray(d.messages)) {
          userText = d.user_input?.text
            || d.messages.find((m) => m?.role === "user" && typeof m?.content === "string")?.content
            || "";
        }
        if (!assistantText && Array.isArray(d.messages)) {
          const lastAssistant = [...d.messages].reverse().find(
            (m) => m?.role === "assistant"
                && typeof m?.content === "string"
                && m.content.trim()
          );
          assistantText = lastAssistant?.content || "";
        }

        // Apply Tauri-side ASR correction so this event source matches
        // the SSE handler's corrected text — dedup needs both sides to
        // be in the same canonical form. HA-side backstop already runs
        // on assistant_text before the event fires, but this is the
        // single line of defense for the user_input_text path AND a
        // safety net if HA somehow misses.
        const userTextC = applyAsrCorrection(userText);
        const assistantTextC = applyAsrCorrection(assistantText);
        // M5: capture conversation_id so the explainability drawer can
        // filter the routing log by it later. WS event carries it
        // directly per slim payload (see comment above).
        const convId = d.conversation_id || null;
        setEvents((prev) => {
          const userIdx = findRecentUserIdx(prev, userTextC, 8);
          const asstIdx = findRecentAssistantIdx(prev, assistantTextC, "home", 20);
          const newUser = (userTextC && userIdx === -1)
            ? [{ id: nextId(), kind: "voice", time: fmtTime(), text: userTextC, convId }]
            : [];
          const newAsst = (assistantTextC && asstIdx === -1)
            ? [{ id: nextId(), kind: "home", time: fmtTime(), text: assistantTextC, convId }]
            : [];
          // If we found existing bubbles (dedup hit) and they're
          // missing a convId, retroactively stamp them so the drawer
          // works on either fire-order race.
          let augmented = prev;
          if (convId) {
            if (userIdx !== -1 && !prev[userIdx]?.convId) {
              augmented = augmented.map((e, i) => i === userIdx ? { ...e, convId } : e);
            }
            if (asstIdx !== -1 && !augmented[asstIdx]?.convId) {
              augmented = augmented.map((e, i) => i === asstIdx ? { ...e, convId } : e);
            }
          }
          if (newUser.length === 0 && newAsst.length === 0) {
            return augmented === prev ? prev : augmented;
          }
          return [...augmented, ...newUser, ...newAsst];
        });
      },
    );
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
    if (sim.active) return undefined;
    if (connection !== "online" || !haClientRef.current) return undefined;
    const client = haClientRef.current;
    const cameraIds = (window.HG_CAMERAS || []).map((c) => c.id);
    if (cameraIds.length === 0) return undefined;

    let cancelled = false;
    client.call({ type: "get_states" }).then((states) => {
      if (cancelled) return;
      setCameraLabels(cameraLabelsFromStates(cameraIds, states));
    }).catch(() => {});

    const unsub = client.subscribeEvents("state_changed", (ev) => {
      setCameraLabels((prev) => updateCameraLabelsFromEvent(cameraIds, prev, ev));
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
    if (sim.active) return undefined;
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
        if (!cancelled) setNetworkMetrics(emptyNetworkMetrics());
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
        const numericMetric = eid.endsWith("_cpu_utilization") || eid.endsWith("_memory_utilization");
        if (numericMetric && !isFinite(val)) {
          rebuildFromStates();
          return;
        }
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
              cpu: eid.endsWith("_cpu_utilization") && isFinite(val) ? val : null,
              mem: eid.endsWith("_memory_utilization") && isFinite(val) ? val : null,
              state: eid.endsWith("_state") ? newState : null,
            });
          }
          return {
            ...prev,
            switches: switches.filter((sw) => sw.cpu != null || sw.mem != null || sw.state),
          };
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
    if (sim.active) return undefined;
    if (connection !== "online" || !haClientRef.current) return undefined;
    const client = haClientRef.current;
    let cancelled = false;

    const cpuRe  = /^sensor\.(system_monitor_)?(processor_use|cpu_use)/;
    // v6: widened to accept `memory_use` (raw MB) when `_percent` isn't
    // exposed by the user's System Monitor config. Same for disk.
    const ramRe  = /^sensor\.(system_monitor_)?(memory_use_percent|memory_use|ram_use)/;
    const diskRe = /^sensor\.(system_monitor_)?(disk_use_percent|disk_use)$/;
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

    // v6: when the entity is a raw `_use` (MB), we capture it as ramMb
    // (and convert from MB→GB at render). When it's a `_use_percent`,
    // capture as ram (percent). The card prefers ram when present,
    // falls back to displaying ramGb otherwise.
    const isPercentRam = (eid) => /_percent$/.test(eid);
    const isPercentDisk = (eid) => /_percent$/.test(eid);

    const rebuild = async () => {
      try {
        const states = await client.call({ type: "get_states" });
        if (cancelled) return;
        let cpu = null, ram = null, ramGb = null, disk = null, diskGb = null, uptime = null;
        for (const s of states) {
          const eid = s.entity_id;
          const val = parseFloat(s.state);
          if (cpuRe.test(eid) && isFinite(val)) cpu = val;
          else if (ramRe.test(eid) && isFinite(val)) {
            if (isPercentRam(eid)) ram = val;
            else ramGb = val / 1024; // MB → GB
          }
          else if (diskRe.test(eid) && isFinite(val)) {
            if (isPercentDisk(eid)) disk = val;
            else diskGb = val / 1024;
          }
          else if (bootRe.test(eid)) uptime = fmtUptime(s.state);
        }
        const any = (cpu != null) || (ram != null) || (ramGb != null) || (disk != null) || (diskGb != null) || uptime;
        setHostMetrics(any ? { cpu, ram, ramGb, disk, diskGb, uptime } : null);
      } catch (e) {
        if (!cancelled) setHostMetrics(null);
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
        const cur = prev || { cpu: null, ram: null, ramGb: null, disk: null, diskGb: null, uptime: null };
        if (cpuRe.test(eid)) cur.cpu = isFinite(val) ? val : null;
        else if (ramRe.test(eid)) {
          if (isPercentRam(eid)) cur.ram = isFinite(val) ? val : null;
          else cur.ramGb = isFinite(val) ? val / 1024 : null;
        }
        else if (diskRe.test(eid)) {
          if (isPercentDisk(eid)) cur.disk = isFinite(val) ? val : null;
          else cur.diskGb = isFinite(val) ? val / 1024 : null;
        }
        else if (bootRe.test(eid)) cur.uptime = fmtUptime(newState);
        const any = (cur.cpu != null) || (cur.ram != null) || (cur.ramGb != null)
          || (cur.disk != null) || (cur.diskGb != null) || cur.uptime;
        return any ? { ...cur } : null;
      });
    });

    return () => { cancelled = true; try { unsub(); } catch {} };
  }, [connection]);

  /* ── Tray v4: Frigate stats from /config/packages/frigate_stats.yaml ─
   *
   * Subscribes to the sensor.frigate_* family:
   *   sensor.frigate_stats          → uptime (s)
   *   sensor.frigate_total_cpu      → aggregate CPU %
   *   sensor.frigate_coral_inference → Coral TPU inference (ms)
   *   sensor.frigate_<camera>_fps   → per-camera process FPS
   *
   * If the package isn't deployed, frigateMetrics stays null and the
   * tray shows the "enable stats sensors" empty-state with deploy steps
   * in the tooltip.
   */
  useEffect(() => {
    if (sim.active) return undefined;
    if (connection !== "online" || !haClientRef.current) return undefined;
    const client = haClientRef.current;
    let cancelled = false;

    const statsRe = /^sensor\.frigate_stats$/;
    const cpuRe = /^sensor\.frigate_total_cpu$/;
    const coralRe = /^sensor\.frigate_coral_inference(_ms)?$/;
    const fpsRe = /^sensor\.frigate_([a-z_]+)_fps$/;

    const fmtUptime = (secs) => {
      const s = parseFloat(secs);
      if (!isFinite(s) || s <= 0) return null;
      const d = Math.floor(s / 86400);
      const h = Math.floor((s % 86400) / 3600);
      const m = Math.floor((s % 3600) / 60);
      if (d > 0) return `${d}d ${h}h`;
      if (h > 0) return `${h}h ${m}m`;
      return `${m}m`;
    };

    const apply = (cur, eid, raw) => {
      const val = parseFloat(raw);
      const next = { ...cur };
      if (statsRe.test(eid)) next.uptime = fmtUptime(raw);
      else if (cpuRe.test(eid)) next.totalCpu = isFinite(val) ? val : next.totalCpu;
      else if (coralRe.test(eid)) next.coralMs = isFinite(val) ? val : next.coralMs;
      else {
        const m = fpsRe.exec(eid);
        if (m) {
          if (!next.fps) next.fps = {};
          next.fps = { ...next.fps, [m[1]]: isFinite(val) ? val : next.fps?.[m[1]] };
        }
      }
      return next;
    };

    const rebuild = async () => {
      try {
        const states = await client.call({ type: "get_states" });
        if (cancelled) return;
        let acc = { totalCpu: null, coralMs: null, uptime: null, fps: {} };
        let anyFrigate = false;
        for (const s of states) {
          const eid = s.entity_id;
          if (!eid.startsWith("sensor.frigate_")) continue;
          if (s.state === "unknown" || s.state === "unavailable") continue;
          anyFrigate = true;
          acc = apply(acc, eid, s.state);
        }
        const hasData = anyFrigate && (
          acc.totalCpu != null || acc.coralMs != null || acc.uptime || Object.keys(acc.fps || {}).length > 0
        );
        setFrigateMetrics(hasData ? acc : null);
      } catch (e) {
        if (!cancelled) setFrigateMetrics(null);
        console.warn("[frigate] state load failed:", e?.message || e);
      }
    };
    rebuild();

    const unsub = client.subscribeEvents("state_changed", (ev) => {
      const d = ev?.data;
      const eid = d?.entity_id;
      if (!eid || !eid.startsWith("sensor.frigate_")) return;
      const newState = d.new_state?.state;
      if (newState === "unknown" || newState === "unavailable") {
        rebuild();
        return;
      }
      if (!(statsRe.test(eid) || cpuRe.test(eid) || coralRe.test(eid) || fpsRe.test(eid))) return;
      setFrigateMetrics((prev) => apply(prev || { totalCpu: null, coralMs: null, uptime: null, fps: {} }, eid, newState));
    });

    return () => { cancelled = true; try { unsub(); } catch {} };
  }, [connection]);

  /* ── Tray v3: bridge /rooms endpoint poll (occupancy + visual age) ── */
  useEffect(() => {
    if (sim.active) return undefined;
    const base = metricsBase || metricsBaseFromEndpoint(endpoint);
    if (!base) return undefined;
    const bridgeUrl = s2sBaseFromEndpoint(endpoint);
    if (!bridgeUrl) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await tauriFetch(`${bridgeUrl}/rooms`, { cache: "no-store" });
        if (cancelled) return;
        if (!r.ok) {
          setRoomContext(null);
          return;
        }
        try { setRoomContext(await r.json()); }
        catch { setRoomContext(null); }
      } catch (e) {
        if (!cancelled) setRoomContext(null);
      }
    };
    tick();
    const id = setInterval(tick, 8000);
    return () => { cancelled = true; clearInterval(id); };
  }, [metricsBase, endpoint]);

  /* ── Phase 2: vision-sidecar health (phash hit rate, cameras cached) ── */
  useEffect(() => {
    if (sim.active) return undefined;
    const base = metricsBase || metricsBaseFromEndpoint(endpoint);
    if (!base) return undefined;
    let cancelled = false;
    const visionUrl = visionBaseFromEndpoint(base, endpoint);
    const tick = async () => {
      if (!visionUrl) return;
      try {
        const r = await tauriFetch(`${visionUrl}/healthz`, { cache: "no-store" });
        if (!cancelled) {
          setVisionSidecarOnline(r.ok);
          if (r.ok) {
            try { setVisionHealth(await r.json()); }
            catch { setVisionHealth(null); }
          } else {
            setVisionHealth(null);
          }
        }
      } catch (e) {
        if (!cancelled) {
          setVisionSidecarOnline(false);
          setVisionHealth(null);
        }
        console.warn("[vision] healthz poll failed:", e?.message || e);
      }
    };
    tick();
    const id = setInterval(tick, 10000);
    return () => { cancelled = true; clearInterval(id); };
  }, [metricsBase, endpoint]);

  const isSpatialWide = spatialLayout && wideMode;
  const spatialRailWidth = wideMode ? SPATIAL_CHAT_RAIL_WIDTH : "100%";
  const spatialStageRight = wideMode ? spatialRailWidth : 0;
  if (typeof window !== "undefined") window.__HOME_SPATIAL_MODE = isSpatialWide;
  const appColumnStyle = spatialLayout ? {
    position: "relative",
    zIndex: 5,
    marginLeft: "auto",
    width: spatialRailWidth,
    maxWidth: "100vw",
    height: "100%",
    minHeight: 0,
    display: "flex",
    flexDirection: "column",
    borderLeft: wideMode ? "1px solid rgba(184,216,255,0.055)" : "none",
    background: theme === "dark" ? "rgba(4,5,8,0.76)" : "rgba(245,246,248,0.88)",
    backdropFilter: "blur(18px)",
    boxShadow: wideMode ? "-12px 0 46px rgba(0,0,0,0.28)" : "none",
  } : {
    width: "100%",
    height: "100%",
    minHeight: 0,
    display: "flex",
    flexDirection: "column",
  };
  const spatialActiveSurface =
    videoLabelerOpen ? "cameras" :
    peopleOpen ? "people" :
    intelligenceOpen ? "intel" :
    lightsOpen ? "lights" : "home";
  const cleanEmbeddedApartmentState = () => {
    try {
      window.__HOME_APARTMENT_EMBEDDED_API?.exitEdit?.();
      window.__HOME_APARTMENT_EMBEDDED_API?.closeCards?.();
    } catch (e) { /* best-effort spatial cleanup */ }
  };
  const closeSpatialToolSurfaces = () => {
    cleanEmbeddedApartmentState();
    setPeopleOpen(false);
    setIntelligenceOpen(false);
    setVideoLabelerOpen(false);
    setLightsOpen(false);
    setWorldStateDrawerOpen(false);
    setSpatialDrawerOpen(false);
    setLookDrawerOpen(false);
    setExplainConvId(null);
  };
  const metricsStrip = (
    <MetricsStrip
      metrics={metrics}
      metricsHistory={metricsHistory}
      style={metricsStyle}
      metricsBase={metricsBase || metricsBaseFromEndpoint(endpoint)}
      bridgeHealth={bridgeHealth}
      networkMetrics={networkMetrics}
      visionSidecarOnline={visionSidecarOnline}
      visionHealth={visionHealth}
      hostMetrics={hostMetrics}
      frigateMetrics={frigateMetrics}
      connection={connection}
      sidecarOnline={sidecarOnline}
      bridgeOnline={bridgeOnline}
      aiStackOnline={aiStackOnline}
      aiStackState={aiStackState}
      aiStackStatusError={aiStackStatusError}
      roomContext={roomContext}
      voice={voice}
      identity={identity}
      media={media}
      cameraLabels={cameraLabels}
      recentPerceptions={events.filter((e) => e.kind === "perception").slice(-5)}
      traceSummary={traceSummary}
      lastTrace={lastTrace}
      setTraceSummary={setTraceSummary}
      setLastTrace={setLastTrace}
      onTracesFetched={handleTracesFetched}
      onRoutingLogFetched={handleRoutingLogFetched}
      proactive={proactive}
      onProactiveAction={handleProactiveStatusAction}
      simActive={sim.active}
      labTurnsRef={labTurnsRef}
      labSamplesRef={labSamplesRef}
      labTick={labTick}
      token={token}
      endpoint={endpoint}
      dockMode={isSpatialWide}
      mobile={mobile}
    />
  );

  return (
    <div ref={rootRef} data-theme={theme} style={{
      "--hg-row-py": density === "condensed" ? `${8}px` : `${14}px`,
      position: "relative",
      display: "flex", flexDirection: "column",
      width: "100%",
      height: "100%",
      minHeight: mobile ? "100dvh" : 0,
      background: spatialLayout ? "#000" : "var(--hg-bg-0)",
      color: "var(--hg-fg-0)",
      fontFamily: "'Geist', system-ui, sans-serif",
      overflow: "hidden",
      paddingBottom: 0,
    }}>
      {isSpatialWide && window.HomeApartmentView && (
        <div data-theme="dark" style={{
          position: "absolute",
          top: 0,
          left: 0,
          bottom: 0,
          right: spatialStageRight,
          zIndex: 0,
          minWidth: 0,
          overflow: "hidden",
          background: "#000",
        }}>
          <window.HomeApartmentView
            open={true}
            embedded={true}
            onClose={() => {}}
            endpoint={endpoint}
            token={token}
            sim={sim}
          />
          <SpatialModeRail
            active={spatialActiveSurface}
            theme={theme}
            opsVisible={spatialOpsDockOpen}
            onHome={() => {
              closeSpatialToolSurfaces();
              setSpatialOpsDockOpen(true);
            }}
            onCameras={() => {
              closeSpatialToolSurfaces();
              setSpatialOpsDockOpen(false);
              setVideoLabelerOpen(true);
            }}
            onPeople={() => {
              closeSpatialToolSurfaces();
              setSpatialOpsDockOpen(false);
              setPeopleOpen(true);
            }}
            onIntelligence={() => {
              closeSpatialToolSurfaces();
              setSpatialOpsDockOpen(false);
              setIntelligenceOpen(true);
            }}
            onLights={() => {
              closeSpatialToolSurfaces();
              setSpatialOpsDockOpen(false);
              setLightsOpen(true);
            }}
            onOps={() => {
              closeSpatialToolSurfaces();
              setSpatialOpsDockOpen((open) => !open);
            }}
            onToggleTheme={() => setTheme((t) => t === "dark" ? "light" : "dark")}
          />
          {spatialOpsDockOpen && (
            <div
              className="hg-fade"
              style={{
                position: "absolute",
                left: 82,
                right: "auto",
                bottom: 50,
                width: "min(620px, calc(100% - 118px))",
                zIndex: 4,
                maxHeight: "min(245px, 26vh)",
                overflow: "hidden",
                border: "1px solid rgba(184,216,255,0.08)",
                borderRadius: 6,
                background: "rgba(3,5,8,0.58)",
                backdropFilter: "blur(12px)",
                boxShadow: "0 14px 44px rgba(0,0,0,0.30)",
              }}
            >
              {metricsStrip}
            </div>
          )}
        </div>
      )}
      <div style={appColumnStyle}>
      <HomeHeader
        theme={theme}
        onToggleTheme={isSpatialWide ? null : () => setTheme((t) => t === "dark" ? "light" : "dark")}
        voice={voice}
        connection={connection}
        sidecarOnline={sidecarOnline}
        bridgeOnline={bridgeOnline}
        bridgeHealth={bridgeHealth}
        visionSidecarOnline={visionSidecarOnline}
        visionHealth={visionHealth}
        frigateMetrics={frigateMetrics}
        cameraLabels={cameraLabels}
        sim={sim}
        muteState={muteState}
        onUnmuteClick={handleUnmuteClick}
        onOpenPeople={isSpatialWide ? null : () => { setVideoLabelerOpen(false); setPeopleOpen(true); }}
        onOpenIntelligence={isSpatialWide ? null : () => { setVideoLabelerOpen(false); setIntelligenceOpen(true); }}
        onOpenVideoLabeler={isSpatialWide ? null : () => {
          // full-screen surfaces are mutually exclusive (see state decl)
          setPeopleOpen(false);
          setIntelligenceOpen(false);
          setApartmentOpen(false);
          setVideoLabelerOpen(true);
        }}
        onOpenSimulationControls={() => setSimulationControlsOpen(true)}
        peopleButtonRef={peopleButtonRef}
        aiStackState={aiStackState}
        metrics={metrics}
        serviceProfile={serviceProfile}
        onOpenRemoteProfile={() => setRemotePanelOpen(true)}
        mobile={mobile}
      />
      <SimulationControlsDialog
        open={simulationControlsOpen}
        sim={sim}
        onClose={() => setSimulationControlsOpen(false)}
      />
      <RemoteProfileDialog
        open={remotePanelOpen}
        profile={serviceProfile}
        probeResults={serviceProbeResults}
        readiness={serviceReadiness}
        running={serviceProbeRunning}
        onClose={() => { setRemotePanelOpen(false); resetFeedScrollTop(); }}
        onProfileChange={applyServiceProfile}
        onRemoteCheck={() => runRemoteCheck(true)}
        onDebugBundle={copyDebugBundle}
        onCopyRecovery={copyRecoveryCommands}
        onCopyServiceUrls={copyServiceUrls}
        mobile={mobile}
      />
      {/* Addendum 14 / Slice 3 — people overlay. Opens from the header
          button; closes via Escape or its own close button. NOT inside
          the metrics drawer (per AR-13). */}
      {window.HomePeopleOverlay && (
        <window.HomePeopleOverlay
          open={peopleOpen}
          onClose={closePeopleOverlay}
          endpoint={endpoint}
          token={token}
          client={haClientRef.current}
          connection={connection}
          sim={sim}
          spatialMode={isSpatialWide}
        />
      )}
      {/* M5 (Addendum 27) — explainability drawer. Mounted alongside
          people overlay so it can co-exist (people open + drawer open
          for a tool-firing turn = both visible, drawer on the right). */}
      {window.HomeIntelligenceOverlay && (
        <window.HomeIntelligenceOverlay
          open={intelligenceOpen}
          onClose={() => setIntelligenceOpen(false)}
          metricsBase={metricsBase}
          endpoint={endpoint}
          spatialMode={isSpatialWide}
        />
      )}
      {/* /labeler — full-screen video timeline labeler (M0 shell; see
          home-video-labeler.jsx). Sim containment lives in the overlay +
          its data layer (media URLs bypass tauriFetch). */}
      {window.HomeVideoLabelerOverlay && (
        <window.HomeVideoLabelerOverlay
          open={videoLabelerOpen}
          onClose={() => setVideoLabelerOpen(false)}
          sim={sim}
          spatialMode={isSpatialWide}
        />
      )}
      {window.HomeExplainDrawer && (
        <window.HomeExplainDrawer
          open={explainConvId != null}
          convId={explainConvId}
          onClose={() => setExplainConvId(null)}
          endpoint={endpoint}
          token={token}
          sim={sim}
        />
      )}
      {window.HomeLightsDrawer && (
        <window.HomeLightsDrawer
          open={lightsOpen}
          onClose={() => setLightsOpen(false)}
          client={haClientRef.current}
          connection={connection}
          sim={sim}
          spatialMode={isSpatialWide}
          askExternal={(payload) => {
            // Pre-fill the chat input with a structured `/ask`-prefixed
            // message containing full layer context + the user's intent.
            // The `/ask` prefix forces external-provider routing (ChatGPT)
            // for that single turn — bypasses the local Qwen3-VL
            // classifier, which has no code-editing tools and would just
            // refuse / give a generic answer for these change requests.
            //
            // The user reviews the populated message + presses Send to
            // dispatch. Nothing happens automatically; this is just a
            // structured-prompt bootstrap.
            const { title, intro, knobs, fileLocation, whyFrozen, userIntent } = payload || {};
            const knobsBlock = (knobs || []).map(k => {
              const cur = k.current != null ? ` = ${k.current}` : "";
              const desc = k.desc ? `  — ${k.desc}` : "";
              return `  ${k.name}${cur}${desc}`;
            }).join("\n");
            const text =
              `/ask I want to change a layer of my Living Lights automation system.\n\n` +
              `Layer: ${title}\n` +
              `File location: ${fileLocation}\n` +
              `Why it's hard to change: ${whyFrozen}\n\n` +
              `What this layer does:\n${intro}\n\n` +
              `What's tunable (with current values):\n${knobsBlock}\n\n` +
              `What I want changed:\n${userIntent}\n\n` +
              `Please:\n` +
              `1. Explain what changing this would actually do — trade-offs, side effects, things to watch for.\n` +
              `2. Propose specific values to try (give me concrete numbers, not ranges).\n` +
              `3. Tell me exactly how to apply the change — file path + the lines to edit + any rebuild/restart steps.`;
            if (typeof setInput === "function") setInput(text);
          }}
        />
      )}
      {/* F-32 (Addendum 27) — world-state drawer. Same right-anchored
          slot as the explain drawer; users typically open one OR the
          other (both technically valid but the explain drawer wins
          z-order if both happen to be open). */}
      {window.HomeWorldStateDrawer && (
        <window.HomeWorldStateDrawer
          open={worldStateDrawerOpen}
          onClose={() => setWorldStateDrawerOpen(false)}
          endpoint={endpoint}
          token={token}
          sim={sim}
          initialRoom={worldStateInitialRoom}
        />
      )}
      {/* Addendum 38 Phase 1 — /spatial light-footprint drawer. Same
          right-anchored slot as the world-state + explain drawers. */}
      {window.HomeSpatialDrawer && (
        <window.HomeSpatialDrawer
          open={spatialDrawerOpen}
          onClose={() => setSpatialDrawerOpen(false)}
          endpoint={endpoint}
          token={token}
          sim={sim}
        />
      )}
      {/* /apartment — full-screen 3D spatial command center. Full-viewport
          takeover (not the right-anchored drawer slot); engine stays warm
          across open/close. */}
      {window.HomeApartmentView && !spatialLayout && (
        <window.HomeApartmentView
          open={apartmentOpen}
          onClose={() => setApartmentOpen(false)}
          endpoint={endpoint}
          token={token}
          sim={sim}
        />
      )}
      {/* Phase 0.5 — /look "Thinking with Visual Primitives" drawer. Same
          right-anchored slot; keyed on the command nonce so a repeated
          /look remounts fresh and re-runs. */}
      {window.HomeLookDrawer && (
        <window.HomeLookDrawer
          key={lookInitial.nonce}
          open={lookDrawerOpen}
          onClose={() => setLookDrawerOpen(false)}
          metricsBase={metricsBase || metricsBaseFromEndpoint(endpoint)}
          sim={sim}
          initialCamera={lookInitial.camera}
          initialQuestion={lookInitial.question}
          onTranscript={(e) => {
            // Bug-fix: /look Q&A is retained in the chat transcript.
            if (!e) return;
            if (e.type === "question") {
              addEvent({ kind: "user", text: e.text });
            } else if (e.type === "answer") {
              addEvent({
                kind: "perception",
                text: `${e.camera} — ${e.answer || "(no answer)"}`,
                snapshotUrl: e.annotatedUrl || null,
              });
            } else if (e.type === "error") {
              addEvent({ kind: "system", tone: "error",
                         text: `look · ${e.text}` });
            }
          }}
        />
      )}
      {/* Boot sequence — one atomic conditional. While bootPhase !== "ready"
          the whole middle of the app (WelcomeBanner → VoiceBanner) is replaced
          by BootSequence; HomeHeader + InputRow stay mounted so the frame
          doesn't jump. The → "ready" reveal uses hg-fade so it reads as the
          boot logo settling into place. */}
      {bootPhase !== "ready" ? (
        <BootSequence
          mode={bootPhase === "settling" ? "settling" : bootMode}
          onComplete={handleBootComplete}
        />
      ) : (
      <div className="hg-fade" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
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
          wideMode={wideMode && !isSpatialWide}
          spatialMode={isSpatialWide}
          suppressOpen={isSpatialWide && input.startsWith("/")}
          simCameraStates={sim.active ? (simCameraStates || window.SimDefaultCameraMap) : null}
          simActive={sim.active}
        />
      )}
      <div
        ref={feedRef}
        className="hg-scroll"
        style={{
          flex: 1, overflowY: "auto",
          background: "var(--hg-bg-0)",
          paddingTop: mobile ? 8 : 0,
          paddingBottom: mobile ? 18 : 0,
          boxSizing: "border-box",
        }}>
        {isFirstRunVisible(connection, events) ? (
          <FirstRun
            connection={connection}
            endpoint={endpoint}
            token={token}
            onConnect={connectTo}
            availableModels={availableModels}
            onPickModel={confirmModel}
            onSimulation={() => {
              sim.activate?.("healthy");
              addEvent({ kind: "system", text: "[sim] simulation mode activated - healthy", tone: "info" });
            }}
            compact={isSpatialWide || mobile}
            serviceProfile={serviceProfile}
            onProfileChange={applyServiceProfile}
            onOpenRemotePanel={() => setRemotePanelOpen(true)}
            onRemoteCheck={() => runRemoteCheck(true)}
            serviceProbeResults={serviceProbeResults}
            serviceProbeRunning={serviceProbeRunning}
          />
        ) : (
          <div style={{
            // Phase 1.5 item 7: chat widens with the window past 700px,
            // capped at 1200px so very wide displays still feel readable.
            maxWidth: isSpatialWide ? "100%" : wideMode ? "min(1200px, 95vw)" : 640,
            margin: "0 auto",
          }}>
            {!isSpatialWide && <BootBanner metrics={metrics} />}
            {groupEventsBySpeaker(
              debugMode ? events : events.filter((e) => e.kind !== "diag")
            ).map((g, i) => (
              <TurnBlock key={i} group={g} density={isSpatialWide ? "compact" : density}
                onConfirmAction={confirmAction} onCancelAction={cancelAction}
                onUndoAction={undoAction}
                onControlAction={onControlAction} controlLifecycles={controlLifecycles}
                onWhy={(cid) => setExplainConvId(cid)} />
            ))}
          </div>
        )}
      </div>
      {!isSpatialWide && metricsStrip}
      <VoiceBanner voice={voice} onRetry={toggleMic} />
      </div>
      )}
      <InputRow
        value={input}
        onChange={setInput}
        onSend={sendInput}
        voice={voice}
        onMicToggle={toggleMic}
        isStreaming={events.some((e) => e.streaming)}
        onStop={stopStreaming}
        focusToken={focusToken}
        mobile={mobile}
        theme={theme}
      />
      {/* External Reasoning provider key-entry modal. position:fixed
       * inside the component itself, so this can render anywhere. See
       * home-external.jsx for the modal definition. */}
      {externalKeyModalOpen && window.ExternalKeyModal && (
        <window.ExternalKeyModal
          onClose={(result) => {
            setExternalKeyModalOpen(false);
            if (result?.saved)   addEvent({ kind: "system", text: "external provider key saved · auto-routing enabled", tone: "ok" });
            if (result?.cleared) addEvent({ kind: "system", text: "external provider key cleared", tone: "info" });
          }}
        />
      )}
      </div>
    </div>
  );
}

function EmptyHint() { return <BootBanner />; }

Object.assign(window, { HomeApp, HomeHeader, MetricsStrip, InputRow, MicButton, VoiceBanner, EventRenderer, fmtTime });
