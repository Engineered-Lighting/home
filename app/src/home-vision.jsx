/* Home — vision card (Pattern E from the design).
 *
 *   collapsed
 *   ┌───────────────────────────────────────────────┐
 *   │ • vision   5 cameras · all clear          ▾   │
 *   └───────────────────────────────────────────────┘
 *
 *   open
 *   ┌───────────────────────────────────────────────┐
 *   │ • vision   living room                    ▴   │
 *   │ ▎living room   kitchen   dining   workshop …  │   tabs
 *   ├───────────────────────────────────────────────┤
 *   │                                               │
 *   │ [ live 16:9 frame from HA camera_proxy ]      │
 *   │                                               │
 *   ├───────────────────────────────────────────────┤
 *   │ undetected                       live · 1fps  │   meta strip
 *   └───────────────────────────────────────────────┘
 *
 * Real frames come from HA's /api/camera_proxy/{entity_id} JPEG endpoint,
 * fetched via the Tauri HTTP plugin (with auth header) and rendered as
 * blob URLs. Refresh cadence is gentle (1 fps for the visible camera,
 * paused when collapsed) since this is a glanceable side panel, not
 * a forensic feed.
 *
 * Activity labels come from Frigate via HA's binary_sensor.{camera}_{label}_occupancy
 * entities — `home-app.jsx` subscribes to state_changed and passes the live set
 * down as the `labels` prop. Empty set renders as "clear".
 */

const HG_CAMERAS = [
  { id: "living_room", entity: "camera.living_room", name: "living room" },
  { id: "kitchen",     entity: "camera.kitchen",     name: "kitchen"     },
  { id: "dining_room", entity: "camera.dining_room", name: "dining room" },
  { id: "workshop",    entity: "camera.workshop",    name: "workshop"    },
  { id: "driveway",    entity: "camera.driveway",    name: "driveway"    },
];

function _haBaseFromWs(haUrl) {
  if (!haUrl) return "";
  try {
    const u = new URL(haUrl.replace(/^ws/, "http"));
    return `${u.protocol}//${u.host}`;
  } catch {
    return "";
  }
}

/* Live frame: asks HA's WS for a signed URL to the MJPEG stream
 * endpoint, then sets it on an <img>. The signed URL doesn't need
 * an Authorization header, so it works in a plain <img src=> tag
 * (no CORS or token-in-image problems). MJPEG keeps pushing frames
 * over a single connection — the browser renders each one as it
 * arrives, so we don't have to poll. Re-sign every ~50 min before
 * the default 60-min HA signature expiry. */
