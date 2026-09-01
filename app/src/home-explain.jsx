/* eslint-disable */
/**
 * home-explain.jsx — Addendum 27 Milestone 5: "Why did the assistant do this?"
 *
 * A right-side slide-in drawer that opens when the user clicks any
 * kind:"home" / kind:"action" / kind:"external" bubble (gated on
 * debugMode === true for privacy per plan Section 12 Prompt 3).
 *
 * Data sources — REUSES existing routing log only; no new server-side
 * deps:
 *   • GET /api/extended_openai_conversation/routing_log?conv_id=<id>
 *     pulls ALL entries (routing_decision, tool_call,
 *     perception_dispatch, confirmation) sharing one conversation_id.
 *
 * Renders:
 *   • Decision summary — intent, matched rule, dispatched, model,
 *     latency, route, source
 *   • Tool calls — collapsible: name + args + result.ok + latency_ms +
 *     error if any
 *   • Final assistant text — captured from local_reply_snippet or
 *     ext_reply_snippet
 *   • Raw JSONL section — select-all + copy for offline analysis
 *
 * No new state — everything is fetch-on-open + ephemeral.
 *
 * Accessibility — role="dialog", aria-modal, Escape closes, focus
 * trap on close button.
 */

const { useState, useEffect, useRef, useMemo, useCallback } = React;

const EXPLAIN_FONT_MONO = '"Geist Mono", "JetBrains Mono", monospace';
const EXPLAIN_FONT_SANS = '"Geist", "Inter", sans-serif';

// ── Helpers (delegated to home-explain-helpers.js — loaded earlier) ──
//
// fmtExplainField, ageOfExplainTsHelper, partitionExplainEntriesHelper
// are set on `window` by the helpers script. We resolve them AT CALL
// TIME (inside functions) rather than at module load — Babel-standalone
// processes JSX modules asynchronously, so there's a window where the
// helpers haven't loaded yet when the JSX module first evaluates.
// Resolving at call time guarantees we always see the helper.
function fmt(value) {
  if (window.fmtExplainField) return window.fmtExplainField(value);
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") { try { return JSON.stringify(value); } catch { return String(value); } }
  return String(value);
}
function ageOf(ts) {
  if (window.ageOfExplainTsHelper) return window.ageOfExplainTsHelper(ts);
  return "—";
}
function partitionEntries(entries) {
  if (window.partitionExplainEntriesHelper) return window.partitionExplainEntriesHelper(entries);
  return { decision: (entries || [])[0] || null, toolCalls: [], perceptions: [], confirmations: [], other: [] };
}

// ── Section primitives ───────────────────────────────────────────────

function Section({ title, count, defaultOpen = true, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{
      borderBottom: "1px solid var(--hg-border-soft)",
      padding: "14px 24px",
    }}>
      <button
        onClick={() => setOpen(o => !o)}
        className="hg-focusable"
        style={{
          background: "transparent", border: "none", padding: 0,
          color: "var(--hg-fg-2)",
          fontFamily: EXPLAIN_FONT_MONO, fontSize: 10,
          letterSpacing: "0.18em", textTransform: "uppercase",
          cursor: "pointer", display: "flex", alignItems: "center", gap: 6,
        }}
      >
        <span style={{ width: "1ch", display: "inline-block" }}>{open ? "▾" : "▸"}</span>
        <span>{title}</span>
        {count !== undefined && (
          <span style={{ color: "var(--hg-fg-4)", marginLeft: 6 }}>· {count}</span>
        )}
      </button>
      {open && <div style={{ marginTop: 12 }}>{children}</div>}
    </div>
  );
}

function KV({ label, value, mono = true }) {
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "10ch minmax(0, 1fr)",
      gap: 12,
      fontSize: 11.5,
      fontFamily: mono ? EXPLAIN_FONT_MONO : EXPLAIN_FONT_SANS,
      padding: "3px 0",
    }}>
      <span style={{ color: "var(--hg-fg-4)", textTransform: "lowercase", letterSpacing: "0.06em" }}>
        {label}
      </span>
      <span style={{ color: "var(--hg-fg-1)", wordBreak: "break-word" }}>
        {value}
      </span>
    </div>
  );
}

