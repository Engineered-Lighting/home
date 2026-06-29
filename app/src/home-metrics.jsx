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

/* Typography weight system — kill the 500-everywhere problem.
 *   400 = labels, secondary text
 *   600 = hero numerals, primary state
 * No 500 in this file. */
const HM_WEIGHT_LABEL = 400;
const HM_WEIGHT_HERO  = 600;

/* ── Surface ───────────────────────────────────────────────────────────
 *
 * The card shell only — no semantics. Owns radius/border/bg/padding/hover.
 * Compose with Card for semantics (title/body/meta).
 */
function Surface({ children, tone = "default", pad = 14, style = {}, onClick }) {
  const isInteractive = !!onClick;
  return (
    <div
      onClick={onClick}
      style={{
        background: tone === "elevated" ? "var(--hg-bg-1)" : "var(--hg-bg-1)",
        border: "1px solid var(--hg-border-soft)",
        borderRadius: 6,
        padding: pad,
        cursor: isInteractive ? "pointer" : "default",
        transition: "border-color 220ms, background 220ms",
        // v6.2: fill grid cells so sibling cards align to the same
        // height (e.g. HAOS HOST stretching to match FRIGATE).
        display: "flex",
        flexDirection: "column",
        height: "100%",
        ...(isInteractive && {
          ":hover": { borderColor: "var(--hg-border)" },
        }),
        ...style,
      }}
    >{children}</div>
  );
}

/* ── StatRow ───────────────────────────────────────────────────────────
 *
 * One-line label-value-unit triplet. Tabular-nums on the value.
 * Use this anywhere you'd write a key:value pair.
 */
function StatRow({ label, value, unit, tone = "default" }) {
  const valColor = tone === "warn" ? "var(--hg-warn)"
                : tone === "crit" ? "var(--hg-crit)"
                : "var(--hg-fg-1)";
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "1fr auto auto",
      columnGap: 8,
      alignItems: "baseline",
      fontFamily: HM_FONT_MONO,
      fontSize: 11,
      lineHeight: 1.6,
    }}>
      <span style={{ color: "var(--hg-fg-4)", fontWeight: HM_WEIGHT_LABEL }}>{label}</span>
      <span style={{ color: valColor, fontWeight: HM_WEIGHT_HERO }}>{value}</span>
      {unit && <span style={{ color: "var(--hg-fg-4)", fontSize: 10 }}>{unit}</span>}
    </div>
  );
}

/* ── Card ──────────────────────────────────────────────────────────────
 *
 * Composes Surface with a title row + body slot + optional meta footer.
 *   <Card title="ACTIVE ROOM" badge="warm" meta="22s ago">{body}</Card>
 */
function Card({ title, badge, meta, children, pad = 14 }) {
  return (
    <Surface pad={pad}>
      {(title || badge) && (
        <div style={{
          display: "flex", alignItems: "baseline", justifyContent: "space-between",
          marginBottom: 8, gap: 8,
        }}>
          {title && (
            <span style={{
              fontFamily: HM_FONT_MONO,
              fontSize: 9.5,
              letterSpacing: "0.18em",
              textTransform: "uppercase",
              color: "var(--hg-fg-4)",
              fontWeight: HM_WEIGHT_LABEL,
            }}>{title}</span>
          )}
          {badge && (
            <span style={{
              fontFamily: HM_FONT_MONO,
              fontSize: 9,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              color: ({
                ice:  "var(--hg-ice-bright)",
                warn: "var(--hg-warn)",
                crit: "var(--hg-crit)",
              })[badge.tone] || "var(--hg-fg-3)",
              border: `1px solid ${({
                ice:  "var(--hg-ice-bright)",
                warn: "var(--hg-warn)",
                crit: "var(--hg-crit)",
              })[badge.tone] || "var(--hg-border)"}`,
              padding: "1px 6px",
              borderRadius: 2,
              fontWeight: HM_WEIGHT_LABEL,
            }}>{badge.text || badge}</span>
          )}
        </div>
      )}
      <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>{children}</div>
      {meta && (
        <div style={{
          fontFamily: HM_FONT_MONO,
          fontSize: 9.5,
          color: "var(--hg-fg-5)",
          marginTop: 8,
          letterSpacing: "0.08em",
        }}>{meta}</div>
      )}
    </Surface>
  );
}

