/* ============================================================================
 * home-video-labeler-timeline.jsx — VLTimeline + VLThumbStrip
 *
 * M0 shipped the structural pieces (nice-step ruler, click-to-seek, the
 * imperatively-positioned playhead, sprite filmstrip). M1 turns VLTimeline
 * into the real multi-lane segment editor:
 *   - lane grid: 110px sidebar (lane title + active marker) next to a
 *     scrollable track area; 4 lanes × 56px under the 30px ruler
 *   - segment blocks with pointer-capture drag — move (clamped between
 *     neighbors), resize-start / resize-end (clips the neighbor down to
 *     min-duration, then stops). Live-local state during pointermove;
 *     COMMIT + undo entry on pointerup only; suppressClick after a drag.
 *     Mechanics adapted from the recovered gesture editor; per-lane
 *     NON-OVERLAP enforced via the pure ops in home-video-labeler-data.js.
 *   - snapping: other-lane boundaries + playhead + sprite keyframe
 *     instants, tol 6/pxPerSec seconds, Alt disables, flash line on snap
 *   - Ctrl+wheel zoom via element-level addEventListener('wheel',
 *     {passive:false}) — window-level listeners are passive-by-default and
 *     can't preventDefault Chromium's page zoom. onZoom(dir, anchorT) asks
 *     the overlay to change pxPerSec; a layout effect here keeps anchorT
 *     stationary (or the view center, for keyboard zoom).
 *   - virtualization: only ticks/segments intersecting the visible range
 *     render; scroll state is rAF-throttled; auto-follow keeps the playhead
 *     in view while playing unless the user scrolled in the last 1.5s.
 *
 * M3 adds the suggestion ghost sub-rows: an optional `suggestions` prop
 * ({axis: [SEG]}) renders each axis's prelabels as a slim 16px strip at the
 * bottom of its lane — dashed border + diagonal hatch in the value color,
 * 55%-opacity caption, a 2px confidence bar (fg-4→ice ramp) and a warn '!'
 * diamond when the seg carries disagreement. Ghosts are click-to-select only
 * (onSelectSuggestion) — no drag. 4s-stride windows overlap by design; the
 * ghosts simply stack at reduced opacity (deliberately NOT non-overlap
 * clipped). Canonical blocks shrink to make room only when a lane actually
 * has suggestions, so M0/M1 visuals are untouched otherwise. The `selection`
 * prop now optionally carries kind: 'canonical' | 'suggestion'.
 *
 * M4: suggestions decorated with `_outlier` (VLEditor matches their window
 * geometry against /window-signals cluster outliers) carry a hollow
 * var(--hg-crit) dot beside the disagreement diamond — the badge the M3
 * spec reserved for cluster-outlier triage.
 *
 * Multi-person (person_slot): when a lane holds segments in >1 slot
 * ((seg.person_slot||null); activity/posture only in practice), the lane's
 * block area divides into stacked sub-rows — primary (you) on top, then
 * p2..p9 — and each non-primary block carries a small slot chip. Single-
 * slot lanes render exactly as before. Drag/resize stay within the
 * segment's own slot row both visually (slot keyed geometry) and logically
 * (the pure ops clamp per slot); same-lane OTHER-slot boundaries join the
 * snap candidates. Double-click creates in the sub-row under the cursor —
 * onCreate(axis, t, slot) carries the slot (null = primary).
 *
 * Playback stays out of React: the playhead node is handed out via
 * `playheadRef` and positioned with style.transform by the player (M0
 * pattern kept); `playheadTimeRef` mirrors the current time for snap /
 * follow logic without re-rendering.
 *
 * Globals: VLTimeline, VLThumbStrip (one Object.assign at the bottom).
 * All top-level identifiers VL_-prefixed (shared global scope).
 * ========================================================================= */

const { useState, useEffect, useRef, useCallback, useMemo, useLayoutEffect } = React;

const VL_FONT_MONO = "'Geist Mono', ui-monospace, monospace";
const VL_FONT_SANS = "'Geist', system-ui, sans-serif";

const VL_RULER_H = 30;     // tick strip + labels
const VL_LANE_H = 56;      // per-lane row height (segment blocks)
const VL_SIDEBAR_W = 110;  // lane title sidebar
const VL_VIEW_PAD = 200;   // px rendered beyond the visible range
const VL_SUG_H = 16;       // suggestion ghost sub-row height (M3)

