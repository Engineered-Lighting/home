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

/* Export to window so home-app.jsx can pick them up. */
Object.assign(window, {
  HmSection: Section,
  HmMeterRow: MeterRow,
  HmTimelineRow: TimelineRow,
  HmStatusLine: StatusLine,
  HmEmptyState: EmptyState,
  HmHealthDot: HealthDot,
  HmMeterGroup: MeterGroup,
});
