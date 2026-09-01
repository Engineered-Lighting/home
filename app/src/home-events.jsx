/* Home — turn-based dialogue, redesigned.
 *
 *   YOU                                   09:24
 *
 *   Make the living room lights cooler.
 *
 *
 *   HOME                                  09:24
 *
 *   Interpreting as color temperature change
 *   Resolved area: living_room  ·  3 fixtures
 *
 *   light.turn_on  area=living_room  kelvin=5000           ok · 83ms
 *
 *   Done. Shifted three fixtures to 5000k.
 *   Island unchanged — match?
 *
 * Two type families, two sizes, generous rhythm.
 *   – Prose (you / home)          Inter 14.5 / 1.7
 *   – Syntax (thinking/tool/act)  Geist Mono 12.5 / 1.7
 *   – Meta (speaker, time, ok)    Geist Mono 10.5 uppercase, tracked
 */

const HG_SANS = "'Geist', -apple-system, BlinkMacSystemFont, system-ui, sans-serif";
const HG_MONO = "'Geist Mono', ui-monospace, 'SF Mono', Menlo, monospace";

const HG_FG_BRIGHT = { color: "var(--hg-fg-0)" };
const HG_FG        = { color: "var(--hg-fg-1)" };
const HG_MUTE      = { color: "var(--hg-fg-2)" };
const HG_DIM       = { color: "var(--hg-fg-3)" };
const HG_FAINT     = { color: "var(--hg-fg-4)" };
const HG_META      = { color: "var(--hg-fg-3)" }; // information-carrying keys/labels — readable rung
const HG_GHOST     = { color: "var(--hg-fg-5)" };
const HG_ICE       = { color: "var(--hg-ice)" };
const HG_WARN      = { color: "var(--hg-warn)" };

/* Unified scales — every text element is one of these. */
const T_META = {
  fontFamily: HG_MONO,
  fontSize: 10.5,
  letterSpacing: "0.20em",
  textTransform: "uppercase",
  fontWeight: 500,
  lineHeight: 1.5,
};
const T_PROSE = {
  fontFamily: HG_SANS,
  fontSize: 14.5,
  lineHeight: 1.7,
  letterSpacing: "-0.005em",
  fontWeight: 400,
};
const T_SYNTAX = {
  fontFamily: HG_MONO,
  fontSize: 12.5,
  lineHeight: 1.7,
  letterSpacing: "0",
  fontWeight: 400,
  fontVariantNumeric: "tabular-nums",
};

/* ── Turn header (speaker + time, no rule) ───────────────────────────── */
function TurnHeader({ speaker, time, tone = "user" }) {
  const labelColor =
    tone === "user"   ? "var(--hg-fg-2)" :
    tone === "home"   ? "var(--hg-fg-2)" :
                        "var(--hg-fg-4)";
  return (
    <div style={{
      display: "flex", alignItems: "baseline", justifyContent: "space-between",
      padding: "28px 24px 8px",
    }}>
      <span style={{ ...T_META, color: labelColor, fontSize: 10, letterSpacing: "0.16em", fontWeight: 500, textTransform: "lowercase" }}>{speaker}</span>
      <span style={{
        ...T_META,
        fontSize: 10,
        letterSpacing: "0.08em",
        textTransform: "none",
        color: "var(--hg-fg-3)",
        fontVariantNumeric: "tabular-nums",
      }}>{time}</span>
    </div>
  );
}

/* ── Turn body wrapper ───────────────────────────────────────────────── */
function TurnBody({ children, density = "comfortable" }) {
  return (
    <div style={{
      padding: density === "compact" ? "0 24px 6px" : "0 24px 10px",
      display: "flex", flexDirection: "column",
      gap: density === "compact" ? 4 : 8,
    }}>{children}</div>
  );
}

/* ── Status snippet — small mono, right-aligned ─────────────────────── */
function StatusText({ status, latency }) {
  if (status === "pending") return <span style={HG_ICE}>running…</span>;
  if (status === "pending-confirm") return <span style={{ color: "var(--hg-warn)" }}>awaiting confirm</span>;
  if (status === "cancelled") return <span style={HG_DIM}>cancelled</span>;
  if (status === "error")   return <span style={HG_WARN}>failed</span>;
  if (status === "success") return (
    <span style={{ fontFamily: HG_MONO, fontSize: 11 }}>
      <span style={HG_FG_BRIGHT}>ok</span>
      {latency && <span style={HG_DIM}>{"  ·  " + latency}</span>}
    </span>
  );
  return null;
}

/* ── Prose content (you / voice / home) ──────────────────────────────── */
function UserContent({ text }) {
  return <div style={{ ...T_PROSE, ...HG_FG_BRIGHT }}>{text}</div>;
}

function VoiceContent({ text }) {
  return (
    <div style={{ ...T_PROSE, ...HG_FG_BRIGHT, display: "flex", alignItems: "baseline", gap: 10 }}>
      <span style={{ ...HG_ICE, fontSize: 11, lineHeight: 1, transform: "translateY(-1px)" }}>◉</span>
      <span style={{ flex: 1 }}>{text}</span>
    </div>
  );
}

