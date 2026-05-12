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
 * Activity labels are placeholders for now — V-JEPA-2 will populate
 * them later via the same data shape (activity + confidence).
 */

const HG_CAMERAS = [
  { id: "living_room", entity: "camera.living_room", name: "living room", activity: "undetected" },
  { id: "kitchen",     entity: "camera.kitchen",     name: "kitchen",     activity: "undetected" },
  { id: "dining_room", entity: "camera.dining_room", name: "dining room", activity: "undetected" },
  { id: "workshop",    entity: "camera.workshop",    name: "workshop",    activity: "undetected" },
  { id: "driveway",    entity: "camera.driveway",    name: "driveway",    activity: "undetected" },
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
function useCameraSignedStream({ entity, haUrl, paused = false }) {
  const [signed, setSigned] = React.useState(null);
  const [err, setErr] = React.useState(null);
  React.useEffect(() => {
    if (paused || !entity || !haUrl) return undefined;
    let cancelled = false;
    let resignTimer = null;
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
        // res.path is like "/api/camera_proxy_stream/...?authSig=eyJ..."
        const url = `${base}${res.path}`;
        setSigned(url);
        setErr(null);
        // Re-sign before expiry.
        resignTimer = setTimeout(sign, 50 * 60 * 1000);
      } catch (e) {
        if (cancelled) return;
        console.warn("[vision] sign_path failed:", e?.message);
        setErr(String(e?.message || e));
      }
    };
    sign();
    return () => {
      cancelled = true;
      if (resignTimer) clearTimeout(resignTimer);
    };
  }, [entity, haUrl, paused]);
  return { src: signed, err };
}

/* Single camera frame — 16:9 black surface; image fills it. Overlays
 * sit *outside* the image (in the meta strip), so labels never sit
 * on top of the frame and obscure it. (Earlier design pass with
 * bounding-box overlays was dropped after a real-image test showed
 * low legibility.) */
function HomeVisionFrame({ camera, haUrl, paused }) {
  const { src, err } = useCameraSignedStream({
    entity: camera.entity,
    haUrl,
    paused,
  });
  return (
    <div style={{
      position: "relative",
      width: "100%",
      aspectRatio: "16/9",
      background: "#020203",
      overflow: "hidden",
    }}>
      {src ? (
        <img
          src={src}
          alt={camera.name}
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
  );
}

/* ── Main card ─────────────────────────────────────────────────────── */
function HomeVisionCard({
  cameras = HG_CAMERAS,
  defaultOpen = false,
  defaultIdx = 0,
  haUrl,
  token,
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  const [idx, setIdx] = React.useState(defaultIdx);
  const active = cameras[idx];
  // For now, every camera shows "undetected" — V-JEPA will fill these.
  const occupied = cameras.filter((c) => c.occupants > 0).length;
  const anyOccupied = occupied > 0;

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

      {open && (
        <div className="hg-fade">
          {/* Tabs */}
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

          {/* Live frame */}
          <HomeVisionFrame
            camera={active}
            haUrl={haUrl}
            token={token}
            paused={!open}
          />

          {/* Meta strip below the frame — activity only.
              "live · stream" was redundant noise; the frame is visibly
              moving, that's its own signal. */}
          <div style={{
            display: "flex", alignItems: "center", gap: 8,
            padding: "7px 14px",
            fontFamily: "'Geist Mono', monospace",
            fontSize: 9, letterSpacing: "0.14em",
            textTransform: "lowercase",
            color: "var(--hg-fg-4)",
            background: "var(--hg-bg-0)",
          }}>
            <span style={active.activity && active.activity !== "undetected"
              ? { color: "var(--hg-ice)" }
              : {}}>
              {active.activity || "idle"}
            </span>
            {typeof active.activityConfidence === "number" && active.activity !== "undetected" && (
              <span style={{ color: "var(--hg-fg-3)" }}>
                {active.activityConfidence.toFixed(2)}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

Object.assign(window, { HomeVisionCard, HG_CAMERAS });