function useCameraSignedStream({ entity, haUrl, paused = false, stagger = 0 }) {
  const [signed, setSigned] = React.useState(null);
  const [err, setErr] = React.useState(null);
  // Phase 1.5d / Phase B post-deploy fix: bump on stream-death so
  // consumers can remount the <img> element and force a fresh MJPEG
  // connection. HA's camera_proxy_stream occasionally goes silent
  // (Frigate restart, RTSP dropout, network blip) without firing an
  // onError — the img just stops getting new frames and shows the
  // last one (or black if it never got one).
  //
  // Recovery triggers (in order of speed of detection):
  //   1. img onError handler → onError-triggered reload (instant)
  //   2. visibilitychange / window focus → user returns from idle
  //      and we proactively remount (~0ms after user looks)
  //   3. periodic setInterval (2 min when active, was 5 min) → catches
  //      stale streams during continuous viewing
  //
  // The visibilitychange trigger is THE key fix for the user-reported
  // "black tiles after 5+ min idle" symptom: browsers throttle
  // setInterval when tabs are unfocused, so the periodic remount
  // doesn't fire reliably. Detecting user return is more robust.
  const [streamKey, setStreamKey] = React.useState(0);
  const reload = React.useCallback(() => {
    setStreamKey((k) => k + 1);
  }, []);
  React.useEffect(() => {
    if (paused || !entity || !haUrl) return undefined;
    let cancelled = false;
    let resignTimer = null;
    let refreshTimer = null;
    let staggerTimer = null;
    const sign = async () => {
      try {
        const client = window.__hav_haClient || null;
        if (!client || typeof client.call !== "function") {
          setErr("no ha client");
          return;
        }
        const path = `/api/camera_proxy_stream/${entity}`;
        const res = await client.call({
          type: "auth/sign_path",
          path,
          expires: 3600,
        });
        if (cancelled) return;
        const base = _haBaseFromWs(haUrl);
        const url = `${base}${res.path}`;
        setSigned(url);
        setErr(null);
        resignTimer = setTimeout(sign, 50 * 60 * 1000);
      } catch (e) {
        if (cancelled) return;
        console.warn("[vision] sign_path failed:", e?.message);
        setErr(String(e?.message || e));
      }
    };
    // Phase 1.5f: stagger the initial sign+stream-attach so 5
    // simultaneous mounts (e.g. carousel reopen) don't slam HA's
    // camera_proxy_stream all at once.
    if (stagger > 0) {
      staggerTimer = setTimeout(() => { sign(); }, stagger);
    } else {
      sign();
    }
    // Periodic remount: every 2 minutes (down from 5). Even if the
    // existing connection is healthy, the cost is one frame's worth
    // of black, which is invisible in normal viewing.
    refreshTimer = setInterval(() => {
      setStreamKey((k) => k + 1);
    }, 2 * 60 * 1000);
    return () => {
      cancelled = true;
      if (resignTimer) clearTimeout(resignTimer);
      if (refreshTimer) clearInterval(refreshTimer);
      if (staggerTimer) clearTimeout(staggerTimer);
    };
  }, [entity, haUrl, paused, stagger]);
  // Phase B post-deploy: proactive remount on visibility/focus return.
  // This is what catches "black tiles after 5+ min idle" — when the
  // user looks back at the app, any streams that quietly died during
  // background-throttling get remounted within a frame.
  React.useEffect(() => {
    if (paused) return undefined;
    let lastForceAt = 0;
    const forceReload = (reason) => {
      // Debounce: don't remount more than once per 10s for the same
      // reason set. Initial-page-focus + visibility events fire close
      // together; we only want one remount.
      const now = Date.now();
      if (now - lastForceAt < 10000) return;
      lastForceAt = now;
      console.log(`[vision] ${reason} → remount ${entity}`);
      setStreamKey((k) => k + 1);
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        forceReload("visibilitychange→visible");
      }
    };
    const onFocus = () => forceReload("window focus");
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("focus", onFocus);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("focus", onFocus);
    };
  }, [entity, paused]);
  return { src: signed, err, streamKey, reload };
}

/* Single camera frame — 16:9 black surface; image fills it. Overlays
 * sit *outside* the image (in the meta strip), so labels never sit
 * on top of the frame and obscure it. (Earlier design pass with
 * bounding-box overlays was dropped after a real-image test showed
 * low legibility.) */
