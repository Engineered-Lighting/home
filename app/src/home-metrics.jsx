/* Home — metrics tray v2 primitives.
 *
 * Six composable components that form the entire visual vocabulary of
 * the metrics drawer. Brutal design pass: kill all the rainbow colors,
 * giant numerals, mixed chart types. Replace with one bar + one timeline
 * + one status pill. Everything resolves through `--hg-*` design tokens
 * so light/dark themes adapt cleanly.
 *
 * Used by MetricsStrip in home-app.jsx.
 *
 * Loaded via index.html as a babel-standalone <script type="text/babel">.
 * No imports — accesses globals exposed by earlier scripts (React, etc).
 */

/* Design tokens applied via CSS custom props.
 * No inline hex / hsl ever. Anywhere you'd want a color, use a var.
 */
const HM_FONT_MONO = "'Geist Mono', ui-monospace, monospace";
const HM_FONT_SANS = "'Geist', system-ui, sans-serif";

/* ── HealthDot ─────────────────────────────────────────────────────────
 *
 * 6px round dot. Indicates section/machine health at a glance.
 * Tones map to a single semantic axis:
 *   ok   → ice    (accent — alive, all green)
 *   warn → warn   (amber — at least one metric above threshold)
 *   crit → crit   (red — genuine offline / error)
 *   idle → fg-5   (cold gray — disabled / unknown)
 */
function HealthDot({ tone = "ok", size = 6 }) {
  const color = ({
    ok:   "var(--hg-ice-bright)",
    warn: "var(--hg-warn)",
    crit: "var(--hg-crit)",
    idle: "var(--hg-fg-5)",
  })[tone] || "var(--hg-fg-5)";
  return (
    <span style={{
      display: "inline-block",
      width: size, height: size, borderRadius: 999,
      background: color,
      flexShrink: 0,
    }} />
  );
}

/* ── Section ───────────────────────────────────────────────────────────
 *
 * Container for a logical metric group (Pipeline / AI Box / Home / Network).
 * Single hairline divider at the top — sections stack without gaps.
 * Header: dot · TITLE · subtitle ········ status (right-aligned)
 * Padding: consistent 16px throughout.
 */
function Section({ title, subtitle, status, tone = "ok", children, divider = true }) {
  return (
    <section style={{
      padding: "14px 16px 16px",
      borderTop: divider ? "1px solid var(--hg-border-soft)" : "none",
    }}>
      <header style={{
        display: "flex", alignItems: "center", gap: 8,
        marginBottom: 10,
        fontFamily: HM_FONT_MONO,
        fontSize: 9.5,
        letterSpacing: "0.20em",
        textTransform: "uppercase",
        color: "var(--hg-fg-5)",
      }}>
        <HealthDot tone={tone} />
        <span style={{ color: "var(--hg-fg-2)" }}>{title}</span>
        {subtitle && (
          <span style={{
            color: "var(--hg-fg-5)",
            letterSpacing: "0.16em",
          }}>· {subtitle}</span>
        )}
        {status != null && (
          <span style={{
            marginLeft: "auto",
            color: "var(--hg-fg-4)",
            letterSpacing: "0.12em",
            textTransform: "none",
            fontSize: 10,
          }}>{status}</span>
        )}
      </header>
      {children}
    </section>
  );
}

/* ── MeterRow ──────────────────────────────────────────────────────────
 *
 * The single bar component for percentage / capacity / counter metrics.
 *   label · ▰▰▰▰▱▱▱▱▱▱▱▱▱▱▱▱ value unit
 *
 * The bar fill:
 *   - default: --hg-fg-1 (foreground, neutral active)
 *   - amber if pct > warnAt (default 70)
 *   - crit  if pct > critAt (default 90)
 *
 * No gradients. No giant numbers. Numeric value sits at right, mono-tabular.
 * If value is null/undefined → row shows "—" instead of bar+number.
 */