/* ── Arc ───────────────────────────────────────────────────────────────
 *
 * Compact radial meter — SVG circle with stroke arc. Used ONLY for
 * fraction-of-total metrics where the value moves across the range
 * (e.g. VRAM 86/96 GB). NOT for percent metrics that idle near 0%.
 */
function Arc({ value, max, size = 56, label, unit }) {
  const v = (typeof value === "number" && isFinite(value)) ? value : 0;
  const pct = max ? Math.max(0, Math.min(100, (v / max) * 100)) : 0;
  const stroke = pct > 90 ? "var(--hg-crit)"
                : pct > 70 ? "var(--hg-warn)"
                : "var(--hg-ice-bright)";
  const r = (size / 2) - 4;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - pct / 100);
  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center", gap: 4,
      width: size + 8,
    }}>
      <div style={{ position: "relative", width: size, height: size }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}
          style={{ transform: "rotate(-90deg)" }}>
          <circle cx={size/2} cy={size/2} r={r} fill="none"
            stroke="var(--hg-border-soft)" strokeWidth="3" />
          <circle cx={size/2} cy={size/2} r={r} fill="none"
            stroke={stroke} strokeWidth="3"
            strokeDasharray={c} strokeDashoffset={offset}
            strokeLinecap="round"
            style={{ transition: "stroke-dashoffset 500ms, stroke 300ms" }} />
        </svg>
        <div style={{
          position: "absolute", inset: 0,
          display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          fontFamily: HM_FONT_MONO,
          fontFeatureSettings: '"tnum"',
        }}>
          <span style={{ fontSize: 14, color: "var(--hg-fg-0)", fontWeight: HM_WEIGHT_HERO, lineHeight: 1 }}>
            {Math.round(pct)}
          </span>
          <span style={{ fontSize: 8, color: "var(--hg-fg-4)", marginTop: 1 }}>%</span>
        </div>
      </div>
      {label && (
        <div style={{
          fontFamily: HM_FONT_MONO,
          fontSize: 9,
          letterSpacing: "0.16em",
          textTransform: "uppercase",
          color: "var(--hg-fg-4)",
          fontWeight: HM_WEIGHT_LABEL,
        }}>{label}</div>
      )}
      {unit && (
        <div style={{
          fontFamily: HM_FONT_MONO,
          fontSize: 9,
          color: "var(--hg-fg-5)",
        }}>{unit}</div>
      )}
    </div>
  );
}

/* Helper: data variance check. If history is essentially flat, sparkline
 * is meaningless — return null and let MetricCard show value-only. */
function _hasVariance(history, max = 100) {
  if (!Array.isArray(history) || history.length < 3) return false;
  const nums = history.filter((v) => typeof v === "number" && isFinite(v));
  if (nums.length < 3) return false;
  const lo = Math.min(...nums);
  const hi = Math.max(...nums);
  const range = hi - lo;
  const scale = Number.isFinite(max) && max > 0 ? max : 100;
  // Variance >2% of max OR >2 absolute units (for ttft where max is huge)
  return range > Math.max(2, scale * 0.02);
}

function _pipelineStageModel(stages = [], totalMs = 0) {
  const safeStages = (Array.isArray(stages) ? stages : [])
    .filter((s) => s && Number.isFinite(s.dur))
    .map((s) => ({ ...s, dur: Math.max(0, s.dur) }));
  const sumMs = safeStages.reduce((acc, s) => acc + s.dur, 0);
  const finiteTotalMs = Number.isFinite(totalMs) && totalMs > 0 ? totalMs : 0;
  const displayTotalMs = finiteTotalMs > 0 ? finiteTotalMs : sumMs;
  const barTotalMs = Math.max(displayTotalMs, sumMs, 1);
  const slowest = safeStages.reduce((acc, s) => (s.dur > acc.dur ? s : acc), safeStages[0] || null);
  return { safeStages, displayTotalMs, barTotalMs, slowest };
}

/* ── MetricCard v2 ─────────────────────────────────────────────────────
 *
 * Auto-picks visualization:
 *   viz="auto" (default): sparkline if data has variance >2%, else dot
 *   viz="arc": radial — only for fraction-of-total values (VRAM)
 *   viz="bar": horizontal mini-bar (for state-loaded values)
 *   viz="none": value-only
 *
 * Hero numeral (600 weight), unit (400, faint), label below or above.
 */
