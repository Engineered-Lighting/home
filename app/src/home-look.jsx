/* eslint-disable */
/**
 * home-look.jsx — /look : "Thinking with Visual Primitives" (Phase 0.5).
 *
 * Asks the vision-sidecar's POST /reason a spatial question about a camera.
 * Qwen3-VL answers with a reasoning trace in which it "points while it
 * reasons" — every object it reasons about is emitted inline as
 *   <ref>label</ref><box>x1,y1,x2,y2</box>
 * This drawer renders the paper's two-panel figure: the original camera
 * frame, and the same frame with those visual primitives drawn over it.
 *
 * Coordinate convention follows home-spatial.jsx: a viewBox'd <svg> laid
 * over the frame. /reason returns each box as pixel coords in the source
 * frame (bbox_px) plus the frame dimensions, so the <svg> viewBox is
 * "0 0 frame_w frame_h" and the boxes drop straight in with no math.
 *
 * Sidecar URL: :8091 on the metrics-sidecar host (same derivation as the
 * /describe-clip command). Real-connection only; sim mode shows a note.
 */

const { useState, useEffect, useRef, useCallback } = React;

const LK_FONT_MONO = '"Geist Mono", "JetBrains Mono", monospace';
const LK_FONT_SANS = '"Geist", "Inter", sans-serif';

/* Camera roster — shared with the vision card (home-vision.jsx). Falls
 * back to the known five if that global isn't up yet. */
const LK_CAMERAS = (window.HG_CAMERAS && window.HG_CAMERAS.length)
  ? window.HG_CAMERAS
  : [
      { id: "living_room", entity: "camera.living_room", name: "living room" },
      { id: "kitchen",     entity: "camera.kitchen",     name: "kitchen"     },
      { id: "dining_room", entity: "camera.dining_room", name: "dining room" },
      { id: "workshop",    entity: "camera.workshop",    name: "workshop"    },
      { id: "driveway",    entity: "camera.driveway",    name: "driveway"    },
    ];

const LK_PANEL_LABEL = {
  fontSize: 8.5, letterSpacing: "0.18em", textTransform: "uppercase",
  color: "var(--hg-fg-4)", marginBottom: 5, fontFamily: LK_FONT_MONO,
};
const LK_PANEL_FRAME = {
  position: "relative", width: "100%", background: "#020203",
  border: "1px solid var(--hg-border-soft)", overflow: "hidden",
  borderRadius: 2,
};
const LK_PANEL_MISSING = {
  padding: "30px 8px", textAlign: "center", color: "var(--hg-fg-5)",
  fontFamily: LK_FONT_MONO, fontSize: 9, letterSpacing: "0.16em",
  textTransform: "uppercase",
};

/* Parse "/look <camera> <question>" → { camera, question }. The camera
 * may be one or two words ("living room"); match the longest known camera
 * name/id at the head of the arg, the rest is the question. No camera
 * prefix → the whole arg is the question (drawer defaults to a camera). */
function lkParseArg(arg) {
  const a = String(arg || "").trim();
  if (!a) return { camera: null, question: "" };
  const lower = a.toLowerCase();
  let best = null;
  for (let i = 0; i < LK_CAMERAS.length; i++) {
    const c = LK_CAMERAS[i];
    const cands = [c.name, c.id, c.entity, String(c.id).replace(/_/g, " ")];
    for (let j = 0; j < cands.length; j++) {
      const cl = String(cands[j]).toLowerCase();
      if (lower === cl || lower.indexOf(cl + " ") === 0) {
        if (!best || cl.length > best.len) best = { camera: c.id, len: cl.length };
      }
    }
  }
  if (best) return { camera: best.camera, question: a.slice(best.len).trim() };
  return { camera: null, question: a };
}

/* metricsBase (…:8092 on the AI box) → vision-sidecar base (…:8091). */
function lkVisionUrl(metricsBase) {
  try {
    if (window.HG_DEFAULT_VISION_BASE) {
      return String(window.HG_DEFAULT_VISION_BASE).replace(/\/+$/, "");
    }
  } catch (_) {}
  try {
    if (metricsBase) {
      const u = new URL(metricsBase);
      return u.protocol + "//" + u.hostname + ":8091";
    }
  } catch (_) {}
  return "";
}