function MeterRow({
  label,
  value,
  max = 100,
  unit = "%",
  warnAt = 70,
  critAt = 90,
  hideBar = false,             // for ttft/tok/s style scalar values
  // For label-only or counter rows (e.g. "1d 14h"), pass `text` instead of value
  text,
  // Optional indent (for nested rows like "frigate fps · living_room")
  indent = 0,
}) {
  const hasValue = value != null && isFinite(value);
  const pct = hasValue ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  let fill = "var(--hg-fg-1)";
  if (pct > critAt) fill = "var(--hg-crit)";
  else if (pct > warnAt) fill = "var(--hg-warn)";

  const numFmt = hasValue ? (
    Number.isInteger(value) || value === 0 ? value.toString()
      : value < 10 ? value.toFixed(1) : Math.round(value).toString()
  ) : null;

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: hideBar
        ? "minmax(100px, 160px) 1fr auto"
        : "minmax(100px, 160px) 1fr auto auto",
      alignItems: "center",
      columnGap: 12,
      minHeight: 22,
      paddingLeft: indent,
    }}>
      <span style={{
        fontFamily: HM_FONT_MONO,
        fontSize: 10,
        color: "var(--hg-fg-3)",
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
      }}>{label}</span>
      {!hideBar ? (
        <div style={{
          height: 4,
          background: "var(--hg-border-soft)",
          position: "relative",
          overflow: "hidden",
        }}>
          {hasValue && (
            <div style={{
              position: "absolute", inset: 0,
              width: `${pct}%`,
              background: fill,
              transition: "width 500ms cubic-bezier(.4,0,.2,1), background 300ms",
            }} />
          )}
        </div>
      ) : <span />}
      <span style={{
        fontFamily: HM_FONT_MONO,
        fontSize: 12,
        fontFeatureSettings: '"tnum"',
        color: hasValue ? "var(--hg-fg-1)" : "var(--hg-fg-4)",
        fontWeight: 500,
        textAlign: "right",
        minWidth: 32,
      }}>{text != null ? text : (hasValue ? numFmt : "—")}</span>
      {!hideBar && (
        <span style={{
          fontFamily: HM_FONT_MONO,
          fontSize: 10,
          color: "var(--hg-fg-4)",
          minWidth: 18,
        }}>{unit}</span>
      )}
    </div>
  );
}

/* ── TimelineRow ───────────────────────────────────────────────────────
 *
 * Same visual grammar as MeterRow but the bar is POSITIONED within a
 * total-duration scale. Used for the voice-turn waterfall.
 *   label · ▱▱▰▰▰▱▱▱▱▱▱  durationMs
 *
 * Single fill color (no rainbow per stage). Inactive stages render as
 * a dim "—" with no bar.
 */
function TimelineRow({ label, startMs = 0, durMs = 0, totalMs = 1, indent = 0 }) {
  const active = durMs > 0;
  const leftPct = totalMs > 0 ? Math.max(0, Math.min(100, (startMs / totalMs) * 100)) : 0;
  const widthPct = totalMs > 0 ? Math.max(0.5, Math.min(100, (durMs / totalMs) * 100)) : 0;
  const fmtMs = active ? `${Math.round(durMs)} ms` : "—";
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "minmax(100px, 160px) 1fr auto",
      alignItems: "center",
      columnGap: 12,
      minHeight: 22,
      paddingLeft: indent,
    }}>
      <span style={{
        fontFamily: HM_FONT_MONO,
        fontSize: 10,
        color: "var(--hg-fg-3)",
      }}>{label}</span>
      <div style={{
        height: 4,
        background: "var(--hg-border-soft)",
        position: "relative",
        overflow: "hidden",
      }}>
        {active && (
          <div style={{
            position: "absolute", top: 0, bottom: 0,
            left: `${leftPct}%`,
            width: `${widthPct}%`,
            background: "var(--hg-fg-1)",
            transition: "all 400ms cubic-bezier(.4,0,.2,1)",
          }} />
        )}
      </div>
      <span style={{
        fontFamily: HM_FONT_MONO,
        fontSize: 12,
        fontFeatureSettings: '"tnum"',
        color: active ? "var(--hg-fg-1)" : "var(--hg-fg-4)",
        textAlign: "right",
        minWidth: 56,
      }}>{fmtMs}</span>
    </div>
  );
}