function HomeContent({ text, streaming }) {
  const displayText = (() => {
    try {
      return window.HomeNormalizeChatEventText
        ? window.HomeNormalizeChatEventText(text || "")
        : (text || "");
    } catch {
      return text || "";
    }
  })();
  return (
    <div style={{ ...T_PROSE, ...HG_FG_BRIGHT }}>
      {displayText}
      {streaming && <span className="hg-caret" />}
    </div>
  );
}

/* ── Syntax content (thinking / tool / act) ──────────────────────────── */
function ThinkingContent({ text }) {
  return (
    <div style={{ ...T_SYNTAX, ...HG_MUTE, fontStyle: "italic" }}>
      {text}
    </div>
  );
}

function ToolContent({ name, args = {}, status = "success", latency }) {
  const [open, setOpen] = React.useState(false);
  const caret = open ? "▾" : "▸";
  return (
    <div>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          ...T_SYNTAX,
          display: "grid",
          gridTemplateColumns: "1.4ch 4ch minmax(0, 1fr) auto",
          columnGap: 8,
          alignItems: "baseline",
          cursor: "pointer",
          userSelect: "none",
        }}
      >
        <span style={{ color: "var(--hg-fg-4)" }}>{caret}</span>
        <span style={HG_DIM}>tool</span>
        <span style={HG_FG}>{name}</span>
        <span style={{ textAlign: "right", whiteSpace: "nowrap" }}>
          <StatusText status={status} latency={latency} />
        </span>
      </div>
      {open && Object.keys(args).length > 0 && (
        <div style={{
          ...T_SYNTAX,
          marginTop: 4,
          marginLeft: "calc(1.4ch + 8px)",
          paddingLeft: 12,
          borderLeft: "1px solid var(--hg-border-soft)",
          color: "var(--hg-fg-3)",
        }}>
          {Object.entries(args).map(([k, v]) => (
            <div key={k}><span style={HG_META}>{k.padEnd(9, "\u00a0")}</span><span style={HG_MUTE}>{v}</span></div>
          ))}
        </div>
      )}
    </div>
  );
}

// Master plan F.1: inverse-service mapping for the undo button.
// Returns null if the service has no clean inverse (e.g. media_play
// can't be cleanly undone — was it idle or paused before?).
const INVERSE_SERVICE = {
  "light.turn_on": "light.turn_off",
  "light.turn_off": "light.turn_on",
  "switch.turn_on": "switch.turn_off",
  "switch.turn_off": "switch.turn_on",
  "fan.turn_on": "fan.turn_off",
  "fan.turn_off": "fan.turn_on",
  "cover.open_cover": "cover.close_cover",
  "cover.close_cover": "cover.open_cover",
  "media_player.media_play": "media_player.media_pause",
  "media_player.media_pause": "media_player.media_play",
  "media_player.turn_on": "media_player.turn_off",
  "media_player.turn_off": "media_player.turn_on",
};