function lkAppendCacheBust(url, cacheBust) {
  if (!url) return "";
  const stamp = cacheBust || Date.now();
  return String(url) + (String(url).includes("?") ? "&" : "?") + "cb=" + stamp;
}

function lkJoinUrl(base, path, cacheBust) {
  if (!path) return "";
  const raw = String(path);
  if (/^https?:\/\//i.test(raw)) return lkAppendCacheBust(raw, cacheBust);
  const b = String(base || "").replace(/\/+$/, "");
  const p = raw.replace(/^\/+/, "");
  return lkAppendCacheBust(b + "/" + p, cacheBust);
}

async function lkReasonZoomRequest(opts) {
  const options = opts || {};
  const question = String(options.question || "").trim();
  if (!question) throw new Error("question required");
  const visionBase = String(options.visionBase || lkVisionUrl(options.metricsBase) || "").replace(/\/+$/, "");
  if (!visionBase) throw new Error("vision-sidecar URL not derivable");
  const fetchImpl = options.fetchImpl || window.tauriFetch || fetch;
  const camera = options.camera || "auto";
  const r = await fetchImpl(visionBase + "/reason_zoom", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ camera, question }),
    cache: "no-store",
    signal: options.signal,
  });
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.text()).slice(0, 180); } catch (_) {}
    throw new Error("reason_zoom · HTTP " + r.status + (detail ? " · " + detail : ""));
  }
  const data = await r.json();
  const shot = data.camera || camera || "auto";
  const cb = options.cacheBust || Date.now();
  return {
    ...data,
    camera: shot,
    overviewUrl: lkJoinUrl(visionBase, data.overview_url, cb),
    detailUrl: lkJoinUrl(visionBase, data.detail_url, cb),
    visionBase,
  };
}

async function lkReasonRequest(opts) {
  const options = opts || {};
  const question = String(options.question || "").trim();
  if (!question) throw new Error("question required");
  const visionBase = String(options.visionBase || lkVisionUrl(options.metricsBase) || "").replace(/\/+$/, "");
  if (!visionBase) throw new Error("vision-sidecar URL not derivable");
  const fetchImpl = options.fetchImpl || window.tauriFetch || fetch;
  const camera = options.camera || "auto";
  const r = await fetchImpl(visionBase + "/reason", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ camera, question }),
    cache: "no-store",
    signal: options.signal,
  });
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.text()).slice(0, 180); } catch (_) {}
    throw new Error("reason · HTTP " + r.status + (detail ? " · " + detail : ""));
  }
  const data = await r.json();
  const shot = data.camera || camera || "auto";
  const cb = options.cacheBust || Date.now();
  return {
    ...data,
    camera: shot,
    annotatedUrl: lkJoinUrl(visionBase, data.annotated_url, cb),
    visionBase,
  };
}

/* A plain text segment of a reasoning trace, with any stray bare <box>
 * tags (boxes not paired with a <ref>) removed. */
function lkPlain(s, key) {
  return (
    <React.Fragment key={key}>
      {String(s).replace(/<box>[\d,\s]*<\/box>/gi, "")}
    </React.Fragment>
  );
}

/* Render a grounded-reasoning trace readably: <ref>label</ref> becomes a
 * highlighted chip (the box itself lives on the image), and the trailing
 * "ANSWER:" line is dropped (it is shown separately as the answer). */
function lkRenderReasoning(text) {
  const t = String(text || "").replace(/\n*ANSWER:[\s\S]*$/i, "").trim();
  const out = [];
  const re = /<ref>([\s\S]*?)<\/ref>\s*(?:<box>[\d,\s]*<\/box>)?/gi;
  let last = 0, m, k = 0;
  while ((m = re.exec(t)) !== null) {
    if (m.index > last) out.push(lkPlain(t.slice(last, m.index), "p" + k));
    out.push(
      <span key={"ref" + k} style={{
        color: "var(--hg-ice-bright)",
        borderBottom: "1px solid var(--hg-ice)", padding: "0 1px",
      }}>{String(m[1]).trim()}</span>
    );
    last = m.index + m[0].length;
    k++;
  }
  if (last < t.length) out.push(lkPlain(t.slice(last), "p" + k));
  return out;
}