function MetricCard({ label, value, max = 100, unit, history = [],
                     viz = "auto", warnAt = 70, critAt = 90,
                     hint }) {
  const hasValue = value != null && isFinite(value);
  const pct = hasValue && max ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  const valColor = pct > critAt ? "var(--hg-crit)"
                  : pct > warnAt ? "var(--hg-warn)"
                  : "var(--hg-fg-0)";

  // Auto-decide viz
  let actualViz = viz;
  if (viz === "auto") {
    actualViz = _hasVariance(history, max) ? "spark" : "none";
  }

  const valueFmt = hasValue
    ? (Number.isInteger(value) ? value.toString() : value.toFixed(value < 10 ? 1 : 0))
    : "—";

  if (actualViz === "arc") {
    return <Arc value={value} max={max} label={label} unit={unit} size={64} />;
  }

  return (
    <div style={{
      display: "flex", flexDirection: "column", gap: 4,
      minWidth: 0,
    }}>
      <div style={{
        fontFamily: HM_FONT_MONO,
        fontSize: 9.5,
        letterSpacing: "0.18em",
        textTransform: "uppercase",
        color: "var(--hg-fg-4)",
        fontWeight: HM_WEIGHT_LABEL,
      }}>{label}</div>
      <div style={{
        display: "flex", alignItems: "baseline", gap: 3,
        fontFamily: HM_FONT_MONO,
        fontFeatureSettings: '"tnum"',
      }}>
        <span style={{
          fontSize: 18, lineHeight: 1,
          color: hasValue ? valColor : "var(--hg-fg-5)",
          fontWeight: HM_WEIGHT_HERO,
        }}>{valueFmt}</span>
        {unit && (
          <span style={{
            fontSize: 9.5, color: "var(--hg-fg-4)",
            fontWeight: HM_WEIGHT_LABEL,
          }}>{unit}</span>
        )}
        {actualViz === "none" && hasValue && (
          <span style={{
            marginLeft: "auto", width: 5, height: 5, borderRadius: 999,
            background: pct > critAt ? "var(--hg-crit)" : pct > warnAt ? "var(--hg-warn)" : "var(--hg-fg-3)",
            alignSelf: "center",
            opacity: 0.6,
          }} />
        )}
      </div>
      {actualViz === "spark" && (
        <Sparkline data={history} max={max} width={80} height={14}
                   warnAt={warnAt} critAt={critAt} />
      )}
      {actualViz === "bar" && hasValue && (
        <div style={{ height: 3, background: "var(--hg-border-soft)", position: "relative", marginTop: 2 }}>
          <div style={{
            position: "absolute", inset: 0,
            width: `${pct}%`,
            background: pct > critAt ? "var(--hg-crit)" : pct > warnAt ? "var(--hg-warn)" : "var(--hg-fg-1)",
            transition: "width 500ms cubic-bezier(.4,0,.2,1)",
          }} />
        </div>
      )}
      {hint && (
        <div style={{
          fontFamily: HM_FONT_MONO, fontSize: 9,
          color: "var(--hg-fg-5)", marginTop: 2,
        }}>{hint}</div>
      )}
    </div>
  );
}

/* ── TimelineStrip ─────────────────────────────────────────────────────
 *
 * Compact horizontal pipeline timing strip. Stages render as labeled
 * segments along a horizontal track. Slowest stage highlighted.
 * Used in PipelineCard for last voice turn.
 */