// ── M5+ (Addendum 27): Tool timeline — horizontal bar visualization ──
// of per-tool latencies within a turn. Rendered above the tool-calls
// list when there are 2+ tool calls. Each bar's width is proportional
// to its share of the SUMMED tool latency (not total turn time).
// Single-tool turns skip the timeline — it'd just be a 100%-wide bar
// with nothing to compare against.

function ToolTimelineBar({ toolCalls, hoveredIdx, onHover }) {
  const compute = window.computeToolTimelineHelper;
  if (!compute) return null;
  const bars = compute(toolCalls);
  if (bars.length < 2) return null;
  return (
    <div style={{ margin: "0 0 10px 0", padding: "0 0 4px 0" }}>
      <div style={{
        fontSize: 9, letterSpacing: "0.12em",
        color: "var(--hg-fg-4)", textTransform: "uppercase",
        marginBottom: 4,
      }}>tool latency timeline</div>
      <div style={{
        position: "relative",
        height: 14,
        background: "var(--hg-bg-1)",
        border: "1px solid var(--hg-border-soft)",
        overflow: "hidden",
      }}>
        {bars.map((b, i) => {
          const isHovered = hoveredIdx === i;
          // Color: ok=ice, error=crit, unknown=fg-3
          const color = b.ok === false ? "var(--hg-crit)"
                      : b.ok === true ? "var(--hg-ice)"
                      : "var(--hg-fg-3)";
          return (
            <div
              key={i}
              onMouseEnter={() => onHover && onHover(i)}
              onMouseLeave={() => onHover && onHover(null)}
              title={`${b.name}: ${Math.round(b.latency_ms)}ms (${b.widthPct.toFixed(0)}%)`}
              style={{
                position: "absolute",
                top: 0, bottom: 0,
                left: `${b.offsetPct}%`,
                width: `${b.widthPct}%`,
                background: color,
                opacity: hoveredIdx == null ? 0.65 : (isHovered ? 1 : 0.25),
                transition: "opacity 120ms",
                borderRight: i < bars.length - 1 ? "1px solid var(--hg-bg-1)" : "none",
                cursor: "default",
              }}
            />
          );
        })}
      </div>
      <div style={{
        display: "flex", justifyContent: "space-between",
        marginTop: 3, fontSize: 8.5,
        color: "var(--hg-fg-4)", fontFamily: EXPLAIN_FONT_MONO,
        letterSpacing: "0.06em",
      }}>
        <span>0ms</span>
        <span>{bars.reduce((a, b) => a + b.latency_ms, 0).toFixed(0)}ms total</span>
      </div>
    </div>
  );
}

// ── Tool calls section (owns the cross-highlight hover state) ───────

function ToolCallsSection({ toolCalls }) {
  const [hoveredIdx, setHoveredIdx] = useState(null);
  return (
    <Section title="tool calls" count={toolCalls.length}>
      <ToolTimelineBar
        toolCalls={toolCalls}
        hoveredIdx={hoveredIdx}
        onHover={setHoveredIdx}
      />
      {toolCalls.map((e, i) => (
        <ToolCallRow
          key={i}
          entry={e}
          highlighted={hoveredIdx === i}
          onHover={(h) => setHoveredIdx(h ? i : null)}
        />
      ))}
    </Section>
  );
}

// ── Tool call row (collapsible per row) ──────────────────────────────

