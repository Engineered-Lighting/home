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
function HomeHeader({ theme, onToggleTheme, voice, connection }) {
  const isLive = voice.state !== "inactive" && voice.state !== "no-mic";
  const statusText =
    voice.state === "listening"  ? "listening"  :
    voice.state === "processing" ? "processing" :
    voice.state === "speaking"   ? "speaking"   :
    connection === "online"       ? "online"     :
    connection === "connecting"   ? "connecting" :
    connection === "auth_invalid" ? "bad token"  : "offline";
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
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <ConnectionDot state={isLive ? "live" : connection} />
          {(isLive || connection !== "online") && (
            <span style={{
              color: isLive ? "var(--hg-ice)"
                    : connection === "auth_invalid" ? "var(--hg-warn)"
                    : "var(--hg-fg-3)",
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

function MetricsStrip({ metrics }) {
  const [expanded, setExpanded] = useState(false);
  const [history, setHistory] = useState({
    ttft: [], tps: [], gpu: [], vram: [], cpu: [],
  });
  useEffect(() => {
    setHistory((h) => {
      const next = {};
      const keys = ["ttft", "tps", "gpu", "vram", "cpu"];
      for (const k of keys) {
        const arr = [...(h[k] || []), metrics[k]];
        next[k] = arr.slice(-40);
      }
      return next;
    });
  }, [metrics.ttft, metrics.tps, metrics.gpu, metrics.vram, metrics.cpu]);

  return (
    <div style={{ borderTop: "1px solid var(--hg-border-soft)", background: "var(--hg-bg-0)" }}>
      <div
        onClick={() => setExpanded((x) => !x)}
        style={{
          padding: "6px 12px 6px 16px",
          display: "flex", alignItems: "center", gap: 0,
          fontFamily: "'Geist Mono', ui-monospace, monospace",
          fontSize: 11,
          color: "var(--hg-fg-3)",
          fontVariantNumeric: "tabular-nums",
          cursor: "pointer",
          userSelect: "none",
        }}
      >
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>
          {metrics.model}<Sep />
          ttft <Num v={metrics.ttft} suffix="ms" /><Sep />
          <Num v={metrics.tps} /> tok/s<Sep />
          gpu <Num v={metrics.gpu} suffix="%" />
        </span>
        <span style={{
          marginLeft: 10, color: "var(--hg-fg-4)", flexShrink: 0,
          transition: "transform 220ms cubic-bezier(.4,0,.2,1)",
          transform: expanded ? "rotate(180deg)" : "rotate(0deg)",
          display: "inline-flex", alignItems: "center", justifyContent: "center", width: 14, height: 14,
        }}>
          <svg width="9" height="6" viewBox="0 0 9 6" fill="none">
            <path d="M1 1L4.5 4.5L8 1" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      </div>
      {expanded && (
        <div style={{
          display: "grid", gridTemplateColumns: "repeat(5, 1fr)",
          borderTop: "1px solid var(--hg-border-soft)",
          animation: "hg-fade-up 240ms cubic-bezier(.4,0,.2,1)",
        }}>
          <MetricTile label="ttft"  value={metrics.ttft} suffix="ms" history={history.ttft} color="var(--hg-fg-2)" />
          <MetricTile label="tok/s" value={metrics.tps}             history={history.tps}  color="var(--hg-fg-2)" />
          <MetricTile label="gpu"   value={metrics.gpu}  suffix="%" history={history.gpu}  color="var(--hg-fg-2)" />
          <MetricTile label="vram"  value={metrics.vram} suffix={`/${metrics.vramMax}g`} history={history.vram} color="var(--hg-fg-2)" />
          <div style={{
            padding: "10px 12px 8px",
            display: "flex", flexDirection: "column", gap: 4,
          }}>
            <div style={{
              fontFamily: "'Geist Mono', monospace", fontSize: 9, letterSpacing: "0.18em",
              textTransform: "uppercase", color: "var(--hg-fg-5)",
            }}>cpu</div>
            <div style={{
              display: "flex", alignItems: "baseline", gap: 3,
              fontFamily: "'Geist Mono', monospace", fontVariantNumeric: "tabular-nums",
            }}>
              <span style={{ fontSize: 16, color: "var(--hg-fg-0)", fontWeight: 500 }}>{metrics.cpu}</span>
              <span style={{ fontSize: 10, color: "var(--hg-fg-4)" }}>%</span>
            </div>
            <Sparkline data={history.cpu} color="var(--hg-fg-2)" height={20} />
          </div>
        </div>
      )}
      {expanded && (
        <div style={{
          padding: "10px 16px 12px",
          borderTop: "1px solid var(--hg-border-soft)",
          fontFamily: "'Geist Mono', monospace",
          fontSize: 10.5,
          color: "var(--hg-fg-3)",
          display: "flex", alignItems: "center", gap: 14,
          fontVariantNumeric: "tabular-nums",
        }}>
          <span style={{ color: "var(--hg-fg-5)", letterSpacing: "0.16em", textTransform: "uppercase", fontSize: 9 }}>vram</span>
          <div style={{ flex: 1, height: 4, background: "var(--hg-border-soft)", position: "relative" }}>
            <div style={{
              position: "absolute", top: 0, left: 0, bottom: 0,
              width: `${Math.min(100, (metrics.vram / metrics.vramMax) * 100)}%`,
              background: "var(--hg-ice)",
              transition: "width 600ms cubic-bezier(.4,0,.2,1)",
            }}/>
          </div>
          <span style={{ color: "var(--hg-fg-1)" }}>{metrics.vram}<span style={{ color: "var(--hg-fg-4)" }}> / {metrics.vramMax} GB</span></span>
        </div>
      )}
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
  { cmd: "/connect",  hint: "<url>",   desc: "connect to a local model endpoint" },
  { cmd: "/endpoint", hint: "<url>",   desc: "change endpoint url" },
  { cmd: "/model",    hint: "<name>",  desc: "switch active model" },
  { cmd: "/clear",    hint: "",        desc: "clear the conversation" },
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

function MicButton({ state, onClick }) {
  const isOff = state === "inactive";
  const isErr = state === "no-mic";
  const isListening = state === "listening";
  const isProcessing = state === "processing";
  const isSpeaking = state === "speaking";

  const bg = (isListening || isSpeaking) ? "var(--hg-ice-glow)" : "transparent";
  const fg = isErr ? "var(--hg-warn)"
            : (isListening || isSpeaking || isProcessing) ? "var(--hg-ice-bright)"
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
      ) : isListening || isSpeaking ? (
        <IconWaveLive bars={5} height={12} color="currentColor" />
      ) : (
        <IconMic size={15} />
      )}
    </button>
  );
}

/* ── Voice mode banner — quiet text strip above input ────────────────── */
function VoiceBanner({ voice }) {
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
  return (
    <div style={{ ...base, color: "var(--hg-ice)" }}>
      <IconWaveLive bars={18} height={9} color="var(--hg-ice)" />
      <span>{voice.state}…</span>
      <span style={{ marginLeft: "auto", color: "var(--hg-fg-4)" }}>tap mic to end</span>
    </div>
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

function metricsBaseFromEndpoint(endpoint) {
  // ws://192.168.0.125:8123  →  http://192.168.0.125:8092
  // Default to the same host as HA, swapping :8123 (or any port) for :8092.
  try {
    const u = new URL(endpoint.replace(/^ws/, "http"));
    return `http://${u.hostname}:8092`;
  } catch {
    return "http://192.168.0.100:8092";
  }
}

function HomeApp({ density = "airy", metricsStyle = "ticker", initialEvents, voiceOverride, themeOverride, autoplay = true }) {
  const initialPrefs = useMemo(() => loadPrefs({
    endpoint: "",
    token: "",
    model: "",
    theme: "dark",
    metricsBase: "",
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
  const [availableModels, setAvailableModels] = useState(null);
  const [metrics, setMetrics] = useState(DEFAULT_METRICS);
  const [conversationId, setConversationId] = useState(initialConvId);

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

  /* Persist prefs whenever they change */
  useEffect(() => {
    savePrefs({ endpoint, token, model, theme, metricsBase });
  }, [endpoint, token, model, theme, metricsBase]);

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
        setMetrics((prev) => ({
          ...prev,
          model:   m.model    || prev.model,
          ttft:    m.ttft_ms ?? prev.ttft,
          tps:     m.tps     ?? prev.tps,
          gpu:     m.gpu_util_pct  ?? prev.gpu,
          vram:    m.vram_used_gb  ?? prev.vram,
          vramMax: m.vram_total_gb ?? prev.vramMax,
          cpu:     m.cpu_pct       ?? prev.cpu,
          ram:     m.ram_used_gb   ?? prev.ram,
          ramMax:  m.ram_total_gb  ?? prev.ramMax,
        }));
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
    const [cmd, ...rest] = raw.trim().slice(1).split(/\s+/);
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
      case "help":
        addEvent({ kind: "system", text: "/connect <url> <token> · /token <t> · /model <name> · /metrics <url> · /clear · /demo · /about · /help", tone: "info" });
        return true;
      default:
        addEvent({ kind: "system", text: `unknown command: /${cmd}`, tone: "warn" });
        return true;
    }
  }, [addEvent, connectTo, endpoint, metricsBase, playScript, stopStreaming, token]);

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

  const toggleMic = useCallback(() => {
    if (voice.state === "inactive" || voice.state === "no-mic") {
      startVoiceMode();
    } else {
      stopVoiceMode("inactive");
    }
  }, [voice.state, startVoiceMode, stopVoiceMode]);

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
        const assistantEvent = entry.assistant
          ? [{ id: nextId(), kind: "home", time: fmtTime(), text: entry.assistant }]
          : [];
        return [...prev, ...newUserEvents, ...actionCards, ...assistantEvent];
      });
    };
    es.onerror = (e) => {
      console.warn("[sse] error — will auto-reconnect");
    };
    return () => { try { es.close(); } catch {} };
  }, [connection, endpoint, metricsBase]);

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
      />
      {connection === "online" && (
        <HomeVisionCard haUrl={endpoint} token={token} />
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
          <div style={{ maxWidth: 640, margin: "0 auto" }}>
            <BootBanner metrics={metrics} />
            {groupEventsBySpeaker(events).map((g, i) => (
              <TurnBlock key={i} group={g} density={density}
                onConfirmAction={confirmAction} onCancelAction={cancelAction} />
            ))}
          </div>
        )}
      </div>
      <MetricsStrip metrics={metrics} style={metricsStyle} />
      <VoiceBanner voice={voice} />
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