function TimelineStrip({ stages = [], totalMs = 1 }) {
  if (!stages.length || totalMs <= 0) return null;
  // Find slowest stage for emphasis
  const slowestDur = Math.max(...stages.map((s) => s.dur || 0));
  return (
    <div>
      {/* Bar track */}
      <div style={{
        position: "relative",
        height: 6,
        background: "var(--hg-border-soft)",
        borderRadius: 3,
        marginBottom: 6,
        overflow: "hidden",
      }}>
        {stages.reduce((acc, s) => {
          const leftPct = (acc.cursor / totalMs) * 100;
          const widthPct = Math.max(0.2, (s.dur / totalMs) * 100);
          const isSlowest = s.dur > 0 && s.dur === slowestDur;
          acc.elements.push(
            <div key={s.label} style={{
              position: "absolute", top: 0, bottom: 0,
              left: `${leftPct}%`,
              width: `${widthPct}%`,
              background: isSlowest ? "var(--hg-ice-bright)" : "var(--hg-fg-2)",
              opacity: s.dur > 0 ? (isSlowest ? 1 : 0.55) : 0,
              transition: "all 400ms",
            }} />
          );
          acc.cursor += s.dur;
          return acc;
        }, { cursor: 0, elements: [] }).elements}
      </div>
      {/* Stage labels */}
      <div style={{
        display: "flex", justifyContent: "space-between", flexWrap: "wrap",
        fontFamily: HM_FONT_MONO,
        fontSize: 9.5,
        gap: 6,
      }}>
        {stages.map((s) => {
          const isSlowest = s.dur > 0 && s.dur === slowestDur;
          return (
            <span key={s.label} style={{
              display: "inline-flex", gap: 4, alignItems: "baseline",
              color: isSlowest ? "var(--hg-fg-1)" : "var(--hg-fg-4)",
              fontWeight: isSlowest ? HM_WEIGHT_HERO : HM_WEIGHT_LABEL,
            }}>
              <span>{s.label}</span>
              <span style={{ fontFeatureSettings: '"tnum"' }}>{s.dur > 0 ? `${Math.round(s.dur)}` : "—"}</span>
            </span>
          );
        })}
      </div>
    </div>
  );
}

/* ── PipelineBar ───────────────────────────────────────────────────────
 *
 * Tray v5: a more substantial voice-turn breakdown.
 * Renders:
 *   - hero total ms ("1842 ms")
 *   - 14px stacked proportional bar (STT / LLM / Synth / Audio)
 *   - legend underneath with each stage's ms
 *   - slowest stage highlighted in ice-bright; others rendered as a muted track
 *   - one-line "slowest: <stage> · <ms> ms" callout
 *
 * Props: { stages: [{label, dur}], totalMs }
 */
function PipelineBar({ stages = [], totalMs = 0 }) {
  const { safeStages, displayTotalMs, barTotalMs, slowest } = _pipelineStageModel(stages, totalMs);
  if (!safeStages.length || displayTotalMs <= 0) {
    return (
      <div style={{
        fontFamily: HM_FONT_MONO, fontSize: 10.5,
        color: "var(--hg-fg-5)",
      }}>no recent turn</div>
    );
  }
  return (
    <div>
      {/* Hero total ms */}
      <div style={{
        display: "flex", alignItems: "baseline", gap: 5,
        marginBottom: 8,
      }}>
        <span style={{
          fontFamily: HM_FONT_SANS,
          fontSize: 22, lineHeight: 1,
          color: "var(--hg-fg-0)",
          fontWeight: HM_WEIGHT_HERO,
          fontFeatureSettings: '"tnum"',
        }}>{Math.round(displayTotalMs)}</span>
        <span style={{
          fontFamily: HM_FONT_MONO,
          fontSize: 10.5, color: "var(--hg-fg-4)",
          fontWeight: HM_WEIGHT_LABEL,
        }}>ms total</span>
      </div>

      {/* Stacked proportional bar */}
      <div style={{
        position: "relative",
        height: 14,
        background: "var(--hg-border-soft)",
        borderRadius: 3,
        marginBottom: 8,
        overflow: "hidden",
        display: "flex",
      }}>
        {safeStages.map((s) => {
          const widthPct = Math.max(0, Math.min(100, (s.dur / barTotalMs) * 100));
          const isSlowest = s === slowest;
          return (
            <div key={s.label} title={`${s.label} · ${Math.round(s.dur)} ms`} style={{
              width: `${widthPct}%`,
              background: isSlowest ? "var(--hg-ice-bright)" : "var(--hg-fg-2)",
              opacity: s.dur > 0 ? (isSlowest ? 1 : 0.45) : 0,
              transition: "width 400ms cubic-bezier(.4,0,.2,1)",
              borderRight: widthPct > 0 ? "1px solid var(--hg-bg-0)" : "none",
              boxSizing: "border-box",
            }} />
          );
        })}
      </div>

      {/* Legend: one row per stage so SYNTH/AUDIO/LLM labels align with
          their values and never collide with the bar segments above. */}
      <div style={{
        display: "flex", flexDirection: "column",
        gap: 3,
        fontFamily: HM_FONT_MONO,
        fontSize: 11,
      }}>
        {safeStages.map((s) => {
          const isSlowest = s === slowest;
          return (
            <div key={s.label} style={{
              display: "grid",
              gridTemplateColumns: "70px 1fr auto",
              alignItems: "baseline",
              gap: 10,
              padding: "1px 0",
            }}>
              <span style={{
                color: isSlowest ? "var(--hg-fg-2)" : "var(--hg-fg-4)",
                textTransform: "uppercase",
                letterSpacing: "0.14em",
                fontSize: 9.5,
                fontWeight: HM_WEIGHT_LABEL,
              }}>{s.label}</span>
              {/* per-stage micro bar to reinforce magnitude visually */}
              <div style={{
                height: 2,
                background: "var(--hg-border-soft)",
                position: "relative",
                marginTop: 4,
                borderRadius: 1,
              }}>
                <div style={{
                  position: "absolute", inset: 0,
                  width: `${Math.max(0, Math.min(100, (s.dur / barTotalMs) * 100))}%`,
                  background: isSlowest ? "var(--hg-ice-bright)" : "var(--hg-fg-3)",
                  opacity: s.dur > 0 ? 0.9 : 0,
                  borderRadius: 1,
                }} />
              </div>
              <span style={{
                color: isSlowest ? "var(--hg-ice-bright)" : "var(--hg-fg-2)",
                fontWeight: isSlowest ? HM_WEIGHT_HERO : HM_WEIGHT_LABEL,
                fontFeatureSettings: '"tnum"',
                fontSize: 11,
                whiteSpace: "nowrap",
              }}>{s.dur > 0 ? `${Math.round(s.dur)} ms` : "—"}</span>
            </div>
          );
        })}
      </div>

      {/* Slowest callout */}
      {slowest.dur > 0 && (
        <div style={{
          marginTop: 8,
          fontFamily: HM_FONT_MONO,
          fontSize: 9.5,
          color: "var(--hg-fg-4)",
          letterSpacing: "0.04em",
        }}>
          slowest · <span style={{ color: "var(--hg-fg-2)" }}>{slowest.label} {Math.round(slowest.dur)} ms</span>
        </div>
      )}
    </div>
  );
}

