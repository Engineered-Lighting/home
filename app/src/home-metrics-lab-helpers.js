/* ============================================================================
 * home-metrics-lab-helpers.js
 *
 * Pure-function helpers for the Lab tab. Plain JS (no JSX, no React) so a
 * Node test runner (`tools/run-lab-tests.js`) can `require()` this directly
 * without Babel. Browser loads this as a <script> BEFORE home-metrics-lab.jsx
 * and consumes via window.HomeMetricsLabHelpers.*
 *
 * These functions are the LOAD-BEARING logic that previously hid inside the
 * JSX file and could only be validated by visual inspection. Bugs found in
 * earlier rounds (tooltip snapping to top of viewport, flat resource lines)
 * are the kind of error that pure-function tests catch immediately.
 * ============================================================================ */

(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  // Always expose on window/global for browser consumption.
  if (typeof window !== "undefined") {
    window.HomeMetricsLabHelpers = api;
  } else if (typeof global !== "undefined") {
    global.HomeMetricsLabHelpers = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* ── Per-metric thresholds (0..1 normalized). Mirrors design Handoff §7.10
   * for gpu/cpu/ram/temp. VRAM has its own pill threshold (not a line). */
  const THRESHOLDS = {
    gpu:  { warn: 0.90, crit: 0.97 },
    cpu:  { warn: 0.80, crit: 0.95 },
    ram:  { warn: 0.75, crit: 0.90 },
    temp: { warn: 0.78, crit: 0.90 },
  };

  /* Per-stage profile baselines (0..1 normalized). USED ONLY when a stage
   * has zero real samples. Earlier values (gpu.llm=0.75 etc) faked
   * dramatic peaks that LOOKED like real measurements — the user saw
   * "GPU spiked to 75%" when in reality no GPU sample was captured for
   * that stage. That broke the entire data-trust contract of the chart.
   *
   * New policy: synthetic = a flat low neutral value (~0.08-0.12) PER
   * METRIC, NOT per stage. The line stays mostly straight when data is
   * missing instead of inventing a stage shape. Real samples ALWAYS win
   * — when present they're rendered with full fidelity. Synthetic
   * anchors get a `kind: "synthetic"` flag the renderer uses to dim
   * the line in that region. */
  const STAGE_PROFILES = {
    gpu:  { stt: 0.08, llm: 0.10, synth: 0.10, audio: 0.08,
            prefill: 0.10, gen: 0.10, ambient: 0.05,
            ext_req: 0.05, ext_api: 0.05, ext_recv: 0.05,
            _default: 0.08 },
    cpu:  { stt: 0.10, llm: 0.08, synth: 0.08, audio: 0.10,
            prefill: 0.08, gen: 0.08, ambient: 0.05,
            ext_req: 0.05, ext_api: 0.05, ext_recv: 0.05,
            _default: 0.08 },
    ram:  { stt: 0.10, llm: 0.10, synth: 0.10, audio: 0.10,
            prefill: 0.10, gen: 0.10, ambient: 0.10,
            ext_req: 0.10, ext_api: 0.10, ext_recv: 0.10,
            _default: 0.10 },
    temp: { stt: 0.30, llm: 0.32, synth: 0.32, audio: 0.30,
            prefill: 0.32, gen: 0.32, ambient: 0.30,
            ext_req: 0.30, ext_api: 0.30, ext_recv: 0.30,
            _default: 0.30 },
  };

  /* Floor value for idle time between turns. Drawn at the BOTTOM of the
   * row so lines visibly dip during inter-turn silence. */
  const IDLE_FLOOR = 0.03;

  /* ──────────────────────────────────────────────────────────────────────
   * Seeded PRNG — FNV-1a 32-bit string hash → mulberry32 PRNG.
   *
   * Used by buildDenseStageAnchors to generate STABLE noise: re-rendering
   * the same turn produces the same anchor values (no flicker between
   * renders, no jittery animation). Pattern mirrors simulation-data.jsx.
   * ──────────────────────────────────────────────────────────────────── */
  function fnv1a(str) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    return h >>> 0;
  }
  function mulberry32(seed) {
    let s = seed >>> 0;
    return function () {
      s = (s + 0x6D2B79F5) >>> 0;
      let t = s;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function seededRng(parts) {
    return mulberry32(fnv1a(parts.join(":")));
  }

  /* ──────────────────────────────────────────────────────────────────────
   * formatMs — integer-display a ms value, "—" for non-numeric.
   *
   * The prior bug: tooltip rendered raw float arithmetic results like
   * "282.10000000000000014 ms". Centralizing this prevents drift across
   * the three render sites (tooltip value, tooltip micro, hero prev).
   * Test: tools/run-lab-tests.js → "formatMs" suite (9 cases).
   * ────────────────────────────────────────────────────────────────────── */
  function formatMs(v) {
    if (v == null) return "—";
    if (typeof v !== "number" || isNaN(v) || !isFinite(v)) return "—";
    return String(Math.round(v));
  }

  /* ──────────────────────────────────────────────────────────────────────
   * computeTooltipPos — clamp a tooltip rectangle to the viewport.
   *
   * Cursor is at (cursorX, cursorY) in CLIENT coords (e.clientX/clientY).
   * Tooltip is rendered with `position: fixed` so it consumes client coords
   * directly — DO NOT subtract a container rect. Prior bug: subtracted
   * container-relative offsets, so the FIXED-positioned tooltip landed at
   * viewport pixel (small, small) = top-left corner / "snapped to top."
   *
   * Returns { top, left } clamped to keep the TIP_W × TIP_H_APPROX box
   * inside [PAD, vw-PAD-TIP_W] × [PAD, vh-PAD-TIP_H_APPROX]. Prefers ABOVE
   * the cursor when there's room, else BELOW.
   * ────────────────────────────────────────────────────────────────────── */
  function computeTooltipPos(cursorX, cursorY, vw, vh, opts) {
    opts = opts || {};
    const TIP_W = opts.tipW || 260;
    const TIP_H = opts.tipH || 120;
    const PAD   = opts.pad  || 12;
    vw = typeof vw === "number" ? vw : 1280;
    vh = typeof vh === "number" ? vh : 800;
    const wantsAbove = cursorY - TIP_H - PAD > PAD;
    const top = wantsAbove
      ? Math.max(PAD, cursorY - TIP_H - PAD)
      : Math.min(vh - TIP_H - PAD, cursorY + PAD);
    const left = Math.max(
      PAD,
      Math.min(vw - TIP_W - PAD, cursorX - TIP_W / 2)
    );
    return { top, left, placedAbove: wantsAbove };
  }

  /* ──────────────────────────────────────────────────────────────────────
   * buildDenseStageAnchors — N sub-anchors per stage for visible wiggle.
   *
   * The design's demo generates 28 samples per call to get smooth lines
   * with intra-stage variance. Our metrics poll generates 0-1 real
   * samples per typical 500-1500 ms turn — too sparse for a smooth path.
   *
   * Strategy:
   *   For each stage: subdivide into N sub-windows (proportional to stage
   *   duration; default ~6 per stage, capped 2..12). For each sub-window:
   *     - If a real sample's t falls inside it → use that sample's value
   *     - Else → STAGE_PROFILES[metricId][stage] + deterministic noise
   *
   * Noise is seeded via mulberry32(fnv1a(turn.id : metricId : stage : subIdx))
   * so re-rendering the same turn produces THE SAME anchor values (no
   * jittery animation, no fingerprint drift).
   *
   * `opts = { perStage: 6 }` — override sub-anchor density per stage.
   *
   * Returns [{ x, v, kind, stage }] — many anchors total, x monotonically
   * non-decreasing. Replaces buildStageAnchors as the path source for
   * the live chart; buildStageAnchors stays exported as an alias with
   * perStage:1 so prior tests keep passing.
   * ────────────────────────────────────────────────────────────────────── */
  function buildDenseStageAnchors(turn, metricId, layout, opts) {
    opts = opts || {};
    const perStageDefault = opts.perStage != null ? opts.perStage : 6;
    layout = layout || { slotStart: 0, slotEnd: 280 };
    const slotStart = layout.slotStart;
    const slotEnd = layout.slotEnd;
    const cw = slotEnd - slotStart;
    const totalMs = (turn && turn.totalMs) || 1;
    const stages = (turn && turn.stages) || [];
    const samples = (turn && turn.samples) || [];
    const profile = STAGE_PROFILES[metricId] || {};
    const turnStartedAt = (turn && turn.startedAt) || 0;
    const turnId = (turn && turn.id) || "anon";
    const out = [];
    let cumMs = 0;
    for (let i = 0; i < stages.length; i++) {
      const stg = stages[i];
      const stageMs = stg.ms || 0;
      const stageStartMs = stg.startedAt != null
        ? (stg.startedAt - turnStartedAt)
        : cumMs;
      const stageEndMs = stg.endedAt != null
        ? (stg.endedAt - turnStartedAt)
        : (cumMs + stageMs);
      cumMs = stageEndMs;
      const stageSpan = Math.max(1, stageEndMs - stageStartMs);
      // Density proportional to stage duration: longer stages get more
      // sub-anchors. Bounded [2, 12]. perStage acts as a multiplier.
      const density = Math.max(2, Math.min(12, perStageDefault));
      // Profile lookup: per-stage match → _default → 0.08 final floor.
      // Catches new stage labels (tool_*, future additions) without
      // collapsing the line to zero on unknown labels.
      let profileVal;
      if (typeof profile[stg.label] === "number") {
        profileVal = profile[stg.label];
      } else if (typeof profile._default === "number") {
        profileVal = profile._default;
      } else {
        profileVal = 0.08;
      }
      const rng = seededRng([turnId, metricId, stg.label]);
      for (let k = 0; k < density; k++) {
        // Sub-window: divide stage into `density` equal segments.
        const subStartMs = stageStartMs + (k / density) * stageSpan;
        const subEndMs   = stageStartMs + ((k + 1) / density) * stageSpan;
        // Find real samples in THIS sub-window
        const inSub = [];
        for (let j = 0; j < samples.length; j++) {
          const s = samples[j];
          const localT = (s.t || 0) - turnStartedAt;
          if (localT >= subStartMs && localT < subEndMs) {
            const v = s[metricId + "Pct"];
            if (typeof v === "number" && !isNaN(v)) inSub.push(v);
          }
        }
        let v, kind;
        if (inSub.length > 0) {
          // Peak-aware aggregation: take the MAX of samples in the
          // sub-window, not the mean. GPU during an LLM call spikes
          // hard and briefly — averaging drowns the spike in idle
          // samples flanking it. Max preserves the peak. (The downside
          // is "spikes look slightly fatter" because each sub-window
          // shows its max; acceptable trade for not hiding load.)
          let mx = inSub[0];
          for (let m = 1; m < inSub.length; m++) {
            if (inSub[m] > mx) mx = inSub[m];
          }
          v = mx;
          kind = "real";
        } else {
          // 2026-05-18 fallback chain: sub-window empty → try whole
          // stage. Sparse polling (750ms idle, 250ms active) often
          // means a single stage micro-window (~30ms for fast LLM
          // turns) captures zero samples even when the WHOLE stage
          // (200-800ms) had a sample land in it. Prefer stage-wide
          // real data over invented synthetic baseline.
          const inStage = [];
          for (let j = 0; j < samples.length; j++) {
            const s = samples[j];
            const localT = (s.t || 0) - turnStartedAt;
            if (localT >= stageStartMs && localT < stageEndMs) {
              const sv = s[metricId + "Pct"];
              if (typeof sv === "number" && !isNaN(sv)) inStage.push(sv);
            }
          }
          if (inStage.length > 0) {
            let mx = inStage[0];
            for (let m = 1; m < inStage.length; m++) {
              if (inStage[m] > mx) mx = inStage[m];
            }
            v = mx;
            kind = "real";
          } else {
            // Truly no samples in the stage. Synthetic baseline +
            // small deterministic ±5% noise so it doesn't render
            // dead-flat. Profiles are intentionally LOW (~0.08-0.12)
            // so synthetic regions read as "no signal" not "moderate
            // load".
            const noise = (rng() - 0.5) * 0.10;
            v = profileVal + noise * 0.5;
            kind = "synthetic";
          }
        }
        const centerMs = (subStartMs + subEndMs) / 2;
        const frac = totalMs > 0 ? centerMs / totalMs : 0.5;
        out.push({
          x: slotStart + frac * cw,
          v: Math.max(0, Math.min(1, v)),
          kind: kind,
          stage: stg.label,
        });
      }
    }
    // Addendum 32 Phase A: per-turn-per-metric anchor stats for
    // /lab-dump (AR32-7 H9 probe — distinguishes "renderer never
    // ran" from "renderer ran but found no real data"). Keyed by
    // `${turnId}_${metricId}` so a single dump shows the breakdown
    // across all recent turns + all 4 metrics.
    if (typeof window !== "undefined") {
      const stats = window.__hav_lastAnchorStats || {};
      let real = 0, synthetic = 0;
      for (let i = 0; i < out.length; i++) {
        if (out[i].kind === "real") real++;
        else synthetic++;
      }
      stats[`${turnId}_${metricId}`] = {
        real: real, synthetic: synthetic, total: out.length,
        sample_count_in_turn: samples.length,
        first_sample_t: samples.length > 0 ? samples[0].t : null,
        last_sample_t: samples.length > 0 ? samples[samples.length - 1].t : null,
        turn_started_at: turnStartedAt,
        last_call_ts: Date.now(),
      };
      window.__hav_lastAnchorStats = stats;
    }
    return out;
  }

  /* buildStageAnchors — original 1-anchor-per-stage version.
   *
   * Kept as a separate function (rather than aliasing through
   * buildDenseStageAnchors with perStage:1) because the dense generator
   * clamps density to a [2, 12] range — perStage:1 would become 2 and
   * break the "exactly N stages = N anchors" contract that suite 2
   * tests rely on. The dense path is the one wired into makeHistoryPath
   * for live rendering; this stays exported for back-compat + tests.
   * ────────────────────────────────────────────────────────────────────── */
  function buildStageAnchors(turn, metricId, layout) {
    layout = layout || { slotStart: 0, slotEnd: 280 };
    const slotStart = layout.slotStart;
    const slotEnd = layout.slotEnd;
    const cw = slotEnd - slotStart;
    const totalMs = (turn && turn.totalMs) || 1;
    const stages = (turn && turn.stages) || [];
    const samples = (turn && turn.samples) || [];
    const profile = STAGE_PROFILES[metricId] || {};
    const turnStartedAt = (turn && turn.startedAt) || 0;
    const out = [];
    let cumMs = 0;
    for (let i = 0; i < stages.length; i++) {
      const stg = stages[i];
      const stageMs = stg.ms || 0;
      const stageStartMs = stg.startedAt != null
        ? (stg.startedAt - turnStartedAt)
        : cumMs;
      const stageEndMs = stg.endedAt != null
        ? (stg.endedAt - turnStartedAt)
        : (cumMs + stageMs);
      cumMs = stageEndMs;
      const inStage = [];
      for (let j = 0; j < samples.length; j++) {
        const s = samples[j];
        const localT = (s.t || 0) - turnStartedAt;
        if (localT >= stageStartMs && localT < stageEndMs) {
          const v = s[metricId + "Pct"];
          if (typeof v === "number" && !isNaN(v)) inStage.push(v);
        }
      }
      let v, kind;
      if (inStage.length > 0) {
        let sum = 0;
        for (let k = 0; k < inStage.length; k++) sum += inStage[k];
        v = sum / inStage.length;
        kind = "real";
      } else {
        if (typeof profile[stg.label] === "number") {
          v = profile[stg.label];
        } else if (typeof profile._default === "number") {
          v = profile._default;
        } else {
          v = 0.08;
        }
        kind = "synthetic";
      }
      const centerMs = (stageStartMs + stageEndMs) / 2;
      const frac = totalMs > 0 ? centerMs / totalMs : 0.5;
      out.push({
        x: slotStart + frac * cw,
        v: Math.max(0, Math.min(1, v)),
        kind: kind,
        stage: stg.label,
      });
    }
    return out;
  }

  /* ──────────────────────────────────────────────────────────────────────
   * pathFromAnchors — render anchor list as an SVG `d` attribute.
   *
   * Each anchor's y-coord = yBase + (1 - v) * rowH. Higher v → higher on
   * screen (smaller y). ────────────────────────────────────────────────── */
  function pathFromAnchors(anchors, layout) {
    if (!anchors || anchors.length === 0) return "";
    layout = layout || { yBase: 0, rowH: 32 };
    const yBase = layout.yBase;
    const rowH = layout.rowH;
    let d = "";
    for (let i = 0; i < anchors.length; i++) {
      const p = anchors[i];
      const y = yBase + (1 - p.v) * rowH;
      d += (i === 0 ? "M" : " L") + p.x.toFixed(1) + "," + y.toFixed(1);
    }
    return d;
  }

  /* scaleAnchorsForDisplay — visual-only range expansion for resource rows.
   *
   * Hardware lines are most useful when they show shape, not only absolute
   * percent. A GPU that moves 10→14% is meaningful, but an absolute 0..100
   * y-scale compresses that into ~1px and reads as broken. This keeps raw
   * anchors unchanged for hover values, peaks, and thresholds; callers pass a
   * scaled copy only to pathFromAnchors().
   *
   * Guardrails:
   *   - require several real anchors, so seeded synthetic placeholders are
   *     not amplified into fake activity;
   *   - keep idle-floor anchors pinned low, so gaps between calls still read
   *     as silence;
   *   - use a minimum span, so tiny real movement is visible but not melodramatic.
   */
  function scaleAnchorsForDisplay(anchors, opts) {
    opts = opts || {};
    if (!Array.isArray(anchors) || anchors.length === 0) return [];
    const idleFloor = opts.idleFloor != null ? opts.idleFloor : IDLE_FLOOR;
    const minRealAnchors = opts.minRealAnchors != null ? opts.minRealAnchors : 3;
    const minVisualSpan = opts.minVisualSpan != null ? opts.minVisualSpan : 0.08;
    const targetLow = opts.targetLow != null ? opts.targetLow : 0.16;
    const targetHigh = opts.targetHigh != null ? opts.targetHigh : 0.88;

    const realVals = anchors
      .filter((a) => a && a.kind === "real" && typeof a.v === "number" && isFinite(a.v))
      .map((a) => Math.max(0, Math.min(1, a.v)));
    if (realVals.length < minRealAnchors) return anchors.map((a) => ({ ...a }));

    let lo = realVals[0];
    let hi = realVals[0];
    for (let i = 1; i < realVals.length; i++) {
      if (realVals[i] < lo) lo = realVals[i];
      if (realVals[i] > hi) hi = realVals[i];
    }
    const rawSpan = hi - lo;
    if (!(rawSpan > 0)) return anchors.map((a) => ({ ...a }));

    const span = Math.max(rawSpan, minVisualSpan);
    const center = (lo + hi) / 2;
    lo = Math.max(0, center - span / 2);
    hi = Math.min(1, center + span / 2);
    const denom = Math.max(0.001, hi - lo);
    const targetSpan = Math.max(0.001, targetHigh - targetLow);

    return anchors.map((a) => {
      if (!a || typeof a.v !== "number" || !isFinite(a.v)) return { ...a };
      if (a.kind !== "real") {
        return { ...a };
      }
      const normalized = Math.max(0, Math.min(1, (a.v - lo) / denom));
      return { ...a, v: targetLow + normalized * targetSpan };
    });
  }

  /* ──────────────────────────────────────────────────────────────────────
   * pickLineColor — return CSS color var per peak vs threshold.
   *
   * Returns a CSS variable string (so theme switching works automatically):
   *   peak >= THRESHOLDS[metric].crit → var(--hg-crit)
   *   peak >= THRESHOLDS[metric].warn → var(--hg-warn)
   *   else                            → var(--hg-fg-1)  (neutral white)
   * Neutral default matches the AI tab's resource-line palette — color
   * (amber/red) is reserved exclusively for tier-driven warn/crit. ──── */
  function pickLineColor(peak, metricId) {
    const t = THRESHOLDS[metricId];
    if (!t) return "var(--hg-fg-1)";
    if (peak >= t.crit) return "var(--hg-crit)";
    if (peak >= t.warn) return "var(--hg-warn)";
    return "var(--hg-fg-1)";
  }

  /* ──────────────────────────────────────────────────────────────────────
   * Convenience: compose buildStageAnchors over all turns to build the
   * full multi-turn path, anchoring idle-floor between turns. Returns
   * { path, peak } so the renderer can pick threshold color in one shot.
   *
   * Each turn occupies its own per_call slot. Between turns we drop two
   * idle-floor anchors (slot-end + slot-start of next) so the line
   * visibly dips during inter-turn silence — matches the design's
   * activeMask behavior.
   * ────────────────────────────────────────────────────────────────────── */
  function makeHistoryPath(turns, metricId, opts) {
    opts = opts || {};
    const perCall = opts.perCall || 280;
    const callFrac = opts.callFrac != null ? opts.callFrac : 0.82;
    const callPad = (1 - callFrac) / 2;
    const svgW = opts.svgW || (turns.length * perCall);
    const yBase = opts.yBase || 0;
    const rowH = opts.rowH || 32;
    const anchors = [];
    let peak = 0;
    for (let i = 0; i < turns.length; i++) {
      const slotStart = i * perCall + callPad * perCall;
      const slotEnd = (i + 1 - callPad) * perCall;
      // Pre-slot idle anchor pair
      if (i === 0) {
        anchors.push({ x: 0, v: IDLE_FLOOR });
      } else {
        anchors.push({ x: i * perCall - 4, v: IDLE_FLOOR });
      }
      anchors.push({ x: slotStart - 2, v: IDLE_FLOOR });
      // Per-stage DENSE anchors inside the slot — 4 stages × ~6 anchors
      // each = ~24 anchors per turn, matching the design demo's wiggle.
      // Real samples in-window override the seeded profile noise.
      const stageAnchors = buildDenseStageAnchors(turns[i], metricId, { slotStart, slotEnd });
      for (let a = 0; a < stageAnchors.length; a++) {
        anchors.push(stageAnchors[a]);
        if (stageAnchors[a].v > peak) peak = stageAnchors[a].v;
      }
      // Post-slot idle anchor — added ONLY when another turn follows.
      // For the rightmost turn we deliberately STOP the line at its
      // last anchor so it doesn't slope down to IDLE_FLOOR at x=svgW
      // (which produced a long diagonal escape that read as the line
      // leaving its row, especially for TEMP at ~30% baseline).
      if (i < turns.length - 1) {
        anchors.push({ x: slotEnd + 2, v: IDLE_FLOOR });
      }
    }
    return {
      path: pathFromAnchors(anchors, { yBase, rowH }),
      peak: peak,
      anchorCount: anchors.length,
    };
  }

  /* ──────────────────────────────────────────────────────────────────────
   * pickPollInterval — adaptive cadence selector.
   *
   * Returns 250 ms when a voice turn is active (lastTrace fired in last
   * 3 s OR voice.state is not "inactive"), else 750 ms. Used by the
   * metrics poller to densify samples during turns without burning
   * idle CPU. nowMs is overridable for testing.
   * ────────────────────────────────────────────────────────────────────── */
  function pickPollInterval(ctx) {
    ctx = ctx || {};
    const nowMs = ctx.nowMs || Date.now();
    const lastTurnEndedAt = ctx.lastTurnEndedAt || 0;
    const voiceState = ctx.voiceState || "inactive";
    const recent = lastTurnEndedAt > 0 && (nowMs - lastTurnEndedAt) < 3000;
    const speaking = voiceState !== "inactive";
    return (recent || speaking) ? 250 : 750;
  }

  /* ──────────────────────────────────────────────────────────────────────
   * valueAtX — linear-interpolate the y value at a given x position from
   * an anchor list.
   *
   * Used by the line-graph hover overlay: cursor is at SVG-local x, we
   * find the surrounding two anchors and interpolate. Returns null when
   * x is outside the anchor range or anchors is empty.
   *
   * Anchors MUST be sorted by x ascending (makeHistoryPath produces them
   * in order; tests verify monotonicity). Linear scan is fine — even at
   * 21 turns × 24 anchors/turn = ~500 anchors per line, the scan is
   * negligible compared to a render frame.
   * ────────────────────────────────────────────────────────────────────── */
  function valueAtX(anchors, x) {
    if (!anchors || anchors.length === 0) return null;
    if (anchors.length === 1) return anchors[0].v;
    if (x <= anchors[0].x) return anchors[0].v;
    if (x >= anchors[anchors.length - 1].x) return anchors[anchors.length - 1].v;
    for (let i = 0; i < anchors.length - 1; i++) {
      const a = anchors[i];
      const b = anchors[i + 1];
      if (x >= a.x && x <= b.x) {
        const span = b.x - a.x;
        if (span === 0) return a.v;
        const frac = (x - a.x) / span;
        return a.v + (b.v - a.v) * frac;
      }
    }
    return null;
  }

  /* ──────────────────────────────────────────────────────────────────────
   * makeHistoryPathWithAnchors — like makeHistoryPath but ALSO returns the
   * anchor array so callers can do hover lookups (valueAtX). Same path
   * string + peak, plus `anchors`. Existing makeHistoryPath stays
   * backward-compatible (just doesn't expose anchors).
   * ────────────────────────────────────────────────────────────────────── */
  function makeHistoryPathWithAnchors(turns, metricId, opts) {
    opts = opts || {};
    const perCall = opts.perCall || 280;
    const callFrac = opts.callFrac != null ? opts.callFrac : 0.82;
    const callPad = (1 - callFrac) / 2;
    const svgW = opts.svgW || (turns.length * perCall);
    const yBase = opts.yBase || 0;
    const rowH = opts.rowH || 32;
    const anchors = [];
    let peak = 0;
    for (let i = 0; i < turns.length; i++) {
      const slotStart = i * perCall + callPad * perCall;
      const slotEnd = (i + 1 - callPad) * perCall;
      if (i === 0) anchors.push({ x: 0, v: IDLE_FLOOR });
      else anchors.push({ x: i * perCall - 4, v: IDLE_FLOOR });
      anchors.push({ x: slotStart - 2, v: IDLE_FLOOR });
      const stageAnchors = buildDenseStageAnchors(turns[i], metricId, { slotStart, slotEnd });
      for (let a = 0; a < stageAnchors.length; a++) {
        anchors.push(stageAnchors[a]);
        if (stageAnchors[a].v > peak) peak = stageAnchors[a].v;
      }
      // Same right-edge fix as makeHistoryPath: skip the post-slot
      // IDLE_FLOOR anchor + the chart-end anchor for the rightmost
      // turn. Otherwise TEMP at ~30% synthetic baseline slopes
      // diagonally down to 3% across the last ~10% of the chart,
      // visually escaping its row.
      if (i < turns.length - 1) {
        anchors.push({ x: slotEnd + 2, v: IDLE_FLOOR });
      }
    }
    return {
      path: pathFromAnchors(anchors, { yBase, rowH }),
      peak: peak,
      anchors: anchors,
    };
  }

  /* ──────────────────────────────────────────────────────────────────────
   * traceTurnId — canonical id for a raw bridge trace. Primary key:
   * trace.turn_id (bridge-supplied). Secondary key (defensive vs same-ms
   * collisions per AR16-7): `t-${t_done}-${user_text.slice(0,16)}`.
   * Pure; used by the lab writer to dedup.
   * ────────────────────────────────────────────────────────────────────── */
  function traceTurnId(trace) {
    if (!trace) return null;
    if (trace.turn_id) return String(trace.turn_id);
    if (trace.t_done == null) return null;
    return "t-" + trace.t_done + "-" + String(trace.user_text || "").slice(0, 16);
  }

  /* ──────────────────────────────────────────────────────────────────────
   * dedupTraces — given (existingTurnIds:Set, incomingTraces:Array)
   * return only the traces not already represented. Order-preserving.
   * Pure; used by the lab backfill writer (F-2) to filter the bulk
   * `traces[]` array from /traces/latest?n=50 before doing the heavier
   * stage-extraction work.
   * ────────────────────────────────────────────────────────────────────── */
  function dedupTraces(existingTurnIds, incomingTraces) {
    if (!Array.isArray(incomingTraces) || incomingTraces.length === 0) return [];
    if (!existingTurnIds || typeof existingTurnIds.has !== "function") {
      return incomingTraces.slice();
    }
    const out = [];
    const seenThisBatch = new Set();
    for (let i = 0; i < incomingTraces.length; i++) {
      const id = traceTurnId(incomingTraces[i]);
      if (id == null) continue;
      if (existingTurnIds.has(id)) continue;
      if (seenThisBatch.has(id)) continue;  // dedup within batch too
      seenThisBatch.add(id);
      out.push(incomingTraces[i]);
    }
    return out;
  }

  /* ──────────────────────────────────────────────────────────────────────
   * computeBaselinePrev — extract the previous turn's totalMs for the
   * "prev" line in the chart hero. Returns null when fewer than 2 turns. */
  function computeBaselinePrev(turns) {
    if (!turns || turns.length < 2) return null;
    const prev = turns[turns.length - 2];
    return (prev && typeof prev.totalMs === "number") ? prev.totalMs : null;
  }

  /* ──────────────────────────────────────────────────────────────────────
   * computeStageBaselines (F-15, Addendum 27) — rolling p50 + p90 per
   * stage label across the trailing N turns. Used by the lab tooltip
   * to flag stages exceeding their typical band ("STT 600ms over p50
   * baseline"). Returns:
   *   { stt: {p50, p90, count}, llm: {...}, synth: {...}, audio: {...} }
   * Stages with fewer than `minSamples` (default 4) get count<minSamples
   * and the tooltip suppresses callouts — too noisy to flag drift
   * against a baseline that hasn't stabilized.
   *
   * Excludes the most-recent turn from the baseline so a slow turn
   * doesn't poison its own comparison. Default lookback is 30 turns.
   * ────────────────────────────────────────────────────────────────────── */
  function computeStageBaselines(turns, opts) {
    opts = opts || {};
    const lookback = opts.lookback == null ? 30 : opts.lookback;
    const excludeLast = opts.excludeLast !== false;   // default true
    const out = {};
    if (!Array.isArray(turns) || turns.length === 0) return out;
    const end = excludeLast ? Math.max(0, turns.length - 1) : turns.length;
    const start = Math.max(0, end - lookback);
    const byStage = {};   // label → number[]
    for (let i = start; i < end; i++) {
      const t = turns[i];
      const stages = (t && Array.isArray(t.stages)) ? t.stages : [];
      for (let j = 0; j < stages.length; j++) {
        const s = stages[j];
        if (!s || typeof s.ms !== "number" || !Number.isFinite(s.ms) || s.ms < 0) continue;
        const label = s.label;
        if (!label) continue;
        if (!byStage[label]) byStage[label] = [];
        byStage[label].push(s.ms);
      }
    }
    const percentile = (arr, p) => {
      if (!arr || arr.length === 0) return null;
      const sorted = arr.slice().sort((a, b) => a - b);
      const idx = Math.max(0, Math.min(sorted.length - 1, Math.round(p * (sorted.length - 1))));
      return sorted[idx];
    };
    for (const label of Object.keys(byStage)) {
      const arr = byStage[label];
      out[label] = {
        p50: percentile(arr, 0.5),
        p90: percentile(arr, 0.9),
        count: arr.length,
      };
    }
    return out;
  }

  /* ──────────────────────────────────────────────────────────────────────
   * computeStageDelta (F-15) — given a stage's actual ms + its baseline
   * map, return a structured callout the tooltip can render:
   *   {
   *     band: "ok" | "slow" | "very_slow",
   *     deltaMs: <signed int>,           // actual - p50
   *     ratio: <float>,                  // actual / p50
   *     baseline_p50: <int>,             // for "vs 200 ms p50" text
   *     summary: "STT 600ms over p50 baseline (3.5×)",
   *   }
   * Returns null when baseline is unstable (count < minSamples) OR
   * stage isn't above the warn threshold (default 1.5×). Tooltip omits
   * the line entirely in that case.
   *
   * Bands: "slow" = 1.5× to 2.5× p50; "very_slow" = > 2.5× OR > p90×1.5.
   * ────────────────────────────────────────────────────────────────────── */
  function computeStageDelta(stageMs, stageLabel, baselines, opts) {
    opts = opts || {};
    const minSamples = opts.minSamples == null ? 4 : opts.minSamples;
    const slowRatio = opts.slowRatio == null ? 1.5 : opts.slowRatio;
    const verySlowRatio = opts.verySlowRatio == null ? 2.5 : opts.verySlowRatio;
    if (typeof stageMs !== "number" || !Number.isFinite(stageMs) || stageMs <= 0) return null;
    if (!stageLabel || !baselines || !baselines[stageLabel]) return null;
    const b = baselines[stageLabel];
    if (!b || b.count < minSamples) return null;
    const p50 = b.p50;
    if (!p50 || p50 <= 0) return null;
    const ratio = stageMs / p50;
    if (ratio < slowRatio) return null;
    const band = (ratio >= verySlowRatio || (b.p90 && stageMs > b.p90 * 1.5)) ? "very_slow" : "slow";
    const deltaMs = Math.round(stageMs - p50);
    const upperLabel = stageLabel.toUpperCase();
    const summary = `${upperLabel} ${deltaMs > 0 ? "+" : ""}${deltaMs}ms vs ${Math.round(p50)}ms baseline (${ratio.toFixed(1)}×)`;
    return { band, deltaMs, ratio: +ratio.toFixed(2), baseline_p50: Math.round(p50), summary };
  }

  /* ──────────────────────────────────────────────────────────────────────
   * loadLabTurnsFromStorage — restore LabTurn[] from a localStorage facade.
   *
   * Takes a storage object with { getItem, setItem } so tests can pass a
   * Map-backed stub and production passes window.localStorage. Returns
   * [] for any of: key missing, JSON parse failure, schema mismatch.
   * Filters out turns whose endedAt is older than `ttlMs` from `nowMs`
   * (defaults to Date.now()) so a stale-from-last-week session doesn't
   * show up after a long break.
   * Test: tools/run-lab-tests.js → "localStorage persistence" suite.
   * ────────────────────────────────────────────────────────────────────── */
  function loadLabTurnsFromStorage(storage, key, ttlMs, nowMs) {
    if (!storage || typeof storage.getItem !== "function") return [];
    nowMs = nowMs || Date.now();
    try {
      const raw = storage.getItem(key);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!parsed || !Array.isArray(parsed.turns)) return [];
      const cutoff = nowMs - (ttlMs || 0);
      const out = [];
      for (let i = 0; i < parsed.turns.length; i++) {
        const t = parsed.turns[i];
        if (!t || typeof t.endedAt !== "number") continue;
        if (ttlMs && t.endedAt <= cutoff) continue;
        out.push(t);
      }
      return out;
    } catch (e) {
      return [];
    }
  }

  /* ──────────────────────────────────────────────────────────────────────
   * persistLabTurns — write LabTurn[] to a localStorage facade.
   *
   * Caps: at most 32 turns (most recent), at most 50 samples per turn.
   * The cap keeps the JSON payload under ~250 KB worst-case (well within
   * the ~5 MB Chromium localStorage quota). Silent on any storage error
   * (quota exceeded, disabled).
   * ────────────────────────────────────────────────────────────────────── */
  function persistLabTurns(storage, key, turns) {
    if (!storage || typeof storage.setItem !== "function") return;
    if (!Array.isArray(turns)) return;
    try {
      const trimmed = turns.slice(-32).map((t) => {
        const samples = Array.isArray(t.samples) ? t.samples.slice(0, 50) : [];
        return Object.assign({}, t, { samples });
      });
      storage.setItem(key, JSON.stringify({ version: 1, turns: trimmed }));
    } catch (e) {
      // Quota exceeded / storage disabled / serialization error — silent
    }
  }

  /* ── Multi-round turn grouping (Addendum 29 Change 3) ───────────────────
   * A single user turn that invokes tools produces N+1 sidecar entries
   * (each LLM round emits one). The naive trace adapter creates N+1
   * separate bars in the lab strip — fragmenting one logical user turn.
   *
   * Grouping rule: a new incoming trace SHOULD merge into an existing
   * LabTurn iff:
   *   a) same `user_text` (exact match, post-trim)
   *   b) existing turn's `endedAt` is within `windowMs` of the new
   *      trace's `startedAt` (default 2500ms; tunable)
   *   c) existing turn doesn't already track this trace's sidecar id
   *      (idempotency under SSE/poll race per AV29-18)
   *
   * Returns the matching turn (mutable ref) OR null. Caller does the
   * append (concat stages + bump totalMs + update endedAt + add the
   * new sidecar id to sidecar_ids + prefer non-empty assistant_text).
   */
  function findTurnToGroupInto(existingTurns, newTrace, windowMs) {
    if (!Array.isArray(existingTurns) || existingTurns.length === 0) return null;
    if (!newTrace || typeof newTrace !== "object") return null;
    const w = Number.isFinite(windowMs) ? windowMs : 2500;
    const newText = String(newTrace.user_text || "").trim();
    if (!newText) return null;
    const newId = newTrace.turn_id != null ? String(newTrace.turn_id) : null;
    const newStart = Number.isFinite(newTrace.startedAt)
      ? newTrace.startedAt
      : (Number.isFinite(newTrace.t_done) ? Date.now() - newTrace.t_done : Date.now());
    // Walk backwards (most-recent first) — turns are in chronological order.
    for (let i = existingTurns.length - 1; i >= 0; i--) {
      const t = existingTurns[i];
      if (!t) continue;
      const txt = String(t.transcript || "").trim();
      if (txt !== newText) continue;
      // Already-merged check: same sidecar id → no merge (idempotency).
      const sids = t.sidecar_ids || [];
      if (newId && sids.indexOf(newId) !== -1) return null;
      // Time-window check.
      const gap = Math.abs(newStart - (t.endedAt || 0));
      if (gap <= w) return t;
      // If gap > window AND we've already passed the same-text frontier,
      // no earlier turn will match (turns are chronological, gap only grows).
      return null;
    }
    return null;
  }

  /* Merge a new trace into an existing turn. Mutates the turn in place
   * (caller's responsibility to wrap in a state update or just mutate
   * the ref then bump labTick). Returns the same turn for chaining.
   *
   * Per AV29-2 (ordering): the new trace's stages are appended AFTER
   * existing ones — chronological by construction since the grouping
   * check requires newStart >= turn.endedAt - windowMs (typically much
   * after the existing endedAt). The caller's processTrace should also
   * sort the final stages array by startedAt as a defense.
   *
   * Per AV29-12 (empty assistant): we only OVERWRITE assistant_text
   * when the new trace has a non-empty value. Tool-call-only rounds
   * with empty assistant don't clobber a previously-rendered text.
   */
  function mergeTraceIntoTurn(turn, newTrace) {
    if (!turn || !newTrace) return turn;
    if (!Array.isArray(turn.stages)) turn.stages = [];
    if (!Array.isArray(turn.sidecar_ids)) turn.sidecar_ids = [];
    const incomingStages = Array.isArray(newTrace.stages) ? newTrace.stages : [];

    // Addendum 29 Change 4: tool-segment splicing. If the previous
    // round had pending tool_calls (tracked on turn.pending_tools),
    // the gap between that round's gen.endedAt and this round's
    // prefill.startedAt IS the tool execution time. Insert one labeled
    // segment per pending tool name, distributing the gap evenly.
    //
    // This makes the bar visualize EVERY service called for the user
    // turn (per the user's "every service called in the stack" ask).
    // AV29-3 covered by ordering tests (tools land BETWEEN consecutive
    // gen→prefill pairs) + name match assertion (segment.label ==
    // tool_name from pending_tools[i]).
    if (Array.isArray(turn.pending_tools) && turn.pending_tools.length > 0
        && turn.stages.length > 0 && incomingStages.length > 0) {
      const prevLast = turn.stages[turn.stages.length - 1];
      const firstNew = incomingStages[0];
      const gapStart = prevLast && Number.isFinite(prevLast.endedAt) ? prevLast.endedAt : 0;
      const gapEnd   = firstNew && Number.isFinite(firstNew.startedAt) ? firstNew.startedAt : gapStart;
      const gapMs = Math.max(0, gapEnd - gapStart);
      const tools = turn.pending_tools.slice();
      const perTool = tools.length > 0 ? gapMs / tools.length : 0;
      const toolStages = [];
      let cursor = gapStart;
      for (let i = 0; i < tools.length; i++) {
        // Minimum 1ms width for visibility even when the gap is 0
        // (typical for very fast in-memory tools that complete in
        // microseconds — the routing log shows them as latency_ms=0).
        const ms = Math.max(1, Math.round(perTool));
        toolStages.push({
          label: String(tools[i]),
          ms,
          startedAt: cursor,
          endedAt: cursor + ms,
          kind: "tool",
        });
        cursor += ms;
      }
      // Insert the tool stages into the turn's stages BEFORE the new
      // round's stages. We then concat the new round's stages on top.
      turn.stages = turn.stages.concat(toolStages);
      turn.pending_tools = [];
    }

    turn.stages = turn.stages.concat(incomingStages);
    // Keep stages sorted by startedAt (defense against out-of-order SSE
    // delivery, AV29-11).
    turn.stages.sort((a, b) => (a.startedAt || 0) - (b.startedAt || 0));
    // Recompute totalMs as sum of all stage durations (NOT just max-
    // endedAt minus min-startedAt, which would include gaps).
    let total = 0;
    for (let i = 0; i < turn.stages.length; i++) {
      total += turn.stages[i].ms || 0;
    }
    turn.totalMs = total;
    // Track this sidecar id for future idempotency checks.
    if (newTrace.turn_id != null) turn.sidecar_ids.push(String(newTrace.turn_id));
    // endedAt = the latest stage's endedAt.
    const lastStage = turn.stages[turn.stages.length - 1];
    if (lastStage && Number.isFinite(lastStage.endedAt)) {
      turn.endedAt = lastStage.endedAt;
    }
    // Prefer the most recent NON-EMPTY assistant text. Empty values
    // (tool-call-only rounds) don't clobber.
    const newAssistant = String(newTrace.assistant_text || "").trim();
    if (newAssistant) turn.assistant_text = newAssistant;
    // Capture this round's tool_calls as pending for the NEXT merge.
    // Sidecar entry shape: tool_calls = [{function: {name: "X"}}, ...].
    if (Array.isArray(newTrace.tool_calls) && newTrace.tool_calls.length > 0) {
      const names = newTrace.tool_calls
        .map((tc) => tc && tc.function && tc.function.name)
        .filter((n) => typeof n === "string" && n.length > 0);
      turn.pending_tools = names;
    } else {
      turn.pending_tools = [];
    }
    return turn;
  }

  /* ── In-flight stub eviction (Addendum 29 Change 2 / AV29-4) ────────────
   * A stub LabTurn with inFlight=true is created when the user dispatches
   * a query (typed via sendToHA). The real trace arrives via SSE/poll and
   * triggers a merge (or a new turn) that flips the flag. If the request
   * silently errors (network drop, HA crash) the stub orphans — this
   * helper sweeps stubs older than `maxAgeMs` (default 60s).
   *
   * Pure function: takes turns + now, returns filtered array. Caller
   * mutates labTurnsRef + bumps labTick.
   */
  function evictStaleInFlight(turns, nowMs, maxAgeMs) {
    if (!Array.isArray(turns)) return [];
    const now = Number.isFinite(nowMs) ? nowMs : Date.now();
    const cutoff = Number.isFinite(maxAgeMs) ? maxAgeMs : 60000;
    return turns.filter((t) => {
      if (!t || !t.inFlight) return true;
      const age = now - (t.startedAt || 0);
      return age <= cutoff;
    });
  }

  /* ── /describe-clip arg parser (Addendum 30B) ───────────────────────────
   * Old parser: positional split on whitespace. "living room" became
   *   camera="living", frames="room" → "frames must be 2-8 (got room)".
   * New parser: walk tokens left-to-right, accumulate non-numeric tokens
   * as the camera name, then accept up to 2 trailing numerics as frames
   * + interval_s. Normalizes camera to entity_id form (lowercase,
   * underscored, strip "camera." prefix).
   *
   * Returns:
   *   { camera: "living_room", frames: 4, interval_s: 1.5 }      on success
   *   { error: "<message>" }                                      on bad input
   */
  function parseDescribeClipArg(raw) {
    const parts = String(raw || "").trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return { error: "missing camera" };
    const isNumeric = (s) => /^[\d.]+$/.test(s);
    // First token MUST be non-numeric (camera name); leading numeric
    // means user typed args without a camera.
    if (isNumeric(parts[0])) return { error: "missing camera (first token was numeric)" };
    let i = 0;
    const cameraTokens = [];
    for (; i < parts.length; i++) {
      if (isNumeric(parts[i])) break;
      cameraTokens.push(parts[i]);
    }
    const cameraRaw = cameraTokens.join(" ").trim();
    if (!cameraRaw) return { error: "empty camera name" };
    const camera = cameraRaw
      .replace(/^camera\./i, "")
      .replace(/\s+/g, "_")
      .toLowerCase();
    const numericPart = parts.slice(i);
    let frames, interval_s;
    if (numericPart[0] != null) {
      const f = parseInt(numericPart[0], 10);
      if (!Number.isFinite(f) || f < 2 || f > 8) {
        return { error: `frames must be 2-8 (got ${numericPart[0]})` };
      }
      frames = f;
    }
    if (numericPart[1] != null) {
      const s = parseFloat(numericPart[1]);
      if (!Number.isFinite(s) || s < 0.2 || s > 2.0) {
        return { error: `interval_s must be 0.2-2.0 (got ${numericPart[1]})` };
      }
      interval_s = s;
    }
    return { camera, frames, interval_s };
  }

  /* ──────────────────────────────────────────────────────────────────────
   * attachSampleToRecentTurns — Addendum 32 Phase C fix (H3 — snapshot
   * timing).
   *
   * turn.samples is the IMMUTABLE snapshot taken at turn-creation time.
   * Samples that arrive AFTER turn creation (which is most of them,
   * since SSE arrives within ms of LLM completion but the metrics poll
   * fires every 250-750ms) never got attached. Result: most turn.samples
   * arrays end up empty, anchors fall to STAGE_PROFILES synthetic
   * baseline, resource lines render as flat ~12%/10%/12%/34% across
   * the entire history strip. /lab-dump confirmed this:
   * `live_in_window_count: 15` but `persisted_samples_count: 0` for a
   * fresh in-flight stub.
   *
   * Fix: every time a new sample lands in labSamplesRef, scan the last
   * `scanLookback` turns (default 5) and append the sample to any turn
   * whose [startedAt-windowMs, endedAt+windowMs] window contains the
   * sample's t. turn.samples grows PROGRESSIVELY instead of being a
   * one-shot snapshot. Per-turn cap matches persistLabTurns.
   *
   * Mutates the input turns in place. Returns the count of turns that
   * received the sample.
   * ────────────────────────────────────────────────────────────────────── */
  function attachSampleToRecentTurns(turns, sample, opts) {
    opts = opts || {};
    const scanLookback = opts.scanLookback != null ? opts.scanLookback : 5;
    const windowMs = opts.windowMs != null ? opts.windowMs : 3000;
    const perTurnCap = opts.perTurnCap != null ? opts.perTurnCap : 50;
    if (!sample || typeof sample.t !== "number") return 0;
    if (!Array.isArray(turns) || turns.length === 0) return 0;
    const start = Math.max(0, turns.length - scanLookback);
    let attached = 0;
    for (let i = start; i < turns.length; i++) {
      const t = turns[i];
      if (!t || typeof t.startedAt !== "number") continue;
      const endedAt = typeof t.endedAt === "number" ? t.endedAt : t.startedAt;
      if (sample.t < t.startedAt - windowMs) continue;
      if (sample.t > endedAt + windowMs) continue;
      if (!Array.isArray(t.samples)) t.samples = [];
      // Dedup by timestamp — if a sample with the same t is already
      // there (e.g., snapshot already grabbed it), skip.
      let alreadyPresent = false;
      for (let j = t.samples.length - 1; j >= 0; j--) {
        if (t.samples[j].t === sample.t) { alreadyPresent = true; break; }
        if (t.samples[j].t < sample.t - 1000) break;  // bounded scan
      }
      if (alreadyPresent) continue;
      t.samples.push(sample);
      if (t.samples.length > perTurnCap) {
        t.samples = t.samples.slice(-perTurnCap);
      }
      attached++;
    }
    return attached;
  }

  /* ────────────────────────────────────────────────────────────────────── */
  /* deriveLabTier -- model/stack recovery policy used by the Lab tab.
   *
   * Kept in the helper module so local tests can lock down the user-visible
   * recovery affordances. The JSX keeps a fallback copy, but delegates here
   * in the normal browser load path.
   */
  function deriveLabTier(metrics, aiStackState, lastTrace, baseline) {
    metrics = metrics || {};
    aiStackState = aiStackState || {};
    baseline = baseline || {};

    const overall = aiStackState.overall || "unknown";
    const services = aiStackState.services || [];
    const inFlight = aiStackState.in_flight;
    const vramPct = (metrics.vramMax || 0) > 0
      ? Math.min(1, (metrics.vram || 0) / metrics.vramMax)
      : 0;
    const lastTurnMs = (lastTrace && (lastTrace.t_done || lastTrace.totalMs || 0)) || 0;
    const baselineP50 = baseline.p50 || null;

    const downServices = services.filter((s) =>
      s.health === "offline" || s.container === "exited" || s.container === "dead"
    );
    const errorServices = services.filter((s) =>
      s.health === "error" || s.health === "crashed" || s.probe === "error"
    );
    const degradedServices = services.filter((s) =>
      s.health === "degraded" || s.health === "stale" || s.probe === "stale"
    );

    if (overall === "offline" || overall === "down" || downServices.length >= 3) {
      return {
        tier: "offline",
        chipTone: "crit",
        chipText: "offline",
        statusWord: "ai stack offline",
        statusWordTone: "crit",
        sub: "last seen - tap start to bring it up",
        actions: [
          { label: "start ai stack", verb: "start", style: "cool", glyph: "▶" },
          { label: "logs", verb: "logs", style: "default", glyph: "▤" },
        ],
        banner: {
          tone: "crit", lbl: "offline",
          why: "ai box unreachable · last seen recently",
          act: "retry",
        },
        progress: null,
        chartBadge: { text: "-", tone: "default" },
      };
    }

    if (overall === "starting" || overall === "warming" || (inFlight && inFlight.verb === "start")) {
      const eta = inFlight && inFlight.eta_s ? `~${inFlight.eta_s}s` : "";
      const step = (inFlight && inFlight.step) || "starting ai stack";
      const pct = (inFlight && inFlight.progress_pct) || 50;
      return {
        tier: "warming",
        chipTone: "cool",
        chipText: "warming",
        statusWord: "starting ai stack",
        statusWordTone: "default",
        sub: `${step}${eta ? " · " + eta : ""}`,
        actions: [],
        banner: null,
        progress: { step, pct, eta },
        chartBadge: { text: "-", tone: "default" },
      };
    }

    if (errorServices.length > 0 || overall === "error") {
      const e = errorServices[0] || { name: aiStackState.crashed_service || "service" };
      return {
        tier: "error",
        chipTone: "crit",
        chipText: "error",
        statusWord: `${e.name} crashed`,
        statusWordTone: "crit",
        sub: e.detail || "service crashed · retrying",
        actions: [
          { label: "view logs", verb: "logs", style: "default", glyph: "▤" },
          { label: "restart", verb: "restart", style: "danger", glyph: "▶" },
        ],
        banner: {
          tone: "crit", lbl: "error",
          why: `<b>${e.name}</b> crashed · ${e.detail || "see logs"}`,
          act: "logs",
        },
        progress: null,
        chartBadge: { text: "-", tone: "default" },
      };
    }

    if (vramPct > 0.85) {
      return {
        tier: "pressured",
        chipTone: "warn",
        chipText: "pressured",
        statusWord: `vram at ${Math.round(vramPct * 100)}%`,
        statusWordTone: "warn",
        sub: `voice synthesis may queue · ${((metrics.vramMax - metrics.vram) || 0).toFixed(1)}g headroom`,
        actions: [
          { label: "stop ai models", verb: "stopStack", style: "danger", glyph: "✕", confirm: true },
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

    if (degradedServices.length >= 2) {
      return {
        tier: "partial",
        chipTone: "warn",
        chipText: "partial",
        statusWord: `${degradedServices.length} services down`,
        statusWordTone: "warn",
        sub: degradedServices.map((s) => s.name).join(" · ") + " · core stack healthy",
        actions: [
          { label: "restart all", verb: "restart", style: "danger", glyph: "▶", confirm: true },
          { label: "logs", verb: "logs", style: "default", glyph: "▤" },
        ],
        banner: {
          tone: "warn", lbl: "partial",
          why: `<b>${degradedServices.length} of ${services.length || 6}</b> services degraded · core stack healthy`,
          act: "view",
        },
        progress: null,
        chartBadge: { text: "ok", tone: "ok" },
      };
    }

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
          { label: "restart", verb: "restart", style: "danger", glyph: "▶", confirm: true },
          { label: "logs", verb: "logs", style: "default", glyph: "▤" },
        ],
        banner: {
          tone: "warn", lbl: "degraded",
          why: `${d.name} heartbeat missed · auto-retrying`,
          act: "view logs",
        },
        progress: null,
        chartBadge: { text: "ok", tone: "ok" },
      };
    }

    if (lastTurnMs > 0 && baselineP50 && lastTurnMs > 1.5 * baselineP50) {
      const xs = (lastTurnMs / baselineP50).toFixed(1);
      return {
        tier: "slow",
        chipTone: "warn",
        chipText: "slow",
        statusWord: `last turn ${(lastTurnMs / 1000).toFixed(1)}s`,
        statusWordTone: "warn",
        sub: `${xs}x baseline · investigate stage breakdown`,
        actions: [],
        banner: null,
        progress: null,
        chartBadge: { text: "slowing", tone: "warn" },
      };
    }

    const uptime = aiStackState.uptime_h ? `warm ${aiStackState.uptime_h}h · ` : "";
    return {
      tier: "ready",
      chipTone: "cool",
      chipText: "ready",
      statusWord: metrics.model || "ai stack",
      statusWordTone: "default",
      sub: `${uptime}ai stack online · everything healthy`,
      actions: [],
      banner: null,
      progress: null,
      chartBadge: { text: "fast", tone: "ok" },
    };
  }

  return {
    THRESHOLDS,
    STAGE_PROFILES,
    IDLE_FLOOR,
    computeTooltipPos,
    buildStageAnchors,
    buildDenseStageAnchors,
    pathFromAnchors,
    scaleAnchorsForDisplay,
    pickLineColor,
    makeHistoryPath,
    pickPollInterval,
    computeBaselinePrev,
    computeStageBaselines,
    computeStageDelta,
    formatMs,
    loadLabTurnsFromStorage,
    persistLabTurns,
    valueAtX,
    makeHistoryPathWithAnchors,
    traceTurnId,
    dedupTraces,
    parseDescribeClipArg,
    findTurnToGroupInto,
    mergeTraceIntoTurn,
    evictStaleInFlight,
    attachSampleToRecentTurns,
    deriveLabTier,
  };
});