/* ── StatusLine ────────────────────────────────────────────────────────
 *
 * Inline chip rail for "uptime 1m · ha ✓ · rooms 6 · media 6 · tts chatterbox".
 * Each chip: { label, value, tone? }. Tone (default neutral, optional warn/crit).
 *
 * Compact, single-line on wide screens; auto-wraps on narrow.
 */
function StatusLine({ items = [] }) {
  return (
    <div style={{
      display: "flex",
      flexWrap: "wrap",
      gap: "4px 14px",
      fontFamily: HM_FONT_MONO,
      fontSize: 10,
      color: "var(--hg-fg-3)",
      lineHeight: 1.5,
    }}>
      {items.map((it, i) => {
        if (!it) return null;
        const toneColor = ({
          warn: "var(--hg-warn)",
          crit: "var(--hg-crit)",
          ok:   "var(--hg-fg-1)",
        })[it.tone] || "var(--hg-fg-1)";
        return (
          <span key={i} style={{
            display: "inline-flex", alignItems: "baseline", gap: 4,
          }}>
            <span style={{ color: "var(--hg-fg-4)" }}>{it.label}</span>
            <span style={{ color: toneColor, fontFeatureSettings: '"tnum"' }}>{it.value}</span>
          </span>
        );
      })}
    </div>
  );
}

/* ── EmptyState ────────────────────────────────────────────────────────
 *
 * Single-line muted placeholder for "section has no data yet".
 * Optionally a tiny action link on the right (opens a tooltip with steps).
 */
function EmptyState({ message, action, onActionClick }) {
  const [showTooltip, setShowTooltip] = React.useState(false);
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      fontFamily: HM_FONT_MONO,
      fontSize: 10,
      color: "var(--hg-fg-4)",
      minHeight: 22,
      gap: 12,
      position: "relative",
    }}>
      <span style={{ fontStyle: "normal" }}>{message}</span>
      {action && (
        <span
          onClick={() => { setShowTooltip((v) => !v); onActionClick?.(); }}
          style={{
            color: "var(--hg-fg-2)",
            cursor: onActionClick || action.tooltip ? "pointer" : "default",
            borderBottom: "1px dotted var(--hg-fg-4)",
            paddingBottom: 1,
            whiteSpace: "nowrap",
          }}
        >{action.label} →</span>
      )}
      {showTooltip && action?.tooltip && (
        <div style={{
          position: "absolute", right: 0, top: "100%",
          marginTop: 6, padding: "8px 10px",
          background: "var(--hg-bg-2)",
          border: "1px solid var(--hg-border)",
          fontFamily: HM_FONT_MONO,
          fontSize: 10,
          color: "var(--hg-fg-2)",
          lineHeight: 1.5,
          whiteSpace: "pre-wrap",
          maxWidth: 320,
          zIndex: 10,
          boxShadow: "0 8px 24px rgba(0,0,0,0.3)",
        }}>{action.tooltip}</div>
      )}
    </div>
  );
}

/* ── MeterGroup ────────────────────────────────────────────────────────
 *
 * Helper: a subgroup label + a stack of MeterRows. Used inside Network
 * section for grouping by device.
 */