/* ── DepHealthStrip ────────────────────────────────────────────────────
 *
 * Compact dependency-health row. Plain-language state labels — no
 * meaningless dashes. Hover for a tooltip.
 *
 * Props: { items: [{label, state, tone, hint?}] }
 *   tone: "ok" | "warn" | "crit" | "idle"
 *   state: short plain-language label ("online", "warming", "unknown")
 */
function DepHealthStrip({ items = [] }) {
  if (!items.length) return null;
  // v6.3: condensed back to single-line items — each item renders as
  // `● label state` on one row. Much tighter than the stacked v6 grid;
  // fits all 6 items (model · ai box · haos · bridge · tts · frigate)
  // on one row on typical screens, wraps gracefully when narrow.
  // Visual weight is intentionally light — this is glance-only metadata.
  return (
    <div style={{
      display: "flex",
      flexWrap: "wrap",
      gap: "4px 14px",
      padding: "7px 12px",
      background: "var(--hg-bg-1)",
      border: "1px solid var(--hg-border-soft)",
      borderRadius: 4,
      fontFamily: HM_FONT_MONO,
      fontSize: 9.5,
      lineHeight: 1.4,
    }}>
      {items.map((s) => {
        const valColor =
          s.tone === "crit" ? "var(--hg-crit)" :
          s.tone === "warn" ? "var(--hg-warn)" :
          s.tone === "idle" ? "var(--hg-fg-5)" :
          "var(--hg-fg-2)";
        return (
          <span key={s.label} title={s.hint || ""} style={{
            display: "inline-flex", alignItems: "center", gap: 5,
            minWidth: 0,
            whiteSpace: "nowrap",
          }}>
            <HealthDot tone={s.tone || "idle"} size={4} />
            <span style={{
              color: "var(--hg-fg-5)",
              fontWeight: HM_WEIGHT_LABEL,
              letterSpacing: "0.08em",
            }}>{s.label}</span>
            <span style={{
              color: valColor,
              fontWeight: HM_WEIGHT_HERO,
              fontFeatureSettings: '"tnum"',
            }}>{s.state || "unknown"}</span>
          </span>
        );
      })}
    </div>
  );
}

/* ── RoomGrid ──────────────────────────────────────────────────────────
 *
 * Tile grid for spatial room state. 3-column on wide, 2-column on narrow.
 * Each tile: name, occupancy glyph, age, optional secondary line.
 * Occupied rooms get bg-1 + ice-bright dot.
 */
