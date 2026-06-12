/* ============================================================================
 * home-video-labeler-timeline.jsx — VLTimeline + VLThumbStrip (M0 foundation)
 *
 * M0 ships the structural pieces the M1 multi-lane editor lands on:
 *   - VLTimeline: time ruler (nice tick steps), click-to-seek, and a
 *     playhead line the player drives IMPERATIVELY — the node is handed
 *     out via `playheadRef` and positioned with style.transform at frame
 *     rate (requestVideoFrameCallback writes; React never re-renders for
 *     playback). Lane rows render as empty tracks when a `lanes` prop is
 *     given so M1's segment blocks slot in without restructuring.
 *   - VLThumbStrip: sprite-sheet filmstrip — fixed-height tiles cut from
 *     the sheet grid via background-position math; click seeks to the
 *     tile's window center.
 *
 * Globals: VLTimeline, VLThumbStrip (one Object.assign at the bottom).
 * All top-level identifiers VL_-prefixed (shared global scope).
 * ========================================================================= */

const { useState, useEffect, useRef, useCallback, useMemo } = React;

const VL_FONT_MONO = "'Geist Mono', ui-monospace, monospace";
const VL_FONT_SANS = "'Geist', system-ui, sans-serif";

const VL_RULER_H = 30;   // tick strip + labels
const VL_LANE_H = 28;    // per-lane row height (M1 segment blocks)

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

function VLTimeline({ duration, playheadRef, pxPerSec, onSeek, lanes }) {
  const scrollRef = useRef(null);
  const dur = Math.max(0, Number(duration) || 0);
  const pps = Math.max(0.01, Number(pxPerSec) || 1);
  const width = Math.max(1, Math.ceil(dur * pps));
  const laneList = lanes || [];
  const height = VL_RULER_H + laneList.length * VL_LANE_H;

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

  const handleClick = useCallback((e) => {
    const el = scrollRef.current;
    if (!el || !onSeek || !(dur > 0)) return;
    const rect = el.getBoundingClientRect();
    const t = (e.clientX - rect.left + el.scrollLeft) / pps;
    onSeek(Math.max(0, Math.min(dur, t)));
  }, [pps, dur, onSeek]);

  return (
    <div
      ref={scrollRef}
      className="hg-scroll"
      onClick={handleClick}
      style={{
        position: "relative", overflowX: "auto", overflowY: "hidden",
        borderTop: "1px solid var(--hg-border-soft)",
        background: "var(--hg-bg-0)", cursor: "crosshair",
        flex: "none",
      }}
    >
      <div style={{ position: "relative", width, height }}>
        {/* ruler */}
        <div style={{
          position: "absolute", left: 0, top: 0, width: "100%", height: VL_RULER_H,
          borderBottom: "1px solid var(--hg-border-soft)",
        }}>
          {ticks.minor.map((t) => (
            <span key={"m" + t.toFixed(3)} style={{
              position: "absolute", left: t * pps, bottom: 0,
              width: 1, height: 5, background: "var(--hg-fg-5)", opacity: 0.55,
            }} />
          ))}
          {ticks.major.map((t) => (
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

        {/* lane rows — empty tracks in M0; segment blocks land in M1 */}
        {laneList.map((lane, i) => (
          <div key={lane.id || i} style={{
            position: "absolute", left: 0, width: "100%",
            top: VL_RULER_H + i * VL_LANE_H, height: VL_LANE_H,
            borderBottom: "1px solid var(--hg-border-soft)",
          }}>
            <span style={{
              position: "absolute", left: 6, top: 7,
              fontFamily: VL_FONT_MONO, fontSize: 8.5,
              letterSpacing: "0.18em", textTransform: "lowercase",
              color: "var(--hg-fg-5)", pointerEvents: "none",
            }}>{lane.name || lane.id}</span>
          </div>
        ))}

        {/* playhead — positioned imperatively by the player via transform */}
        <div
          ref={(node) => { if (playheadRef) playheadRef.current = node; }}
          style={{
            position: "absolute", left: 0, top: 0, bottom: 0, width: 1,
            background: "var(--hg-ice)", boxShadow: "0 0 6px var(--hg-ice-glow)",
            pointerEvents: "none", willChange: "transform",
          }}
        />
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
          title={D ? D.fmtDuration(tile.i * interval) : ""}
          onClick={() => onSeek && onSeek(tile.i * interval + interval / 2)}
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