function ActionContent({ id, title, service, target, attrs = {}, status = "pending", latency, reason, onConfirm, onCancel, onUndo, traceId }) {
  const needsConfirm = status === "pending-confirm";
  const isErr = status === "error";
  // Phase B F0-07: auto-open error cards so the user sees the failure
  // reason immediately, not buried behind a click. Same for pending-confirm
  // (already auto-opened before this change).
  const [open, setOpen] = React.useState(needsConfirm || isErr);
  const caret = open ? "▾" : "▸";
  // F.1 + F.2: undo + why state
  const [undoFiring, setUndoFiring] = React.useState(false);
  const [undoDone, setUndoDone] = React.useState(false);
  const canUndo = onUndo && status === "success" && service in INVERSE_SERVICE && !undoDone;

  // Build the key/value rows shown when expanded.
  // Order is intentional: service first (what was called), target second
  // (where), then attrs (how), then reason for errors.
  const rows = [];
  if (service) rows.push(["service", service]);
  if (target)  rows.push(["target", target]);
  Object.entries(attrs).forEach(([k, v]) => rows.push([k, String(v)]));
  if (reason)  rows.push(["reason", reason]);

  // Pad keys to longest for vertical alignment (mono font, fixed width).
  const keyWidth = Math.max(7, ...rows.map(([k]) => k.length));

  return (
    <div>
      {/* Header row — always shows caret + label + title + status. Undo
          button (when applicable) sits inline to the right of status so
          it's discoverable without expanding. */}
      <div
        style={{
          ...T_SYNTAX,
          display: "grid",
          gridTemplateColumns: canUndo
            ? "1.4ch 8ch minmax(0,1fr) auto auto"
            : "1.4ch 8ch minmax(0,1fr) auto",
          columnGap: 8,
          alignItems: "baseline",
          userSelect: "none",
        }}
      >
        <span
          onClick={() => setOpen(o => !o)}
          style={{ color: "var(--hg-fg-4)", cursor: "pointer" }}
        >{caret}</span>
        <span
          onClick={() => setOpen(o => !o)}
          style={{ ...(isErr ? HG_WARN : HG_DIM), cursor: "pointer" }}
        >action</span>
        <span
          onClick={() => setOpen(o => !o)}
          style={{ ...(isErr ? HG_WARN : HG_FG), cursor: "pointer", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        >{title || service || ""}</span>
        <span
          onClick={() => setOpen(o => !o)}
          style={{ textAlign: "right", whiteSpace: "nowrap", cursor: "pointer" }}
        >
          <StatusText status={status} latency={latency} />
        </span>
        {/* F.1 (revised): inline undo button in the header — visible
            without requiring expansion. */}
        {canUndo && (
          <button
            disabled={undoFiring}
            onClick={async (ev) => {
              ev.stopPropagation();
              if (undoFiring) return;
              setUndoFiring(true);
              try {
                const inverse = INVERSE_SERVICE[service];
                await onUndo({ id, originalService: service, inverseService: inverse, target, attrs });
                setUndoDone(true);
              } catch (e) {
                console.warn("[action-undo] failed", e);
              } finally {
                setUndoFiring(false);
              }
            }}
            className="hg-focusable"
            style={{
              background: "transparent",
              border: "1px solid var(--hg-border)",
              color: undoFiring ? "var(--hg-fg-4)" : "var(--hg-fg-2)",
              padding: "1px 7px",
              cursor: undoFiring ? "default" : "pointer",
              fontFamily: HG_MONO,
              fontSize: 9,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              lineHeight: 1.3,
              marginLeft: 8,
            }}
            title={`Fire ${INVERSE_SERVICE[service]} on the same target`}
          >{undoFiring ? "…" : "undo"}</button>
        )}
        {undoDone && (
          <span style={{ ...HG_FAINT, fontSize: 9, marginLeft: 8 }}>
            ↺
          </span>
        )}
      </div>

      {/* Detail panel — left rule + aligned key/value pairs */}
      {open && (
        <div style={{
          marginTop: 6,
          marginLeft: "calc(1.4ch / 2)",   // align rule under caret centre
          paddingLeft: 14,
          borderLeft: "1px solid var(--hg-border-soft)",
        }}>
          <div style={{ ...T_SYNTAX, display: "grid", rowGap: 2 }}>
            {rows.map(([k, v]) => (
              <div key={k} style={{ display: "grid", gridTemplateColumns: `${keyWidth + 3}ch minmax(0,1fr)`, columnGap: 4 }}>
                <span style={HG_META}>{k}</span>
                <span style={{ ...(k === "reason" ? HG_WARN : HG_MUTE), wordBreak: "break-word" }}>{v}</span>
              </div>
            ))}
          </div>
          {needsConfirm && (
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button onClick={() => onConfirm && onConfirm(id)} className="hg-focusable" style={{
                background: "transparent", border: "1px solid var(--hg-ice)",
                color: "var(--hg-ice-bright)", padding: "4px 10px", cursor: "pointer",
                fontFamily: HG_MONO, fontSize: 10, letterSpacing: "0.14em", textTransform: "uppercase",
              }}>confirm ↵</button>
              <button onClick={() => onCancel && onCancel(id)} className="hg-focusable" style={{
                background: "transparent", border: "1px solid var(--hg-border)",
                color: "var(--hg-fg-2)", padding: "4px 10px", cursor: "pointer",
                fontFamily: HG_MONO, fontSize: 10, letterSpacing: "0.14em", textTransform: "uppercase",
              }}>cancel <span className="hg-esc-hint">esc</span></button>
            </div>
          )}
          {/* F.1: undo button moved to header row above; show only the
              "reverted" confirmation here when it applies. */}
          {undoDone && (
            <div style={{ marginTop: 10, fontSize: 10, color: "var(--hg-fg-3)", fontFamily: HG_MONO }}>
              ↺ reverted — fired {INVERSE_SERVICE[service]}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* Shared row for tool/act:
 *   label   head   piece · piece · piece                           ok · 83ms
 * Label is dim and reserved-width; tail pieces are space-separated.
 */
function SyntaxLine({ label, labelStyle, head, tail = [], status }) {
  return (
    <div style={{
      ...T_SYNTAX,
      display: "grid",
      gridTemplateColumns: "5ch minmax(0, 1fr) auto",
      columnGap: 8,
      alignItems: "baseline",
    }}>
      <span style={labelStyle}>{label}</span>
      <span style={HG_FG}>
        {head}
        {tail.length > 0 && (
          <>
            {tail.map((p, i) => (
              <React.Fragment key={i}>
                <span style={HG_FAINT}>{"  ·  "}</span>
                <span style={HG_MUTE}>{p}</span>
              </React.Fragment>
            ))}
          </>
        )}
      </span>
      <span style={{ textAlign: "right", whiteSpace: "nowrap" }}>{status}</span>
    </div>
  );
}

function SystemContent({ text, tone }) {
  const isErr = tone === "error";
  const [expanded, setExpanded] = React.useState(false);
  const raw = String(text || "");
  const lines = raw.split(/\r?\n/);
  const isLong = raw.length > 520 || lines.length > 6;
  const visibleText = !isLong || expanded
    ? raw
    : `${lines.slice(0, 3).join("\n")}${lines.length > 3 ? "\n..." : raw.length > 260 ? "..." : ""}`;
  // Phase 1.5: `whiteSpace: pre-line` lets multi-line system events
  // (notably the new /help vertical list) render with line breaks
  // intact. wordBreak softens long lines at narrow viewports.
  return (
    <div style={{
      ...T_SYNTAX, ...(isErr ? HG_WARN : HG_DIM),
      fontStyle: "italic",
      whiteSpace: "pre-line",
      wordBreak: "break-word",
      padding: isLong ? "8px 10px" : 0,
      border: isLong ? "1px solid var(--hg-border-soft)" : "none",
      borderRadius: isLong ? 6 : 0,
      background: isLong ? "rgba(255,255,255,0.018)" : "transparent",
    }}>
      <span style={HG_FAINT}>system  </span>{visibleText}
      {isLong && (
        <button
          type="button"
          className="hg-focusable"
          onClick={() => setExpanded((open) => !open)}
          style={{
            display: "inline-flex",
            marginTop: 8,
            background: "transparent",
            border: "1px solid var(--hg-border-soft)",
            color: "var(--hg-fg-3)",
            cursor: "pointer",
            fontFamily: HG_MONO,
            fontSize: 9,
            letterSpacing: "0.14em",
            padding: "7px 10px",
            minHeight: 32,
            alignItems: "center",
            boxSizing: "border-box",
            textTransform: "uppercase",
          }}
        >
          {expanded ? "collapse log" : `show ${lines.length} lines`}
        </button>
      )}
    </div>
  );
}

/* The assistant's silent observation channel — vision-sidecar
 * descriptions triggered by Frigate person detection. Rendered in
 * italic, lower weight, with a faint "perceived" label so it's
 * visually distinct from anything the assistant SPOKE. The user
 * asked for a different font weight to signal "this is perception,
 * not speech." */
/* Tray v4: compact perception chip — replaces italic prose log.
 * Format: [glyph] room — short summary           age
 * Expandable on click to show full text.
 *
 * M1 (Addendum 27): when the event payload includes a snapshotUrl
 * (auto-trigger from vision-sidecar's snapshot cache), render a small
 * thumbnail to the left of the room label. Click on the thumbnail or
 * the text expands the chip — thumbnail grows to a larger preview,
 * text shows full caption. snapshotUrl is omitted for bridge-sourced
 * perception lines (those came from a separate code path with no
 * cached frame); the chip falls back to the text-only layout.
 */
function usePerceptionImageReady(snapshotUrl) {
  const [state, setState] = React.useState(() => snapshotUrl ? "loading" : "idle");
  React.useEffect(() => {
    if (!snapshotUrl) {
      setState("idle");
      return undefined;
    }
    let cancelled = false;
    setState("loading");
    const img = new Image();
    img.decoding = "async";
    img.onload = async () => {
      try { if (img.decode) await img.decode(); } catch (_) {}
      if (!cancelled) setState("loaded");
    };
    img.onerror = () => {
      if (!cancelled) setState("error");
    };
    img.src = snapshotUrl;
    return () => {
      cancelled = true;
      img.onload = null;
      img.onerror = null;
      img.src = "";
    };
  }, [snapshotUrl]);
  return state;
}

function PerceptionContent({ text, snapshotUrl, imageMode, imageUnavailable = false }) {
  const [expanded, setExpanded] = React.useState(false);
  const [lightboxOpen, setLightboxOpen] = React.useState(false);
  const imageState = usePerceptionImageReady(snapshotUrl);
  const showImageUnavailable = imageUnavailable || imageState === "error";
  const isAnnotated = imageMode === "annotated";
  // Parse the text — bridge emits "perceived ROOM: SUMMARY" or just SUMMARY.
  // Try to extract a room name from the first colon-prefixed token.
  let room = null;
  let summary = text || "";
  const m = /^([a-z_]+)\s*:\s*(.+)$/i.exec(summary.trim());
  if (m) {
    room = m[1].replace(/_/g, " ");
    summary = m[2];
  }
  const short = expanded ? summary : (summary.length > 90 ? summary.slice(0, 88) + "…" : summary);
  const hasThumb = Boolean(snapshotUrl) && imageState === "loaded";
  const clickable = summary.length > 90 || hasThumb;
  const openLightbox = React.useCallback((e) => {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    if (hasThumb) setLightboxOpen(true);
  }, [hasThumb]);
  const closeLightbox = React.useCallback(() => setLightboxOpen(false), []);
  const lightboxRef = React.useRef(null);
  // Overlay layer: HomeOverlay owns Escape (topmost-only) + Tab trap + focus
  // move-in/restore — no per-lightbox window listener.
  window.HomeOverlay.useOverlayLayer({
    key: "perception-lightbox",
    active: !!lightboxOpen,
    onEscape: closeLightbox,
    getRoot: () => lightboxRef.current,
    trap: true,
    initialFocus: "root",
  });
  const lightbox = lightboxOpen && hasThumb ? (
    <div
      ref={lightboxRef}
      role="dialog"
      aria-modal="true"
      aria-label={room ? `${room} perception image` : "perception image"}
      onClick={closeLightbox}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 99999,
        background: "rgba(0,0,0,0.92)",
        display: "grid",
        gridTemplateRows: "auto minmax(0, 1fr)",
        gap: 10,
        padding: "calc(env(safe-area-inset-top, 0px) + 14px) 14px calc(env(safe-area-inset-bottom, 0px) + 14px)",
        boxSizing: "border-box",
      }}
    >
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        color: "rgba(255,255,255,0.8)",
        fontFamily: HG_MONO,
        fontSize: 11,
        letterSpacing: "0.08em",
      }}>
        <span>{room ? room : "perception"}</span>
        <button
          type="button"
          onClick={closeLightbox}
          style={{
            color: "white",
            background: "rgba(255,255,255,0.08)",
            border: "1px solid rgba(255,255,255,0.18)",
            borderRadius: 4,
            padding: "9px 12px",
            fontFamily: HG_MONO,
            fontSize: 11,
            letterSpacing: "0.08em",
            textTransform: "lowercase",
          }}
        >close</button>
      </div>
      <img
        src={snapshotUrl}
        alt={room ? `${room} perception full size` : "perception full size"}
        onClick={(e) => e.stopPropagation()}
        style={{
          alignSelf: "center",
          justifySelf: "center",
          maxWidth: "100%",
          maxHeight: "100%",
          objectFit: "contain",
          borderRadius: 6,
          background: "black",
          boxShadow: "0 18px 60px rgba(0,0,0,0.45)",
        }}
      />
    </div>
  ) : null;
  // Layout: thumbnail (when present) | room label | summary | (right slot)
  const cols = hasThumb
    ? (isAnnotated ? "1fr"
      : expanded ? "minmax(180px, 280px) minmax(60px, 100px) 1fr auto"
                : "minmax(40px, 56px) minmax(60px, 100px) 1fr auto")
    : (room ? "minmax(60px, 100px) 1fr auto"
            : "auto 1fr auto");
  const thumbSize = expanded ? 200 : 40;
  if (hasThumb && isAnnotated) {
    return (
      <React.Fragment>
        <div
          onClick={() => setExpanded((v) => !v)}
          style={{
            ...T_SYNTAX,
            display: "grid",
            gridTemplateColumns: "1fr",
            gap: 8,
            cursor: "pointer",
            color: "var(--hg-fg-3)",
            fontSize: 11,
            lineHeight: 1.5,
            paddingTop: 4,
            paddingBottom: 4,
          }}
        >
          <div style={{
            display: "grid",
            gridTemplateColumns: room ? "minmax(74px, 108px) 1fr" : "auto 1fr",
            columnGap: 10,
            alignItems: "baseline",
          }}>
            <span style={{
              ...HG_META,
              fontWeight: 400,
              letterSpacing: "0.06em",
              color: "var(--hg-fg-3)",
            }}>{room ? room : "perceived"}</span>
            <span style={{
              color: "var(--hg-fg-2)",
              fontFamily: "'Geist', system-ui, sans-serif",
              fontSize: 12,
              fontWeight: 400,
            }}>{short}</span>
            {showImageUnavailable && (
              <span role="status" style={{
                color: "var(--hg-warn, #d6a448)",
                fontFamily: HG_MONO,
                fontSize: 9,
                letterSpacing: "0.06em",
              }}>image unavailable</span>
            )}
          </div>
          <img
            src={snapshotUrl}
            alt={room ? `${room} segmented view` : "segmented perception view"}
            loading="lazy"
            decoding="async"
            onClick={openLightbox}
            onError={(e) => { e.currentTarget.style.display = "none"; }}
            style={{
              width: "100%",
              maxHeight: expanded ? "62vh" : "min(360px, 46vh)",
              objectFit: "contain",
              borderRadius: 5,
              border: "1px solid var(--hg-border)",
              background: "rgba(0,0,0,0.35)",
              display: "block",
              cursor: "zoom-in",
            }}
          />
        </div>
        {lightbox}
      </React.Fragment>
    );
  }
  return (
    <React.Fragment>
      <div
        onClick={() => clickable && setExpanded((v) => !v)}
        style={{
          ...T_SYNTAX,
          display: "grid",
          gridTemplateColumns: cols,
          columnGap: 10,
          alignItems: hasThumb ? "center" : "baseline",
          cursor: clickable ? "pointer" : "default",
          color: "var(--hg-fg-3)",
          fontSize: 11,
          lineHeight: 1.5,
          paddingTop: 2, paddingBottom: 2,
        }}
      >
        {hasThumb && (
          <img
            src={snapshotUrl}
            alt={room ? `${room} snapshot` : "perception snapshot"}
            loading="lazy"
            decoding="async"
            onClick={openLightbox}
            onError={(e) => { e.currentTarget.style.display = "none"; }}
            style={{
              width: expanded ? "min(100%, 280px)" : thumbSize,
              height: expanded ? "auto" : thumbSize * 0.6,
              maxHeight: expanded ? "42vh" : undefined,
              objectFit: expanded ? "contain" : "cover",
              borderRadius: 3,
              border: "1px solid var(--hg-border)",
              display: "block",
              cursor: "zoom-in",
              background: "rgba(0,0,0,0.35)",
            }}
          />
        )}
        <span style={{
          ...HG_META,
          fontWeight: 400,
          letterSpacing: "0.06em",
          color: "var(--hg-fg-3)",
        }}>{room ? room : "perceived"}</span>
        <span style={{
          color: "var(--hg-fg-2)",
          fontFamily: "'Geist', system-ui, sans-serif",
          fontSize: 12,
          fontWeight: 400,
        }}>{short}</span>
        <span role={showImageUnavailable ? "status" : undefined} style={{
          color: showImageUnavailable ? "var(--hg-warn, #d6a448)" : undefined,
          fontFamily: HG_MONO,
          fontSize: 9,
          letterSpacing: "0.06em",
          whiteSpace: "nowrap",
        }}>{showImageUnavailable ? "image unavailable" : ""}</span>
      </div>
      {lightbox}
    </React.Fragment>
  );
}

/* The coordinator's own initiative — a proactive greeting/room-prompt, an
 * away-mode flip, a return-home scene, a follow-up echo. Compact, mono,
 * dimmed — its own channel, visually adjacent to PerceptionContent: ambient,
 * not a real spoken turn answering a user request. (home-proactive.jsx
 * emits these via addEvent({ kind:"proactive", text }).) */
function ProactiveContent({ text }) {
  return (
    <div style={{
      ...T_SYNTAX,
      display: "flex", alignItems: "baseline", gap: 8,
      fontSize: 11, lineHeight: 1.5,
      paddingTop: 2, paddingBottom: 2,
    }}>
      <span style={{ color: "var(--hg-ice)", fontSize: 9, transform: "translateY(-1px)" }}>◆</span>
      <span style={{ ...HG_FAINT, letterSpacing: "0.06em", color: "var(--hg-fg-4)" }}>proactive</span>
      <span style={{ color: "var(--hg-fg-2)", fontFamily: HG_SANS, fontSize: 12, flex: 1 }}>{text}</span>
    </div>
  );
}

/* Internal diagnostic channel — bridge telemetry ([parakeet], [kokoro],
 * [direct], …) and the proactive coordinator's [proactive] lines. Hidden
 * from the feed entirely unless `/debug on` (HomeApp filters kind:"diag"
 * out otherwise). Faint, small, mono — pure debug texture. */
function DiagContent({ text, channel }) {
  return (
    <div style={{
      ...T_SYNTAX,
      fontSize: 10.5,
      lineHeight: 1.5,
      color: "var(--hg-fg-4)",
      fontStyle: "italic",
      wordBreak: "break-word",
      opacity: 0.85,
    }}>
      {text}
    </div>
  );
}

/* External Reasoning Fallback: answers from a non-local provider
 * (OpenAI for MVP) carry a subtle 'external' tag so the user can
 * always see where an answer came from. Same prose typography as
 * HomeContent — feels seamless — but the small uppercase tag and ice
 * color mark it as not-from-the-home-agent. Streaming caret while
 * chunks arrive. See home-external.jsx + the routing plan. */
function ExternalContent({ text, streaming }) {
  return (
    <div style={{
      ...T_PROSE, ...HG_FG_BRIGHT,
      display: "flex", alignItems: "baseline", gap: 8,
    }}>
      <span style={{
        ...HG_FAINT,
        fontSize: 9,
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        color: "var(--hg-ice)",
        flexShrink: 0,
      }}>external</span>
      <span style={{ flex: 1 }}>
        {text}
        {streaming && <span className="hg-caret" />}
      </span>
    </div>
  );
}

/* ── HelpContent — categorized clickable command list ──────────────── */
//
// Renders the /help payload as grouped sections. Each row hover-
// brightens; click dispatches a window CustomEvent("hg-fill-input")
// with the full command signature so HomeApp can fill + focus the
// input. The user just clicks → hits Enter.
//
// Hint line at the bottom tells the user about the click-to-fill UX
// (one-time; no animation that'd nag them after they learn it).
function HelpContent({ groups = [], totalCount = 0, tip = "" }) {
  const ROW_BASE = "var(--hg-fg-2)";
  const ROW_HOVER = "var(--hg-fg-0)";
  const mobile = typeof window !== "undefined" && (
    (window.matchMedia && window.matchMedia("(max-width: 699px)").matches) ||
    window.innerWidth <= 699
  );
  const dispatchFill = (cmd) => {
    // Send the full signature (cmd + hint placeholder) so the user
    // sees what's expected — but the signature with <hint> placeholder
    // doesn't actually execute until they replace + hit enter.
    try {
      window.dispatchEvent(new CustomEvent("hg-fill-input", { detail: cmd }));
    } catch {
      // ignore — older browsers may not support CustomEvent constructor
    }
  };
  return (
    <div className="hg-scroll" style={{
      ...T_PROSE,
      color: "var(--hg-fg-2)",
      maxHeight: mobile ? "34vh" : "42vh",
      overflowY: "auto",
      paddingRight: mobile ? 0 : 4,
    }}>
      <div style={{
        ...T_META,
        marginBottom: mobile ? 8 : 10,
        color: "var(--hg-fg-3)",
        textTransform: "lowercase",
        letterSpacing: mobile ? "0.055em" : "0.08em",
        fontSize: mobile ? 10 : 10.5,
      }}>
        {totalCount > 0 ? `${totalCount} commands` : "commands"}
        {tip && (
          <span style={{ color: "var(--hg-fg-4)", marginLeft: 8 }}>
            · {tip}
          </span>
        )}
      </div>
      {groups.map((group) => (
        <div key={group.id} style={{ marginBottom: mobile ? 11 : 14 }}>
          <div style={{
            ...T_META,
            color: "var(--hg-fg-3)",
            fontSize: mobile ? 9 : 9.5,
            letterSpacing: mobile ? "0.11em" : "0.18em",
            textTransform: "uppercase",
            marginBottom: mobile ? 4 : 5,
            paddingBottom: 3,
            borderBottom: "1px solid var(--hg-border-soft)",
            lineHeight: 1.35,
          }}>
            <span>{group.label}</span>
            {group.desc && !mobile && (
              <span style={{
                color: "var(--hg-fg-4)",
                marginLeft: 8,
                letterSpacing: "0.04em",
                textTransform: "none",
                fontSize: 9.5,
              }}>· {group.desc}</span>
            )}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
            {(group.commands || []).map((c) => {
              // Build the signature shown in the row. The hint goes
              // into the input ONLY if it's not a placeholder pattern
              // (<x>, [x], etc.) — for those, fill with just the cmd
              // so the user can either tab-complete or type the arg.
              const looksLikePlaceholder = c.hint && /^[<\[]|[>\]]$/.test(c.hint.trim());
              const fillValue = looksLikePlaceholder ? c.cmd : (c.cmd + (c.hint ? " " + c.hint : ""));
              const displaySig = c.hint ? `${c.cmd} ${c.hint}` : c.cmd;
              return (
                <HelpRow
                  key={c.cmd}
                  sig={displaySig}
                  desc={c.desc}
                  onClick={() => dispatchFill(fillValue)}
                  baseColor={ROW_BASE}
                  hoverColor={ROW_HOVER}
                  mobile={mobile}
                />
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function HelpRow({ sig, desc, onClick, baseColor, hoverColor, mobile = false }) {
  const [hovered, setHovered] = React.useState(false);
  return (
    <div
      role="button"
      tabIndex={0}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick && onClick();
        }
      }}
      title="click to paste this command into the input box"
      className="hg-help-row"
      style={{
        ...T_SYNTAX,
        display: "grid",
        gridTemplateColumns: mobile ? "minmax(0, 1fr)" : "minmax(0, 28ch) minmax(0, 1fr)",
        gap: mobile ? 2 : 14,
        padding: mobile ? "6px 7px" : "3px 6px",
        color: hovered ? hoverColor : baseColor,
        background: hovered ? "color-mix(in oklab, var(--hg-fg-1) 6%, transparent)" : "transparent",
        cursor: "pointer",
        borderRadius: mobile ? 4 : 2,
        transition: "color 100ms, background 100ms",
        fontSize: mobile ? 11 : 11.5,
        lineHeight: mobile ? 1.28 : 1.2,
      }}
    >
      <span className="hg-help-sig" style={{
        color: hovered ? "var(--hg-fg-0)" : "var(--hg-fg-1)",
        fontFamily: HG_MONO,
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}>{sig}</span>
      <span className="hg-help-desc" style={{
        color: hovered ? "var(--hg-fg-1)" : "var(--hg-fg-3)",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}>— {desc}</span>
    </div>
  );
}

/* ── Event dispatch ──────────────────────────────────────────────────── */
function EventContent({ e, onConfirm, onCancel, onUndo, onControlAction, lifecycle, onWhy }) {
  // M5 (Addendum 27): wrap clickable bubble kinds with a thin <button>
  // that opens the explainability drawer. Only home / external / action
  // qualify, ONLY when (a) the event carries a convId AND (b) the
  // parent supplied an onWhy handler (parent gates on debugMode).
  // Control-card actions (e.controllable) keep their own click handlers
  // — interactive sliders take precedence over "open drawer."
  const inner = (() => {
    switch (e.kind) {
      case "user":       return <UserContent text={e.text} />;
      case "voice":      return <VoiceContent text={e.text} />;
      case "thinking":   return <ThinkingContent text={e.text} />;
      case "tool":       return <ToolContent name={e.name} args={e.args} status={e.status} latency={e.latency} />;
      case "action":
        if (e.controllable && e.actionCtx) {
          if (e.actionCtx.domain === "light" && window.LightControlCard) {
            return <window.LightControlCard ctx={e.actionCtx} lifecycle={lifecycle || "active"} onControlAction={onControlAction} />;
          }
          if (e.actionCtx.domain === "media_player" && window.MediaControlCard) {
            return <window.MediaControlCard ctx={e.actionCtx} lifecycle={lifecycle || "active"} onControlAction={onControlAction} />;
          }
        }
        return <ActionContent id={e.id} title={e.title} service={e.service} target={e.target} attrs={e.attrs} status={e.status} latency={e.latency} reason={e.reason} traceId={e.traceId} onConfirm={onConfirm} onCancel={onCancel} onUndo={onUndo} />;
      case "home":       return <HomeContent text={e.text} streaming={e.streaming} />;
      case "external":   return <ExternalContent text={e.text} streaming={e.streaming} />;
      case "perception": return <PerceptionContent text={e.text} snapshotUrl={e.snapshotUrl} imageMode={e.imageMode} imageUnavailable={e.imageUnavailable} />;
      case "proactive":  return <ProactiveContent text={e.text} />;
      case "diag":       return <DiagContent text={e.text} channel={e.channel} />;
      case "system":     return <SystemContent text={e.text} tone={e.tone} />;
      case "help":       return <HelpContent groups={e.groups} totalCount={e.totalCount} tip={e.tip} />;
      default: return null;
    }
  })();

  // Gate: only debug-mode-eligible kinds; controllable action cards
  // already own their click area (slider drag) so SKIP them.
  const isWhyEligible = onWhy && e.convId &&
    (e.kind === "home" || e.kind === "external" ||
     (e.kind === "action" && !(e.controllable && e.actionCtx)));

  if (!isWhyEligible) return inner;

  return (
    <div
      onClick={(ev) => {
        // Don't hijack text-selection. If the user is selecting text
        // within the bubble, the click came at the end of a drag with
        // an active selection — let it through.
        const sel = window.getSelection && window.getSelection();
        if (sel && sel.toString().length > 0) return;
        ev.stopPropagation();
        onWhy(e.convId, e);
      }}
      title="click to see why · routing + tool calls + timing"
      style={{ cursor: "pointer" }}
      className="hg-why-clickable"
    >
      {inner}
    </div>
  );
}

/* ── Grouping ────────────────────────────────────────────────────────── */
function speakerOf(e) {
  if (e.kind === "user" || e.kind === "voice") return "you";
  if (e.kind === "system") return "system";
  if (e.kind === "help") return "system";    // help groups under system
  if (e.kind === "perception") return "perception";
  // proactive lines group under the assistant ("home") — they ARE the
  // assistant taking initiative. (The default already returns "home";
  // this is explicit for intent.)
  if (e.kind === "proactive") return "home";
  return "home";
}

function groupEventsBySpeaker(events) {
  const groups = [];
  let cur = null;
  for (const e of events) {
    const sp = speakerOf(e);
    if (!cur || cur.speaker !== sp) {
      cur = { speaker: sp, time: e.time, events: [] };
      groups.push(cur);
    }
    cur.events.push(e);
    cur.time = e.time;
  }
  return groups;
}

/* ── Turn block ──────────────────────────────────────────────────────── */
function TurnBlock({ group, density, onConfirmAction, onCancelAction, onUndoAction, onControlAction, controlLifecycles, onWhy }) {
  const tone = group.speaker === "system" ? "system" : group.speaker;
  return (
    <div className="hg-fade">
      <TurnHeader speaker={group.speaker} time={group.time} tone={tone} />
      <TurnBody density={density}>
        {group.events.map((e) => (
          <EventContent key={e.id} e={e}
            onConfirm={onConfirmAction} onCancel={onCancelAction} onUndo={onUndoAction}
            onControlAction={onControlAction}
            lifecycle={controlLifecycles ? controlLifecycles.get(e.id) : undefined}
            onWhy={onWhy} />
        ))}
      </TurnBody>
    </div>
  );
}

Object.assign(window, {
  HG_SANS, HG_MONO, T_META, T_PROSE, T_SYNTAX,
  TurnHeader, TurnBody, TurnBlock,
  EventContent, StatusText,
  UserContent, VoiceContent, ThinkingContent, ToolContent,
  ActionContent, HomeContent, SystemContent, PerceptionContent, ProactiveContent, DiagContent,
  HelpContent, HelpRow,
  groupEventsBySpeaker, speakerOf,
});