function RoomGrid({ rooms = [] }) {
  if (!rooms.length) return null;
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
      gap: 8,
    }}>
      {rooms.map((r) => {
        const occupied = r.occupied;
        return (
          <div key={r.id} style={{
            padding: "10px 12px",
            background: occupied ? "var(--hg-bg-2)" : "transparent",
            border: `1px solid ${occupied ? "var(--hg-border)" : "var(--hg-border-soft)"}`,
            borderRadius: 5,
            minHeight: 56,
            display: "flex", flexDirection: "column",
            justifyContent: "space-between", gap: 4,
            transition: "background 300ms, border-color 300ms",
          }}>
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6,
            }}>
              <span style={{
                fontFamily: HM_FONT_MONO,
                fontSize: 10.5,
                color: occupied ? "var(--hg-fg-1)" : "var(--hg-fg-3)",
                fontWeight: occupied ? HM_WEIGHT_HERO : HM_WEIGHT_LABEL,
                letterSpacing: "0.02em",
                textTransform: "none",
              }}>{r.name || r.id.replace(/_/g, " ")}</span>
              <span style={{
                width: 5, height: 5, borderRadius: 999,
                background: occupied ? "var(--hg-ice-bright)" : "var(--hg-fg-5)",
                flexShrink: 0,
              }} />
            </div>
            <div style={{
              display: "flex", justifyContent: "space-between", gap: 4,
              fontFamily: HM_FONT_MONO, fontSize: 9.5,
            }}>
              <span style={{ color: occupied ? "var(--hg-fg-2)" : "var(--hg-fg-5)" }}>
                {occupied ? (r.identity || "occupied") : "empty"}
              </span>
              <span style={{ color: "var(--hg-fg-5)", fontFeatureSettings: '"tnum"' }}>
                {r.age || ""}
              </span>
            </div>
            {r.secondary && (
              <div style={{
                fontFamily: HM_FONT_MONO, fontSize: 9,
                color: "var(--hg-fg-4)", marginTop: 2,
              }}>{r.secondary}</div>
            )}
          </div>
        );
      })}
    </div>
  );
}

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
function Sparkline({ data = [], width = 60, height = 12, max, warnAt = 70, critAt = 90, fluid = false }) {
  if (!Array.isArray(data) || data.length < 2) {
    return <svg width={fluid ? "100%" : width} height={height} aria-hidden style={{ display: "block" }} />;
  }
  const numeric = data
    .map((v) => (typeof v === "number" && isFinite(v)) ? v : null)
    .filter((v) => v !== null);
  if (numeric.length < 2) {
    return <svg width={fluid ? "100%" : width} height={height} aria-hidden style={{ display: "block" }} />;
  }
  const lo = 0;
  const hi = max != null ? max : Math.max(...numeric, 1);
  const range = Math.max(1e-3, hi - lo);
  const last = numeric[numeric.length - 1];
  const lastPct = max != null ? (last / max) * 100 : 50;

  // Find peak (max value) + its index for the marker
  let peakIdx = 0, peakVal = numeric[0];
  for (let i = 1; i < numeric.length; i++) {
    if (numeric[i] > peakVal) { peakVal = numeric[i]; peakIdx = i; }
  }
  const peakPct = max != null ? (peakVal / max) * 100 : 50;

  let stroke = "var(--hg-fg-2)";
  let fill = "rgba(155, 164, 177, 0.16)"; // muted area fill matching fg-2
  if (lastPct > critAt) {
    stroke = "var(--hg-crit)";
    fill = "rgba(225, 85, 83, 0.18)";
  } else if (lastPct > warnAt) {
    stroke = "var(--hg-warn)";
    fill = "rgba(233, 196, 106, 0.18)";
  }
  const n = numeric.length;
  const stepX = width / (n - 1);
  // Polyline points for the trend line
  const pts = numeric.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - lo) / range) * (height - 2) - 1;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const ptsStr = pts.join(" ");
  // Closed polygon for the filled area below the line
  const areaPts = `0,${height} ${ptsStr} ${(width).toFixed(2)},${height}`;
  // Peak marker position
  const peakX = peakIdx * stepX;
  const peakY = height - ((peakVal - lo) / range) * (height - 2) - 1;
  // Reference lines at 50% + 100% of max (faint)
  const midY = height - 0.5 * (height - 2) - 1;
  return (
    <svg width={fluid ? "100%" : width} height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      aria-hidden style={{ display: "block", overflow: "visible" }}>
      {/* Faint reference line at 50% */}
      {max != null && (
        <line x1="0" y1={midY} x2={width} y2={midY}
          stroke="var(--hg-border-soft)" strokeWidth="0.5"
          strokeDasharray="2 4"
          vectorEffect="non-scaling-stroke" />
      )}
      {/* Filled area under the curve */}
      <polygon
        fill={fill}
        stroke="none"
        points={areaPts}
        style={{ transition: "fill 300ms" }}
      />
      {/* Trend line */}
      <polyline
        fill="none"
        stroke={stroke}
        strokeWidth="1.4"
        strokeLinejoin="round"
        strokeLinecap="round"
        points={ptsStr}
        vectorEffect="non-scaling-stroke"
        style={{ transition: "stroke 300ms" }}
      />
      {/* Peak marker — only when peak is meaningfully high (>25% of max) */}
      {max != null && peakPct > 25 && (
        <circle cx={peakX} cy={peakY} r="1.6"
          fill={peakPct > critAt ? "var(--hg-crit)"
              : peakPct > warnAt ? "var(--hg-warn)"
              : "var(--hg-fg-1)"}
          vectorEffect="non-scaling-stroke" />
      )}
    </svg>
  );
}