/* Smallest "nice" step whose px spacing is ≥ ~80 at this zoom. */
function vlTickStep(pxPerSec) {
  const ladder = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600];
  for (const s of ladder) {
    if (s * pxPerSec >= 80) return s;
  }
  return ladder[ladder.length - 1];
}

function vlTickLabel(t, step) {
  const D = window.HomeVideoLabelerData;
  if (step < 1) return t.toFixed(1) + "s";
  return D ? D.fmtDuration(t) : String(Math.round(t));
}

/* Block caption: quality lanes carry value ARRAYS. */
function vlValueText(value) {
  if (Array.isArray(value)) return value.join(" + ").replace(/_/g, " ");
  return String(value == null ? "" : value).replace(/_/g, " ");
}

/* lanes: [{axis, title, segments, multiValue, colorFor(value)}]
 * selection: {kind?: 'canonical'|'suggestion', axis, segId} | null
 * activeAxis: axis string · suggestions: {axis: [SEG]} | null (M3 ghosts)
 * onSeek(t) · onSelect(axis, segId|null) · onCreate(axis, t, slot|null)
 * onSelectSuggestion(axis, segId) — ghost click
 * onCommit(axis, nextSegments, undoEntry) — pointerup only
 * onZoom(dir, anchorT) — Ctrl+wheel / keyboard zoom request */