function HomeVisionFrame({ camera, haUrl, paused, wide = false, stagger = 0 }) {
  const { src, err, streamKey, reload } = useCameraSignedStream({
    entity: camera.entity,
    haUrl,
    paused,
    stagger,
  });
  // Cap the vision frame so it never eats the chat at wide window
  // sizes. 16:9 frame at 100% width grows linearly with window width
  // — at 2000px wide that's 1125px tall, pushing the chat off-screen.
  // Strategy: wrap the frame in a centered flex; inner frame has a
  // max-width derived from 16:9 of 38vh (portrait mode) or 50vh
  // (wide mode where the carousel tile is already width-constrained
  // by its outer container).
  // Below the breakpoint, behavior is unchanged: width:100%
  // with aspect-ratio: 16/9.
  const maxW = wide ? "calc(50vh * 16 / 9)" : "calc(38vh * 16 / 9)";
  return (
    <div style={{
      width: "100%",
      display: "flex",
      justifyContent: "center",
      background: "#020203",
    }}>
      <div style={{
        position: "relative",
        width: "100%",
        maxWidth: maxW,
        aspectRatio: "16/9",
        background: "#020203",
        overflow: "hidden",
      }}>
        {src ? (
          <img
            // Phase 1.5d: streamKey is bumped every 5 min AND on
            // onError, forcing a fresh MJPEG handshake. HA's
            // camera_proxy_stream sometimes goes silent without
            // firing an event — the periodic remount catches that
            // case. The onError remount catches outright failures.
            key={streamKey}
            src={src}
            alt={camera.name}
            onError={() => {
              console.warn("[vision] img onError for", camera.name, "— reloading");
              if (typeof reload === "function") reload();
            }}
            style={{
              width: "100%", height: "100%",
              objectFit: "cover",
              display: "block",
            }}
          />
        ) : (
          <div style={{
            position: "absolute", inset: 0,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontFamily: "'Geist Mono', monospace",
            fontSize: 9, letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: err ? "var(--hg-warn)" : "var(--hg-fg-5)",
            padding: 12, textAlign: "center",
          }}>
            {err ? `error · ${err}` : "loading…"}
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Main card ─────────────────────────────────────────────────────── */
function HomeVisionCard({
  cameras = HG_CAMERAS,
  defaultOpen = false,
  defaultIdx = 0,
  haUrl,
  token,
  labels = {},
  identity = null,
  media = {},
  wideMode = false,
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  const [idx, setIdx] = React.useState(defaultIdx);
  // Phase 1.5g: once the carousel has ever been opened, stay mounted
  // forever — collapse just CSS-hides the subtree so the underlying
  // MJPEG TCP connections persist across collapse/reopen cycles.
  // Without this, Chrome/WebView2's HTTP/1.1 connection pool (6 per
  // origin) gets saturated by 5 zombie close-wait connections + 5
  // new tile mounts on reopen — black tiles, no recovery.
  const [hasEverOpened, setHasEverOpened] = React.useState(defaultOpen);
  React.useEffect(() => {
    if (open) setHasEverOpened(true);
  }, [open]);
  // Phase B post-deploy fix: bump openCount on every open→true
  // transition. The MJPEG streams may silently die during a long
  // closed period (browser tab-throttling stops the periodic
  // remount, HA may close idle MJPEG connections). When the user
  // reopens the drawer, force all tiles to remount by changing
  // their `key` (drives a fresh signed URL fetch + handshake).
  const [openCount, setOpenCount] = React.useState(defaultOpen ? 1 : 0);
  const prevOpenRef = React.useRef(defaultOpen);
  React.useEffect(() => {
    if (open && !prevOpenRef.current) {
      // closed → open transition
      setOpenCount((c) => c + 1);
      console.log("[vision] drawer reopened; forcing stream remount");
    }
    prevOpenRef.current = open;
  }, [open]);
  const active = cameras[idx];
  const occupied = cameras.filter((c) => (labels[c.id] || []).length > 0).length;
  const anyOccupied = occupied > 0;
  // Phase 1.5 item 7: carousel refs + IntersectionObserver
  // setup. In wide-mode the camera tiles live in a horizontal
  // scroll-snap container; whichever tile is closest to the visual
  // center becomes the active camera. Click-to-center uses
  // scrollIntoView on the tile.
  const carouselRef = React.useRef(null);
  const tileRefs = React.useRef([]);
  // Phase 1.5c: timestamp of the last explicit tile-click. The IO-
  // driven setIdx callback short-circuits if it fires within
  // CLICK_SUPPRESS_MS of a click, so a leftward click (target idx <
  // current idx) doesn't get reverted by the observer noticing that
  // the (still partially-visible) current tile out-intersects the
  // not-yet-scrolled-to target.
  const lastClickAt = React.useRef(0);
  // When wide-mode entry happens, scroll the carousel so the current
  // idx is centered (otherwise idx=2 would still leave the carousel
  // scrolled to idx=0).
  React.useEffect(() => {
    if (!wideMode || !open) return undefined;
    const t = tileRefs.current[idx];
    if (t && typeof t.scrollIntoView === "function") {
      // Use rAF so this runs after layout from the wideMode flip.
      requestAnimationFrame(() => {
        try { t.scrollIntoView({ behavior: "instant", inline: "center", block: "nearest" }); }
        catch (e) { /* older WebView2 — fall back to smooth */
          try { t.scrollIntoView({ behavior: "smooth", inline: "center" }); } catch {}
        }
      });
    }
    // We intentionally only run on the wideMode↔open edges, not on
    // every idx change (click handler does its own scrollIntoView).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wideMode, open]);
  // Debounced setIdx-from-scroll: when carousel scroll-snaps near a
  // new tile, IntersectionObserver flips active. 120ms debounce so
  // mid-scroll partial intersections don't strobe.
  React.useEffect(() => {
    if (!wideMode || !open) return undefined;
    const root = carouselRef.current;
    if (!root) return undefined;
    let timer = null;
    const CLICK_SUPPRESS_MS = 800;
    const observer = new IntersectionObserver((entries) => {
      // Phase 1.5c: short-circuit while a smooth-scroll-from-click is
      // still mid-flight. Without this, leftward clicks (target idx <
      // current idx) revert because the current tile stays partially
      // visible during the scroll and the IO calls setIdx back to it.
      if (Date.now() - lastClickAt.current < CLICK_SUPPRESS_MS) return;
      // Find the most-intersecting tile from this batch.
      let best = null;
      let bestRatio = 0;
      for (const e of entries) {
        if (e.intersectionRatio > bestRatio) {
          best = e.target;
          bestRatio = e.intersectionRatio;
        }
      }
      if (best && bestRatio > 0.5) {
        const i = tileRefs.current.indexOf(best);
        if (i >= 0 && i !== idx) {
          clearTimeout(timer);
          timer = setTimeout(() => setIdx(i), 120);
        }
      }
    }, {
      root,
      // Only the center 60% of the carousel counts as "centered".
      rootMargin: "0px -20% 0px -20%",
      threshold: [0.5, 0.75, 0.95],
    });
    for (const tile of tileRefs.current) {
      if (tile) observer.observe(tile);
    }
    return () => {
      clearTimeout(timer);
      observer.disconnect();
    };
  }, [wideMode, open, idx, cameras.length]);

  return (
    <div style={{
      flex: "0 0 auto",
      borderBottom: "1px solid var(--hg-border-soft)",
      background: "var(--hg-bg-0)",
    }}>
      {/* Header row — collapse/expand toggle */}
      <button
        onClick={() => setOpen((o) => !o)}
        className="hg-focusable"
        style={{
          width: "100%",
          display: "flex", alignItems: "center", gap: 10,
          padding: "9px 16px",
          background: "transparent",
          border: "none",
          cursor: "pointer",
          textAlign: "left",
          color: "var(--hg-fg-1)",
        }}
      >
        <span style={{
          width: 5, height: 5, borderRadius: 999,
          background: "var(--hg-ice)",
          boxShadow: anyOccupied ? "0 0 5px var(--hg-ice-glow)" : "none",
          opacity: anyOccupied ? 1 : 0.55,
        }}/>
        <span style={{
          fontFamily: "'Geist Mono', monospace",
          fontSize: 10, letterSpacing: "0.16em",
          textTransform: "lowercase",
          color: "var(--hg-fg-2)", fontWeight: 500,
        }}>vision</span>
        <span style={{
          fontFamily: "'Geist Mono', monospace",
          fontSize: 9, color: "var(--hg-fg-4)", letterSpacing: "0.14em",
        }}>
          {open ? active.name : `${cameras.length} cameras · ${anyOccupied ? `${occupied} occupied` : "all clear"}`}
        </span>
        <div style={{ flex: 1 }}/>
        <span style={{
          fontFamily: "'Geist Mono', monospace",
          fontSize: 11, color: "var(--hg-fg-3)",
        }}>{open ? "▴" : "▾"}</span>
      </button>

      {/* Phase 1.5g: once the carousel has ever been opened, keep it
          mounted forever — collapse just hides via CSS. This preserves
          the MJPEG TCP connections across collapse/reopen cycles,
          bypassing the browser HTTP/1.1 connection-pool exhaustion
          that left tiles black on reopen. The `hg-fade` class still
          plays on the first-ever open; subsequent opens are instant
          (already-rendered). */}
      {hasEverOpened && (
        <div
          className={open ? "hg-fade" : ""}
          style={{ display: open ? "block" : "none" }}
        >
          {/* Phase 2.1: unified layout — the same 5 HomeVisionFrame
              instances are ALWAYS rendered, regardless of wideMode.
              CSS switches between carousel view (wide) and
              single-active-tile view (portrait). Without this, the
              wideMode boolean flipping during a window resize
              previously unmounted the entire camera frame set and
              triggered the same browser HTTP/1.1 connection-pool
              exhaustion that bit collapse/reopen — black tiles after
              resize. Now connections persist across resizes. */}
          {!wideMode && (
            /* Tabs — visible only in portrait mode */
            <div
              className="hg-scroll"
              style={{
                display: "flex", gap: 0,
                borderTop: "1px solid var(--hg-border-soft)",
                borderBottom: "1px solid var(--hg-border-soft)",
                overflowX: "auto",
                scrollbarWidth: "none",
                padding: "0 8px",
                background: "var(--hg-bg-1)",
              }}
            >
              {cameras.map((c, i) => {
                const on = i === idx;
                return (
                  <div
                    key={c.id}
                    onClick={() => setIdx(i)}
                    style={{
                      flex: "0 0 auto",
                      padding: "8px 10px 7px",
                      position: "relative",
                      fontFamily: "'Geist Mono', monospace",
                      fontSize: 9, letterSpacing: "0.14em",
                      textTransform: "lowercase",
                      color: on ? "var(--hg-fg-0)" : "var(--hg-fg-4)",
                      cursor: "pointer",
                      display: "flex", alignItems: "center", gap: 5,
                      whiteSpace: "nowrap",
                      transition: "color 120ms ease",
                    }}
                  >
                    {c.name}
                    {on && (
                      <span style={{
                        position: "absolute", left: 6, right: 6, bottom: -1, height: 1,
                        background: "var(--hg-ice)",
                      }}/>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <div
            ref={carouselRef}
            className={wideMode ? "hg-scroll" : ""}
            style={wideMode ? {
              display: "flex",
              gap: 14,
              overflowX: "auto",
              scrollSnapType: "x mandatory",
              padding: "12px 20vw",
              background: "var(--hg-bg-1)",
              borderTop: "1px solid var(--hg-border-soft)",
              borderBottom: "1px solid var(--hg-border-soft)",
            } : {
              /* Portrait — column-stack with hidden non-active tiles.
                 The five HomeVisionFrame instances stay mounted but
                 only the active one is visible. */
              display: "block",
              background: "var(--hg-bg-0)",
            }}
          >
            {cameras.map((c, i) => {
              const isActive = i === idx;
              const tileVisible = wideMode || isActive;
              return (
                <div
                  key={c.id}
                  ref={(el) => (tileRefs.current[i] = el)}
                  onClick={wideMode ? () => {
                    lastClickAt.current = Date.now();
                    setIdx(i);
                    try {
                      tileRefs.current[i]?.scrollIntoView({
                        behavior: "smooth",
                        inline: "center",
                        block: "nearest",
                      });
                    } catch (e) { /* noop */ }
                  } : undefined}
                  style={wideMode ? {
                    flex: "0 0 auto",
                    width: "min(38vw, 560px)",
                    scrollSnapAlign: "center",
                    cursor: "pointer",
                    transform: isActive ? "scale(1.05)" : "scale(0.96)",
                    transition: "transform 220ms ease, box-shadow 220ms ease",
                    boxShadow: isActive ? "0 0 18px var(--hg-ice-glow)" : "none",
                    borderRadius: 4,
                    overflow: "hidden",
                    position: "relative",
                  } : {
                    /* In portrait, non-active tiles are display:none
                       so they stay MOUNTED (MJPEG streams alive) but
                       invisible. Active tile fills width naturally. */
                    display: tileVisible ? "block" : "none",
                    width: "100%",
                    position: "relative",
                  }}
                >
                  <HomeVisionFrame
                    // Phase B post-deploy: key includes openCount so the
                    // Frame component remounts on every drawer reopen,
                    // forcing a fresh signed URL + MJPEG handshake.
                    // Catches streams that died during long closed periods.
                    key={`${c.id}-${openCount}`}
                    camera={c}
                    haUrl={haUrl}
                    token={token}
                    paused={false}
                    wide={wideMode}
                    stagger={i * 200}
                  />
                  {/* Wide-mode tile name overlay — only shown in carousel layout. */}
                  {wideMode && (
                    <div style={{
                      position: "absolute", left: 0, right: 0, bottom: 0,
                      padding: "3px 8px",
                      fontFamily: "'Geist Mono', monospace",
                      fontSize: 8, letterSpacing: "0.16em",
                      textTransform: "lowercase",
                      color: isActive ? "var(--hg-ice-bright)" : "var(--hg-fg-3)",
                      background: "linear-gradient(transparent, rgba(0,0,0,0.7))",
                      pointerEvents: "none",
                    }}>
                      {c.name}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Meta strip below the frame — Phase 1.5 merges the
              face-rec identity pill INLINE with Frigate object labels.
              Visual hierarchy: identity pill (bordered, glowing, bold
              for high-confidence) ranks higher than the comma-separated
              object labels behind it. flexWrap keeps it readable at
              narrow widths. */}
          {(() => {
            const objLabels = (labels[active.id] || []);
            const showIdentity = identity && identity.name
              && identity.camera === active.id
              && (Date.now() / 1000 - (identity.ts || 0)) < 120;
            const dim = showIdentity && identity.confidence_band !== "high";
            return (
              <div style={{
                display: "flex", alignItems: "center", gap: 8,
                flexWrap: "wrap",
                padding: "7px 14px",
                fontFamily: "'Geist Mono', monospace",
                fontSize: 9, letterSpacing: "0.14em",
                textTransform: "lowercase",
                color: "var(--hg-fg-4)",
                background: "var(--hg-bg-0)",
              }}>
                {showIdentity && (
                  <span
                    title={`Face match: ${identity.name} (${identity.confidence_band})`}
                    style={{
                      display: "inline-flex", alignItems: "center", gap: 4,
                      padding: "1px 6px",
                      border: `1px solid ${dim ? "var(--hg-fg-4)" : "var(--hg-ice)"}`,
                      borderRadius: 2,
                      color: dim ? "var(--hg-fg-2)" : "var(--hg-ice-bright)",
                      fontWeight: dim ? 400 : 600,
                      boxShadow: dim ? "none" : "0 0 6px var(--hg-ice-glow)",
                    }}
                  >
                    <span style={{
                      width: 4, height: 4, borderRadius: 999,
                      background: dim ? "var(--hg-fg-3)" : "var(--hg-ice-bright)",
                    }}/>
                    {identity.name.toLowerCase()}
                  </span>
                )}
                {objLabels.length > 0 ? (
                  <span style={{ color: "var(--hg-ice)" }}>
                    {objLabels.join(" · ")}
                  </span>
                ) : (
                  /* Only show "clear" when no identity pill is present —
                     otherwise the strip would read "[marcelo] clear" which
                     contradicts itself (Marcelo IS detected). */
                  !showIdentity && <span>clear</span>
                )}
              </div>
            );
          })()}
          {/* Phase 2 media sub-line — second row below the identity +
              labels strip. Visible only when there's an active media
              entry for the currently-displayed camera's room. */}
          {(() => {
            const m = media && media[active.id];
            if (!m || !m.state) return null;
            const isPlaying = m.state === "playing" || m.state === "on";
            const isPaused = m.state === "paused";
            if (!isPlaying && !isPaused) return null;
            const verb = isPaused ? "paused" : "playing";
            const detail = m.title
              ? (m.artist ? `${m.title} · ${m.artist}` : m.title)
              : "";
            return (
              <div style={{
                display: "flex", alignItems: "center", gap: 6,
                padding: "5px 14px 7px",
                fontFamily: "'Geist Mono', monospace",
                fontSize: 9, letterSpacing: "0.14em",
                textTransform: "lowercase",
                color: isPlaying ? "var(--hg-ice-bright)" : "var(--hg-fg-3)",
                background: "var(--hg-bg-0)",
              }}>
                <span style={{
                  width: 4, height: 4, borderRadius: 999,
                  background: isPlaying ? "var(--hg-ice-bright)" : "var(--hg-fg-3)",
                  boxShadow: isPlaying ? "0 0 4px var(--hg-ice-glow)" : "none",
                }}/>
                <span>
                  {(m.app_name || "media").toLowerCase()} {verb}
                  {detail && <> · {detail.toLowerCase()}</>}
                </span>
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { HomeVisionCard, HG_CAMERAS });