/* ── Tabs ──────────────────────────────────────────────────────────────
 *
 * Segmented control. Lowercase mono, letter-spaced. Active tab has
 * 1px underline in fg-0; others sit at fg-4. Optional `extra` slot on
 * the right (for ⚙ diagnostics icon).
 */
function Tabs({ value, onChange, tabs = [], extra = null, compact = false }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 2,
      borderBottom: "1px solid var(--hg-border-soft)",
      padding: compact ? "5px 8px" : "6px 10px",
      minHeight: compact ? 34 : 38,
    }}>
      {tabs.map((t) => {
        const active = t.id === value;
        return (
          <button
            key={t.id}
            onClick={() => onChange?.(t.id)}
            className="hg-focusable"
            style={{
              background: active ? "var(--hg-bg-2)" : "transparent",
              border: "1px solid",
              borderColor: active ? "var(--hg-border)" : "transparent",
              padding: compact ? "4px 9px" : "5px 11px",
              cursor: "pointer",
              fontFamily: HM_FONT_MONO,
              fontSize: compact ? 10 : 10.5,
              letterSpacing: "0.10em",
              textTransform: "lowercase",
              color: active ? "var(--hg-fg-0)" : "var(--hg-fg-4)",
              fontWeight: active ? HM_WEIGHT_HERO : HM_WEIGHT_LABEL,
              borderRadius: 4,
              transition: "color 200ms, background 200ms, border-color 200ms",
              display: "inline-flex", alignItems: "center", gap: 6,
            }}
          >
            <span>{t.label}</span>
            {t.warn && <HealthDot tone="warn" size={4} />}
          </button>
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
function TrayBody({ maxHeight = 280, children, compact = false }) {
  return (
    <div className="hg-scroll" style={{
      maxHeight,
      overflowY: "auto",
      overflowX: "hidden",
      padding: compact ? "10px 12px 12px" : "14px 16px 16px",
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
  // Tray v4 — primary primitives
  HmSurface: Surface,
  HmCard: Card,
  HmStatRow: StatRow,
  HmMetricCard: MetricCard,
  HmArc: Arc,
  HmTimelineStrip: TimelineStrip,
  HmPipelineBar: PipelineBar,
  HmDepHealthStrip: DepHealthStrip,
  HmRoomGrid: RoomGrid,
  // Carried from v3 — still useful for chips, modals, scroll wrapper
  HmTabs: Tabs,
  HmTrayBody: TrayBody,
  HmStatusLine: StatusLine,
  HmHealthDot: HealthDot,
  HmEmptyState: EmptyState,
  HmDiagModal: DiagModal,
  HmSparkline: Sparkline,        // exposed for direct use if needed
  // Legacy v2 — kept for any external usage; will deprecate
  HmSection: Section,
  HmMeterRow: MeterRow,
  HmTimelineRow: TimelineRow,
  HmRoomRow: RoomRow,
  HmMeterGroup: MeterGroup,
});