function ToolCallRow({ entry, highlighted, onHover }) {
  const [open, setOpen] = useState(false);
  const name = entry.tool || entry.name || "(unknown)";
  const result = entry.result || {};
  const ok = result.ok === undefined ? null : result.ok;
  const latency = entry.latency_ms ?? result.latency_ms;
  const errMsg = result.error_message || result.error || null;
  const dot = ok === false ? "var(--hg-crit)"
            : ok === true ? "var(--hg-ice)"
            : "var(--hg-fg-4)";
  return (
    <div
      onMouseEnter={() => onHover && onHover(true)}
      onMouseLeave={() => onHover && onHover(false)}
      style={{
        borderTop: "1px solid var(--hg-border-soft)",
        padding: "8px 0",
        // M5+: cross-highlight from the timeline bar. Subtle bg shift
        // so the row visually links to the hovered timeline segment.
        background: highlighted ? "color-mix(in oklab, var(--hg-ice) 8%, transparent)" : "transparent",
        transition: "background 120ms",
      }}>
      <button
        onClick={() => setOpen(o => !o)}
        className="hg-focusable"
        style={{
          background: "transparent", border: "none", padding: 0,
          width: "100%", textAlign: "left", cursor: "pointer",
          display: "grid", gridTemplateColumns: "8px 1fr auto auto", gap: 10,
          alignItems: "center", color: "var(--hg-fg-1)",
          fontFamily: EXPLAIN_FONT_MONO, fontSize: 11.5,
        }}
      >
        <span style={{ width: 6, height: 6, background: dot, borderRadius: 6 }} />
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {name}
        </span>
        <span style={{ color: "var(--hg-fg-4)", fontSize: 10 }}>
          {latency != null ? `${Math.round(latency)}ms` : ""}
        </span>
        <span style={{ width: "1ch", color: "var(--hg-fg-4)" }}>{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div style={{ marginTop: 8, paddingLeft: 18, fontSize: 11, color: "var(--hg-fg-2)" }}>
          {errMsg && (
            <div style={{ color: "var(--hg-crit)", marginBottom: 6 }}>
              error: {errMsg}
            </div>
          )}
          <div style={{ color: "var(--hg-fg-4)", marginBottom: 4 }}>args</div>
          <pre style={{
            margin: 0, padding: "6px 8px",
            background: "var(--hg-bg-1)",
            border: "1px solid var(--hg-border-soft)",
            fontFamily: EXPLAIN_FONT_MONO, fontSize: 10.5,
            color: "var(--hg-fg-1)", whiteSpace: "pre-wrap",
            wordBreak: "break-word", overflowX: "auto",
          }}>{fmt(entry.args || entry.arguments || {})}</pre>
          {result && Object.keys(result).length > 0 && (
            <>
              <div style={{ color: "var(--hg-fg-4)", margin: "8px 0 4px" }}>result</div>
              <pre style={{
                margin: 0, padding: "6px 8px",
                background: "var(--hg-bg-1)",
                border: "1px solid var(--hg-border-soft)",
                fontFamily: EXPLAIN_FONT_MONO, fontSize: 10.5,
                color: "var(--hg-fg-1)", whiteSpace: "pre-wrap",
                wordBreak: "break-word", overflowX: "auto",
              }}>{fmt(result)}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main drawer ──────────────────────────────────────────────────────

function HomeExplainDrawer({ open, onClose, convId, endpoint, token, sim }) {
  const [entries, setEntries] = useState(null);   // null = loading, [] = fetched/empty, [..] = ok
  const [error, setError] = useState(null);
  // Attempt number while fetchWithRetry is still reconnecting (null = idle).
  const [reconnecting, setReconnecting] = useState(null);
  // Bumped by the "retry now" button to re-run the fetch effect after retries
  // are exhausted (the fetch lives in an effect, not a standalone callback).
  const [reloadNonce, setReloadNonce] = useState(0);
  const [openedAt, setOpenedAt] = useState(null);
  const [fetchMs, setFetchMs] = useState(null);
  const containerRef = useRef(null);
  const fetchAbortRef = useRef(null);

  // Sim-mode fixture: if sim is active AND a convId is passed, render a
  // canned 4-entry fixture so the drawer is testable without HA. The
  // sim layer attaches a global window.__SIM_EXPLAIN_FIXTURE() function
  // (see simulation-data.jsx). Falls back to live fetch if missing.
  const simFixture = useCallback(() => {
    if (!sim?.active || typeof window.__SIM_EXPLAIN_FIXTURE !== "function") return null;
    try { return window.__SIM_EXPLAIN_FIXTURE(convId); } catch { return null; }
  }, [sim?.active, convId]);

  // Fetch when drawer opens with a convId
  useEffect(() => {
    if (!open || !convId) return undefined;
    setOpenedAt(Date.now());
    setEntries(null);
    setError(null);
    setReconnecting(null);
    setFetchMs(null);

    const fixture = simFixture();
    if (fixture) {
      // Sim path: immediate render, no network
      setEntries(Array.isArray(fixture) ? fixture : (fixture.entries || []));
      setFetchMs(0);
      return undefined;
    }

    if (!endpoint || !token) {
      setError("not connected — /connect <url> <token>");
      setEntries([]);
      return undefined;
    }

    const controller = new AbortController();
    fetchAbortRef.current = controller;
    const t0 = Date.now();
    (async () => {
      try {
        const base = endpoint.replace(/\/+$/, "");
        const url = `${base}/api/extended_openai_conversation/routing_log?conv_id=${encodeURIComponent(convId)}`;
        // Retry through the AI-box reboot window with a reconnecting banner;
        // fetchWithRetry only throws once retries are spent or the status is
        // non-retryable.
        const r = await window.fetchWithRetry({
          url,
          options: {
            headers: { Authorization: `Bearer ${token}` },
            cache: "no-store",
            signal: controller.signal,
          },
          onAttempt: ({ attempt, nextDelay }) => {
            if (nextDelay != null) setReconnecting(attempt);
          },
        });
        setReconnecting(null);
        if (!r.ok) {
          setError(`HTTP ${r.status}`);
          setEntries([]);
          setFetchMs(Date.now() - t0);
          return;
        }
        const data = await r.json();
        const arr = Array.isArray(data?.entries) ? data.entries : [];
        setEntries(arr);
        setFetchMs(Date.now() - t0);
      } catch (e) {
        // Our own controller only aborts on unmount/dep-change cleanup.
        if (controller.signal.aborted) return;
        setReconnecting(null);
        const status = e && e.lastStatus;
        if (e && e.reason === "http" && status) setError(`HTTP ${status}`);
        else if (e && e.attempts) setError(`routing log unreachable after ${e.attempts} ${e.attempts === 1 ? "attempt" : "attempts"}`);
        else setError(e?.message || String(e));
        setEntries([]);
        setFetchMs(Date.now() - t0);
      }
    })();

    return () => {
      try { controller.abort(); } catch {}
    };
  }, [open, convId, endpoint, token, simFixture, reloadNonce]);

  // Overlay layer: topmost-only Escape + focus-in/restore (HomeOverlay owns
  // the keydown dispatch — no per-drawer window listener).
  window.HomeOverlay.useOverlayLayer({
    key: "explain",
    active: !!open,
    onEscape: () => { onClose && onClose(); },
    rootRef: containerRef,
  });

  const partitioned = useMemo(
    () => partitionEntries(entries || []),
    [entries]
  );

  if (!open) return null;

  const { decision, toolCalls, perceptions, confirmations, other } = partitioned;

  const decisionRow = decision || {};
  const finalText = decisionRow.local_reply_snippet
    || decisionRow.ext_reply_snippet
    || decisionRow.text
    || "";

  return (
    <div
      ref={containerRef}
      role="dialog"
      aria-modal="false"
      aria-label="Why did the assistant do this?"
      style={{
        position: "fixed",
        top: 0, right: 0, bottom: 0,
        width: "min(520px, 100vw)",
        background: "var(--hg-bg-0)",
        borderLeft: "1px solid var(--hg-border)",
        boxShadow: "-12px 0 32px rgba(0,0,0,0.3)",
        zIndex: 1100,
        display: "flex", flexDirection: "column",
        fontFamily: EXPLAIN_FONT_MONO,
        animation: "explain-slide-in 220ms cubic-bezier(0.16,1,0.3,1)",
      }}
    >
      <style>{`
        @keyframes explain-slide-in {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>

      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "14px 24px 12px",
        borderBottom: "1px solid var(--hg-border-soft)",
      }}>
        <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.05 }}>
          <span style={{
            fontFamily: EXPLAIN_FONT_SANS, fontSize: 15, fontWeight: 500,
            color: "var(--hg-fg-0)", letterSpacing: "-0.02em",
          }}>why</span>
          <span style={{
            fontSize: 8.5, letterSpacing: "0.24em", fontWeight: 500,
            color: "var(--hg-fg-4)", marginTop: 3,
          }}>routing · tools · final</span>
        </div>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 6, alignItems: "center" }}>
          {fetchMs != null && (
            <span style={{ color: "var(--hg-fg-4)", fontSize: 10, marginRight: 4 }}>
              {fetchMs}ms
            </span>
          )}
          <button
            onClick={onClose}
            aria-label="Close"
            className="hg-focusable"
            style={{
              background: "transparent", border: "1px solid var(--hg-border-soft)",
              color: "var(--hg-fg-2)", padding: "4px 11px",
              fontFamily: EXPLAIN_FONT_MONO, fontSize: 10.5, letterSpacing: "0.12em",
              cursor: "pointer", textTransform: "lowercase",
            }}
          >close · esc</button>
        </span>
      </div>

      {/* Body — scrollable. `hg-scroll` class applies the app's themed thin
          (6px) scrollbar from home-tokens.css. */}
      <div className="hg-scroll" style={{ flex: 1, overflowY: "auto", overflowX: "hidden" }}>
        {/* Conv id header */}
        <div style={{ padding: "12px 24px", borderBottom: "1px solid var(--hg-border-soft)" }}>
          <KV label="conv_id" value={convId || "—"} />
          {decision && <KV label="when" value={ageOf(decision.ts)} />}
        </div>

        {/* Loading state */}
        {entries === null && reconnecting == null && (
          <div style={{ padding: "40px 24px", textAlign: "center", color: "var(--hg-fg-3)", fontSize: 11 }}>
            loading routing log…
          </div>
        )}

        {/* Reconnecting (retrying through a box-reboot window) */}
        {reconnecting != null && !error && (
          <div style={{ padding: "40px 24px", textAlign: "center", color: "var(--hg-ice)", fontSize: 11 }}>
            reconnecting to routing log… waiting for the network (attempt {reconnecting}).
          </div>
        )}

        {/* Error state */}
        {error && entries !== null && (
          <div style={{
            padding: "20px 24px", color: "var(--hg-crit)", fontSize: 11,
            display: "flex", alignItems: "center", gap: 12,
          }}>
            <span style={{ flex: 1 }}>{error}</span>
            <button
              onClick={() => setReloadNonce((n) => n + 1)}
              className="hg-focusable"
              style={{
                background: "transparent", border: "1px solid var(--hg-crit)",
                color: "var(--hg-crit)", padding: "4px 11px",
                fontFamily: EXPLAIN_FONT_MONO, fontSize: 10, letterSpacing: "0.12em",
                cursor: "pointer", textTransform: "lowercase", whiteSpace: "nowrap",
              }}
            >retry now</button>
          </div>
        )}

        {/* Empty state */}
        {entries !== null && entries.length === 0 && !error && (
          <div style={{ padding: "40px 24px", textAlign: "center", color: "var(--hg-fg-3)", fontSize: 11 }}>
            no routing log entries for this conv_id
          </div>
        )}

        {/* Decision summary */}
        {decision && (
          <Section title="decision">
            <KV label="intent" value={fmt(decision.intent)} />
            <KV label="matched" value={fmt(decision.matched)} />
            <KV label="dispatched" value={fmt(decision.dispatched)} />
            <KV label="src" value={fmt(decision.src)} />
            <KV label="text" value={fmt(decision.text)} />
            {decision.ext_latency_ms != null && (
              <KV label="ext_lat" value={`${decision.ext_latency_ms}ms`} />
            )}
            {decision.ext_status != null && (
              <KV label="ext_status" value={fmt(decision.ext_status)} />
            )}
          </Section>
        )}

        {/* Tool calls — wrapped in a section that owns the hover-link
            state so the timeline bar + ToolCallRow rows highlight in
            sync. */}
        {toolCalls.length > 0 && (
          <ToolCallsSection toolCalls={toolCalls} />
        )}

        {/* Perception dispatches */}
        {perceptions.length > 0 && (
          <Section title="perception" count={perceptions.length} defaultOpen={false}>
            {perceptions.map((e, i) => (
              <div key={i} style={{
                borderTop: i > 0 ? "1px solid var(--hg-border-soft)" : "none",
                padding: "6px 0",
                fontSize: 11, color: "var(--hg-fg-2)",
              }}>
                <KV label="room" value={fmt(e.room)} />
                <KV label="verdict" value={fmt(e.verdict)} />
                {e.latency_ms != null && <KV label="latency" value={`${e.latency_ms}ms`} />}
                {e.source && <KV label="source" value={fmt(e.source)} />}
                {e.caption_chars != null && <KV label="chars" value={fmt(e.caption_chars)} />}
              </div>
            ))}
          </Section>
        )}

        {/* Confirmations */}
        {confirmations.length > 0 && (
          <Section title="confirmation" count={confirmations.length} defaultOpen={false}>
            {confirmations.map((e, i) => (
              <pre key={i} style={{
                margin: 0, padding: "6px 8px",
                fontFamily: EXPLAIN_FONT_MONO, fontSize: 10.5,
                color: "var(--hg-fg-2)", whiteSpace: "pre-wrap",
              }}>{fmt(e)}</pre>
            ))}
          </Section>
        )}

        {/* Final assistant text */}
        {finalText && (
          <Section title="final text">
            <div style={{
              fontFamily: EXPLAIN_FONT_SANS, fontSize: 13, lineHeight: 1.5,
              color: "var(--hg-fg-0)", whiteSpace: "pre-wrap", padding: "4px 0",
            }}>
              {finalText}
            </div>
          </Section>
        )}

        {/* Raw JSONL — paste-back surface */}
        {entries !== null && entries.length > 0 && (
          <Section title="raw jsonl" defaultOpen={false}>
            <pre style={{
              margin: 0, padding: "8px",
              background: "var(--hg-bg-1)",
              border: "1px solid var(--hg-border-soft)",
              fontFamily: EXPLAIN_FONT_MONO, fontSize: 10,
              color: "var(--hg-fg-2)", whiteSpace: "pre-wrap",
              wordBreak: "break-word", overflowX: "auto",
              maxHeight: 240, overflowY: "auto",
            }}>{entries.map((e) => JSON.stringify(e)).join("\n")}</pre>
          </Section>
        )}

        {/* Other entries — fallback display */}
        {other.length > 0 && (
          <Section title="other" count={other.length} defaultOpen={false}>
            {other.map((e, i) => (
              <pre key={i} style={{
                margin: 0, padding: "6px 8px",
                fontFamily: EXPLAIN_FONT_MONO, fontSize: 10.5,
                color: "var(--hg-fg-2)", whiteSpace: "pre-wrap",
              }}>{fmt(e)}</pre>
            ))}
          </Section>
        )}
      </div>
    </div>
  );
}

window.HomeExplainDrawer = HomeExplainDrawer;