function MeterGroup({ label, children }) {
  return (
    <div>
      {label && (
        <div style={{
          fontFamily: HM_FONT_MONO,
          fontSize: 9.5,
          letterSpacing: "0.18em",
          textTransform: "uppercase",
          color: "var(--hg-fg-4)",
          marginBottom: 6,
          marginTop: 8,
        }}>{label}</div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {children}
      </div>
    </div>
  );
}

/* ── Sparkline ─────────────────────────────────────────────────────────
 *
 * Tiny inline trend chart. SVG, no labels, single-color stroke.
 * Default 60px × 12px. Caller passes an array of values; the chart
 * normalizes against min/max within the window. If `max` is supplied
 * (e.g. 100 for a percentage) the scale is fixed, which makes vertical
 * movement comparable across metrics.
 *
 * Threshold tone: if the LATEST value > threshold (pct of max), stroke
 * shifts to amber/crit. Same semantic as MeterRow.
 */
function Sparkline({ data = [], width = 60, height = 12, max, warnAt = 70, critAt = 90 }) {
  if (!Array.isArray(data) || data.length < 2) {
    return <svg width={width} height={height} aria-hidden style={{ display: "block" }} />;
  }
  const numeric = data
    .map((v) => (typeof v === "number" && isFinite(v)) ? v : null)
    .filter((v) => v !== null);
  if (numeric.length < 2) {
    return <svg width={width} height={height} aria-hidden style={{ display: "block" }} />;
  }
  const lo = 0;
  const hi = max != null ? max : Math.max(...numeric, 1);
  const range = Math.max(1e-3, hi - lo);
  const last = numeric[numeric.length - 1];
  const lastPct = max != null ? (last / max) * 100 : 50;
  let stroke = "var(--hg-fg-2)";
  if (lastPct > critAt) stroke = "var(--hg-crit)";
  else if (lastPct > warnAt) stroke = "var(--hg-warn)";
  const n = numeric.length;
  const stepX = width / (n - 1);
  const pts = numeric.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - lo) / range) * (height - 2) - 1;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}
      aria-hidden style={{ display: "block", overflow: "visible" }}>
      <polyline
        fill="none"
        stroke={stroke}
        strokeWidth="1"
        strokeLinejoin="round"
        strokeLinecap="round"
        points={pts}
        style={{ transition: "stroke 300ms" }}
      />
    </svg>
  );
}

/* ── MetricCard ────────────────────────────────────────────────────────
 *
 * Hero metric: label + big value + unit + inline sparkline. Used in
 * the AI / Network tabs.
 *   LABEL
 *   12 %
 *   ▁▂▃▅▆▄▃▂
 */
function MetricCard({ label, value, unit, history = [], max = 100, warnAt = 70, critAt = 90 }) {
  const hasValue = value != null && isFinite(value);
  const pct = hasValue && max ? (value / max) * 100 : 0;
  const valueColor = pct > critAt ? "var(--hg-crit)"
                    : pct > warnAt ? "var(--hg-warn)"
                    : "var(--hg-fg-0)";
  return (
    <div style={{
      display: "flex", flexDirection: "column", gap: 5,
      minWidth: 0,
    }}>
      <div style={{
        fontFamily: HM_FONT_MONO,
        fontSize: 9.5,
        letterSpacing: "0.18em",
        textTransform: "uppercase",
        color: "var(--hg-fg-4)",
      }}>{label}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 4 }}>
        <span style={{
          fontFamily: HM_FONT_MONO,
          fontFeatureSettings: '"tnum"',
          fontSize: 18, fontWeight: 500,
          color: hasValue ? valueColor : "var(--hg-fg-4)",
          lineHeight: 1,
        }}>{hasValue ? (Number.isInteger(value) ? value : value.toFixed(value < 10 ? 1 : 0)) : "—"}</span>
        {unit && (
          <span style={{
            fontFamily: HM_FONT_MONO,
            fontSize: 10,
            color: "var(--hg-fg-4)",
          }}>{unit}</span>
        )}
      </div>
      <Sparkline data={history} max={max === 100 ? 100 : undefined} width={80} height={14}
                 warnAt={warnAt} critAt={critAt} />
    </div>
  );
}

/* ── Tabs ──────────────────────────────────────────────────────────────
 *
 * Segmented control. Lowercase mono, letter-spaced. Active tab has
 * 1px underline in fg-0; others sit at fg-4. Optional `extra` slot on
 * the right (for ⚙ diagnostics icon).
 */
function Tabs({ value, onChange, tabs = [], extra = null }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 0,
      borderBottom: "1px solid var(--hg-border-soft)",
      padding: "0 8px",
      minHeight: 32,
    }}>
      {tabs.map((t) => {
        const active = t.id === value;
        return (
          <button
            key={t.id}
            onClick={() => onChange?.(t.id)}
            className="hg-focusable"
            style={{
              background: "transparent",
              border: "none",
              padding: "8px 10px",
              cursor: "pointer",
              fontFamily: HM_FONT_MONO,
              fontSize: 10.5,
              letterSpacing: "0.14em",
              textTransform: "lowercase",
              color: active ? "var(--hg-fg-0)" : "var(--hg-fg-4)",
              borderBottom: active ? "1px solid var(--hg-fg-0)" : "1px solid transparent",
              marginBottom: -1,
              transition: "color 200ms, border-color 200ms",
            }}
          >{t.label}{t.warn ? <HealthDot tone="warn" size={4} /> : null}</button>
        );
      })}
      {extra && (
        <div style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center" }}>
          {extra}
        </div>
      )}
    </div>
  );
}

