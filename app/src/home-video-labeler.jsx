/* ============================================================================
 * home-video-labeler.jsx — the /labeler full-screen video timeline labeler.
 *
 * M0: overlay shell (HomePeopleOverlay template), maximize-on-open /
 * restore-on-close (home-apartment.jsx pattern), video list + manual
 * import, proxy playback with custom transport + frame-rate timecode,
 * VLTimeline + VLThumbStrip wired to currentTime/seek, and a jobs tab.
 * The segment editor proper (lanes, drag, picker, save) is M1.
 *
 * Sim containment: the overlay renders an explicit "unavailable in
 * simulation mode" state BEFORE any media element exists — <video src>
 * and sprite background-images bypass tauriFetch's sim guard, so the
 *gate lives here AND in vlBase() (null base under __SIM_ACTIVE).
 *
 * Global: HomeVideoLabelerOverlay. Service must be optional: every fetch
 * is no-throw ({ok,...}); the app boots and the overlay renders offline
 * chips with the box down.
 * ========================================================================= */

const { useState, useEffect, useRef, useCallback, useMemo } = React;

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
 * ──────────────────────────────────────────────────────────────────── */
function VLVideoRow({ video, selected, onClick }) {
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
 * ──────────────────────────────────────────────────────────────────── */
function VLPlayer({ video }) {
  const D = window.HomeVideoLabelerData;
  const videoRef = useRef(null);
  const timecodeRef = useRef(null);
  const playheadRef = useRef(null);
  const wrapRef = useRef(null);
  const pxPerSecRef = useRef(1);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [manifest, setManifest] = useState(null);
  const [fitWidth, setFitWidth] = useState(0);
  const [metaDur, setMetaDur] = useState(0);
  const [mediaErr, setMediaErr] = useState(false);

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

  /* M0 zoom = fit-to-width (zoom levels land in M1) */
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const measure = () => setFitWidth(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const pxPerSec = duration > 0 && fitWidth > 0 ? fitWidth / duration : 1;
  pxPerSecRef.current = pxPerSec;

  const syncNow = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    const t = v.currentTime || 0;
    if (timecodeRef.current) timecodeRef.current.textContent = D.fmtTimecode(t, fps);
    if (playheadRef.current) {
      playheadRef.current.style.transform = "translateX(" + (t * pxPerSecRef.current) + "px)";
    }
  }, [fps]);

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

      {/* timeline + filmstrip */}
      <div ref={wrapRef} style={{ flex: "none", minWidth: 0 }}>
        {window.VLTimeline && (
          <window.VLTimeline
            duration={duration}
            playheadRef={playheadRef}
            pxPerSec={pxPerSec}
            onSeek={seekTo}
          />
        )}
        {window.VLThumbStrip && (
          <window.VLThumbStrip video={video} manifest={manifest} onSeek={seekTo} />
        )}
      </div>
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
  const wasMaximizedRef = useRef(null);
  const toastTimerRef = useRef(null);

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
     focused field. */
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      const t = e.target;
      if (t && (/INPUT|TEXTAREA|SELECT/.test(t.tagName) || t.isContentEditable)) return;
      e.stopImmediatePropagation();
      e.preventDefault();
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

  if (!open) return null;

  const base = D.vlBase();
  const healthLive = !!(healthInfo && healthInfo !== false);
  const selected = (videos || []).find((v) => v.id === selectedId) || null;

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
              {(videos || []).map((v) => (
                <VLVideoRow
                  key={v.id}
                  video={v}
                  selected={v.id === selectedId}
                  onClick={() => setSelectedId(v.id)}
                />
              ))}
            </div>
          </div>

          {/* center — player */}
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
            {selected ? (
              <VLPlayer key={selected.id} video={selected} />
            ) : (
              <div style={{
                flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
                fontFamily: VL_FONT_MONO, fontSize: 11, letterSpacing: "0.12em",
                color: "var(--hg-fg-4)",
              }}>select a video to review</div>
            )}
          </div>
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
