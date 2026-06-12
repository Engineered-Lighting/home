/* ============================================================================
 * home-video-labeler.jsx — the /labeler full-screen video timeline labeler.
 *
 * M0: overlay shell (HomePeopleOverlay template), maximize-on-open /
 * restore-on-close (home-apartment.jsx pattern), video list + manual
 * import, proxy playback with custom transport + frame-rate timecode,
 * VLTimeline + VLThumbStrip wired to currentTime/seek, and a jobs tab.
 *
 * M1 adds the segment editor (VLEditor): useReducer doc state mirroring
 * the server shape (per-axis lanes activity_primary/posture/quality/custom;
 * quality holds value ARRAYS), axis-scoped undo/redo (cap 50, pushed on
 * pointerup-commit only), pessimistic PUT save with revision + 409
 * reload/overwrite banner + tmp-id remap via id_map, debounced 1s draft
 * autosave with id-validated restore banner, VLLabelPicker (autocomplete +
 * number-key palette + inline custom-label create), VLInspector (value /
 * frame-stepped bounds / review actions / aux form / custom-label
 * manager), and the full keyboard map — every handler behind the
 * input-focus guard. Degrades gracefully: getLabels 404 ⇒ empty doc rev 0,
 * save disabled, "labels API not deployed" chip.
 *
 * M3 adds the review UX: suggestion ghost-lanes (GET /suggestions, the
 * prelabel layer — loaded per selected video, 404 ⇒ quiet "not deployed"
 * chip), a selection model extended with kind: 'canonical'|'suggestion',
 * suggestion mode in the inspector (value/conf/source/evidence + accept/
 * reject/needs-review, A/R/N/X keys; Shift+A accepts every suggestion
 * overlapping the selection across all axes), the evidence keyframe strip
 * (window id derived as aw_<video>_<start_ms> from the suggestion's
 * geometry), left-rail queue filter chips over client-cached per-video
 * suggestion stats, and the calibration-locked bulk-accept button.
 * Accepting PROJECTS the suggestion into the canonical lane: the server
 * graduates the row in place, and the client carves overlapping canonical
 * segments locally (PUT enforces per-lane non-overlap) — a carve marks the
 * doc dirty so the next save reconciles the trimmed neighbors.
 *
 * Sim containment: the overlay renders an explicit "unavailable in
 * simulation mode" state BEFORE any media element exists — <video src>
 * and sprite background-images bypass tauriFetch's sim guard, so the
 * gate lives here AND in vlBase() (null base under __SIM_ACTIVE).
 *
 * Global: HomeVideoLabelerOverlay. Service must be optional: every fetch
 * is no-throw ({ok,...}); the app boots and the overlay renders offline
 * chips with the box down.
 * ========================================================================= */

const { useState, useEffect, useRef, useCallback, useMemo, useReducer } = React;

/* shared with home-video-labeler-timeline.jsx (same values; var-safe) */
const VL_FONT_MONO = "'Geist Mono', ui-monospace, monospace";
const VL_FONT_SANS = "'Geist', system-ui, sans-serif";

const VL_BTN = {
  background: "transparent", border: "1px solid var(--hg-border-soft)",
  color: "var(--hg-fg-2)", padding: "4px 11px",
  fontFamily: VL_FONT_MONO, fontSize: 11, letterSpacing: "0.12em",
  cursor: "pointer", textTransform: "lowercase",
};

const VL_BTN_SM = {
  ...VL_BTN, padding: "3px 9px", fontSize: 10, color: "var(--hg-fg-3)",
};

/* tiny bordered status chip (import_status / proxy / holdout …) */
function VLChip({ label, tone, title }) {
  const color = tone === "live" ? "var(--hg-ice)"
    : tone === "warn" ? "var(--hg-warn)"
    : tone === "crit" ? "var(--hg-crit)"
    : tone === "dim" ? "var(--hg-fg-5)"
    : "var(--hg-fg-3)";
  return (
    <span title={title} style={{
      display: "inline-flex", alignItems: "center",
      border: "1px solid var(--hg-border-soft)", color,
      padding: "1px 6px", fontFamily: VL_FONT_MONO, fontSize: 8.5,
      letterSpacing: "0.12em", textTransform: "lowercase", whiteSpace: "nowrap",
    }}>{label}</span>
  );
}

/* ─────────────────────────────────────────────────────────────────────
 * VLVideoRow — left-rail entry: filename, duration, status chips.
 * `sug` (M3): client-cached suggestion stats {total, lowConf, disagreement}
 * — only present once the video has been opened this session.
 * ──────────────────────────────────────────────────────────────────── */
function VLVideoRow({ video, selected, onClick, sug }) {
  const D = window.HomeVideoLabelerData;
  const st = video.import_status || "unknown";
  const stTone = st === "ready" ? "live" : (st === "failed" || st === "error") ? "crit" : "warn";
  return (
    <button
      onClick={onClick}
      className="hg-focusable"
      style={{
        display: "block", width: "100%", textAlign: "left",
        background: selected ? "var(--hg-bg-2)" : "transparent",
        border: "none",
        borderLeft: selected ? "2px solid var(--hg-ice)" : "2px solid transparent",
        borderBottom: "1px solid var(--hg-border-soft)",
        padding: "8px 12px", cursor: "pointer", fontFamily: VL_FONT_MONO,
      }}
    >
      <div style={{
        color: selected ? "var(--hg-fg-0)" : "var(--hg-fg-1)", fontSize: 11,
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>{video.filename}</div>
      <div style={{ display: "flex", gap: 5, marginTop: 5, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: 9.5, color: "var(--hg-fg-4)", letterSpacing: "0.08em" }}>
          {D.fmtDuration(video.duration_s)}
        </span>
        <VLChip label={st} tone={stTone} title={"import status: " + st} />
        <VLChip label={video.has_proxy ? "proxy" : "no proxy"} tone={video.has_proxy ? "live" : "dim"} />
        {video.has_sprite && <VLChip label="sprite" tone="default" />}
        {video.is_holdout && <VLChip label="holdout" tone="warn" title="blind holdout — excluded from review/training" />}
        {sug && sug.total > 0 && (
          <VLChip
            label={sug.total + " sug"}
            tone="warn"
            title={sug.lowConf + " low-conf · " + sug.disagreement + " disagreement"}
          />
        )}
        {sug && sug.disagreement > 0 && (
          <VLChip label={"! " + sug.disagreement} tone="crit" title="suggestions with a disagreement cross-check fired" />
        )}
      </div>
    </button>
  );
}

/* ─────────────────────────────────────────────────────────────────────
 * VLPlayer — proxy <video> + custom transport + frame-rate timecode +
 * timeline/filmstrip. React stays out of the playback hot path: a
 * requestVideoFrameCallback chain writes the timecode text + playhead
 * transform straight into the DOM, with a 4Hz interval as the fallback
 * (no rVFC / paused seeks).
 *
 * M1: takes an optional `editor` bundle from VLEditor — lanes, selection,
 * commit/select/create callbacks, the number-key palette (rendered as a
 * chip row above the timeline) and a transportRef it populates with the
 * imperative API the overlay keyboard map drives (getTime/seek/stepFrame/
 * zoom…). Zoom state lives here: pxPerSec = fit-to-width or a continuous
 * zoom level in [fit … 240 px/s]; VLTimeline calls onZoom (Ctrl+wheel)
 * and keeps the anchor stationary itself. Without `editor` the player
 * behaves exactly like M0 (plain ruler + playhead).
 * ──────────────────────────────────────────────────────────────────── */
function VLPlayer({ video, editor }) {
  const D = window.HomeVideoLabelerData;
  const videoRef = useRef(null);
  const timecodeRef = useRef(null);
  const playheadRef = useRef(null);
  const playheadTimeRef = useRef(0);
  const wrapRef = useRef(null);
  const pxPerSecRef = useRef(1);
  const fitPpsRef = useRef(1);
  const editorRef = useRef(editor);
  editorRef.current = editor;
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [manifest, setManifest] = useState(null);
  const [fitWidth, setFitWidth] = useState(0);
  const [metaDur, setMetaDur] = useState(0);
  const [mediaErr, setMediaErr] = useState(false);
  const [zoomPps, setZoomPps] = useState(null); // null = fit-to-width

  const fps = (video && video.fps) || 30;
  const duration = (video && video.duration_s) || metaDur || 0;
  const src = D.streamUrl(video.id);

  /* sprite manifest — via the API client, never a bare media fetch */
  useEffect(() => {
    let dead = false;
    (async () => {
      const r = await D.getSprite(video.id);
      if (!dead && r.ok && r.data) setManifest(r.data);
    })();
    return () => { dead = true; };
  }, [video.id]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const measure = () => setFitWidth(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  /* the editor timeline reserves VL_SIDEBAR_W+1px for the lane sidebar */
  const trackW = Math.max(80, fitWidth - (editor ? VL_SIDEBAR_W + 1 : 0));
  const fitPps = duration > 0 && trackW > 0 ? trackW / duration : 1;
  fitPpsRef.current = fitPps;
  const pxPerSec = (editor && zoomPps != null)
    ? Math.min(240, Math.max(fitPps, zoomPps))
    : fitPps;
  pxPerSecRef.current = pxPerSec;

  const syncNow = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    const t = v.currentTime || 0;
    playheadTimeRef.current = t;
    if (timecodeRef.current) timecodeRef.current.textContent = D.fmtTimecode(t, fps);
    if (playheadRef.current) {
      playheadRef.current.style.transform = "translateX(" + (t * pxPerSecRef.current) + "px)";
    }
  }, [fps]);

  /* reposition the playhead immediately on zoom (don't wait for the 4Hz tick) */
  useEffect(() => { syncNow(); }, [pxPerSec, syncNow]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return undefined;
    let alive = true;
    let handle = null;
    const hasRVFC = typeof v.requestVideoFrameCallback === "function";
    const tick = () => {
      if (!alive) return;
      syncNow();
      handle = v.requestVideoFrameCallback(tick);
    };
    if (hasRVFC) handle = v.requestVideoFrameCallback(tick);
    // 4Hz fallback: covers paused seeks, pre-play, and no-rVFC engines
    const iv = setInterval(syncNow, 250);
    return () => {
      alive = false;
      if (hasRVFC && handle != null && typeof v.cancelVideoFrameCallback === "function") {
        try { v.cancelVideoFrameCallback(handle); } catch (e) { /* */ }
      }
      clearInterval(iv);
    };
    // mediaErr in deps: a retry remounts the <video>, so the rVFC chain
    // must re-bind to the new element (the 4Hz interval alone would
    // otherwise carry playback updates after a recovery)
  }, [syncNow, mediaErr]);

  const seekTo = useCallback((t) => {
    const v = videoRef.current;
    if (!v) return;
    const max = duration || v.duration || 0;
    v.currentTime = Math.max(0, Math.min(max, t));
    syncNow();
  }, [duration, syncNow]);

  const skip = useCallback((dt) => {
    const v = videoRef.current;
    if (v) seekTo((v.currentTime || 0) + dt);
  }, [seekTo]);

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play().catch(() => setMediaErr(true));
    else v.pause();
  }, []);

  const pickRate = useCallback((r) => {
    const v = videoRef.current;
    if (v) v.playbackRate = r;
    setRate(r);
  }, []);

  /* frame step per the plan: currentTime = (n±1 + 0.5)/fps */
  const stepFrame = useCallback((n) => {
    const v = videoRef.current;
    if (!v) return;
    const idx = Math.round((v.currentTime || 0) * fps - 0.5) + n;
    seekTo((idx + 0.5) / fps);
  }, [fps, seekTo]);

  /* continuous zoom: ×1.35 per step, clamped [fit … 240 px/s]; at/below
     fit collapses back to null so window resizes keep fitting. */
  const zoomBy = useCallback((dir) => {
    setZoomPps((prev) => {
      const cur = prev == null ? fitPpsRef.current : prev;
      const next = cur * (dir > 0 ? 1.35 : 1 / 1.35);
      if (next <= fitPpsRef.current * 1.001) return null;
      return Math.min(240, next);
    });
  }, []);
  const zoomFit = useCallback(() => setZoomPps(null), []);

  /* imperative transport for the editor's keyboard map */
  useEffect(() => {
    const tr = editor && editor.transportRef;
    if (!tr) return undefined;
    tr.current = {
      getTime: () => (videoRef.current && videoRef.current.currentTime) || 0,
      getDuration: () => duration,
      getFps: () => fps,
      isPlaying: () => !!(videoRef.current && !videoRef.current.paused),
      seekTo, skip, stepFrame, togglePlay, zoomBy, zoomFit,
    };
  });
  useEffect(() => () => {
    const ed = editorRef.current;
    if (ed && ed.transportRef) ed.transportRef.current = null;
  }, []);

  /* sprite keyframe instants — snap candidates for the timeline */
  const keyframeTimes = useMemo(() => {
    if (!manifest || !(manifest.interval_s > 0) || !(manifest.count > 0)) return [];
    const out = [];
    for (let i = 0; i < manifest.count; i++) out.push(i * manifest.interval_s);
    return out;
  }, [manifest]);

  const zoomed = editor && zoomPps != null;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minWidth: 0 }}>
      {/* video surface */}
      <div style={{ flex: 1, minHeight: 0, background: "#000", position: "relative" }}>
        {!mediaErr ? (
          <video
            ref={videoRef}
            src={src}
            controls={false}
            playsInline
            preload="metadata"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onLoadedMetadata={(e) => setMetaDur(e.currentTarget.duration || 0)}
            onError={() => setMediaErr(true)}
            style={{ width: "100%", height: "100%", objectFit: "contain", display: "block" }}
          />
        ) : (
          <div style={{
            position: "absolute", inset: 0, display: "flex",
            flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8,
            fontFamily: VL_FONT_MONO, color: "var(--hg-fg-4)", fontSize: 11,
            letterSpacing: "0.1em",
          }}>
            <span style={{ color: "var(--hg-warn)" }}>stream unavailable</span>
            <span style={{ fontSize: 9.5, color: "var(--hg-fg-5)" }}>{src}</span>
            <button style={VL_BTN_SM} onClick={() => {
              setMediaErr(false);
              // remount happens via key on src change; nudge a reload here
              const v = videoRef.current;
              if (v) { try { v.load(); } catch (e) { /* */ } }
            }}>retry</button>
          </div>
        )}
      </div>

      {/* transport */}
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        padding: "8px 12px", borderTop: "1px solid var(--hg-border-soft)",
        flex: "none",
      }}>
        <button style={{ ...VL_BTN_SM, minWidth: 52, textAlign: "center", color: "var(--hg-fg-1)" }}
          onClick={togglePlay}>{playing ? "pause" : "play"}</button>
        <button style={VL_BTN_SM} onClick={() => skip(-10)}>-10s</button>
        <button style={VL_BTN_SM} onClick={() => skip(-1)}>-1s</button>
        <button style={VL_BTN_SM} onClick={() => skip(1)}>+1s</button>
        <button style={VL_BTN_SM} onClick={() => skip(10)}>+10s</button>
        <span style={{ width: 10 }} />
        {[0.5, 1, 1.5, 2].map((r) => (
          <button
            key={r}
            onClick={() => pickRate(r)}
            style={{
              ...VL_BTN_SM,
              color: rate === r ? "var(--hg-ice)" : "var(--hg-fg-4)",
              borderColor: rate === r ? "var(--hg-ice)" : "var(--hg-border-soft)",
            }}
          >{r}×</button>
        ))}
        {editor && (
          <span style={{ display: "inline-flex", gap: 4, marginLeft: 10 }}>
            <button style={VL_BTN_SM} title="zoom out (-)" onClick={() => zoomBy(-1)}>−</button>
            <button
              style={{ ...VL_BTN_SM, color: zoomed ? "var(--hg-fg-3)" : "var(--hg-ice)" }}
              title="fit to width (F)"
              onClick={zoomFit}
            >fit</button>
            <button style={VL_BTN_SM} title="zoom in (+) · ctrl+wheel on the timeline" onClick={() => zoomBy(1)}>+</button>
          </span>
        )}
        <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "baseline", gap: 6 }}>
          <span ref={timecodeRef} style={{
            fontFamily: VL_FONT_MONO, fontSize: 11, color: "var(--hg-ice)",
            border: "1px solid var(--hg-border-soft)", padding: "3px 9px",
            minWidth: 74, textAlign: "center", letterSpacing: "0.08em",
          }}>00:00.00</span>
          <span style={{ fontFamily: VL_FONT_MONO, fontSize: 9.5, color: "var(--hg-fg-4)" }}>
            / {D.fmtDuration(duration)} · {fps.toFixed ? fps.toFixed(2) : fps} fps
          </span>
        </span>
      </div>

      {/* number-key palette — first 10 ACTIVE values of the active axis */}
      {editor && (
        <div className="hg-scroll" style={{
          display: "flex", alignItems: "center", gap: 4,
          padding: "5px 12px", borderTop: "1px solid var(--hg-border-soft)",
          overflowX: "auto", flex: "none",
        }}>
          <span style={{
            fontFamily: VL_FONT_MONO, fontSize: 8.5, letterSpacing: "0.16em",
            color: "var(--hg-fg-5)", textTransform: "lowercase", flex: "none",
          }}>{editor.axisTitle} ·</span>
          {(editor.palette || []).map((p) => (
            <button
              key={p.key + String(p.value)}
              onClick={() => editor.onPalettePick && editor.onPalettePick(p.value)}
              title={"apply " + p.label + " (key " + p.key + ")"}
              style={{
                ...VL_BTN_SM, padding: "2px 7px", fontSize: 9,
                whiteSpace: "nowrap", flex: "none",
                color: p.color, borderColor: "var(--hg-border-soft)",
              }}
            >
              <span style={{ color: "var(--hg-fg-5)" }}>{p.key} </span>{p.label}
            </button>
          ))}
          {(editor.palette || []).length === 0 && (
            <span style={{
              fontFamily: VL_FONT_MONO, fontSize: 9, color: "var(--hg-fg-5)",
              letterSpacing: "0.08em",
            }}>no values for this lane yet — press C to create a custom label</span>
          )}
        </div>
      )}

      {/* timeline + filmstrip */}
      <div ref={wrapRef} style={{ flex: "none", minWidth: 0 }}>
        {window.VLTimeline && (
          <window.VLTimeline
            duration={duration}
            fps={fps}
            playheadRef={playheadRef}
            playheadTimeRef={playheadTimeRef}
            pxPerSec={pxPerSec}
            onSeek={seekTo}
            lanes={editor ? editor.lanes : null}
            selection={editor ? editor.selection : null}
            activeAxis={editor ? editor.activeAxis : null}
            suggestions={editor ? editor.suggestions : null}
            snapTimes={keyframeTimes}
            minDur={editor ? editor.minDur : 0.2}
            following={playing}
            onSelect={editor ? editor.onSelect : null}
            onCommit={editor ? editor.onCommit : null}
            onCreate={editor ? editor.onCreate : null}
            onSelectSuggestion={editor ? editor.onSelectSuggestion : null}
            onZoom={editor ? zoomBy : null}
          />
        )}
        {window.VLThumbStrip && (
          <window.VLThumbStrip video={video} manifest={manifest} onSeek={seekTo} />
        )}
      </div>
    </div>
  );
}