/* ── TrayBody ──────────────────────────────────────────────────────────
 *
 * Scrollable container for tab content. Single source of vertical
 * constraint for the entire expanded tray. Without this the body
 * would push past the viewport and chat input becomes unreachable
 * (the v2 bug the user reported).
 */
function TrayBody({ maxHeight = 280, children }) {
  return (
    <div className="hg-scroll" style={{
      maxHeight,
      overflowY: "auto",
      overflowX: "hidden",
      padding: "14px 16px 16px",
    }}>
      {children}
    </div>
  );
}

/* ── RoomRow ───────────────────────────────────────────────────────────
 *
 * Single per-room occupancy line for the HOME tab.
 *   living_room    👤 marcelo  ·  22s
 */
function RoomRow({ room, occupant, ageS, media }) {
  const ageStr = ageS == null ? "" : ageS < 60 ? `${Math.round(ageS)}s`
    : ageS < 3600 ? `${Math.round(ageS / 60)}m`
    : `${Math.round(ageS / 3600)}h`;
  const occupied = !!occupant;
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "minmax(110px, 160px) 1fr auto",
      columnGap: 12,
      alignItems: "center",
      minHeight: 22,
      fontFamily: HM_FONT_MONO,
      fontSize: 10.5,
    }}>
      <span style={{ color: "var(--hg-fg-2)" }}>{room.replace(/_/g, " ")}</span>
      <span style={{
        color: occupied ? "var(--hg-fg-1)" : "var(--hg-fg-4)",
        display: "inline-flex", alignItems: "baseline", gap: 6,
      }}>
        <span>{occupied ? "•" : "—"}</span>
        <span>{occupied ? occupant : "empty"}</span>
        {media && (
          <span style={{ color: "var(--hg-fg-4)", marginLeft: 8 }}>
            · {media}
          </span>
        )}
      </span>
      <span style={{
        color: "var(--hg-fg-4)",
        fontVariantNumeric: "tabular-nums",
      }}>{ageStr}</span>
    </div>
  );
}

/* ── DiagModal ─────────────────────────────────────────────────────────
 *
 * Right-side drawer with raw diagnostic data. Opened by the ⚙ icon
 * in the tab bar. Click backdrop or ESC to close.
 */