function VLTimeline({
  duration, fps, lanes, selection, activeAxis, suggestions,
  playheadRef, playheadTimeRef, pxPerSec, snapTimes, minDur, following,
  onSeek, onSelect, onCommit, onCreate, onZoom, onSelectSuggestion,
}) {
  const scrollRef = useRef(null);
  const dragRef = useRef(null);
  const suppressClickRef = useRef(false);
  const pendingZoomRef = useRef(null);
  const lastUserScrollRef = useRef(0);
  const lastAutoScrollRef = useRef(0);
  const viewRafRef = useRef(0);
  const dragSegsRef = useRef(null);
  const [dragSegs, setDragSegs] = useState(null);   // {axis, segments} during a drag
  const [snapFlash, setSnapFlash] = useState(null); // snapped time (s) | null
  const [view, setView] = useState({ left: 0, width: 0 });

  const dur = Math.max(0, Number(duration) || 0);
  const pps = Math.max(0.01, Number(pxPerSec) || 1);
  const minD = Math.max(0.01, Number(minDur) || 0.2);
  const laneList = lanes || [];
  const width = Math.max(view.width || 1, Math.ceil(dur * pps) + 1);
  const height = VL_RULER_H + laneList.length * VL_LANE_H;

  /* everything the once-registered drag/wheel handlers need, render-fresh */
  const liveRef = useRef({});
  liveRef.current = {
    dur, pps, minD, laneList,
    snapTimes: snapTimes || [],
    playheadTimeRef, onSeek, onSelect, onCommit, onCreate, onZoom,
  };

  /* ---------------- scroll / view state (rAF-throttled) ---------------- */

  const syncView = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setView((v) => (v.left === el.scrollLeft && v.width === el.clientWidth)
      ? v : { left: el.scrollLeft, width: el.clientWidth });
  }, []);

  const handleScroll = useCallback(() => {
    if (Date.now() - lastAutoScrollRef.current > 120) lastUserScrollRef.current = Date.now();
    if (viewRafRef.current) return;
    viewRafRef.current = requestAnimationFrame(() => {
      viewRafRef.current = 0;
      syncView();
    });
  }, [syncView]);

  useEffect(() => {
    syncView();
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(syncView);
    ro.observe(el);
    return () => ro.disconnect();
  }, [syncView]);

  useEffect(() => () => {
    if (viewRafRef.current) cancelAnimationFrame(viewRafRef.current);
  }, []);

  /* ---------------- Ctrl+wheel zoom (element-level, non-passive) ------- */

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return undefined;
    const onWheel = (e) => {
      if (!e.ctrlKey) return;
      e.preventDefault(); // suppress Chromium page zoom — needs passive:false
      const live = liveRef.current;
      if (!live.onZoom) return;
      const rect = el.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const anchorT = (el.scrollLeft + x) / live.pps;
      pendingZoomRef.current = { anchorT, anchorX: x };
      live.onZoom(e.deltaY < 0 ? 1 : -1, anchorT);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  /* On pxPerSec change keep the zoom anchor (wheel) or the view center
     (keyboard zoom) stationary. Layout effect: scrollLeft must be set
     before paint or the view visibly jumps. */
  const prevPpsRef = useRef(pps);
  useLayoutEffect(() => {
    const prev = prevPpsRef.current;
    prevPpsRef.current = pps;
    const el = scrollRef.current;
    if (!el || prev === pps) return;
    const pending = pendingZoomRef.current;
    pendingZoomRef.current = null;
    lastAutoScrollRef.current = Date.now();
    if (pending) {
      el.scrollLeft = Math.max(0, pending.anchorT * pps - pending.anchorX);
    } else {
      const centerT = (el.scrollLeft + el.clientWidth / 2) / prev;
      el.scrollLeft = Math.max(0, centerT * pps - el.clientWidth / 2);
    }
    syncView();
  }, [pps, syncView]);

  /* ---------------- auto-follow the playhead while playing ------------- */

  useEffect(() => {
    if (!following) return undefined;
    const iv = setInterval(() => {
      const el = scrollRef.current;
      const live = liveRef.current;
      const tRef = live.playheadTimeRef;
      if (!el || !tRef) return;
      if (Date.now() - lastUserScrollRef.current < 1500) return; // user owns the view
      const x = (tRef.current || 0) * live.pps;
      if (x < el.scrollLeft + 40 || x > el.scrollLeft + el.clientWidth - 80) {
        lastAutoScrollRef.current = Date.now();
        el.scrollLeft = Math.max(0, x - el.clientWidth * 0.15);
      }
    }, 250);
    return () => clearInterval(iv);
  }, [following]);

  /* ---------------- drag mechanics (pointer capture) ------------------- */

  const beginDrag = useCallback((e, lane, seg, mode) => {
    if (e.button !== 0) return;
    const D = window.HomeVideoLabelerData;
    if (!D || dragRef.current) return;
    e.preventDefault();
    e.stopPropagation();
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch (err) { /* */ }
    const live = liveRef.current;
    // snap candidates frozen at drag start: other-lane boundaries + the
    // same lane's OTHER-slot boundaries (align "both watching tv") +
    // keyframes (the live playhead is appended per-move)
    const mySlot = (seg.person_slot || null);
    const cands = [];
    for (const ln of live.laneList) {
      if (ln.axis === lane.axis) {
        for (const s of ln.segments || []) {
          if (((s.person_slot || null)) !== mySlot) cands.push(s.start_s, s.end_s);
        }
        continue;
      }
      for (const s of ln.segments || []) cands.push(s.start_s, s.end_s);
    }
    for (const t of live.snapTimes) cands.push(t);
    dragRef.current = {
      axis: lane.axis,
      segId: seg.id,
      mode, // 'move' | 'start' | 'end'
      pointerId: e.pointerId,
      startX: e.clientX,
      moved: false,
      origStart: seg.start_s,
      origEnd: seg.end_s,
      segments: (lane.segments || []).map((s) => Object.assign({}, s)), // pre-drag snapshot
      cands,
      pps: live.pps,             // zoom frozen for the drag
      snapTol: 6 / live.pps,     // px-constant tolerance in seconds
    };
    if (live.onSelect) live.onSelect(lane.axis, seg.id);
  }, []);

  useEffect(() => {
    const onMove = (e) => {
      const drag = dragRef.current;
      const D = window.HomeVideoLabelerData;
      if (!drag || !D || e.pointerId !== drag.pointerId) return;
      const dx = e.clientX - drag.startX;
      if (Math.abs(dx) > 3) drag.moved = true;
      if (!drag.moved) return;
      const live = liveRef.current;
      const dt = dx / drag.pps;
      let snapT = null;
      let next;
      const cands = e.altKey
        ? null // Alt disables snapping
        : drag.cands.concat([(live.playheadTimeRef && live.playheadTimeRef.current) || 0]);
      if (drag.mode === "move") {
        let target = drag.origStart + dt;
        const len = drag.origEnd - drag.origStart;
        if (cands) {
          const s1 = D.findSnap(cands, target, drag.snapTol);
          const s2 = D.findSnap(cands, target + len, drag.snapTol);
          const d1 = s1 == null ? Infinity : Math.abs(s1 - target);
          const d2 = s2 == null ? Infinity : Math.abs(s2 - (target + len));
          if (s1 != null && d1 <= d2) { target = s1; snapT = s1; }
          else if (s2 != null) { target = s2 - len; snapT = s2; }
        }
        next = D.moveSegment(drag.segments, drag.segId, target, { duration: live.dur });
      } else {
        const edge = drag.mode === "start" ? "start" : "end";
        let target = (edge === "start" ? drag.origStart : drag.origEnd) + dt;
        if (cands) {
          const s = D.findSnap(cands, target, drag.snapTol);
          if (s != null) { target = s; snapT = s; }
        }
        next = D.resizeSegment(drag.segments, drag.segId, edge, target, {
          duration: live.dur, minDur: live.minD, clipNeighbors: true,
        });
      }
      dragSegsRef.current = { axis: drag.axis, segments: next };
      setDragSegs(dragSegsRef.current);
      setSnapFlash(snapT);
    };
    const onUp = (e) => {
      const drag = dragRef.current;
      if (!drag || e.pointerId !== drag.pointerId) return;
      dragRef.current = null;
      setSnapFlash(null);
      const cur = dragSegsRef.current;
      dragSegsRef.current = null;
      if (drag.moved) {
        suppressClickRef.current = true;
        window.setTimeout(() => { suppressClickRef.current = false; }, 0);
        const live = liveRef.current;
        if (cur && live.onCommit) {
          live.onCommit(cur.axis, cur.segments, {
            axis: cur.axis, before: drag.segments, after: cur.segments,
          });
        }
      }
      setDragSegs(null);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, []);

  /* ---------------- clicks (seek / select / create) -------------------- */

  const timeFromContainer = useCallback((e) => {
    const el = scrollRef.current;
    if (!el) return 0;
    const rect = el.getBoundingClientRect();
    return Math.max(0, Math.min(dur, (e.clientX - rect.left + el.scrollLeft) / pps));
  }, [dur, pps]);

  /* container catches ruler + any space not claimed by a lane row */
  const handleContainerClick = useCallback((e) => {
    if (suppressClickRef.current || !(dur > 0)) return;
    if (onSeek) onSeek(timeFromContainer(e));
  }, [dur, onSeek, timeFromContainer]);

  /* ---------------- render ---------------- */

  const ticks = useMemo(() => {
    const out = { step: 1, major: [], minor: [] };
    if (!(dur > 0)) return out;
    const step = vlTickStep(pps);
    out.step = step;
    for (let t = 0; t <= dur + 1e-6; t += step) out.major.push(Math.min(t, dur));
    const sub = step / 5;
    if (sub * pps >= 9) {
      for (let t = sub; t < dur; t += sub) {
        const r = t / step;
        if (Math.abs(r - Math.round(r)) < 1e-6) continue;
        out.minor.push(t);
      }
    }
    return out;
  }, [dur, pps]);

  // visible time range (+pad) — everything outside is culled
  const viewT0 = Math.max(0, (view.left - VL_VIEW_PAD) / pps);
  const viewT1 = ((view.left + (view.width || 4000)) + VL_VIEW_PAD) / pps;

  return (
    <div style={{
      display: "flex", flex: "none", minWidth: 0,
      borderTop: "1px solid var(--hg-border-soft)",
      background: "var(--hg-bg-0)",
    }}>
      <style>{`
        @keyframes vl-snap-flash {
          0% { opacity: 1; }
          100% { opacity: 0.55; }
        }
      `}</style>

      {/* lane sidebar — titles + active-axis marker */}
      {laneList.length > 0 && (
        <div style={{
          width: VL_SIDEBAR_W, flex: "none",
          borderRight: "1px solid var(--hg-border-soft)",
        }}>
          <div style={{
            height: VL_RULER_H, borderBottom: "1px solid var(--hg-border-soft)",
            display: "flex", alignItems: "center", paddingLeft: 10,
            fontFamily: VL_FONT_MONO, fontSize: 8, letterSpacing: "0.22em",
            color: "var(--hg-fg-5)", textTransform: "lowercase",
          }}>lanes</div>
          {laneList.map((lane) => {
            const active = lane.axis === activeAxis;
            return (
              <button
                key={lane.axis}
                type="button"
                className="hg-focusable"
                onClick={() => onSelect && onSelect(lane.axis, null)}
                title={"activate " + (lane.title || lane.axis) + " lane ([ / ] cycles)"}
                style={{
                  width: "100%", height: VL_LANE_H, boxSizing: "border-box",
                  border: "none", font: "inherit", color: "inherit", textAlign: "inherit",
                  borderBottom: "1px solid var(--hg-border-soft)",
                  borderLeft: active ? "2px solid var(--hg-ice)" : "2px solid transparent",
                  background: active ? "var(--hg-bg-1)" : "transparent",
                  display: "flex", flexDirection: "column", justifyContent: "center",
                  padding: "0 8px 0 10px", cursor: "pointer",
                }}
              >
                <span style={{
                  fontFamily: VL_FONT_MONO, fontSize: 9.5, letterSpacing: "0.16em",
                  textTransform: "lowercase",
                  color: active ? "var(--hg-fg-1)" : "var(--hg-fg-4)",
                }}>{lane.title || lane.axis}</span>
                <span style={{
                  fontFamily: VL_FONT_MONO, fontSize: 8, color: "var(--hg-fg-5)",
                  marginTop: 3, letterSpacing: "0.08em",
                }}>
                  {((dragSegs && dragSegs.axis === lane.axis ? dragSegs.segments : lane.segments) || []).length}
                  {" seg"}{lane.multiValue ? " · multi" : ""}
                  {((suggestions && suggestions[lane.axis]) || []).length > 0
                    ? " · " + suggestions[lane.axis].length + " sug" : ""}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {/* scrollable track area */}
      <div
        ref={scrollRef}
        className="hg-scroll"
        onScroll={handleScroll}
        onClick={handleContainerClick}
        style={{
          position: "relative", overflowX: "auto", overflowY: "hidden",
          flex: 1, minWidth: 0, cursor: "crosshair",
        }}
      >
        <div style={{ position: "relative", width, height }}>
          {/* ruler */}
          <div style={{
            position: "absolute", left: 0, top: 0, width: "100%", height: VL_RULER_H,
            borderBottom: "1px solid var(--hg-border-soft)",
          }}>
            {ticks.minor.map((t) => (t >= viewT0 && t <= viewT1) && (
              <span key={"m" + t.toFixed(3)} style={{
                position: "absolute", left: t * pps, bottom: 0,
                width: 1, height: 5, background: "var(--hg-fg-5)", opacity: 0.55,
              }} />
            ))}
            {ticks.major.map((t) => (t >= viewT0 && t <= viewT1) && (
              <span key={"M" + t.toFixed(3)}>
                <span style={{
                  position: "absolute", left: t * pps, bottom: 0,
                  width: 1, height: 9, background: "var(--hg-fg-4)",
                }} />
                <span style={{
                  position: "absolute", left: t * pps + 4, top: 4,
                  fontFamily: VL_FONT_MONO, fontSize: 8.5,
                  letterSpacing: "0.1em", color: "var(--hg-fg-4)",
                  whiteSpace: "nowrap",
                }}>{vlTickLabel(t, ticks.step)}</span>
              </span>
            ))}
          </div>

          {/* lane rows + segment blocks */}
          {laneList.map((lane, i) => {
            const segs = (dragSegs && dragSegs.axis === lane.axis)
              ? dragSegs.segments
              : (lane.segments || []);
            const sugs = (suggestions && suggestions[lane.axis]) || [];
            const hasSug = sugs.length > 0;
            /* person-slot sub-rows: divide the block area when the lane
               holds >1 slot (primary on top, p2.. below); single-slot
               lanes keep the exact M0/M1 geometry */
            const D0 = window.HomeVideoLabelerData;
            const slotList = (D0 && D0.laneSlots) ? D0.laneSlots(segs) : [null];
            const multiSlot = slotList.length > 1;
            const areaTop = 5;
            const areaH = hasSug ? VL_LANE_H - VL_SUG_H - 13 : VL_LANE_H - 11;
            const subH = multiSlot ? areaH / slotList.length : areaH;
            return (
              <div
                key={lane.axis}
                onClick={(e) => {
                  e.stopPropagation();
                  if (suppressClickRef.current || !(dur > 0)) return;
                  const rect = e.currentTarget.getBoundingClientRect();
                  const t = Math.max(0, Math.min(dur, (e.clientX - rect.left) / pps));
                  if (onSelect) onSelect(lane.axis, null);
                  if (onSeek) onSeek(t);
                }}
                onDoubleClick={(e) => {
                  e.stopPropagation();
                  if (!(dur > 0) || !onCreate) return;
                  const rect = e.currentTarget.getBoundingClientRect();
                  const t = Math.max(0, Math.min(dur, (e.clientX - rect.left) / pps));
                  /* create in the sub-row under the cursor (null = primary) */
                  let slot = null;
                  if (multiSlot) {
                    const idx = Math.floor((e.clientY - rect.top - areaTop) / subH);
                    slot = slotList[Math.max(0, Math.min(slotList.length - 1, idx))] || null;
                  }
                  onCreate(lane.axis, t, slot);
                }}
                style={{
                  position: "absolute", left: 0, width: "100%",
                  top: VL_RULER_H + i * VL_LANE_H, height: VL_LANE_H,
                  boxSizing: "border-box",
                  borderBottom: "1px solid var(--hg-border-soft)",
                  background: lane.axis === activeAxis
                    ? "color-mix(in srgb, var(--hg-ice) 3%, transparent)"
                    : "transparent",
                }}
              >
                {segs.map((seg) => {
                  if (seg.end_s < viewT0 || seg.start_s > viewT1) return null; // virtualized
                  const color = lane.colorFor ? lane.colorFor(seg.value) : "hsl(0 0% 55%)";
                  const selected = !!(selection
                    && (selection.kind == null || selection.kind === "canonical")
                    && selection.axis === lane.axis && selection.segId === seg.id);
                  const st = seg.review_state;
                  const ghosted = st === "rejected";
                  const excluded = st === "excluded_from_export";
                  const dashed = st === "prelabel";
                  const w = Math.max(3, (seg.end_s - seg.start_s) * pps);
                  const showHandles = w > 18;
                  const slot = (seg.person_slot || null);
                  const slotIdx = multiSlot ? Math.max(0, slotList.indexOf(slot)) : 0;
                  const slotChip = slot != null
                    ? (String(slot).length > 6 ? String(slot).slice(0, 5) + "…" : String(slot))
                    : null;
                  return (
                    <div
                      key={seg.id}
                      onPointerDown={(e) => beginDrag(e, lane, seg, "move")}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (suppressClickRef.current) return;
                        if (onSelect) onSelect(lane.axis, seg.id);
                      }}
                      onDoubleClick={(e) => e.stopPropagation()}
                      title={vlValueText(seg.value) + " · " + (st || "?")
                        + " · " + seg.start_s.toFixed(2) + "–" + seg.end_s.toFixed(2) + "s"
                        + (seg.source ? " · " + seg.source : "")
                        + ((multiSlot || slot) ? " · " + (slot || "you") : "")}
                      style={{
                        position: "absolute",
                        left: seg.start_s * pps,
                        width: w,
                        top: areaTop + slotIdx * subH,
                        height: multiSlot ? Math.max(8, subH - 2) : areaH,
                        boxSizing: "border-box",
                        border: selected
                          ? "1px solid var(--hg-fg-0)"
                          : (dashed ? "1px dashed " + color : "1px solid " + color),
                        boxShadow: selected ? "0 0 0 1px " + color : "none",
                        background: "color-mix(in srgb, " + color + " 32%, rgba(0,0,0,0.6))",
                        opacity: ghosted ? 0.35 : excluded ? 0.45 : 1,
                        color: "var(--hg-fg-0)",
                        display: "flex", alignItems: "stretch",
                        overflow: "hidden",
                        cursor: "grab", touchAction: "none",
                        zIndex: selected ? 3 : 2,
                        fontFamily: VL_FONT_MONO, fontSize: multiSlot ? 8.5 : 9.5,
                      }}
                    >
                      {showHandles && (
                        <div
                          onPointerDown={(e) => beginDrag(e, lane, seg, "start")}
                          title="resize start"
                          style={{
                            flex: "none", width: 7, cursor: "ew-resize",
                            borderRight: "1px solid rgba(255,255,255,0.14)",
                            touchAction: "none",
                          }}
                        />
                      )}
                      <span style={{
                        flex: 1, minWidth: 0, alignSelf: "center", padding: "0 5px",
                        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                        letterSpacing: "0.06em",
                        textDecoration: ghosted ? "line-through" : "none",
                        pointerEvents: "none",
                      }}>
                        {slotChip != null && (
                          <span style={{
                            display: "inline-block",
                            border: "1px solid rgba(255,255,255,0.45)",
                            padding: "0 3px", marginRight: 4,
                            fontSize: 7.5, lineHeight: "10px",
                            letterSpacing: "0.06em", verticalAlign: "middle",
                            opacity: 0.95,
                          }}>{slotChip}</span>
                        )}
                        {st === "accepted" ? "✓ " : ""}{vlValueText(seg.value) || "—"}
                      </span>
                      {showHandles && (
                        <div
                          onPointerDown={(e) => beginDrag(e, lane, seg, "end")}
                          title="resize end"
                          style={{
                            flex: "none", width: 7, cursor: "ew-resize",
                            borderLeft: "1px solid rgba(255,255,255,0.14)",
                            touchAction: "none",
                          }}
                        />
                      )}
                    </div>
                  );
                })}

                {/* suggestion ghost sub-row (M3) — prelabels, click-to-select
                    only; 4s-stride overlaps stack at reduced opacity */}
                {sugs.map((sg) => {
                  if (sg.end_s < viewT0 || sg.start_s > viewT1) return null; // virtualized
                  const D = window.HomeVideoLabelerData;
                  const color = lane.colorFor ? lane.colorFor(sg.value) : "hsl(0 0% 55%)";
                  const selectedS = !!(selection && selection.kind === "suggestion"
                    && selection.axis === lane.axis && selection.segId === sg.id);
                  const conf = typeof sg.confidence === "number"
                    ? Math.max(0, Math.min(1, sg.confidence)) : null;
                  const disagree = D && D.suggestionDisagreement(sg) != null;
                  const outlier = sg._outlier === true; // M4: cluster-outlier window
                  const w = Math.max(3, (sg.end_s - sg.start_s) * pps);
                  const hatch = "color-mix(in srgb, " + color + " 26%, transparent)";
                  return (
                    <div
                      key={"sug_" + sg.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (suppressClickRef.current) return;
                        if (onSelectSuggestion) onSelectSuggestion(lane.axis, sg.id);
                      }}
                      onDoubleClick={(e) => e.stopPropagation()}
                      title={"suggestion · " + vlValueText(sg.value)
                        + (conf != null ? " · conf " + Math.round(conf * 100) + "%" : "")
                        + (sg.source ? " · " + sg.source : "")
                        + (disagree ? " · disagreement!" : "")
                        + (outlier ? " · cluster outlier" : "")
                        + " · " + sg.start_s.toFixed(2) + "–" + sg.end_s.toFixed(2) + "s"}
                      style={{
                        position: "absolute",
                        left: sg.start_s * pps,
                        width: w,
                        top: VL_LANE_H - VL_SUG_H - 4, height: VL_SUG_H,
                        boxSizing: "border-box",
                        border: selectedS ? "1px solid var(--hg-fg-0)" : "1px dashed " + color,
                        boxShadow: selectedS ? "0 0 0 1px " + color : "none",
                        background: "repeating-linear-gradient(45deg, " + hatch
                          + " 0px, " + hatch + " 3px, transparent 3px, transparent 7px)",
                        opacity: selectedS ? 1 : 0.8,
                        cursor: "pointer",
                        overflow: "hidden",
                        zIndex: selectedS ? 3 : 1,
                        fontFamily: VL_FONT_MONO, fontSize: 8,
                      }}
                    >
                      <span style={{
                        position: "absolute", left: 3, top: 1,
                        right: (disagree && outlier) ? 23 : (disagree || outlier) ? 12 : 3,
                        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                        color: "var(--hg-fg-0)", opacity: 0.55,
                        letterSpacing: "0.05em", pointerEvents: "none",
                      }}>{vlValueText(sg.value) || "—"}</span>
                      {conf != null && (
                        <span style={{
                          position: "absolute", left: 0, bottom: 0, height: 2,
                          width: (conf * 100) + "%",
                          background: "color-mix(in srgb, var(--hg-ice) "
                            + Math.round(conf * 100) + "%, var(--hg-fg-4))",
                          pointerEvents: "none",
                        }} />
                      )}
                      {disagree && (
                        <span style={{
                          position: "absolute", right: 3, top: 3, width: 7, height: 7,
                          background: "var(--hg-warn)", transform: "rotate(45deg)",
                          display: "flex", alignItems: "center", justifyContent: "center",
                          pointerEvents: "none",
                        }}>
                          <span style={{
                            transform: "rotate(-45deg)", fontSize: 6, lineHeight: 1,
                            color: "#000", fontWeight: 700,
                          }}>!</span>
                        </span>
                      )}
                      {/* M4 — hollow dot: this suggestion's analysis window is
                          a cluster outlier (sits beside the diamond) */}
                      {outlier && (
                        <span style={{
                          position: "absolute", right: disagree ? 14 : 3, top: 3,
                          width: 7, height: 7, boxSizing: "border-box",
                          border: "1.5px solid var(--hg-crit)", borderRadius: "50%",
                          background: "transparent", pointerEvents: "none",
                        }} />
                      )}
                    </div>
                  );
                })}
              </div>
            );
          })}

          {/* snap flash line (re-keyed per snap value to retrigger) */}
          {snapFlash != null && (
            <div key={"snap" + snapFlash.toFixed(4)} style={{
              position: "absolute", left: snapFlash * pps - 0.5, top: 0, bottom: 0,
              width: 1, background: "var(--hg-ice-bright)",
              boxShadow: "0 0 8px var(--hg-ice-glow)",
              pointerEvents: "none", zIndex: 5,
              animation: "vl-snap-flash 260ms ease-out forwards",
            }} />
          )}

          {/* playhead — positioned imperatively by the player via transform */}
          <div
            ref={(node) => { if (playheadRef) playheadRef.current = node; }}
            style={{
              position: "absolute", left: 0, top: 0, bottom: 0, width: 1,
              background: "var(--hg-ice)", boxShadow: "0 0 6px var(--hg-ice-glow)",
              pointerEvents: "none", willChange: "transform", zIndex: 6,
            }}
          />
        </div>
      </div>
    </div>
  );
}

/* Sprite filmstrip. `manifest` = {tile_w, tile_h, cols, interval_s, count,
 * sheets:[url,...]} from GET /videos/{id}/sprite. Tiles are fixed-height
 * crops of the sheet grid (background-position math); clicking a tile
 * seeks to the center of its interval. Renders a quiet placeholder while
 * the thumbnail job hasn't produced a manifest yet. */
function VLThumbStrip({ video, manifest, onSeek }) {
  const D = window.HomeVideoLabelerData;
  const ready = !!(manifest && manifest.sheets && manifest.sheets.length &&
                   manifest.count > 0 && manifest.tile_w > 0 && manifest.tile_h > 0 &&
                   manifest.cols > 0);

  if (!ready) {
    return (
      <div style={{
        height: 54, display: "flex", alignItems: "center", justifyContent: "center",
        borderTop: "1px solid var(--hg-border-soft)",
        fontFamily: VL_FONT_MONO, fontSize: 9.5, letterSpacing: "0.14em",
        color: "var(--hg-fg-5)", textTransform: "lowercase", flex: "none",
      }}>no sprite sheet yet — thumbnail job pending</div>
    );
  }

  const tileH = 54;
  const scale = tileH / manifest.tile_h;
  const tileW = Math.max(8, Math.round(manifest.tile_w * scale));
  const perSheet = Math.ceil(manifest.count / manifest.sheets.length);
  const interval = manifest.interval_s > 0 ? manifest.interval_s : 1;
  const tiles = [];
  for (let i = 0; i < manifest.count; i++) {
    const sheetIdx = Math.min(manifest.sheets.length - 1, Math.floor(i / perSheet));
    const idx = i % perSheet;
    const col = idx % manifest.cols;
    const row = Math.floor(idx / manifest.cols);
    const url = D ? D.spriteSheetUrl(video && video.id, manifest.sheets[sheetIdx]) : null;
    tiles.push({ i, url, col, row });
  }

  return (
    <div
      className="hg-scroll"
      style={{
        display: "flex", overflowX: "auto", overflowY: "hidden",
        borderTop: "1px solid var(--hg-border-soft)",
        background: "var(--hg-bg-0)", flex: "none",
      }}
    >
      {tiles.map((tile) => (
        <div
          key={tile.i}
          tabIndex={0}
          role="button"
          aria-label={"seek to " + (D ? D.fmtDuration(tile.i * interval) : tile.i * interval + "s")}
          title={D ? D.fmtDuration(tile.i * interval) : ""}
          onClick={() => onSeek && onSeek(tile.i * interval + interval / 2)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              if (e.key === " ") e.preventDefault();
              if (onSeek) onSeek(tile.i * interval + interval / 2);
            }
          }}
          style={{
            flex: "none", width: tileW, height: tileH, cursor: "pointer",
            backgroundImage: tile.url ? "url(" + JSON.stringify(tile.url) + ")" : "none",
            backgroundSize: (manifest.cols * tileW) + "px auto",
            backgroundPosition: (-tile.col * tileW) + "px " + (-tile.row * tileH) + "px",
            backgroundColor: "var(--hg-bg-2)",
            borderRight: "1px solid rgba(0,0,0,0.55)",
          }}
        />
      ))}
    </div>
  );
}

Object.assign(window, { VLTimeline, VLThumbStrip });