/* ═════════════════════════════════════════════════════════════════════
 * M1 — segment editor state + components
 * ════════════════════════════════════════════════════════════════════ */

const VL_AXIS_ORDER = ["activity_primary", "posture", "quality", "custom"];
const VL_AXIS_TITLES = {
  activity_primary: "activity", posture: "posture",
  quality: "quality", custom: "custom",
};
/* review action → resulting review_state (used for the local/tmp path) */
const VL_REVIEW_RESULT = {
  accept: "accepted", reject: "rejected",
  needs_review: "needs_review", exclude: "excluded_from_export",
};

const VL_INPUT = {
  background: "var(--hg-input-bg)", border: "1px solid var(--hg-border-soft)",
  color: "var(--hg-fg-1)", padding: "4px 7px",
  fontFamily: VL_FONT_MONO, fontSize: 10.5, width: "100%",
  outline: "none", boxSizing: "border-box",
};

const VL_SECTION_TITLE = {
  fontFamily: VL_FONT_MONO, fontSize: 8.5, letterSpacing: "0.22em",
  color: "var(--hg-fg-4)", textTransform: "lowercase",
};

function vlEmptyAxes() {
  return { activity_primary: [], posture: [], quality: [], custom: [] };
}

function vlNormalizeAxes(axes) {
  const out = vlEmptyAxes();
  for (const ax of VL_AXIS_ORDER) {
    out[ax] = ((axes && axes[ax]) || []).slice().sort((a, b) => a.start_s - b.start_s);
  }
  return out;
}

function vlCountSegs(axes) {
  let n = 0;
  for (const ax of VL_AXIS_ORDER) n += ((axes && axes[ax]) || []).length;
  return n;
}

function vlValuesEqual(a, b) {
  if (Array.isArray(a) || Array.isArray(b)) {
    const aa = (Array.isArray(a) ? a.slice() : [a]).sort();
    const bb = (Array.isArray(b) ? b.slice() : [b]).sort();
    return JSON.stringify(aa) === JSON.stringify(bb);
  }
  return a === b;
}

function vlIsTempId(id) {
  return typeof id === "string" && (id.indexOf("tmp_") === 0 || id.indexOf("seg_local_") === 0);
}

function vlIsSuggestionSel(sel) {
  return !!(sel && sel.kind === "suggestion");
}

/* low-confidence threshold: disagreement caps server confidence at 0.5, so
   <0.6 nets both genuinely-unsure and disagreement-capped suggestions */
const VL_LOW_CONF = 0.6;

/* per-video suggestion stats for the queue filter chips (client-side only) */
function vlSuggStats(axes) {
  const D = window.HomeVideoLabelerData;
  let total = 0, lowConf = 0, disagreement = 0;
  for (const ax of VL_AXIS_ORDER) {
    for (const s of (axes && axes[ax]) || []) {
      total += 1;
      if (typeof s.confidence === "number" && s.confidence < VL_LOW_CONF) lowConf += 1;
      if (D.suggestionDisagreement(s) != null) disagreement += 1;
    }
  }
  return { total, lowConf, disagreement };
}

const VL_QUEUE_FILTERS = ["all", "unreviewed", "low-conf", "disagreement"];

function vlRemapList(list, idMap) {
  return (list || []).map((s) => idMap[s.id] ? { ...s, id: idMap[s.id] } : s);
}

function vlRemapAxes(axes, idMap) {
  const out = {};
  for (const ax of VL_AXIS_ORDER) out[ax] = vlRemapList(axes && axes[ax], idMap);
  return out;
}

function vlRemapStack(stack, idMap) {
  return (stack || []).map((e) => ({
    axis: e.axis, before: vlRemapList(e.before, idMap), after: vlRemapList(e.after, idMap),
  }));
}

function vlEditorInit() {
  return {
    doc: { axes: vlEmptyAxes() },  // mirrors the server PUT/GET shape
    revision: 0,                   // server revision the doc is based on
    rev: 0,                        // local edit counter
    savedRev: 0,                   // rev at last save — dirty = rev !== savedRev
    selection: null,               // {kind:'canonical'|'suggestion', axis, segId} | null
    activeAxis: "activity_primary",
    undo: [],                      // axis-scoped {axis, before, after}, cap 50
    redo: [],
  };
}

function vlEditorReducer(state, a) {
  switch (a.type) {
    case "LOAD_DOC":
      return {
        ...vlEditorInit(),
        doc: { axes: vlNormalizeAxes(a.axes) },
        revision: a.revision || 0,
        activeAxis: state.activeAxis,
      };
    case "RESTORE_DRAFT":
      return {
        ...state,
        doc: { axes: vlNormalizeAxes(a.axes) },
        rev: state.rev + 1,
        undo: [], redo: [], selection: null,
      };
    case "SET_SEGMENTS": {
      const segments = (a.segments || []).slice().sort((x, y) => x.start_s - y.start_s);
      const axes = { ...state.doc.axes, [a.axis]: segments };
      let selection = state.selection;
      if (selection && selection.kind !== "suggestion"
          && selection.axis === a.axis && !segments.some((s) => s.id === selection.segId)) {
        selection = null;
      }
      return {
        ...state,
        doc: { axes },
        rev: state.rev + 1,
        undo: a.undoEntry ? state.undo.concat([a.undoEntry]).slice(-50) : state.undo,
        redo: a.undoEntry ? [] : state.redo,
        selection,
      };
    }
    case "SELECT":
      return {
        ...state,
        selection: a.segId
          ? { kind: a.kind || "canonical", axis: a.axis, segId: a.segId }
          : null,
        activeAxis: a.axis || state.activeAxis,
      };
    case "SET_ACTIVE_AXIS":
      return { ...state, activeAxis: a.axis };
    case "UNDO": {
      const e = state.undo[state.undo.length - 1];
      if (!e) return state;
      const axes = { ...state.doc.axes, [e.axis]: e.before };
      let selection = state.selection;
      if (selection && selection.kind !== "suggestion"
          && selection.axis === e.axis && !e.before.some((s) => s.id === selection.segId)) selection = null;
      return {
        ...state, doc: { axes }, rev: state.rev + 1, selection,
        undo: state.undo.slice(0, -1), redo: state.redo.concat([e]).slice(-50),
      };
    }
    case "REDO": {
      const e = state.redo[state.redo.length - 1];
      if (!e) return state;
      const axes = { ...state.doc.axes, [e.axis]: e.after };
      let selection = state.selection;
      if (selection && selection.kind !== "suggestion"
          && selection.axis === e.axis && !e.after.some((s) => s.id === selection.segId)) selection = null;
      return {
        ...state, doc: { axes }, rev: state.rev + 1, selection,
        redo: state.redo.slice(0, -1), undo: state.undo.concat([e]).slice(-50),
      };
    }
    /* server-applied review: patches the segment + adopts the bumped
       revision WITHOUT touching rev/savedRev (it isn't a local edit) */
    case "APPLY_REVIEW": {
      const lane = (state.doc.axes[a.axis] || []).map((s) => s.id === a.segId ? { ...s, ...a.patch } : s);
      return {
        ...state,
        doc: { axes: { ...state.doc.axes, [a.axis]: lane } },
        revision: a.revision != null ? a.revision : state.revision,
      };
    }
    /* M3 — a reviewed suggestion graduated server-side; PROJECT it into the
       canonical lane. items: [{axis, segment}] (segment = the server's
       returned canonical row, axis key stripped); empty items just adopts
       the bumped revision (reject path). PUT enforces per-lane non-overlap,
       so overlapping canonical segments are carved around the graduate —
       and ONLY a carve marks the doc dirty (the graduate itself is already
       server state). Undo stacks are left alone: graduation is not a local
       edit and must not be locally undoable. */
    case "GRADUATE": {
      const D = window.HomeVideoLabelerData;
      const axes = { ...state.doc.axes };
      let carved = false;
      for (const it of a.items || []) {
        const seg = it.segment;
        if (!seg || !seg.id) continue;
        const lane = (axes[it.axis] || []).filter((s) => s.id !== seg.id);
        const overlaps = lane.some((s) =>
          s.start_s < seg.end_s - 1e-6 && s.end_s > seg.start_s + 1e-6);
        let next = lane;
        if (overlaps) {
          next = D.carveRange(lane, seg.start_s, seg.end_s, a.minDur || 0.2);
          carved = true;
        }
        axes[it.axis] = next.concat([seg]).sort((x, y) => x.start_s - y.start_s);
      }
      return {
        ...state,
        doc: { axes },
        rev: carved ? state.rev + 1 : state.rev,
        revision: a.revision != null ? a.revision : state.revision,
      };
    }
    /* savedRev = rev at PUT start: edits made while the save was in
       flight stay dirty. id_map remaps tmp ids in doc + selection +
       undo/redo stacks. */
    case "MARK_SAVED": {
      const idMap = a.idMap || {};
      let selection = state.selection;
      if (selection && idMap[selection.segId]) selection = { ...selection, segId: idMap[selection.segId] };
      return {
        ...state,
        doc: { axes: vlRemapAxes(state.doc.axes, idMap) },
        undo: vlRemapStack(state.undo, idMap),
        redo: vlRemapStack(state.redo, idMap),
        selection,
        revision: a.revision,
        savedRev: a.savedRev,
      };
    }
    default:
      return state;
  }
}

/* ─────────────────────────────────────────────────────────────────────
 * VLTimeField — frame-stepped numeric time input; commits on blur/Enter
 * so half-typed values never reach the doc.
 * ──────────────────────────────────────────────────────────────────── */
function VLTimeField({ label, value, fps, onCommit }) {
  const [txt, setTxt] = useState(String(Math.round(value * 1000) / 1000));
  useEffect(() => { setTxt(String(Math.round(value * 1000) / 1000)); }, [value]);
  const commit = () => {
    const n = Number(txt);
    if (Number.isFinite(n) && Math.abs(n - value) > 1e-9) onCommit(n);
    else setTxt(String(Math.round(value * 1000) / 1000));
  };
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 3, flex: 1, minWidth: 0 }}>
      <span style={{ ...VL_SECTION_TITLE, fontSize: 8 }}>{label}</span>
      <input
        type="number"
        step={1 / (fps || 30)}
        min={0}
        value={txt}
        onChange={(e) => setTxt(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); e.currentTarget.blur(); } }}
        style={VL_INPUT}
      />
    </label>
  );
}

/* text field that commits on blur/Enter (aux form, picker create) */
function VLTextField({ label, value, placeholder, textarea, onCommit }) {
  const [txt, setTxt] = useState(value || "");
  useEffect(() => { setTxt(value || ""); }, [value]);
  const commit = () => { if ((value || "") !== txt) onCommit(txt); };
  const common = {
    value: txt,
    placeholder: placeholder || "",
    onChange: (e) => setTxt(e.target.value),
    onBlur: commit,
    style: textarea ? { ...VL_INPUT, minHeight: 44, resize: "vertical" } : VL_INPUT,
  };
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
      <span style={{ ...VL_SECTION_TITLE, fontSize: 8 }}>{label}</span>
      {textarea
        ? <textarea {...common} />
        : <input
            type="text" {...common}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); e.currentTarget.blur(); } }}
          />}
    </label>
  );
}

/* ─────────────────────────────────────────────────────────────────────
 * VLLabelPicker — popover (E / value chip): autocomplete over the axis's
 * ACTIVE canonical values + custom labels (inactive canonicals listed
 * last, dimmed), with an inline create-custom-label form (C opens it
 * directly; a "create …" row appears for unmatched queries). Escape is
 * handled by the input itself — the overlay Escape guard skips inputs.
 * ──────────────────────────────────────────────────────────────────── */