function DiagModal({ open, onClose, bridgeHealth, visionHealth, traceSummary, lastTrace, networkMetrics }) {
  React.useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <div onClick={onClose} style={{
        position: "fixed", inset: 0,
        background: "rgba(0,0,0,0.5)",
        zIndex: 80,
        animation: "hg-fade-up 200ms ease-out",
      }} />
      <div className="hg-scroll" style={{
        position: "fixed", top: 0, right: 0, bottom: 0,
        width: 360, maxWidth: "100vw",
        background: "var(--hg-bg-1)",
        borderLeft: "1px solid var(--hg-border)",
        zIndex: 81,
        overflowY: "auto",
        padding: "16px 18px",
        animation: "hg-fade-up 240ms cubic-bezier(.4,0,.2,1)",
        fontFamily: HM_FONT_MONO,
        fontSize: 10,
        color: "var(--hg-fg-2)",
        lineHeight: 1.6,
      }}>
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          marginBottom: 16,
        }}>
          <span style={{
            color: "var(--hg-fg-1)",
            letterSpacing: "0.22em", textTransform: "uppercase",
            fontSize: 10,
          }}>diagnostics</span>
          <button onClick={onClose} className="hg-focusable" style={{
            background: "transparent", border: "none",
            color: "var(--hg-fg-3)", fontSize: 14, cursor: "pointer",
            padding: 4,
          }}>×</button>
        </div>
        {bridgeHealth && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ color: "var(--hg-fg-5)", letterSpacing: "0.16em", textTransform: "uppercase", fontSize: 9, marginBottom: 4 }}>bridge feature flags</div>
            <div style={{ color: "var(--hg-fg-2)" }}>
              direct_dispatch · <span style={{ color: bridgeHealth.direct_dispatch ? "var(--hg-warn)" : "var(--hg-fg-3)" }}>{String(!!bridgeHealth.direct_dispatch)}</span>{"  "}
              voice_rewriter · <span style={{ color: bridgeHealth.voice_rewriter ? "var(--hg-warn)" : "var(--hg-fg-3)" }}>{String(!!bridgeHealth.voice_rewriter)}</span>{"  "}
              dry_run · <span style={{ color: bridgeHealth.dry_run ? "var(--hg-warn)" : "var(--hg-fg-3)" }}>{String(!!bridgeHealth.dry_run)}</span>
            </div>
          </div>
        )}
        {bridgeHealth?.stale_media_integrations?.length > 0 && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ color: "var(--hg-fg-5)", letterSpacing: "0.16em", textTransform: "uppercase", fontSize: 9, marginBottom: 4 }}>stale media</div>
            {bridgeHealth.stale_media_integrations.map((m, i) => (
              <div key={i} style={{ color: "var(--hg-warn)" }}>
                {m.entity_id} · {Math.round(m.age_hours || 0)}h
              </div>
            ))}
          </div>
        )}
        {visionHealth && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ color: "var(--hg-fg-5)", letterSpacing: "0.16em", textTransform: "uppercase", fontSize: 9, marginBottom: 4 }}>vision phash</div>
            <div>hits {visionHealth.phash_hits} · misses {visionHealth.phash_misses} · rate {Math.round((visionHealth.phash_hit_rate || 0) * 100)}% · cameras {visionHealth.phash_cameras_cached}</div>
          </div>
        )}
        {traceSummary?.count > 0 && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ color: "var(--hg-fg-5)", letterSpacing: "0.16em", textTransform: "uppercase", fontSize: 9, marginBottom: 4 }}>traces · n={traceSummary.count}</div>
            <div>p50/p90 ttfa · {Math.round(traceSummary.ttfa_ms?.p50 || 0)} / {Math.round(traceSummary.ttfa_ms?.p90 || 0)} ms</div>
          </div>
        )}
        {lastTrace && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ color: "var(--hg-fg-5)", letterSpacing: "0.16em", textTransform: "uppercase", fontSize: 9, marginBottom: 4 }}>last trace</div>
            <pre style={{
              fontFamily: HM_FONT_MONO, fontSize: 9,
              color: "var(--hg-fg-3)", whiteSpace: "pre-wrap",
              wordBreak: "break-all",
              margin: 0,
              background: "var(--hg-bg-2)",
              padding: 8,
              border: "1px solid var(--hg-border-soft)",
            }}>{JSON.stringify(lastTrace, null, 2)}</pre>
          </div>
        )}
        {networkMetrics && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ color: "var(--hg-fg-5)", letterSpacing: "0.16em", textTransform: "uppercase", fontSize: 9, marginBottom: 4 }}>network detail</div>
            {networkMetrics.udm && (
              <div>UDM · cpu {networkMetrics.udm.cpu}% · ram {networkMetrics.udm.mem}% · {networkMetrics.udm.state || "?"}</div>
            )}
            {(networkMetrics.switches || []).map((sw) => (
              <div key={sw.name}>{sw.name} · cpu {sw.cpu}% · ram {sw.mem}% · {sw.state || "?"}</div>
            ))}
            <div>clients · {networkMetrics.clientsOnline}/{networkMetrics.clientsKnown}</div>
          </div>
        )}
      </div>
    </>
  );
}

/* Export to window so home-app.jsx can pick them up. */
Object.assign(window, {
  HmSection: Section,
  HmMeterRow: MeterRow,
  HmTimelineRow: TimelineRow,
  HmStatusLine: StatusLine,
  HmEmptyState: EmptyState,
  HmHealthDot: HealthDot,
  HmSparkline: Sparkline,
  HmMetricCard: MetricCard,
  HmTabs: Tabs,
  HmTrayBody: TrayBody,
  HmRoomRow: RoomRow,
  HmDiagModal: DiagModal,
  HmMeterGroup: MeterGroup,
});