/* ── The /look drawer ───────────────────────────────────────────────── */
function HomeLookDrawer({ open, onClose, metricsBase, sim,
                          initialCamera, initialQuestion, onTranscript }) {
  const [camera, setCamera] = useState(initialCamera || "auto");
  const [question, setQuestion] = useState(initialQuestion || "");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);     // POST /reason response
  const [error, setError] = useState(null);
  const [frameUrl, setFrameUrl] = useState(null);   // overview_url (full + zoom region)
  const [detailUrl, setDetailUrl] = useState(null); // detail_url (crop + pass-2 boxes)
  const [frameOk, setFrameOk] = useState(true);
  const abortRef = useRef(null);
  const inputRef = useRef(null);
  // onTranscript can be an unstable inline prop — keep it in a ref so it is
  // NOT a dependency of runLook. (As a runLook dep it would change every
  // parent render, re-key the open effect, and infinite-loop the auto-run.)
  const onTranscriptRef = useRef(onTranscript);
  onTranscriptRef.current = onTranscript;
  const simActive = !!(sim && sim.active);

  /* Run one /reason round-trip for (cam, q). Explicit args (not state) so
   * the auto-run on open never races a setState. */
  const runLook = useCallback(async function (cam, q) {
    const qq = String(q || "").trim();
    setError(null);
    if (!qq) { setError("type a question first"); return; }
    if (simActive) {
      setError("sim mode — /look needs the live vision-sidecar");
      return;
    }
    const vu = lkVisionUrl(metricsBase);
    if (!vu) {
      setError("vision-sidecar URL not derivable — run /metrics <url> first");
      return;
    }
    const ot = onTranscriptRef.current;
    // Retain the /look query in the chat transcript AS the equivalent
    // `/look` command (with the camera, when one was pinned) — so the
    // transcript shows a command was run, not just a bare question.
    // Fired now so a later failure still leaves it on the record.
    const lookCmd = "/look "
      + ((cam && cam !== "auto") ? (String(cam) + " ") : "") + qq;
    if (ot) ot({ type: "question", text: lookCmd });
    setLoading(true);
    setResult(null);
    setFrameUrl(null);
    setDetailUrl(null);
    setFrameOk(true);
    try { if (abortRef.current) abortRef.current.abort(); } catch (_) {}
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      // /reason_zoom does the foveated two-pass loop: pass 1 locates the
      // single region most relevant to the question (the model picks); the
      // sidecar crops the full-res RTSP frame to that region; pass 2
      // re-reasons over the crop. The model "auto-decides what to look
      // at" by virtue of pass 1 — this is the behavior the user expected
      // from /look (single-pass /reason had it boxing whatever it
      // happened to ground on, but never drilling in for detail). For
      // broad questions the model picks a wide box and pass 2 is
      // effectively a re-pass over the whole frame; for detail questions
      // it picks tight and pass 2 sees the zoomed pixels.
      const data = await lkReasonZoomRequest({
        metricsBase,
        camera: cam || "auto",
        question: qq,
        signal: controller.signal,
      });
      setResult(data);
      // /reason_zoom returns relative paths in overview_url / detail_url
      // (e.g. "/reason_zoom/kitchen/overview.jpg"). Prepend the sidecar
      // base. Cache-buster ensures the new frame loads (the sidecar
      // writes the JPEG fresh on every reason_zoom call).
      const shot = data.camera || cam || "kitchen";
      setFrameUrl(data.overviewUrl);
      setDetailUrl(data.detailUrl);
      if (ot) ot({
        type: "answer", camera: shot, answer: data.answer || "",
        annotatedUrl: data.detailUrl || null,
      });
    } catch (e) {
      if (e && e.name === "AbortError") return;
      const msg = (e && e.message) || String(e);
      setError(msg);
      if (ot) ot({ type: "error", text: msg });
    } finally {
      setLoading(false);
    }
  }, [metricsBase, simActive]);

  // On open: seed from the command args; auto-run if a question came in,
  // otherwise focus the input so the user can type one.
  useEffect(function () {
    if (!open) return undefined;
    const cam = initialCamera || "auto";
    const q = initialQuestion || "";
    setCamera(cam);
    setQuestion(q);
    setResult(null);
    setError(null);
    setFrameUrl(null);
    setDetailUrl(null);
    if (q.trim()) {
      runLook(cam, q);
    } else {
      setTimeout(function () {
        try { inputRef.current && inputRef.current.focus(); } catch (_) {}
      }, 60);
    }
    return function () {
      try { abortRef.current && abortRef.current.abort(); } catch (_) {}
    };
  }, [open, initialCamera, initialQuestion, runLook]);

  // Escape closes.
  useEffect(function () {
    if (!open) return undefined;
    const onKey = function (e) {
      if (e.key === "Escape") onClose && onClose();
    };
    window.addEventListener("keydown", onKey);
    return function () { window.removeEventListener("keydown", onKey); };
  }, [open, onClose]);

  if (!open) return null;

  const prims = (result && Array.isArray(result.primitives))
    ? result.primitives : [];
  const fw = (result && result.frame_w) || 1280;
  const fh = (result && result.frame_h) || 720;
  const labelSize = fh / 17;
  const camName = (LK_CAMERAS.filter(function (c) {
    return c.id === camera;
  })[0] || {}).name || camera;
  const canRun = !loading && !!question.trim();

  return (
    <div
      role="dialog" aria-modal="true" aria-label="Look — visual primitives"
      style={{
        position: "fixed", top: 0, right: 0, bottom: 0,
        width: "min(720px, 100vw)", background: "var(--hg-bg-0)",
        borderLeft: "1px solid var(--hg-border)",
        boxShadow: "-12px 0 32px rgba(0,0,0,0.3)", zIndex: 1100,
        display: "flex", flexDirection: "column", fontFamily: LK_FONT_MONO,
        animation: "look-slide-in 220ms cubic-bezier(0.16,1,0.3,1)",
      }}
    >
      <style>{`
        @keyframes look-slide-in {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>

      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "14px 22px 12px",
        borderBottom: "1px solid var(--hg-border-soft)",
      }}>
        <div style={{ display: "flex", flexDirection: "column",
                      lineHeight: 1.05 }}>
          <span style={{ fontFamily: LK_FONT_SANS, fontSize: 15,
                         fontWeight: 500, color: "var(--hg-fg-0)",
                         letterSpacing: "-0.02em" }}>look</span>
          <span style={{ fontSize: 8.5, letterSpacing: "0.24em",
                         fontWeight: 500, color: "var(--hg-fg-4)",
                         marginTop: 3 }}>thinking with visual primitives</span>
        </div>
        <button
          onClick={onClose} aria-label="Close" className="hg-focusable"
          style={{
            marginLeft: "auto", background: "transparent",
            border: "1px solid var(--hg-border-soft)",
            color: "var(--hg-fg-2)", padding: "4px 11px",
            fontFamily: LK_FONT_MONO, fontSize: 10.5,
            letterSpacing: "0.12em", cursor: "pointer",
            textTransform: "lowercase",
          }}
        >close · esc</button>
      </div>

      {/* Body — `hg-scroll` class applies the app's themed thin (6px)
          scrollbar from home-tokens.css instead of the default chunky
          Windows/WebView one. Same fix as the /lights drawer. */}
      <div className="hg-scroll" style={{ flex: 1, overflowY: "auto", overflowX: "hidden" }}>

        {/* Camera picker */}
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap",
                      padding: "12px 22px 4px", alignItems: "center" }}>
          <span style={{ color: "var(--hg-fg-4)", fontSize: 9,
                         letterSpacing: "0.14em", textTransform: "uppercase",
                         marginRight: 2 }}>camera</span>
          {[{ id: "auto", name: "auto" }].concat(LK_CAMERAS).map(function (c) {
            const on = c.id === camera;
            return (
              <button
                key={c.id} onClick={function () { setCamera(c.id); }}
                className="hg-focusable"
                style={{
                  background: on ? "var(--hg-bg-1)" : "transparent",
                  border: "1px solid " + (on ? "var(--hg-ice)"
                                             : "var(--hg-border-soft)"),
                  color: on ? "var(--hg-fg-0)" : "var(--hg-fg-3)",
                  padding: "3px 9px", fontFamily: LK_FONT_MONO,
                  fontSize: 9.5, letterSpacing: "0.06em", cursor: "pointer",
                }}
              >{c.name}</button>
            );
          })}
        </div>

        {/* Question input */}
        <div style={{ display: "flex", gap: 7, padding: "8px 22px 10px",
                      alignItems: "center" }}>
          <input
            ref={inputRef} type="text" value={question}
            onChange={function (e) { setQuestion(e.target.value); }}
            onKeyDown={function (e) {
              if (e.key === "Enter" && canRun) runLook(camera, question);
            }}
            placeholder="ask a spatial question — e.g. what is on the counter?"
            style={{
              flex: 1, minWidth: 0, background: "var(--hg-bg-1)",
              border: "1px solid var(--hg-border-soft)",
              color: "var(--hg-fg-0)", padding: "7px 10px",
              fontFamily: LK_FONT_SANS, fontSize: 12, borderRadius: 3,
              outline: "none",
            }}
          />
          <button
            onClick={function () { runLook(camera, question); }}
            disabled={!canRun} className="hg-focusable"
            style={{
              background: canRun ? "var(--hg-ice)" : "transparent",
              border: "1px solid " + (canRun ? "var(--hg-ice)"
                                             : "var(--hg-border-soft)"),
              color: canRun ? "var(--hg-bg-0)" : "var(--hg-fg-5)",
              padding: "7px 16px", fontFamily: LK_FONT_MONO, fontSize: 10.5,
              letterSpacing: "0.08em", textTransform: "lowercase",
              cursor: canRun ? "pointer" : "default", flex: "0 0 auto",
            }}
          >{loading ? "reasoning…" : "look"}</button>
        </div>

        {/* Sim-mode note */}
        {simActive && (
          <div style={{
            margin: "0 22px 8px", padding: "7px 11px",
            border: "1px solid var(--hg-border-soft)",
            background: "var(--hg-bg-1)", borderRadius: 3,
            fontFamily: LK_FONT_SANS, fontSize: 10.5,
            color: "var(--hg-fg-4)", lineHeight: 1.5,
          }}>
            sim mode — /look reasons over live camera frames. Connect to the
            real hub to use it.
          </div>
        )}

        {/* Error */}
        {error && (
          <div style={{
            margin: "0 22px 8px", padding: "7px 11px",
            border: "1px solid var(--hg-crit)", color: "var(--hg-crit)",
            fontFamily: LK_FONT_MONO, fontSize: 10.5, borderRadius: 3,
            wordBreak: "break-word",
          }}>{error}</div>
        )}

        {/* Loading */}
        {loading && (
          <div style={{ padding: "28px 22px", textAlign: "center",
                        color: "var(--hg-fg-3)", fontSize: 11 }}>
            reasoning over {camName}…
          </div>
        )}

        {/* Empty state */}
        {!loading && !result && !error && (
          <div style={{
            padding: "26px 24px", textAlign: "center",
            color: "var(--hg-fg-4)", fontFamily: LK_FONT_SANS,
            fontSize: 11.5, lineHeight: 1.65,
          }}>
            Ask a spatial question about a camera. The vision model reasons
            by <span style={{ color: "var(--hg-fg-2)" }}>pointing</span> —
            it boxes every object it reasons about, and those boxes are
            drawn on the frame below.
          </div>
        )}

        {/* Result */}
        {result && !loading && (
          <div style={{ padding: "2px 22px 24px" }}>

            {/* Which camera was used — esp. relevant when auto-routed */}
            <div style={{ margin: "8px 0 2px", fontFamily: LK_FONT_MONO,
              fontSize: 9, letterSpacing: "0.1em", color: "var(--hg-fg-4)" }}>
              looked at · <span style={{ color: "var(--hg-ice)" }}>
                {result.camera || camera}</span>
              {camera === "auto" ? " · auto-routed from the question" : ""}
            </div>

            {/* Answer */}
            {result.answer && (
              <div style={{
                margin: "10px 0 14px", padding: "10px 12px",
                border: "1px solid var(--hg-border-soft)",
                borderLeft: "2px solid var(--hg-ice)",
                background: "var(--hg-bg-1)", borderRadius: 3,
              }}>
                <div style={{
                  fontSize: 8.5, letterSpacing: "0.2em",
                  textTransform: "uppercase", color: "var(--hg-fg-4)",
                  marginBottom: 5,
                }}>answer</div>
                <div style={{
                  fontFamily: LK_FONT_SANS, fontSize: 13, lineHeight: 1.5,
                  color: "var(--hg-fg-0)",
                }}>{result.answer}</div>
              </div>
            )}

            {/* Two-panel figure — overview (full frame + zoom region) on
                the left, detail (the crop with pass-2 boxes) on the right.
                Both images are pre-rendered server-side by the sidecar
                (/reason_zoom returns overview_url + detail_url), so we
                just <img> them — no SVG overlay needed because the boxes
                are baked into the JPEGs by the sidecar's PIL renderer. */}
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              <div style={{ flex: "1 1 280px", minWidth: 0 }}>
                <div style={LK_PANEL_LABEL}>
                  overview
                  {result.zoom_label && (
                    <span style={{ color: "var(--hg-fg-5)", marginLeft: 6 }}>
                      → {result.zoom_label}
                    </span>
                  )}
                </div>
                <div style={LK_PANEL_FRAME}>
                  {frameUrl && frameOk ? (
                    <img
                      src={frameUrl} alt="full frame with zoom region"
                      onError={function () { setFrameOk(false); }}
                      style={{ display: "block", width: "100%" }}
                    />
                  ) : (
                    <div style={LK_PANEL_MISSING}>frame unavailable</div>
                  )}
                </div>
              </div>
              <div style={{ flex: "1 1 280px", minWidth: 0 }}>
                <div style={LK_PANEL_LABEL}>
                  detail (zoomed)
                  <span style={{ color: "var(--hg-fg-5)", marginLeft: 6 }}>
                    {prims.length} box{prims.length === 1 ? "" : "es"}
                  </span>
                </div>
                <div style={LK_PANEL_FRAME}>
                  {detailUrl && frameOk ? (
                    <img
                      src={detailUrl} alt="zoomed detail with visual primitives"
                      onError={function () { setFrameOk(false); }}
                      style={{ display: "block", width: "100%" }}
                    />
                  ) : (
                    <div style={LK_PANEL_MISSING}>frame unavailable</div>
                  )}
                </div>
              </div>
            </div>

            {/* Reasoning trace */}
            {result.reasoning && (
              <div style={{ marginTop: 14 }}>
                <div style={LK_PANEL_LABEL}>reasoning trace</div>
                <div style={{
                  fontFamily: LK_FONT_SANS, fontSize: 11.5, lineHeight: 1.65,
                  color: "var(--hg-fg-2)", whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}>{lkRenderReasoning(result.reasoning)}</div>
              </div>
            )}

            {/* Primitives list */}
            {prims.length > 0 && (
              <div style={{ marginTop: 14 }}>
                <div style={LK_PANEL_LABEL}>primitives</div>
                {prims.map(function (p, i) {
                  return (
                    <div key={i} style={{
                      display: "flex", alignItems: "baseline", gap: 8,
                      padding: "3px 0", fontFamily: LK_FONT_MONO,
                      fontSize: 10.5, color: "var(--hg-fg-3)",
                    }}>
                      <span style={{ color: "var(--hg-ice)", fontWeight: 700,
                                     minWidth: 15 }}>{i + 1}</span>
                      <span style={{ color: "var(--hg-fg-1)" }}>
                        {p.label || "object"}
                      </span>
                      <span style={{ color: "var(--hg-fg-5)", fontSize: 9 }}>
                        {(p.bbox_1000 || []).join(", ")}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Meta */}
            <div style={{
              marginTop: 14, paddingTop: 8,
              borderTop: "1px solid var(--hg-border-soft)",
              display: "flex", gap: 12, flexWrap: "wrap",
              fontFamily: LK_FONT_MONO, fontSize: 9,
              color: "var(--hg-fg-5)", letterSpacing: "0.08em",
            }}>
              {result.latency_ms != null && (
                <span>{result.latency_ms}ms</span>
              )}
              {result.model && <span>{result.model}</span>}
              {result.entity_id && <span>{result.entity_id}</span>}
              <span>{fw}×{fh}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

window.HomeLookDrawer = HomeLookDrawer;
window.HomeLookParseArg = lkParseArg;
window.HomeLookReasonRequest = lkReasonRequest;
window.HomeLookReasonZoomRequest = lkReasonZoomRequest;
window.HomeLookVisionUrl = lkVisionUrl;