function VLLabelPicker({ axisTitle, values, createMode, onApply, onCreateCustom, onClose }) {
  const [q, setQ] = useState("");
  const [hi, setHi] = useState(0);
  const [creating, setCreating] = useState(!!createMode);
  const [createName, setCreateName] = useState("");
  const [createColor, setCreateColor] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    const t = setTimeout(() => { if (inputRef.current) inputRef.current.focus(); }, 30);
    return () => clearTimeout(t);
  }, [creating]);

  const matches = useMemo(() => {
    const qq = q.trim().toLowerCase();
    const list = values || [];
    if (!qq) return list.slice(0, 14);
    return list
      .filter((v) => v.label.toLowerCase().includes(qq) || String(v.value).toLowerCase().includes(qq))
      .slice(0, 14);
  }, [q, values]);

  const showCreateRow = q.trim().length > 0 &&
    !matches.some((v) => v.label.toLowerCase() === q.trim().toLowerCase());
  const rowCount = matches.length + (showCreateRow ? 1 : 0);

  useEffect(() => { setHi((h) => Math.max(0, Math.min(h, rowCount - 1))); }, [rowCount]);

  const choose = (i) => {
    if (i < matches.length) { onApply(matches[i].value); onClose(); return; }
    setCreateName(q.trim());
    setCreating(true);
  };

  const doCreate = async () => {
    const name = createName.trim();
    if (!name || busy) return;
    setBusy(true);
    const ok = await onCreateCustom(name, createColor.trim() || null);
    setBusy(false);
    if (ok) onClose();
  };

  return (
    <div style={{
      position: "absolute", left: "50%", top: 80, transform: "translateX(-50%)",
      width: 320, zIndex: 12, background: "var(--hg-bg-1)",
      border: "1px solid var(--hg-border)", boxShadow: "0 18px 50px rgba(0,0,0,0.5)",
      padding: 10, fontFamily: VL_FONT_MONO,
    }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 8 }}>
        <span style={VL_SECTION_TITLE}>{creating ? "new custom label" : "label · " + axisTitle}</span>
        <button onClick={onClose} style={{ ...VL_BTN_SM, marginLeft: "auto", padding: "1px 7px" }}>esc</button>
      </div>
      {!creating ? (
        <>
          <input
            ref={inputRef}
            type="text"
            value={q}
            placeholder="type to filter…"
            onChange={(e) => { setQ(e.target.value); setHi(0); }}
            onKeyDown={(e) => {
              if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); }
              else if (e.key === "ArrowDown") { e.preventDefault(); setHi((h) => Math.min(rowCount - 1, h + 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(0, h - 1)); }
              else if (e.key === "Enter") { e.preventDefault(); if (rowCount > 0) choose(hi); }
            }}
            style={{ ...VL_INPUT, marginBottom: 6 }}
          />
          <div className="hg-scroll" style={{ maxHeight: 240, overflowY: "auto" }}>
            {matches.map((v, i) => (
              <div
                key={String(v.value)}
                onMouseEnter={() => setHi(i)}
                onClick={() => choose(i)}
                style={{
                  display: "flex", alignItems: "center", gap: 7,
                  padding: "5px 7px", cursor: "pointer", fontSize: 10.5,
                  background: hi === i ? "var(--hg-bg-3)" : "transparent",
                  color: v.inactive ? "var(--hg-fg-5)" : "var(--hg-fg-1)",
                }}
              >
                <span style={{ width: 8, height: 8, background: v.color, flex: "none" }} />
                <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{v.label}</span>
                {v.isCustom && <VLChip label="custom" tone="dim" />}
                {v.inactive && <VLChip label="inactive" tone="dim" />}
              </div>
            ))}
            {showCreateRow && (
              <div
                onMouseEnter={() => setHi(matches.length)}
                onClick={() => choose(matches.length)}
                style={{
                  display: "flex", alignItems: "center", gap: 7,
                  padding: "5px 7px", cursor: "pointer", fontSize: 10.5,
                  background: hi === matches.length ? "var(--hg-bg-3)" : "transparent",
                  color: "var(--hg-ice)",
                }}
              >+ create custom "{q.trim()}"</div>
            )}
            {rowCount === 0 && (
              <div style={{ padding: "6px 7px", fontSize: 10, color: "var(--hg-fg-5)" }}>no matches</div>
            )}
          </div>
        </>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          <input
            ref={inputRef}
            type="text"
            value={createName}
            placeholder="label name"
            onChange={(e) => setCreateName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); }
              else if (e.key === "Enter") { e.preventDefault(); doCreate(); }
            }}
            style={VL_INPUT}
          />
          <input
            type="text"
            value={createColor}
            placeholder="color (optional, e.g. hsl(200 50% 60%))"
            onChange={(e) => setCreateColor(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); doCreate(); } }}
            style={VL_INPUT}
          />
          <div style={{ display: "flex", gap: 6 }}>
            <button onClick={doCreate} disabled={busy || !createName.trim()}
              style={{ ...VL_BTN_SM, color: "var(--hg-ice)" }}>{busy ? "creating…" : "create + apply"}</button>
            {!createMode && (
              <button onClick={() => setCreating(false)} style={VL_BTN_SM}>back</button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
 * VLCustomLabelManager — minimal manager inside the inspector: list with
 * usage counts; per-label hide / merge-into / promote-to-canonical.
 * (No rename — the M1 contract has no rename endpoint.)
 * ──────────────────────────────────────────────────────────────────── */
function VLCustomLabelManager({ ontology, refreshOntology, canonicalInfo, showToast }) {
  const D = window.HomeVideoLabelerData;
  const [expanded, setExpanded] = useState(null);
  const [mergeInto, setMergeInto] = useState("");
  const [promoteTo, setPromoteTo] = useState("");
  const [busy, setBusy] = useState(false);
  const customs = (ontology && ontology.custom) || [];

  const act = async (call, what) => {
    if (busy) return;
    setBusy(true);
    const r = await call();
    setBusy(false);
    if (r.ok) {
      showToast("custom label " + what + " · ok");
      setExpanded(null);
      refreshOntology();
    } else {
      showToast(what + " failed · " + (r.error || "service unreachable"));
    }
  };

  if (!customs.length) {
    return (
      <div style={{ fontSize: 9.5, color: "var(--hg-fg-5)", letterSpacing: "0.06em" }}>
        none yet — press C (or type a new name in the picker) to create one
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {customs.map((c) => {
        const open = expanded === c.slug;
        const color = c.color || D.colorFor("custom:" + c.slug);
        const mergeTargets = customs.filter((o) => o.slug !== c.slug && o.status === "active");
        const canonAxis = (c.axis === "posture" || c.axis === "quality") ? c.axis : "activity_primary";
        const info = canonicalInfo(canonAxis);
        const promoteTargets = info.active.concat(info.rest);
        return (
          <div key={c.slug} style={{ borderBottom: "1px solid var(--hg-border-soft)" }}>
            <div
              onClick={() => { setExpanded(open ? null : c.slug); setMergeInto(""); setPromoteTo(""); }}
              style={{
                display: "flex", alignItems: "center", gap: 6,
                padding: "5px 2px", cursor: "pointer", fontSize: 10,
                color: c.status === "active" ? "var(--hg-fg-2)" : "var(--hg-fg-5)",
              }}
            >
              <span style={{ width: 8, height: 8, background: color, flex: "none" }} />
              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {c.name || c.slug}
              </span>
              <span style={{ fontSize: 8.5, color: "var(--hg-fg-5)" }}>{c.axis} · {c.usage_count ?? 0}×</span>
              {c.status !== "active" && <VLChip label={c.status} tone="dim" />}
            </div>
            {open && c.status === "active" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 5, padding: "2px 2px 8px 14px" }}>
                <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
                  <button style={VL_BTN_SM} disabled={busy}
                    onClick={() => act(() => D.hideCustomLabel(c.slug), "hidden")}>hide</button>
                </div>
                {mergeTargets.length > 0 && (
                  <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
                    <select value={mergeInto} onChange={(e) => setMergeInto(e.target.value)}
                      style={{ ...VL_INPUT, width: "auto", flex: 1 }}>
                      <option value="">merge into…</option>
                      {mergeTargets.map((o) => <option key={o.slug} value={o.slug}>{o.name || o.slug}</option>)}
                    </select>
                    <button style={VL_BTN_SM} disabled={busy || !mergeInto}
                      onClick={() => act(() => D.mergeCustomLabel(c.slug, mergeInto), "merged")}>merge</button>
                  </div>
                )}
                <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
                  <select value={promoteTo} onChange={(e) => setPromoteTo(e.target.value)}
                    style={{ ...VL_INPUT, width: "auto", flex: 1 }}>
                    <option value="">promote to canonical…</option>
                    {promoteTargets.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                  <button style={VL_BTN_SM} disabled={busy || !promoteTo}
                    onClick={() => act(() => D.promoteCustomLabel(c.slug, promoteTo), "promoted")}>promote</button>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* aux metadata form — activity segments only */
const VL_AUX_FIELDS = [
  { key: "verb", type: "text" },
  { key: "object_noun", type: "text" },
  { key: "room_zone", type: "text" },
  { key: "attention_target", type: "text" },
  { key: "motion_intensity", type: "select", options: ["", "low", "moderate", "high"] },
  { key: "activity_phase", type: "select", options: ["", "starting", "ongoing", "ending", "transition"] },
];

function VLAuxForm({ seg, ontology, onPatch }) {
  /* prefer value lists the ontology's aux_axes declare for a key */
  const defs = useMemo(() => {
    const m = {};
    for (const d of (ontology && ontology.aux_axes) || []) {
      const k = d && (d.key || d.name || d.id || d.axis);
      if (k && Array.isArray(d.values)) m[k] = d.values;
    }
    return m;
  }, [ontology]);
  const aux = (seg && seg.aux) || {};
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={VL_SECTION_TITLE}>aux · activity metadata</span>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
        {VL_AUX_FIELDS.map((f) => {
          const options = defs[f.key] || (f.type === "select" ? f.options : null);
          const label = f.key.replace(/_/g, " ");
          if (options) {
            const opts = options[0] === "" ? options : [""].concat(options);
            return (
              <label key={f.key} style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
                <span style={{ ...VL_SECTION_TITLE, fontSize: 8 }}>{label}</span>
                <select
                  value={aux[f.key] || ""}
                  onChange={(e) => onPatch({ [f.key]: e.target.value || null })}
                  style={VL_INPUT}
                >
                  {opts.map((o) => <option key={o} value={o}>{o === "" ? "—" : String(o).replace(/_/g, " ")}</option>)}
                </select>
              </label>
            );
          }
          return (
            <VLTextField
              key={f.key}
              label={label}
              value={aux[f.key] || ""}
              onCommit={(v) => onPatch({ [f.key]: v || null })}
            />
          );
        })}
      </div>
      <VLTextField
        label="notes"
        textarea
        value={aux.notes || ""}
        onCommit={(v) => onPatch({ notes: v || null })}
      />
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
 * VLEvidenceStrip (M3) — the selected suggestion's source-window keyframes.
 * The SEG carries no window linkage (seg_to_api drops evidence_json), but
 * suggestions carry their analysis window's EXACT geometry, so the window
 * id is derived: aw_<video>_<start_ms> (videolabeler/jobs/windows.py).
 * Tolerates {keyframes:[...]} or a bare list; entries {t, frame, crop|null}
 * with url/name fallbacks. Click a thumb → seek to its timestamp. Person
 * zoom crops render beside their frame when listed.
 * ──────────────────────────────────────────────────────────────────── */
function VLEvidenceStrip({ video, suggestion, onSeek }) {
  const D = window.HomeVideoLabelerData;
  const windowId = D.windowIdFor(video.id, suggestion.start_s);
  const [kf, setKf] = useState({ status: "loading", frames: [] });

  useEffect(() => {
    let dead = false;
    setKf({ status: "loading", frames: [] });
    (async () => {
      const r = await D.getWindowKeyframes(video.id, windowId);
      if (dead) return;
      if (r.ok && r.data) {
        const raw = Array.isArray(r.data) ? r.data : (r.data.keyframes || []);
        const frames = raw.map((e) => ({
          t: typeof e.t === "number" ? e.t : null,
          frame: e.frame || e.url || e.name || null,
          crop: typeof e.crop === "string" ? e.crop : null,
        })).filter((e) => !!e.frame);
        setKf({ status: frames.length ? "ok" : "empty", frames });
      } else {
        setKf({ status: r.status === 404 ? "missing" : "offline", frames: [] });
      }
    })();
    return () => { dead = true; };
  }, [video.id, windowId]);

  if (kf.status !== "ok") {
    return (
      <div style={{ fontSize: 9, color: "var(--hg-fg-5)", letterSpacing: "0.08em", lineHeight: 1.6 }}>
        {kf.status === "loading" ? "loading keyframes…"
          : kf.status === "missing" ? "no keyframes for this window yet"
          : kf.status === "empty" ? "keyframe list is empty"
          : "keyframes unavailable · service unreachable"}
      </div>
    );
  }

  return (
    <div className="hg-scroll" style={{ display: "flex", gap: 4, overflowX: "auto", paddingBottom: 2 }}>
      {kf.frames.map((f, i) => {
        const frameUrl = D.keyframeUrl(f.frame);
        const cropUrl = f.crop ? D.keyframeUrl(f.crop) : null;
        if (!frameUrl) return null;
        const seek = () => { if (onSeek && f.t != null) onSeek(f.t); };
        const imgStyle = {
          height: 56, display: "block",
          border: "1px solid var(--hg-border-soft)",
          cursor: f.t != null ? "pointer" : "default",
          background: "var(--hg-bg-2)",
        };
        return (
          <span key={i} style={{ display: "inline-flex", gap: 2, flex: "none" }}>
            <img
              src={frameUrl}
              alt={"keyframe " + (i + 1)}
              title={f.t != null ? D.fmtDuration(f.t) + " — click to seek" : "keyframe"}
              onClick={seek}
              onError={(e) => { e.currentTarget.style.display = "none"; }}
              style={imgStyle}
            />
            {cropUrl && (
              <img
                src={cropUrl}
                alt={"person crop " + (i + 1)}
                title={"person crop" + (f.t != null ? " · " + D.fmtDuration(f.t) : "")}
                onClick={seek}
                onError={(e) => { e.currentTarget.style.display = "none"; }}
                style={{ ...imgStyle, width: 56, objectFit: "cover" }}
              />
            )}
          </span>
        );
      })}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
 * VLInspector — right rail (340px): save row, selected-segment editor
 * (value chips, frame-stepped bounds, review actions, aux form), custom
 * label manager. M3: suggestion mode (value/conf/source/evidence +
 * accept/reject/needs-review + the keyframe evidence strip) replaces the
 * segment editor while a SUGGESTION is selected, and a suggestions
 * section with the calibration-locked bulk-accept button.
 * ──────────────────────────────────────────────────────────────────── */
function VLInspector({
  video, fps, ontology, labelsApi, dirty, saving, revision,
  selection, segment, ops, canonicalInfo, refreshOntology, showToast,
  suggestion, suggestionOps, suggSummary, bulk,
}) {
  const D = window.HomeVideoLabelerData;
  const seg = segment;
  const axis = selection && selection.axis;
  const valueList = seg
    ? (Array.isArray(seg.value) ? seg.value : [seg.value])
    : [];
  const sugDisagree = suggestion ? D.suggestionDisagreement(suggestion) : null;
  const sugEvidence = suggestion ? D.suggestionEvidenceText(suggestion) : null;
  const sugConf = suggestion && typeof suggestion.confidence === "number"
    ? Math.max(0, Math.min(1, suggestion.confidence)) : null;

  return (
    <div className="hg-scroll" style={{
      width: 340, flex: "none", minHeight: 0, overflowY: "auto",
      borderLeft: "1px solid var(--hg-border-soft)",
      padding: "12px 14px", fontFamily: VL_FONT_MONO,
      display: "flex", flexDirection: "column", gap: 14,
    }}>
      {/* save row */}
      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <button
            onClick={() => ops.save()}
            disabled={saving || labelsApi.status !== "ok"}
            style={{
              ...VL_BTN_SM,
              color: dirty && labelsApi.status === "ok" ? "var(--hg-ice)" : "var(--hg-fg-4)",
            }}
            title="Ctrl+S"
          >{saving ? "saving…" : "save"}</button>
          <button
            onClick={() => ops.saveNext()}
            disabled={saving || labelsApi.status !== "ok"}
            style={VL_BTN_SM}
            title="Ctrl+Enter — save, then open the next video"
          >save · next</button>
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: 5 }}>
            <VLChip label={dirty ? "unsaved" : "saved"} tone={dirty ? "warn" : "dim"} />
            <VLChip label={"rev " + revision} tone="dim" />
          </span>
        </div>
        {labelsApi.status !== "ok" && (
          <VLChip
            label={labelsApi.status === "loading" ? "loading labels…" : (labelsApi.reason || "labels unavailable")}
            tone={labelsApi.status === "loading" ? "dim" : "warn"}
            title="the editor still works — saving is disabled until the labels API responds"
          />
        )}
        {ontology === false && (
          <VLChip label="ontology offline · builtin taxonomy" tone="warn" />
        )}
      </div>

      {/* selected suggestion — review mode (M3) */}
      {suggestion ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <span style={VL_SECTION_TITLE}>suggestion · review</span>
          <div style={{ display: "flex", gap: 5, alignItems: "center", flexWrap: "wrap" }}>
            <VLChip label={VL_AXIS_TITLES[axis] || axis} tone="default" />
            <VLChip label="prelabel" tone="warn" />
            {suggestion.source && <VLChip label={suggestion.source} tone="dim" />}
            {sugConf != null && (
              <VLChip
                label={"conf " + Math.round(sugConf * 100) + "%"}
                tone={sugConf < VL_LOW_CONF ? "warn" : "default"}
              />
            )}
            {sugDisagree != null && (
              <VLChip
                label={"! disagree " + Number(sugDisagree).toFixed(2)}
                tone="crit"
                title="a cross-check (pose geometry / motion / adjacency) disputes this suggestion"
              />
            )}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
            <span style={{
              fontSize: 12.5, letterSpacing: "0.06em",
              color: D.colorFor(Array.isArray(suggestion.value) && suggestion.value.length
                ? suggestion.value[0] : suggestion.value),
            }}>{vlValueText(suggestion.value) || "—"}</span>
            <span style={{ fontSize: 9.5, color: "var(--hg-fg-4)" }}>
              {suggestion.start_s.toFixed(2)}–{suggestion.end_s.toFixed(2)}s
              {" · "}{(suggestion.end_s - suggestion.start_s).toFixed(2)}s
            </span>
          </div>
          {sugEvidence && (
            <div style={{
              fontSize: 9.5, color: "var(--hg-fg-3)", lineHeight: 1.6,
              borderLeft: "2px solid var(--hg-border-soft)", paddingLeft: 8,
              letterSpacing: "0.03em",
            }}>{sugEvidence}</div>
          )}
          <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
            <button
              onClick={() => suggestionOps.review("accept")}
              title="key A — graduate into the canonical lane"
              style={{ ...VL_BTN_SM, color: D.colorFor("accepted") }}
            >accept</button>
            <button
              onClick={() => suggestionOps.review("reject")}
              title="key R — drop from review (the server keeps the record)"
              style={{ ...VL_BTN_SM, color: D.colorFor("rejected") }}
            >reject</button>
            <button
              onClick={() => suggestionOps.review("needs_review")}
              title="key N — keep on the timeline as needs review"
              style={{ ...VL_BTN_SM, color: D.colorFor("needs_review") }}
            >needs review</button>
            <button
              onClick={() => suggestionOps.acceptOverlapping()}
              title="shift+A — accept every suggestion overlapping this time range, all axes"
              style={VL_BTN_SM}
            >⇧A all here</button>
          </div>
          <span style={{ ...VL_SECTION_TITLE, marginTop: 2 }}>evidence · window keyframes</span>
          <VLEvidenceStrip video={video} suggestion={suggestion} onSeek={suggestionOps.seek} />
        </div>
      ) : (
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <span style={VL_SECTION_TITLE}>selected segment</span>
        {!seg ? (
          <div style={{ fontSize: 10, color: "var(--hg-fg-5)", letterSpacing: "0.06em", lineHeight: 1.7 }}>
            click a block — or double-click empty lane space to create one.
            press ? for all shortcuts.
          </div>
        ) : (
          <>
            <div style={{ display: "flex", gap: 5, alignItems: "center", flexWrap: "wrap" }}>
              <VLChip label={VL_AXIS_TITLES[axis] || axis} tone="default" />
              <VLChip
                label={seg.review_state || "?"}
                tone={seg.review_state === "accepted" ? "live"
                  : seg.review_state === "rejected" || seg.review_state === "excluded_from_export" ? "crit"
                  : seg.review_state === "needs_review" || seg.review_state === "prelabel" ? "warn" : "default"}
              />
              {seg.source && <VLChip label={seg.source} tone="dim" />}
              {seg.confidence != null && <VLChip label={"conf " + Number(seg.confidence).toFixed(2)} tone="dim" />}
              {vlIsTempId(seg.id) && <VLChip label="unsaved id" tone="warn" title={seg.id} />}
            </div>

            {/* value chips → picker */}
            <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
              {valueList.filter((v) => v != null).map((v) => (
                <button
                  key={String(v)}
                  onClick={() => ops.openPicker(false)}
                  title="change value (E)"
                  style={{
                    ...VL_BTN_SM,
                    color: D.colorFor(Array.isArray(seg.value) ? v : v),
                    borderColor: "var(--hg-border-soft)",
                  }}
                >{String(v).replace(/_/g, " ")}</button>
              ))}
              {valueList.filter((v) => v != null).length === 0 && (
                <button onClick={() => ops.openPicker(false)} style={{ ...VL_BTN_SM, color: "var(--hg-warn)" }}>
                  set value…
                </button>
              )}
            </div>

            {/* bounds */}
            <div style={{ display: "flex", gap: 7, alignItems: "flex-end" }}>
              <VLTimeField label="start s" value={seg.start_s} fps={fps} onCommit={(t) => ops.setStart(t)} />
              <VLTimeField label="end s" value={seg.end_s} fps={fps} onCommit={(t) => ops.setEnd(t)} />
              <div style={{ flex: "none", paddingBottom: 5, fontSize: 9.5, color: "var(--hg-fg-4)" }}>
                {(seg.end_s - seg.start_s).toFixed(2)}s
              </div>
            </div>

            {/* review actions */}
            <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
              {[["accept", "A"], ["reject", "R"], ["needs_review", "N"], ["exclude", "X"]].map(([action, k]) => (
                <button
                  key={action}
                  onClick={() => ops.review(action)}
                  title={"key " + k}
                  style={{
                    ...VL_BTN_SM,
                    color: D.colorFor(VL_REVIEW_RESULT[action]),
                  }}
                >{action.replace(/_/g, " ")}</button>
              ))}
            </div>

            {/* aux — activity only */}
            {axis === "activity_primary" && (
              <VLAuxForm seg={seg} ontology={ontology || null} onPatch={(p) => ops.setAux(p)} />
            )}

            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 9, color: "var(--hg-fg-5)" }}>
                person slot · {seg.person_slot == null ? "—" : String(seg.person_slot)}
              </span>
              <button
                onClick={() => ops.deleteSelected()}
                style={{ ...VL_BTN_SM, marginLeft: "auto", color: "var(--hg-crit)", borderColor: "var(--hg-border-soft)" }}
                title="Del"
              >delete</button>
            </div>
          </>
        )}
      </div>
      )}

      {/* suggestions — counts + calibration-locked bulk accept (M3) */}
      {suggSummary && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <span style={VL_SECTION_TITLE}>suggestions</span>
          <div style={{ display: "flex", gap: 5, alignItems: "center", flexWrap: "wrap" }}>
            {suggSummary.status === "loading" && <VLChip label="loading suggestions…" tone="dim" />}
            {suggSummary.status === "missing" && (
              <VLChip label="suggestions API not deployed" tone="dim"
                title="GET /suggestions returned 404 — prelabels land after the backend deploy" />
            )}
            {suggSummary.status === "offline" && (
              <VLChip label="suggestions unavailable" tone="warn" title={suggSummary.reason || "service unreachable"} />
            )}
            {suggSummary.status === "ok" && (suggSummary.total > 0 ? (
              <span style={{ fontSize: 9.5, color: "var(--hg-fg-3)", letterSpacing: "0.06em" }}>
                {suggSummary.total} pending · {suggSummary.lowConf} low-conf · {suggSummary.disagreement} disagree
              </span>
            ) : (
              <span style={{ fontSize: 9.5, color: "var(--hg-fg-5)", letterSpacing: "0.06em" }}>
                none pending — run prelabel on this video
              </span>
            ))}
          </div>
          {bulk && (
            <span title={bulk.tip} style={{ alignSelf: "flex-start" }}>
              <button
                onClick={() => { if (bulk.ok) bulk.run(); }}
                disabled={!bulk.ok}
                style={{
                  ...VL_BTN_SM,
                  color: bulk.ok ? "var(--hg-ice)" : "var(--hg-fg-5)",
                  cursor: bulk.ok ? "pointer" : "not-allowed",
                }}
              >bulk accept{bulk.ok ? "" : " · locked"}</button>
            </span>
          )}
        </div>
      )}

      {/* custom labels */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <span style={VL_SECTION_TITLE}>custom labels</span>
        <VLCustomLabelManager
          ontology={ontology || null}
          refreshOntology={refreshOntology}
          canonicalInfo={canonicalInfo}
          showToast={showToast}
        />
      </div>

      <div style={{ fontSize: 8.5, color: "var(--hg-fg-5)", letterSpacing: "0.08em", marginTop: "auto" }}>
        ? shortcuts · E picker · S split · M merge · U next unreviewed
      </div>
    </div>
  );
}

/* keyboard cheat sheet — toggled with ? */
const VL_CHEAT_ROWS = [
  ["space", "play / pause"], ["← / →", "±1 frame (shift ±1s · ctrl ±10s)"],
  ["↑ / ↓", "select prev / next segment"], ["tab", "cycle active lane"],
  ["1-9 0", "palette — apply / create at playhead"], ["e", "label picker"],
  ["c", "create custom label"], ["i / o", "set start / end to playhead"],
  ["s", "split at playhead"], ["m", "merge with next"],
  [", / .", "nudge nearest boundary ∓/± 1 frame (shift ×10)"],
  ["del", "delete segment"], ["a / r / n / x", "accept / reject / needs review / exclude"],
  ["shift+a", "accept all suggestions overlapping selection"],
  ["p", "toggle private_skip over selection"],
  ["u / shift+u", "next / prev unreviewed (falls back to suggestions)"],
  ["f · + / −", "fit · zoom (ctrl+wheel on timeline)"],
  ["ctrl+z / y", "undo / redo"], ["ctrl+s", "save"], ["ctrl+enter", "save and next"],
];

function VLCheatSheet({ onClose }) {
  return (
    <div
      onClick={onClose}
      style={{
        position: "absolute", right: 16, top: 16, zIndex: 12, width: 330,
        background: "var(--hg-bg-1)", border: "1px solid var(--hg-border)",
        boxShadow: "0 18px 50px rgba(0,0,0,0.5)", padding: "10px 12px",
        fontFamily: VL_FONT_MONO, cursor: "pointer",
      }}
    >
      <div style={{ ...VL_SECTION_TITLE, marginBottom: 8 }}>keyboard · click to close</div>
      {VL_CHEAT_ROWS.map(([k, desc]) => (
        <div key={k} style={{ display: "flex", gap: 10, fontSize: 9.5, padding: "2px 0" }}>
          <span style={{ width: 92, flex: "none", color: "var(--hg-ice)" }}>{k}</span>
          <span style={{ color: "var(--hg-fg-3)" }}>{desc}</span>
        </div>
      ))}
    </div>
  );
}

/* thin banner used for draft restore / 409 conflict / merge prompt */
function VLBanner({ tone, children }) {
  const color = tone === "warn" ? "var(--hg-warn)" : "var(--hg-ice)";
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
      padding: "7px 12px", flex: "none",
      borderBottom: "1px solid var(--hg-border-soft)",
      background: "color-mix(in oklab, " + color + " 6%, transparent)",
      fontFamily: VL_FONT_MONO, fontSize: 10, color,
      letterSpacing: "0.05em",
    }}>{children}</div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
 * VLEditor — the M1 labeling editor for one video (keyed by video.id):
 * owns the doc reducer, load/draft/save flows, the keyboard map and the
 * player+inspector layout. Mounted only on the label tab with a video
 * selected and sim off.
 * ──────────────────────────────────────────────────────────────────── */
function VLEditor({ video, videos, ontology, refreshOntology, onPickVideo, showToast, escInterceptRef, onSuggStats }) {
  const D = window.HomeVideoLabelerData;
  const [state, dispatch] = useReducer(vlEditorReducer, undefined, vlEditorInit);
  const stateRef = useRef(state);
  stateRef.current = state;
  const transportRef = useRef(null);
  const savingRef = useRef(false);
  const [labelsApi, setLabelsApi] = useState({ status: "loading", reason: null });
  const labelsApiRef = useRef(labelsApi);
  labelsApiRef.current = labelsApi;
  const [draftBanner, setDraftBanner] = useState(null); // {axes, count, dropped, ts}
  const [conflict, setConflict] = useState(null);       // {revision, axes}
  const [saving, setSaving] = useState(false);
  const [picker, setPicker] = useState(null);           // {axis, create}
  const [mergePrompt, setMergePrompt] = useState(null); // {axis, leftId, leftLabel, rightLabel}
  const [cheat, setCheat] = useState(false);
  /* ---- M3 review state ---- */
  const [suggestions, setSuggestions] = useState(vlEmptyAxes()); // prelabel layer, per axis
  const suggestionsRef = useRef(suggestions);
  suggestionsRef.current = suggestions;
  const [suggInfo, setSuggInfo] = useState({ status: "loading", reason: null }); // ok|missing|offline
  const [calibration, setCalibration] = useState(null); // null=loading, false=unavailable, doc
  const calibrationRef = useRef(calibration);
  calibrationRef.current = calibration;
  const bulkBusyRef = useRef(false); // serializes Shift+A / bulk-accept runs

  const fps = (video && video.fps) || 30;
  const minDur = Math.max(0.2, 3 / fps); // plan: max(0.2s, 3/fps)
  const dirty = state.rev !== state.savedRev;

  const transport = useCallback(() => transportRef.current || {
    getTime: () => 0,
    getDuration: () => (video && video.duration_s) || 0,
    getFps: () => fps,
    isPlaying: () => false,
    seekTo: () => {}, skip: () => {}, stepFrame: () => {},
    togglePlay: () => {}, zoomBy: () => {}, zoomFit: () => {},
  }, [video, fps]);

  const durationOf = useCallback(() => {
    const tr = transportRef.current;
    return (tr && tr.getDuration()) || (video && video.duration_s) || 0;
  }, [video]);

  /* ---------- load labels + draft-restore offer ---------- */

  useEffect(() => {
    let dead = false;
    (async () => {
      const r = await D.getLabels(video.id);
      if (dead) return;
      if (r.ok && r.data) {
        setLabelsApi({ status: "ok", reason: null });
        const serverAxes = vlNormalizeAxes(r.data.axes);
        const revision = r.data.revision || 0;
        dispatch({ type: "LOAD_DOC", axes: serverAxes, revision });
        const draft = D.loadDraft(video.id);
        if (draft && draft.doc && draft.rev > 0) {
          if ((draft.baseRevision || 0) === revision) {
            const v = D.validateDraftDoc(draft.doc.axes, serverAxes);
            const count = vlCountSegs(v.axes);
            if (count > 0 || v.dropped > 0) {
              setDraftBanner({ axes: v.axes, count, dropped: v.dropped, ts: draft.ts });
            } else {
              D.clearDraft(video.id);
            }
          } else {
            D.clearDraft(video.id); // based on a different revision — stale
          }
        }
      } else if (r.status === 404) {
        /* backend M1 not deployed yet — empty doc rev 0, saving disabled */
        setLabelsApi({ status: "missing", reason: "labels API not deployed" });
        dispatch({ type: "LOAD_DOC", axes: vlEmptyAxes(), revision: 0 });
        const draft = D.loadDraft(video.id);
        if (draft && draft.doc && draft.rev > 0) {
          setDraftBanner({ axes: vlNormalizeAxes(draft.doc.axes), count: vlCountSegs(draft.doc.axes), dropped: 0, ts: draft.ts });
        }
      } else {
        setLabelsApi({ status: "offline", reason: r.error || "service unreachable" });
        dispatch({ type: "LOAD_DOC", axes: vlEmptyAxes(), revision: 0 });
      }
    })();
    return () => { dead = true; };
  }, [video.id]);

  /* ---------- draft autosave: debounced 1s while dirty + unmount flush */

  useEffect(() => {
    if (!dirty) return undefined;
    const t = setTimeout(() => {
      const s = stateRef.current;
      D.saveDraft(video.id, { doc: { axes: s.doc.axes }, baseRevision: s.revision, rev: s.rev });
    }, 1000);
    return () => clearTimeout(t);
  }, [state.rev, dirty, video.id]);

  useEffect(() => () => {
    const s = stateRef.current;
    if (s.rev !== s.savedRev) {
      D.saveDraft(video.id, { doc: { axes: s.doc.axes }, baseRevision: s.revision, rev: s.rev });
    }
  }, [video.id]);

  /* ---------- M3: suggestions + calibration ---------- */

  /* report fresh stats up to the overlay's per-video cache (queue filters) */
  const publishSuggStats = useCallback((axes) => {
    if (onSuggStats) onSuggStats(video.id, vlSuggStats(axes));
  }, [video.id, onSuggStats]);

  useEffect(() => {
    let dead = false;
    (async () => {
      const r = await D.getSuggestions(video.id);
      if (dead) return;
      if (r.ok && r.data) {
        const axes = vlNormalizeAxes(r.data.axes);
        setSuggestions(axes);
        suggestionsRef.current = axes;
        setSuggInfo({ status: "ok", reason: null });
        publishSuggStats(axes);
      } else if (r.status === 404) {
        /* suggestions API not deployed yet — empty ghosts, quiet chip */
        setSuggInfo({ status: "missing", reason: "suggestions API not deployed" });
      } else {
        setSuggInfo({ status: "offline", reason: r.error || "service unreachable" });
      }
    })();
    return () => { dead = true; };
  }, [video.id, publishSuggStats]);

  useEffect(() => {
    let dead = false;
    (async () => {
      const r = await D.getCalibration();
      if (!dead) setCalibration(r.ok && r.data ? r.data : false);
    })();
    return () => { dead = true; };
  }, [video.id]);

  /* dev/test hook: inject suggestion fixtures from the console without the
     prelabel pipeline (window.__vlDev.injectSuggestions({axes})) */
  useEffect(() => {
    window.__vlDev = {
      injectSuggestions: (axes) => {
        const n = vlNormalizeAxes(axes);
        setSuggestions(n);
        suggestionsRef.current = n;
        setSuggInfo({ status: "ok", reason: null });
        publishSuggStats(n);
      },
    };
    return () => { delete window.__vlDev; };
  }, [publishSuggStats]);

  /* drop reviewed suggestions from the layer (server keeps its rows);
     clears a selection that pointed at one of them */
  const removeSuggestionIds = useCallback((ids) => {
    const idSet = {};
    for (const id of ids) idSet[id] = true;
    const cur = suggestionsRef.current;
    const next = {};
    for (const ax of VL_AXIS_ORDER) next[ax] = (cur[ax] || []).filter((s) => !idSet[s.id]);
    suggestionsRef.current = next;
    setSuggestions(next);
    publishSuggStats(next);
    const sel = stateRef.current.selection;
    if (vlIsSuggestionSel(sel) && idSet[sel.segId]) {
      dispatch({ type: "SELECT", axis: sel.axis, segId: null });
    }
  }, [publishSuggStats]);

  /* ---------- ontology-driven value lists ---------- */

  const canonicalInfo = useCallback((axis) => {
    const axes = (ontology && ontology.axes) || null;
    let values, active;
    if (axis === "activity_primary") {
      const ax = axes && axes.activity_primary;
      values = (ax && ax.values) || D.VL_ACTIVITY;
      active = (ax && ax.active) || values;
    } else if (axis === "posture") {
      const ax = axes && axes.posture;
      values = (ax && ax.values) || D.VL_POSTURE;
      active = (ax && ax.active) || values;
    } else if (axis === "quality") {
      const ax = axes && axes.quality_flags;
      values = (ax && ax.values) || D.VL_QUALITY;
      active = values; // quality has no active subset
    } else {
      values = []; active = [];
    }
    const mk = (v) => ({ value: v, label: String(v).replace(/_/g, " "), color: D.colorFor(v) });
    const activeSet = {};
    for (const v of active) activeSet[v] = true;
    return { active: active.map(mk), rest: values.filter((v) => !activeSet[v]).map(mk) };
  }, [ontology]);

  const customsFor = useCallback((axis) => {
    return ((ontology && ontology.custom) || [])
      .filter((c) => c.status === "active" && (axis === "custom" || c.axis === axis))
      .map((c) => ({
        value: axis === "custom" ? c.slug : "custom:" + c.slug,
        label: String(c.name || c.slug).replace(/_/g, " "),
        color: c.color || D.colorFor("custom:" + c.slug),
        isCustom: true,
      }));
  }, [ontology]);

  const palette = useMemo(() => {
    const axis = state.activeAxis;
    const src = axis === "custom" ? customsFor("custom") : canonicalInfo(axis).active;
    const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"];
    return src.slice(0, 10).map((v, i) => ({ key: keys[i], value: v.value, label: v.label, color: v.color }));
  }, [state.activeAxis, canonicalInfo, customsFor]);
  const paletteRef = useRef(palette);
  paletteRef.current = palette;

  const customColors = useMemo(() => {
    const m = {};
    for (const c of (ontology && ontology.custom) || []) {
      if (c.color) m[c.slug] = c.color;
    }
    return m;
  }, [ontology]);

  const lanes = useMemo(() => {
    const axes = state.doc.axes;
    return [
      { axis: "activity_primary", title: "activity", multiValue: false, segments: axes.activity_primary, colorFor: (v) => D.colorFor(v) },
      { axis: "posture", title: "posture", multiValue: false, segments: axes.posture, colorFor: (v) => D.colorFor(v) },
      { axis: "quality", title: "quality", multiValue: true, segments: axes.quality, colorFor: (v) => D.colorFor(Array.isArray(v) && v.length ? v[0] : v) },
      { axis: "custom", title: "custom", multiValue: false, segments: axes.custom, colorFor: (v) => customColors[v] || D.colorFor("custom:" + String(v)) },
    ];
  }, [state.doc, customColors]);

  /* ---------- editing ops (all read state through stateRef) ---------- */

  const laneOf = useCallback((axis) => stateRef.current.doc.axes[axis] || [], []);

  /* canonical selection only — suggestion selections resolve to null here
     so every M1 editing op safely no-ops on a selected suggestion */
  const selectedSeg = useCallback(() => {
    const s = stateRef.current;
    if (!s.selection || vlIsSuggestionSel(s.selection)) return null;
    return (s.doc.axes[s.selection.axis] || []).find((x) => x.id === s.selection.segId) || null;
  }, []);

  const commitLane = useCallback((axis, next) => {
    const before = stateRef.current.doc.axes[axis] || [];
    dispatch({ type: "SET_SEGMENTS", axis, segments: next, undoEntry: { axis, before, after: next } });
  }, []);

  const defaultValueFor = useCallback((axis) => {
    if (axis === "custom") {
      const cs = customsFor("custom");
      return cs.length ? cs[0].value : null;
    }
    const act = canonicalInfo(axis).active;
    return act.length ? act[0].value : null;
  }, [canonicalInfo, customsFor]);

  /* new segment: default 4s at t, clamped to neighbors, reviewed/human/tmp id */
  const createAt = useCallback((axis, t, value) => {
    const dur = durationOf();
    if (!(dur > 0)) { showToast("video metadata not loaded yet"); return; }
    let v = value != null ? value : defaultValueFor(axis);
    if (v == null) {
      if (axis === "custom") setPicker({ axis: "custom", create: true });
      else showToast("no values available for this lane");
      return;
    }
    if (axis === "quality" && !Array.isArray(v)) v = [v];
    const r = D.createSegmentAt(laneOf(axis), t, v, { duration: dur, minDur, prefDur: 4 });
    if (!r) { showToast("no room for a segment there"); return; }
    commitLane(axis, r.segments);
    dispatch({ type: "SELECT", axis, segId: r.id });
  }, [durationOf, defaultValueFor, laneOf, minDur, commitLane, showToast]);

  /* palette / picker apply: set value on the selection, toggle the flag
     on quality lanes, or create at the playhead with nothing selected */
  const applyValue = useCallback((axis, value) => {
    const s = stateRef.current;
    const lane = s.doc.axes[axis] || [];
    const sel = (s.selection && s.selection.axis === axis)
      ? lane.find((x) => x.id === s.selection.segId) : null;
    if (!sel) { createAt(axis, transport().getTime(), value); return; }
    let next;
    if (axis === "quality") {
      const vals = Array.isArray(sel.value) ? sel.value.slice() : (sel.value ? [sel.value] : []);
      const i = vals.indexOf(value);
      if (i >= 0) vals.splice(i, 1); else vals.push(value);
      next = lane.map((x) => x.id === sel.id ? { ...x, value: vals, review_state: "reviewed" } : x);
    } else {
      next = lane.map((x) => x.id === sel.id ? { ...x, value, review_state: "reviewed" } : x);
    }
    commitLane(axis, next);
  }, [createAt, commitLane, transport]);

  /* S — split the active lane at the playhead; halves inherit; accepted → needs_review */
  const splitAtPlayhead = useCallback(() => {
    const axis = stateRef.current.activeAxis;
    const t = transport().getTime();
    const lane = laneOf(axis);
    const orig = lane.find((x) => t > x.start_s && t < x.end_s);
    if (!orig) { showToast("playhead is not inside a " + VL_AXIS_TITLES[axis] + " segment"); return; }
    if (t - orig.start_s < minDur || orig.end_s - t < minDur) {
      showToast("too close to a boundary (min " + minDur.toFixed(2) + "s)");
      return;
    }
    let next = D.splitAt(lane, t);
    if (next === lane) return;
    if (orig.review_state === "accepted") {
      next = next.map((x) => (x.id === orig.id || (x.start_s === t && x.end_s === orig.end_s))
        ? { ...x, review_state: "needs_review" } : x);
    }
    commitLane(axis, next);
    const right = next.find((x) => x.start_s === t && x.end_s === orig.end_s);
    if (right) dispatch({ type: "SELECT", axis, segId: right.id });
  }, [transport, laneOf, minDur, commitLane, showToast]);

  /* M — merge selection with its right neighbor (prompt on value conflict) */
  const doMerge = useCallback((axis, leftId, keep) => {
    const lane = laneOf(axis).slice().sort((a, b) => a.start_s - b.start_s);
    const i = lane.findIndex((x) => x.id === leftId);
    if (i < 0 || i + 1 >= lane.length) { setMergePrompt(null); return; }
    const left = lane[i];
    const right = lane[i + 1];
    const same = vlValuesEqual(left.value, right.value);
    const merged = {
      ...left,
      end_s: Math.max(left.end_s, right.end_s),
      value: keep === "right" ? right.value : left.value,
      aux: keep === "right" ? right.aux : left.aux,
      review_state: (same && left.review_state === right.review_state) ? left.review_state : "needs_review",
    };
    commitLane(axis, lane.slice(0, i).concat([merged], lane.slice(i + 2)));
    dispatch({ type: "SELECT", axis, segId: merged.id });
    setMergePrompt(null);
  }, [laneOf, commitLane]);

  const mergeSelected = useCallback(() => {
    const s = stateRef.current;
    if (!s.selection) { showToast("select a segment first — M merges it with the next"); return; }
    const axis = s.selection.axis;
    const lane = laneOf(axis).slice().sort((a, b) => a.start_s - b.start_s);
    const i = lane.findIndex((x) => x.id === s.selection.segId);
    if (i < 0) return;
    if (i + 1 >= lane.length) { showToast("no segment to the right"); return; }
    if (vlValuesEqual(lane[i].value, lane[i + 1].value)) { doMerge(axis, lane[i].id, "left"); return; }
    setMergePrompt({
      axis, leftId: lane[i].id,
      leftLabel: vlValueText(lane[i].value), rightLabel: vlValueText(lane[i + 1].value),
    });
  }, [laneOf, doMerge, showToast]);

  /* I/O + inspector bounds + ,/. nudge — all resize ops clip neighbors */
  const setEdgeTo = useCallback((edge, t) => {
    const s = stateRef.current;
    const sel = selectedSeg();
    if (!sel) { showToast("no segment selected"); return; }
    const next = D.resizeSegment(laneOf(s.selection.axis), sel.id, edge, t, {
      duration: durationOf(), minDur, clipNeighbors: true,
    });
    commitLane(s.selection.axis, next);
  }, [selectedSeg, laneOf, durationOf, minDur, commitLane, showToast]);

  const setEdgeToPlayhead = useCallback((edge) => {
    setEdgeTo(edge, transport().getTime());
  }, [setEdgeTo, transport]);

  const nudgeBoundary = useCallback((frames) => {
    const sel = selectedSeg();
    if (!sel) { showToast("no segment selected"); return; }
    const t = transport().getTime();
    const edge = Math.abs(sel.start_s - t) <= Math.abs(sel.end_s - t) ? "start" : "end";
    setEdgeTo(edge, (edge === "start" ? sel.start_s : sel.end_s) + frames / fps);
  }, [selectedSeg, transport, setEdgeTo, fps, showToast]);

  const deleteSelected = useCallback(() => {
    const s = stateRef.current;
    const sel = selectedSeg();
    if (!sel) return;
    commitLane(s.selection.axis, laneOf(s.selection.axis).filter((x) => x.id !== sel.id));
    showToast("segment deleted");
  }, [selectedSeg, laneOf, commitLane, showToast]);

  /* A/R/N/X — POST review for server-known ids (updates doc + revision);
     local-only state change for unsaved tmp segments */
  const reviewSelected = useCallback(async (action) => {
    const s = stateRef.current;
    const sel = selectedSeg();
    if (!sel) { showToast("no segment selected"); return; }
    const axis = s.selection.axis;
    const localState = VL_REVIEW_RESULT[action];
    if (vlIsTempId(sel.id) || labelsApiRef.current.status !== "ok") {
      commitLane(axis, laneOf(axis).map((x) => x.id === sel.id ? { ...x, review_state: localState } : x));
      showToast(localState.replace(/_/g, " ") + " · local (syncs on save)");
      return;
    }
    const r = await D.reviewSegment(video.id, sel.id, action);
    if (r.ok && r.data) {
      dispatch({
        type: "APPLY_REVIEW", axis, segId: sel.id,
        patch: { review_state: (r.data.segment && r.data.segment.review_state) || localState },
        revision: r.data.revision,
      });
      showToast(localState.replace(/_/g, " "));
    } else {
      showToast("review failed · " + (r.error || "service unreachable"));
    }
  }, [selectedSeg, laneOf, commitLane, video.id, showToast]);

  /* ---------- M3: suggestion review (accept graduates into the lane) ---- */

  /* A/R/N/X with a SUGGESTION selected. Suggestions are server rows only —
     no local/tmp path — so the labels API must be reachable. accept /
     needs_review / exclude graduate the row out of the prelabel layer
     server-side: project the returned canonical segment into the doc and
     adopt the bumped revision. reject only adopts the revision — the doc
     deliberately does NOT take the rejected row (it would overlap real
     labels and PUT enforces non-overlap); the next save retires it and the
     record survives as history + the calibration verdict already counted. */
  const reviewSuggestion = useCallback(async (action) => {
    const sel = stateRef.current.selection;
    if (!vlIsSuggestionSel(sel)) return;
    const seg = (suggestionsRef.current[sel.axis] || []).find((x) => x.id === sel.segId);
    if (!seg) return;
    if (labelsApiRef.current.status !== "ok") {
      showToast("review unavailable · " + (labelsApiRef.current.reason || "labels API offline"));
      return;
    }
    const r = await D.reviewSegment(video.id, seg.id, action);
    if (!(r.ok && r.data)) {
      showToast("review failed · " + (r.error || "service unreachable"));
      return;
    }
    removeSuggestionIds([seg.id]);
    const clean = { ...(r.data.segment || {}) };
    delete clean.axis; // server echoes the axis; the doc shape carries none
    if (action === "reject") {
      dispatch({ type: "GRADUATE", items: [], revision: r.data.revision });
      showToast("rejected · " + vlValueText(seg.value));
    } else {
      dispatch({
        type: "GRADUATE",
        items: clean.id ? [{ axis: sel.axis, segment: clean }] : [],
        revision: r.data.revision, minDur,
      });
      if (clean.id) dispatch({ type: "SELECT", kind: "canonical", axis: sel.axis, segId: clean.id });
      showToast((VL_REVIEW_RESULT[action] || action).replace(/_/g, " ") + " · " + vlValueText(seg.value));
    }
  }, [video.id, minDur, removeSuggestionIds, showToast]);

  /* sequential accepts over a target list — shared by Shift+A and the
     (calibration-gated) bulk button; failures collect into ONE toast */
  const acceptList = useCallback(async (targets) => {
    if (bulkBusyRef.current || !targets.length) return;
    if (labelsApiRef.current.status !== "ok") {
      showToast("review unavailable · " + (labelsApiRef.current.reason || "labels API offline"));
      return;
    }
    bulkBusyRef.current = true;
    const okIds = [];
    const okItems = [];
    const failures = [];
    let revision = null;
    for (const t of targets) {
      const r = await D.reviewSegment(video.id, t.seg.id, "accept");
      if (r.ok && r.data) {
        if (r.data.revision != null) revision = r.data.revision;
        const clean = { ...(r.data.segment || {}) };
        delete clean.axis;
        if (clean.id) {
          okItems.push({ axis: t.axis, segment: clean });
          okIds.push(t.seg.id);
        }
      } else {
        failures.push(vlValueText(t.seg.value) + " · " + (r.error || "error"));
      }
    }
    bulkBusyRef.current = false;
    if (okIds.length) {
      removeSuggestionIds(okIds);
      dispatch({ type: "GRADUATE", items: okItems, revision, minDur });
    }
    showToast(failures.length
      ? ("accepted " + okIds.length + " · " + failures.length + " failed: "
         + failures.slice(0, 3).join("; ") + (failures.length > 3 ? " +" + (failures.length - 3) + " more" : ""))
      : ("accepted " + okIds.length + " suggestion" + (okIds.length === 1 ? "" : "s")));
  }, [video.id, minDur, removeSuggestionIds, showToast]);

  /* Shift+A — accept ALL suggestions overlapping the selected suggestion's
     time range, across every axis */
  const acceptOverlapping = useCallback(() => {
    const sel = stateRef.current.selection;
    if (!vlIsSuggestionSel(sel)) {
      showToast("select a suggestion first — shift+A accepts everything overlapping it");
      return;
    }
    const anchor = (suggestionsRef.current[sel.axis] || []).find((x) => x.id === sel.segId);
    if (!anchor) return;
    const targets = [];
    for (const ax of VL_AXIS_ORDER) {
      for (const seg of suggestionsRef.current[ax] || []) {
        if (seg.start_s < anchor.end_s - 1e-6 && seg.end_s > anchor.start_s + 1e-6) {
          targets.push({ axis: ax, seg });
        }
      }
    }
    acceptList(targets);
  }, [acceptList, showToast]);

  /* bulk accept — NEVER enabled unless the server's calibration doc says
     bulk_ok for an axis; then accepts that axis's conf ≥ 0.8 suggestions */
  const bulkAcceptCalibrated = useCallback(() => {
    const cal = calibrationRef.current;
    if (!cal || !cal.axes) return;
    const targets = [];
    for (const ax of VL_AXIS_ORDER) {
      const c = cal.axes[ax];
      if (!c || !c.bulk_ok) continue;
      for (const seg of suggestionsRef.current[ax] || []) {
        if (typeof seg.confidence === "number" && seg.confidence >= 0.8) {
          targets.push({ axis: ax, seg });
        }
      }
    }
    if (!targets.length) {
      showToast("no conf ≥ 0.8 suggestions on calibrated axes");
      return;
    }
    acceptList(targets);
  }, [acceptList, showToast]);

  /* P — toggle quality 'private_skip' over the selection's time range */
  const togglePrivate = useCallback(() => {
    const sel = selectedSeg();
    if (!sel) { showToast("select a segment — P flags its range private"); return; }
    const ql = laneOf("quality");
    const hasFlag = (x) => (Array.isArray(x.value) ? x.value : [x.value]).indexOf("private_skip") >= 0;
    const overlapping = ql.filter((x) => x.start_s < sel.end_s && x.end_s > sel.start_s && hasFlag(x));
    if (overlapping.length) {
      const next = ql.map((x) => {
        if (overlapping.indexOf(x) < 0) return x;
        const vals = (Array.isArray(x.value) ? x.value : [x.value]).filter((v) => v !== "private_skip");
        return vals.length ? { ...x, value: vals } : null;
      }).filter(Boolean);
      commitLane("quality", next);
      showToast("private_skip removed");
    } else {
      const carved = D.carveRange(ql, sel.start_s, sel.end_s, minDur);
      const seg = {
        id: D.newTempId(), start_s: sel.start_s, end_s: sel.end_s,
        value: ["private_skip"], review_state: "reviewed", source: "human",
        confidence: null, aux: null, person_slot: null,
      };
      commitLane("quality", carved.concat([seg]).sort((a, b) => a.start_s - b.start_s));
      showToast("range flagged private_skip");
    }
  }, [selectedSeg, laneOf, minDur, commitLane, showToast]);

  /* U / shift+U — jump to the next/prev canonical prelabel|needs_review
     segment; when none exists past the anchor, fall through to the next
     SUGGESTION (M3) so U walks the whole review queue */
  const jumpUnreviewed = useCallback((dir) => {
    const s = stateRef.current;
    const pick = (cands, anchor) => dir > 0
      ? cands.find((c) => c.seg.start_s > anchor + 1e-6)
      : cands.slice().reverse().find((c) => c.seg.start_s < anchor - 1e-6);
    const cands = [];
    for (const axis of VL_AXIS_ORDER) {
      for (const seg of s.doc.axes[axis] || []) {
        if (seg.review_state === "prelabel" || seg.review_state === "needs_review") {
          cands.push({ kind: "canonical", axis, seg });
        }
      }
    }
    cands.sort((a, b) => a.seg.start_s - b.seg.start_s);
    /* anchor: the selected segment OR suggestion's start; else the playhead */
    let anchor = transport().getTime();
    const sel = s.selection;
    if (sel) {
      const src = vlIsSuggestionSel(sel) ? suggestionsRef.current : s.doc.axes;
      const seg = (src[sel.axis] || []).find((x) => x.id === sel.segId);
      if (seg) anchor = seg.start_s;
    }
    let next = pick(cands, anchor);
    if (!next) {
      const sugs = [];
      for (const axis of VL_AXIS_ORDER) {
        for (const seg of suggestionsRef.current[axis] || []) {
          sugs.push({ kind: "suggestion", axis, seg });
        }
      }
      sugs.sort((a, b) => a.seg.start_s - b.seg.start_s);
      next = pick(sugs, anchor);
    }
    if (!next) {
      showToast(dir > 0 ? "nothing to review after this" : "nothing to review before this");
      return;
    }
    dispatch({ type: "SELECT", kind: next.kind, axis: next.axis, segId: next.seg.id });
    transport().seekTo(next.seg.start_s);
  }, [transport, showToast]);

  const selectAdjacent = useCallback((dir) => {
    const s = stateRef.current;
    const axis = s.activeAxis;
    const lane = (s.doc.axes[axis] || []).slice().sort((a, b) => a.start_s - b.start_s);
    if (!lane.length) return;
    let next;
    const idx = (s.selection && s.selection.axis === axis)
      ? lane.findIndex((x) => x.id === s.selection.segId) : -1;
    if (idx < 0) {
      const t = transport().getTime();
      next = dir > 0
        ? (lane.find((x) => x.start_s >= t) || lane[lane.length - 1])
        : (lane.slice().reverse().find((x) => x.start_s <= t) || lane[0]);
    } else {
      next = lane[Math.max(0, Math.min(lane.length - 1, idx + dir))];
    }
    if (next) dispatch({ type: "SELECT", axis, segId: next.id });
  }, [transport]);

  const cycleAxis = useCallback((dir) => {
    const i = VL_AXIS_ORDER.indexOf(stateRef.current.activeAxis);
    dispatch({ type: "SET_ACTIVE_AXIS", axis: VL_AXIS_ORDER[(i + dir + VL_AXIS_ORDER.length) % VL_AXIS_ORDER.length] });
  }, []);

  const setAux = useCallback((patch) => {
    const s = stateRef.current;
    const sel = selectedSeg();
    if (!sel) return;
    const aux = { ...(sel.aux || {}), ...patch };
    commitLane(s.selection.axis, laneOf(s.selection.axis).map((x) => x.id === sel.id ? { ...x, aux } : x));
  }, [selectedSeg, laneOf, commitLane]);

  /* ---------- save flow (pessimistic PUT + 409 banner) ---------- */

  const selectNextVideo = useCallback(() => {
    const list = videos || [];
    const i = list.findIndex((v) => v.id === video.id);
    if (i >= 0 && i + 1 < list.length) onPickVideo(list[i + 1].id);
    else showToast("last video in the list");
  }, [videos, video.id, onPickVideo, showToast]);

  const applySaveResult = useCallback((r, revAtStart, thenNext) => {
    if (r.ok && r.data) {
      dispatch({ type: "MARK_SAVED", revision: r.data.revision, idMap: r.data.id_map || {}, savedRev: revAtStart });
      D.clearDraft(video.id);
      setConflict(null);
      showToast("saved · revision " + r.data.revision);
      if (thenNext) selectNextVideo();
    } else if (r.status === 409 && r.data) {
      setConflict({ revision: r.data.revision, axes: r.data.axes || {} });
    } else if (r.status === 400) {
      showToast("save rejected · " + (r.error || "validation error"));
    } else {
      showToast("save failed · " + (r.error || "service unreachable"));
    }
  }, [video.id, showToast, selectNextVideo]);

  const doSave = useCallback(async (thenNext) => {
    const s = stateRef.current;
    if (labelsApiRef.current.status !== "ok") {
      showToast("saving disabled · " + (labelsApiRef.current.reason || "labels API unavailable"));
      return;
    }
    if (savingRef.current) return;
    if (s.rev === s.savedRev) {
      if (thenNext) selectNextVideo();
      else showToast("nothing to save");
      return;
    }
    savingRef.current = true;
    setSaving(true);
    const revAtStart = s.rev;
    const r = await D.putLabels(video.id, { revision: s.revision, axes: s.doc.axes });
    savingRef.current = false;
    setSaving(false);
    applySaveResult(r, revAtStart, thenNext);
  }, [video.id, showToast, selectNextVideo, applySaveResult]);

  const overwriteConflict = useCallback(async () => {
    if (!conflict || savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    const s = stateRef.current;
    const r = await D.putLabels(video.id, { revision: conflict.revision, axes: s.doc.axes });
    savingRef.current = false;
    setSaving(false);
    applySaveResult(r, s.rev, false);
  }, [conflict, video.id, applySaveResult]);

  const reloadFromConflict = useCallback(() => {
    if (!conflict) return;
    dispatch({ type: "LOAD_DOC", axes: vlNormalizeAxes(conflict.axes), revision: conflict.revision });
    D.clearDraft(video.id);
    setConflict(null);
    showToast("reloaded server copy · revision " + conflict.revision);
  }, [conflict, video.id, showToast]);

  /* custom-label create (picker): POST → refresh ontology → apply */
  const createCustomAndApply = useCallback(async (axis, name, color) => {
    const payload = { axis, name };
    if (color) payload.color = color;
    const r = await D.createCustomLabel(payload);
    if (!r.ok) {
      showToast("create failed · " + (r.error || "custom labels API unavailable"));
      return false;
    }
    const slug = (r.data && ((r.data.custom_label && r.data.custom_label.slug)
        || r.data.slug || (r.data.label && r.data.label.slug)))
      || name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
    refreshOntology();
    applyValue(axis, axis === "custom" ? slug : "custom:" + slug);
    showToast("custom label created · " + slug);
    return true;
  }, [applyValue, refreshOntology, showToast]);

  /* ---------- keyboard map (input-focus guard FIRST, capture phase) --- */

  const keyHandlerRef = useRef(null);
  keyHandlerRef.current = (e) => {
    const tr = transport();
    const key = e.key;
    const ctrl = e.ctrlKey || e.metaKey;
    const handled = () => { e.preventDefault(); e.stopPropagation(); };
    if (key === "Escape") return;            // overlay owns Escape (incl. picker close)
    if (picker || mergePrompt) return;       // modal editor surfaces own the keyboard
    if (ctrl) {
      const k = key.length === 1 ? key.toLowerCase() : key;
      if (k === "z") { handled(); dispatch({ type: e.shiftKey ? "REDO" : "UNDO" }); return; }
      if (k === "y") { handled(); dispatch({ type: "REDO" }); return; }
      if (k === "s") { handled(); doSave(false); return; }
      if (key === "Enter") { handled(); doSave(true); return; }
      if (key === "ArrowLeft") { handled(); tr.skip(-10); return; }
      if (key === "ArrowRight") { handled(); tr.skip(10); return; }
      return; // never claim other ctrl combos (Ctrl+K is the app's)
    }
    if (e.altKey) return;
    switch (key) {
      case " ": handled(); tr.togglePlay(); return;
      case "ArrowLeft": handled(); if (e.shiftKey) tr.skip(-1); else tr.stepFrame(-1); return;
      case "ArrowRight": handled(); if (e.shiftKey) tr.skip(1); else tr.stepFrame(1); return;
      case "ArrowUp": handled(); selectAdjacent(-1); return;
      case "ArrowDown": handled(); selectAdjacent(1); return;
      case "Tab": handled(); cycleAxis(e.shiftKey ? -1 : 1); return;
      default: break;
    }
    if (key.length === 1 && key >= "0" && key <= "9") {
      handled();
      const idx = key === "0" ? 9 : Number(key) - 1;
      const p = paletteRef.current[idx];
      if (p) applyValue(stateRef.current.activeAxis, p.value);
      return;
    }
    const k = key.length === 1 ? key.toLowerCase() : key.toLowerCase();
    switch (k) {
      case "e": handled(); setPicker({ axis: stateRef.current.activeAxis, create: false }); return;
      case "c": handled(); setPicker({ axis: stateRef.current.activeAxis, create: true }); return;
      case "i": handled(); setEdgeToPlayhead("start"); return;
      case "o": handled(); setEdgeToPlayhead("end"); return;
      case "s": handled(); splitAtPlayhead(); return;
      case "m": handled(); mergeSelected(); return;
      case ",": handled(); nudgeBoundary(-1); return;
      case "<": handled(); nudgeBoundary(-10); return;
      case ".": handled(); nudgeBoundary(1); return;
      case ">": handled(); nudgeBoundary(10); return;
      case "delete": case "backspace": handled(); deleteSelected(); return;
      /* A/R/N/X route to the suggestion review flow when a SUGGESTION is
         selected; Shift+A bulk-accepts everything overlapping it */
      case "a":
        handled();
        if (vlIsSuggestionSel(stateRef.current.selection)) {
          if (e.shiftKey) acceptOverlapping(); else reviewSuggestion("accept");
        } else reviewSelected("accept");
        return;
      case "r":
        handled();
        if (vlIsSuggestionSel(stateRef.current.selection)) reviewSuggestion("reject");
        else reviewSelected("reject");
        return;
      case "n":
        handled();
        if (vlIsSuggestionSel(stateRef.current.selection)) reviewSuggestion("needs_review");
        else reviewSelected("needs_review");
        return;
      case "x":
        handled();
        if (vlIsSuggestionSel(stateRef.current.selection)) reviewSuggestion("exclude");
        else reviewSelected("exclude");
        return;
      case "p": handled(); togglePrivate(); return;
      case "u": handled(); jumpUnreviewed(e.shiftKey ? -1 : 1); return;
      case "f": handled(); tr.zoomFit(); return;
      case "+": case "=": handled(); tr.zoomBy(1); return;
      case "-": case "_": handled(); tr.zoomBy(-1); return;
      case "?": handled(); setCheat((c) => !c); return;
      default: return;
    }
  };

  useEffect(() => {
    const onKey = (e) => {
      /* input-focus guard FIRST — single-letter destructive keys must
         never fire while typing in a field */
      const t = e.target;
      if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
      if (t && t.tagName === "BUTTON" && (e.key === " " || e.key === "Enter")) return;
      if (keyHandlerRef.current) keyHandlerRef.current(e);
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, []);

  /* the overlay's capture-phase Escape consults this before closing */
  useEffect(() => {
    if (!escInterceptRef) return undefined;
    escInterceptRef.current = () => {
      if (picker) { setPicker(null); return true; }
      if (mergePrompt) { setMergePrompt(null); return true; }
      if (cheat) { setCheat(false); return true; }
      return false;
    };
    return () => { escInterceptRef.current = null; };
  }, [escInterceptRef, picker, mergePrompt, cheat]);

  /* ---------- wiring ---------- */

  const editorProps = useMemo(() => ({
    lanes,
    selection: state.selection,
    activeAxis: state.activeAxis,
    axisTitle: VL_AXIS_TITLES[state.activeAxis],
    minDur,
    palette,
    suggestions,
    transportRef,
    onPalettePick: (v) => applyValue(stateRef.current.activeAxis, v),
    onSelect: (axis, segId) => dispatch({ type: "SELECT", axis, segId }),
    onCommit: (axis, segments, undoEntry) => dispatch({ type: "SET_SEGMENTS", axis, segments, undoEntry }),
    onCreate: (axis, t) => createAt(axis, t, null),
    onSelectSuggestion: (axis, segId) => dispatch({ type: "SELECT", kind: "suggestion", axis, segId }),
  }), [lanes, state.selection, state.activeAxis, minDur, palette, suggestions, applyValue, createAt]);

  const ops = useMemo(() => ({
    openPicker: (create) => setPicker({ axis: stateRef.current.activeAxis, create: !!create }),
    setStart: (t) => setEdgeTo("start", t),
    setEnd: (t) => setEdgeTo("end", t),
    review: reviewSelected,
    deleteSelected,
    setAux,
    save: () => doSave(false),
    saveNext: () => doSave(true),
  }), [setEdgeTo, reviewSelected, deleteSelected, setAux, doSave]);

  const selSeg = (state.selection && !vlIsSuggestionSel(state.selection))
    ? (state.doc.axes[state.selection.axis] || []).find((x) => x.id === state.selection.segId) || null
    : null;
  const selSugg = vlIsSuggestionSel(state.selection)
    ? (suggestions[state.selection.axis] || []).find((x) => x.id === state.selection.segId) || null
    : null;

  /* suggestions summary for the inspector section */
  const suggSummary = useMemo(
    () => ({ status: suggInfo.status, reason: suggInfo.reason, ...vlSuggStats(suggestions) }),
    [suggInfo, suggestions]
  );

  /* bulk-accept gate: locked until the server's calibration doc clears an
     axis (wilson lower bound ≥ wilson_min over the conf ≥ 0.8 verdicts).
     The tooltip shows "n so far / n needed" per axis — n needed is the
     MINIMUM verdict count that could clear the gate even at 100%
     acceptance: wilson_lower(n,n) ≥ m  ⇔  n ≥ z²·m/(1−m). */
  const bulkInfo = useMemo(() => {
    if (calibration === null) return { ok: false, tip: "bulk accept locked — calibration loading…" };
    if (!calibration || !calibration.axes) {
      return { ok: false, tip: "bulk accept locked — calibration API unavailable" };
    }
    const m = typeof calibration.wilson_min === "number" ? calibration.wilson_min : 0.95;
    const z = 1.96;
    const need = Math.ceil((z * z) * m / (1 - m));
    const parts = [];
    let anyOk = false;
    for (const ax of VL_AXIS_ORDER) {
      const c = calibration.axes[ax];
      if (!c) continue;
      const td = c.top_deciles || {};
      parts.push((VL_AXIS_TITLES[ax] || ax) + " " + (td.n || 0) + "/" + need
        + (c.bulk_ok ? " ✓" : ""));
      if (c.bulk_ok) anyOk = true;
    }
    return {
      ok: anyOk,
      tip: "unlocks per axis at wilson ≥ " + m + " over conf ≥ 0.8 human verdicts — "
        + (parts.length ? parts.join(" · ") : "no calibration data yet"),
    };
  }, [calibration]);

  const suggestionOps = useMemo(() => ({
    review: reviewSuggestion,
    acceptOverlapping,
    seek: (t) => transport().seekTo(t),
  }), [reviewSuggestion, acceptOverlapping, transport]);

  const bulk = useMemo(
    () => ({ ok: bulkInfo.ok, tip: bulkInfo.tip, run: bulkAcceptCalibrated }),
    [bulkInfo, bulkAcceptCalibrated]
  );

  const pickerValues = useMemo(() => {
    if (!picker) return [];
    const customs = customsFor(picker.axis);
    if (picker.axis === "custom") return customs;
    const info = canonicalInfo(picker.axis);
    return info.active.concat(customs, info.rest.map((v) => ({ ...v, inactive: true })));
  }, [picker, canonicalInfo, customsFor]);

  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", minHeight: 0 }}>
      {/* center column — banners + player + popovers */}
      <div style={{
        flex: 1, minWidth: 0, minHeight: 0,
        display: "flex", flexDirection: "column", position: "relative",
      }}>
        {draftBanner && (
          <VLBanner tone="ice">
            <span>
              unsaved draft from {new Date(draftBanner.ts || Date.now()).toLocaleString()}
              {" · "}{draftBanner.count} segments
              {draftBanner.dropped > 0 ? " (" + draftBanner.dropped + " dropped — ids no longer exist)" : ""}
            </span>
            <button style={VL_BTN_SM} onClick={() => {
              dispatch({ type: "RESTORE_DRAFT", axes: draftBanner.axes });
              setDraftBanner(null);
            }}>restore {draftBanner.count} segments</button>
            <button style={VL_BTN_SM} onClick={() => {
              D.clearDraft(video.id);
              setDraftBanner(null);
            }}>discard</button>
          </VLBanner>
        )}
        {conflict && (
          <VLBanner tone="warn">
            <span>save conflict — the server is at revision {conflict.revision} (yours: {state.revision})</span>
            <button style={VL_BTN_SM} onClick={reloadFromConflict}>reload server copy</button>
            <button style={{ ...VL_BTN_SM, color: "var(--hg-warn)" }} onClick={overwriteConflict}
              disabled={saving}>overwrite</button>
          </VLBanner>
        )}
        {mergePrompt && (
          <VLBanner tone="warn">
            <span>merge conflict — keep which value?</span>
            <button style={VL_BTN_SM} onClick={() => doMerge(mergePrompt.axis, mergePrompt.leftId, "left")}>
              left · {mergePrompt.leftLabel}
            </button>
            <button style={VL_BTN_SM} onClick={() => doMerge(mergePrompt.axis, mergePrompt.leftId, "right")}>
              right · {mergePrompt.rightLabel}
            </button>
            <button style={VL_BTN_SM} onClick={() => setMergePrompt(null)}>cancel</button>
          </VLBanner>
        )}

        <VLPlayer video={video} editor={editorProps} />

        {picker && (
          <VLLabelPicker
            axisTitle={VL_AXIS_TITLES[picker.axis]}
            values={pickerValues}
            createMode={picker.create}
            onApply={(v) => applyValue(picker.axis, v)}
            onCreateCustom={(name, color) => createCustomAndApply(picker.axis, name, color)}
            onClose={() => setPicker(null)}
          />
        )}
        {cheat && <VLCheatSheet onClose={() => setCheat(false)} />}
      </div>

      {/* right rail — inspector */}
      <VLInspector
        video={video}
        fps={fps}
        ontology={ontology}
        labelsApi={labelsApi}
        dirty={dirty}
        saving={saving}
        revision={state.revision}
        selection={state.selection}
        segment={selSeg}
        ops={ops}
        canonicalInfo={canonicalInfo}
        refreshOntology={refreshOntology}
        showToast={showToast}
        suggestion={selSugg}
        suggestionOps={suggestionOps}
        suggSummary={suggSummary}
        bulk={bulk}
      />
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
 * VLJobsPanel — table of pipeline jobs. Polls 5s while anything is
 * running/queued, 30s when idle, and only while this tab is mounted AND
 * the document is visible.
 * ──────────────────────────────────────────────────────────────────── */
function VLJobsPanel({ showToast }) {
  const D = window.HomeVideoLabelerData;
  const [jobs, setJobs] = useState(null);   // null=loading, []=empty
  const [err, setErr] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [bump, setBump] = useState(0);      // cancel/retry → immediate re-poll

  useEffect(() => {
    let alive = true;
    let timer = null;
    const tick = async () => {
      if (!alive) return;
      let active = false;
      if (!document.hidden) {
        const r = await D.listJobs();
        if (!alive) return;
        if (r.ok) {
          const list = (r.data && r.data.jobs) || [];
          setJobs(list);
          setErr(null);
          active = list.some((j) => j.state === "running" || j.state === "queued");
        } else {
          setErr(r.error);
          setJobs((cur) => (cur === null ? [] : cur));
        }
      }
      timer = setTimeout(tick, active ? 5000 : 30000);
    };
    tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [bump]);

  const act = useCallback(async (job, kind) => {
    setBusyId(job.id);
    const r = kind === "cancel" ? await D.cancelJob(job.id) : await D.retryJob(job.id);
    setBusyId(null);
    if (r.ok) {
      showToast("job " + kind + " · ok");
      setBump((b) => b + 1);
    } else {
      showToast(kind + " failed · " + (r.error || "service unreachable"));
    }
  }, [showToast]);

  const stateColor = (s) => s === "running" ? "var(--hg-ice)"
    : s === "queued" ? "var(--hg-fg-3)"
    : s === "succeeded" ? "var(--hg-fg-4)"
    : s === "failed" ? "var(--hg-crit)"
    : "var(--hg-fg-5)";

  return (
    <div className="hg-scroll" style={{ flex: 1, overflow: "auto", padding: "18px 24px", fontFamily: VL_FONT_MONO }}>
      {err && (
        <div style={{
          border: "1px solid var(--hg-warn)",
          background: "color-mix(in oklab, var(--hg-warn) 6%, transparent)",
          padding: "10px 14px", color: "var(--hg-warn)",
          fontSize: 11, letterSpacing: "0.04em", marginBottom: 14,
        }}>
          <strong style={{ marginRight: 8 }}>jobs unavailable:</strong>{err}
        </div>
      )}
      {jobs === null && !err && (
        <div style={{ color: "var(--hg-fg-3)", fontSize: 11 }}>loading jobs…</div>
      )}
      {jobs !== null && jobs.length === 0 && !err && (
        <div style={{ color: "var(--hg-fg-4)", fontSize: 11, letterSpacing: "0.08em" }}>
          no jobs yet — import inbox to queue the first probe/proxy run
        </div>
      )}
      {jobs !== null && jobs.length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10.5 }}>
          <thead>
            <tr>
              {["type", "state", "progress", "detail", ""].map((h) => (
                <th key={h} style={{
                  textAlign: "left", padding: "6px 10px",
                  borderBottom: "1px solid var(--hg-border)",
                  color: "var(--hg-fg-4)", fontSize: 8.5,
                  letterSpacing: "0.18em", textTransform: "lowercase", fontWeight: 500,
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id} style={{ borderBottom: "1px solid var(--hg-border-soft)" }}>
                <td style={{ padding: "8px 10px", color: "var(--hg-fg-1)" }}>
                  {j.type}
                  <span style={{ color: "var(--hg-fg-5)", marginLeft: 8, fontSize: 9 }}>{j.lane || ""}</span>
                </td>
                <td style={{ padding: "8px 10px", color: stateColor(j.state), letterSpacing: "0.1em" }}>
                  {j.state}{j.attempts > 1 ? " ·" + j.attempts : ""}
                </td>
                <td style={{ padding: "8px 10px", width: 160 }}>
                  <div style={{ width: 140, height: 3, background: "var(--hg-bg-3)" }}>
                    <div style={{
                      width: Math.round(Math.max(0, Math.min(1, j.progress || 0)) * 140),
                      height: 3,
                      background: j.state === "failed" ? "var(--hg-crit)" : "var(--hg-ice)",
                      transition: "width 400ms ease",
                    }} />
                  </div>
                </td>
                <td style={{
                  padding: "8px 10px", maxWidth: 360, overflow: "hidden",
                  textOverflow: "ellipsis", whiteSpace: "nowrap",
                  color: j.error ? "var(--hg-crit)" : "var(--hg-fg-4)", fontSize: 9.5,
                }} title={j.error || j.progress_msg || ""}>
                  {j.error || j.progress_msg || "—"}
                </td>
                <td style={{ padding: "8px 10px", textAlign: "right", whiteSpace: "nowrap" }}>
                  {(j.state === "running" || j.state === "queued") && (
                    <button style={VL_BTN_SM} disabled={busyId === j.id}
                      onClick={() => act(j, "cancel")}>cancel</button>
                  )}
                  {(j.state === "failed" || j.state === "cancelled") && (
                    <button style={VL_BTN_SM} disabled={busyId === j.id}
                      onClick={() => act(j, "retry")}>retry</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
 * HomeVideoLabelerOverlay — full-app overlay (z 2100; people's panels
 * sit at 1100/2000). Opens from the header film-strip button or
 * /labeler · /vl; closes via Escape (capture-phase, stops the app's own
 * window Escape handler) or the close button.
 * ──────────────────────────────────────────────────────────────────── */
function HomeVideoLabelerOverlay({ open, onClose, sim }) {
  const D = window.HomeVideoLabelerData;
  const simActive = !!(sim && sim.active);
  const [tab, setTab] = useState("label");      // label | jobs
  const [videos, setVideos] = useState(null);   // null=loading, []=empty
  const [videosErr, setVideosErr] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [healthInfo, setHealthInfo] = useState(null); // null=unknown, false=down, object=live
  const [importing, setImporting] = useState(false);
  const [toast, setToast] = useState(null);
  const [ontology, setOntology] = useState(null);     // null=loading, false=offline, object=loaded
  /* M3 queue filters: per-video suggestion stats are cached here as videos
     get opened (the editor loads /suggestions for its own video only) */
  const [queueFilter, setQueueFilter] = useState("all");
  const [suggStats, setSuggStats] = useState({});     // {videoId: {total, lowConf, disagreement}}
  const wasMaximizedRef = useRef(null);
  const toastTimerRef = useRef(null);
  /* VLEditor installs a callback here; the capture-phase Escape handler
     consults it so picker/prompt/cheat-sheet close before the overlay */
  const escInterceptRef = useRef(null);

  const showToast = useCallback((text) => {
    setToast(text);
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = setTimeout(() => setToast(null), 2800);
  }, []);
  useEffect(() => () => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
  }, []);

  const refresh = useCallback(async () => {
    if (simActive) return;
    const [hv, lv] = await Promise.all([D.health(), D.listVideos()]);
    setHealthInfo(hv.ok ? hv.data : false);
    if (lv.ok) {
      const list = (lv.data && lv.data.videos) || [];
      setVideos(list);
      setVideosErr(null);
      setSelectedId((cur) => (cur && list.some((v) => v.id === cur)) ? cur : (list[0] ? list[0].id : null));
    } else {
      setVideos([]);
      setVideosErr(lv.error);
    }
  }, [simActive]);

  /* load on open + keep the health chip honest (20s repoll) */
  useEffect(() => {
    if (!open || simActive) return undefined;
    refresh();
    const iv = setInterval(async () => {
      if (document.hidden) return;
      const hv = await D.health();
      setHealthInfo(hv.ok ? hv.data : false);
    }, 20000);
    return () => clearInterval(iv);
  }, [open, simActive, refresh]);

  /* ontology — once per open (custom-label mutations re-fetch via this) */
  const refreshOntology = useCallback(async () => {
    if (simActive) return;
    const r = await D.getOntology();
    setOntology(r.ok && r.data ? r.data : false);
  }, [simActive]);
  useEffect(() => {
    if (open && !simActive) refreshOntology();
  }, [open, simActive, refreshOntology]);

  /* maximize on open / restore on close — the 2-pane body is unusable at
     the default 820×900 (home-apartment.jsx precedent) */
  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const w = await window.getTauriWindow?.();
        if (w && !cancelled) {
          wasMaximizedRef.current = await w.isMaximized?.();
          if (!wasMaximizedRef.current && !cancelled) await w.maximize?.();
        }
      } catch (e) { /* browser mode */ }
    })();
    return () => {
      cancelled = true;
      (async () => {
        try {
          const w = await window.getTauriWindow?.();
          if (w && wasMaximizedRef.current === false) await w.unmaximize?.();
        } catch (e) { /* */ }
      })();
    };
  }, [open]);

  /* Escape: capture-phase + stopImmediatePropagation — the app's own
     window-level Escape handler mutates chat state (pending confirms) and
     must NOT see this press. Input-focus guard: never steal Escape from a
     focused field. Editor surfaces (picker/merge prompt/cheat sheet) get
     first claim via escInterceptRef. */
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      const t = e.target;
      if (t && (/INPUT|TEXTAREA|SELECT/.test(t.tagName) || t.isContentEditable)) return;
      e.stopImmediatePropagation();
      e.preventDefault();
      if (escInterceptRef.current && escInterceptRef.current()) return;
      onClose?.();
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, [open, onClose]);

  const importInbox = useCallback(async () => {
    setImporting(true);
    const r = await D.importManual();
    setImporting(false);
    if (r.ok) {
      const job = r.data && r.data.job;
      showToast(job ? ("import queued · job " + job.id) : "import queued");
      refresh();
    } else {
      showToast("import failed · " + (r.error || "service unreachable"));
    }
  }, [refresh, showToast]);

  const onSuggStats = useCallback((videoId, stats) => {
    setSuggStats((cur) => ({ ...cur, [videoId]: stats }));
  }, []);

  if (!open) return null;

  const base = D.vlBase();
  const healthLive = !!(healthInfo && healthInfo !== false);
  const selected = (videos || []).find((v) => v.id === selectedId) || null;

  /* queue filter: HONEST client-side triage over cached stats only —
     matches sorted by the filter's metric (desc), then videos we have no
     data for (never opened → never loaded /suggestions); cached videos
     with a zero metric are filtered out (the selected one stays). */
  const visibleVideos = (() => {
    const list = videos || [];
    if (queueFilter === "all") return list;
    const metric = (st) => !st ? -1
      : queueFilter === "unreviewed" ? st.total
      : queueFilter === "low-conf" ? st.lowConf
      : st.disagreement;
    const matches = [];
    const unknown = [];
    for (const v of list) {
      const st = suggStats[v.id];
      if (!st) unknown.push(v);
      else if (metric(st) > 0 || v.id === selectedId) matches.push(v);
    }
    matches.sort((a, b) => metric(suggStats[b.id]) - metric(suggStats[a.id]));
    return matches.concat(unknown);
  })();

  const tabBtn = (id) => (
    <button
      key={id}
      onClick={() => setTab(id)}
      style={{
        background: "transparent", border: "none", padding: "6px 12px",
        fontFamily: VL_FONT_MONO, fontSize: 11, letterSpacing: "0.16em",
        textTransform: "lowercase", cursor: "pointer",
        color: tab === id ? "var(--hg-fg-0)" : "var(--hg-fg-3)",
        borderBottom: tab === id ? "1px solid var(--hg-fg-0)" : "1px solid transparent",
      }}
    >{id}</button>
  );

  const overlay = (
    <div
      style={{
        position: "fixed", inset: 0,
        background: "var(--hg-bg-0)",
        zIndex: 2100,
        display: "flex", flexDirection: "column",
        fontFamily: VL_FONT_MONO,
        animation: "vl-fade-in 220ms cubic-bezier(0.16,1,0.3,1)",
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Video labeler — timeline segment labeling"
    >
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "16px 24px 14px",
        borderBottom: "1px solid var(--hg-border-soft)",
      }}>
        <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.05 }}>
          <span style={{
            fontFamily: VL_FONT_SANS, fontSize: 17, fontWeight: 500,
            color: "var(--hg-fg-0)", letterSpacing: "-0.02em",
          }}>video labeler</span>
          <span style={{
            fontSize: 8.5, letterSpacing: "0.24em", fontWeight: 500,
            color: "var(--hg-fg-4)", marginTop: 3,
          }}>timeline segment labeler</span>
        </div>
        {simActive && (
          <span style={{
            fontFamily: VL_FONT_MONO, fontSize: 9, letterSpacing: "0.12em",
            color: "var(--hg-warn)", border: "1px solid var(--hg-border-soft)",
            padding: "3px 8px", marginLeft: 6,
          }}>sim</span>
        )}
        <span style={{ display: "inline-flex", marginLeft: 14 }}>
          {tabBtn("label")}
          {tabBtn("jobs")}
        </span>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center" }}>
          <button onClick={refresh} title="Refresh" className="hg-focusable"
            style={{ ...VL_BTN, fontSize: 10, color: "var(--hg-fg-3)", padding: "4px 9px" }}>refresh</button>
          <button onClick={onClose} aria-label="Close" className="hg-focusable"
            style={VL_BTN}>close · esc</button>
        </span>
      </div>

      {/* Body */}
      {simActive ? (
        /* Sim gate FIRST — no media elements, no fetches. */
        <div style={{
          flex: 1, display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center", gap: 10,
        }}>
          <span style={{
            fontFamily: VL_FONT_SANS, fontSize: 13, color: "var(--hg-fg-2)",
          }}>labeler unavailable in simulation mode</span>
          <span style={{
            fontFamily: VL_FONT_MONO, fontSize: 10, letterSpacing: "0.1em",
            color: "var(--hg-fg-4)",
          }}>media streams bypass the sim guard — exit with /simulation off to review footage</span>
        </div>
      ) : tab === "jobs" ? (
        <VLJobsPanel showToast={showToast} />
      ) : (
        <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
          {/* left rail — video list */}
          <div style={{
            width: 300, flex: "none", display: "flex", flexDirection: "column",
            borderRight: "1px solid var(--hg-border-soft)", minHeight: 0,
          }}>
            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "10px 12px", borderBottom: "1px solid var(--hg-border-soft)",
            }}>
              <button
                onClick={importInbox}
                disabled={importing}
                className="hg-focusable"
                style={{ ...VL_BTN_SM, color: importing ? "var(--hg-fg-5)" : "var(--hg-fg-2)" }}
              >{importing ? "importing…" : "import inbox"}</button>
              <span style={{
                marginLeft: "auto", fontFamily: VL_FONT_MONO, fontSize: 8.5,
                letterSpacing: "0.1em",
                color: healthLive ? "var(--hg-ice)" : "var(--hg-fg-5)",
              }} title={healthLive
                ? ("jobs running: " + (healthInfo.jobs_running ?? "—")
                   + " · gpu free: " + (healthInfo.gpu_free_gb ?? "—") + "gb"
                   + " · disk free: " + (healthInfo.disk_free_gb ?? "—") + "gb")
                : "service unreachable"}>
                labeler · {healthInfo === null ? "…" : healthLive ? "live" : "offline"}
              </span>
            </div>
            {/* queue filter chips (M3) — client-side triage over cached
                suggestion stats; no server review-queue endpoint yet */}
            <div style={{
              display: "flex", gap: 4, padding: "7px 10px", flexWrap: "wrap",
              borderBottom: "1px solid var(--hg-border-soft)", alignItems: "center",
            }}>
              {VL_QUEUE_FILTERS.map((f) => (
                <button
                  key={f}
                  onClick={() => setQueueFilter(f)}
                  title={f === "all" ? "every video"
                    : "videos with " + (f === "unreviewed" ? "pending suggestions"
                      : f === "low-conf" ? "low-confidence suggestions" : "disagreement-flagged suggestions")
                      + " — uses data loaded this session; unopened videos sort last"}
                  style={{
                    background: "transparent",
                    border: "1px solid " + (queueFilter === f ? "var(--hg-ice)" : "var(--hg-border-soft)"),
                    color: queueFilter === f ? "var(--hg-ice)" : "var(--hg-fg-4)",
                    padding: "2px 7px", fontFamily: VL_FONT_MONO, fontSize: 8.5,
                    letterSpacing: "0.1em", textTransform: "lowercase", cursor: "pointer",
                  }}
                >{f}</button>
              ))}
            </div>
            <div className="hg-scroll" style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
              {videosErr && (
                <div style={{
                  padding: "10px 12px", color: "var(--hg-warn)",
                  fontSize: 10, letterSpacing: "0.05em",
                }}>service unreachable · {videosErr}</div>
              )}
              {videos === null && !videosErr && (
                <div style={{ padding: "12px", color: "var(--hg-fg-3)", fontSize: 11 }}>loading videos…</div>
              )}
              {videos !== null && videos.length === 0 && !videosErr && (
                <div style={{
                  padding: "14px 12px", color: "var(--hg-fg-4)",
                  fontSize: 10.5, letterSpacing: "0.06em", lineHeight: 1.7,
                }}>
                  no videos yet — scp footage into /data/inbox on the box,
                  then import inbox.
                </div>
              )}
              {queueFilter !== "all" && (
                <div style={{
                  padding: "6px 12px", color: "var(--hg-fg-5)", fontSize: 8.5,
                  letterSpacing: "0.08em", borderBottom: "1px solid var(--hg-border-soft)",
                }}>
                  sorted by loaded suggestion data — open a video to load its counts
                </div>
              )}
              {visibleVideos.map((v) => (
                <VLVideoRow
                  key={v.id}
                  video={v}
                  selected={v.id === selectedId}
                  onClick={() => setSelectedId(v.id)}
                  sug={suggStats[v.id] || null}
                />
              ))}
            </div>
          </div>

          {/* center + right rail — the M1 editor (player, lanes, inspector) */}
          {selected ? (
            <VLEditor
              key={selected.id}
              video={selected}
              videos={videos || []}
              ontology={ontology}
              refreshOntology={refreshOntology}
              onPickVideo={setSelectedId}
              showToast={showToast}
              escInterceptRef={escInterceptRef}
              onSuggStats={onSuggStats}
            />
          ) : (
            <div style={{
              flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
              fontFamily: VL_FONT_MONO, fontSize: 11, letterSpacing: "0.12em",
              color: "var(--hg-fg-4)",
            }}>select a video to review</div>
          )}
        </div>
      )}

      {/* Status bar */}
      <div style={{
        display: "flex", alignItems: "center", gap: 14,
        padding: "7px 24px", borderTop: "1px solid var(--hg-border-soft)",
        fontFamily: VL_FONT_MONO, fontSize: 9, letterSpacing: "0.1em",
        color: "var(--hg-fg-4)", flex: "none",
      }}>
        <span>base · {base || "sim — service calls disabled"}</span>
        {!simActive && healthLive && (
          <span>
            gpu {healthInfo.gpu_free_gb ?? "—"}gb free
            · disk {healthInfo.disk_free_gb ?? "—"}gb free
            · {healthInfo.jobs_running ?? 0} jobs running
          </span>
        )}
        <span style={{ marginLeft: "auto", color: "var(--hg-fg-5)" }}>
          /labeler base &lt;url&gt; to change the service base
        </span>
      </div>

      {toast && (
        <div style={{
          position: "absolute", bottom: 48, left: "50%", transform: "translateX(-50%)",
          fontFamily: VL_FONT_MONO, fontSize: 10, color: "var(--hg-fg-1)",
          background: "rgba(10,12,16,0.85)", border: "1px solid var(--hg-border-soft)",
          padding: "7px 13px", zIndex: 6,
        }}>{toast}</div>
      )}

      <style>{`
        @keyframes vl-fade-in {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );

  return typeof document !== "undefined" && document.body
    ? ReactDOM.createPortal(overlay, document.body)
    : overlay;
}

Object.assign(window, { HomeVideoLabelerOverlay });
