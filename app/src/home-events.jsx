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
  return (
    <div style={{ ...T_PROSE, ...HG_FG_BRIGHT }}>
      {text}
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
            <div key={k}><span style={HG_FAINT}>{k.padEnd(9, "\u00a0")}</span><span style={HG_MUTE}>{v}</span></div>
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
                <span style={HG_FAINT}>{k}</span>
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
              }}>cancel esc</button>
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
  // Phase 1.5: `whiteSpace: pre-line` lets multi-line system events
  // (notably the new /help vertical list) render with line breaks
  // intact. wordBreak softens long lines at narrow viewports.
  return (
    <div style={{
      ...T_SYNTAX, ...(isErr ? HG_WARN : HG_DIM),
      fontStyle: "italic",
      whiteSpace: "pre-line",
      wordBreak: "break-word",
    }}>
      <span style={HG_FAINT}>system  </span>{text}
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
 * Expandable on click to show full text. */
function PerceptionContent({ text }) {
  const [expanded, setExpanded] = React.useState(false);
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
  return (
    <div
      onClick={() => summary.length > 90 && setExpanded((v) => !v)}
      style={{
        ...T_SYNTAX,
        display: "grid",
        gridTemplateColumns: room ? "minmax(80px, 120px) 1fr auto" : "auto 1fr auto",
        columnGap: 10,
        alignItems: "baseline",
        cursor: summary.length > 90 ? "pointer" : "default",
        color: "var(--hg-fg-3)",
        fontSize: 11,
        lineHeight: 1.5,
        paddingTop: 2, paddingBottom: 2,
      }}
    >
      <span style={{
        ...HG_FAINT,
        fontWeight: 400,
        letterSpacing: "0.06em",
        color: "var(--hg-fg-4)",
      }}>{room ? room : "perceived"}</span>
      <span style={{
        color: "var(--hg-fg-2)",
        fontFamily: "'Geist', system-ui, sans-serif",
        fontSize: 12,
        fontWeight: 400,
      }}>{short}</span>
      <span />
    </div>
  );
}

/* ── Event dispatch ──────────────────────────────────────────────────── */
function EventContent({ e, onConfirm, onCancel, onUndo }) {
  switch (e.kind) {
    case "user":       return <UserContent text={e.text} />;
    case "voice":      return <VoiceContent text={e.text} />;
    case "thinking":   return <ThinkingContent text={e.text} />;
    case "tool":       return <ToolContent name={e.name} args={e.args} status={e.status} latency={e.latency} />;
    case "action":     return <ActionContent id={e.id} title={e.title} service={e.service} target={e.target} attrs={e.attrs} status={e.status} latency={e.latency} reason={e.reason} traceId={e.traceId} onConfirm={onConfirm} onCancel={onCancel} onUndo={onUndo} />;
    case "home":       return <HomeContent text={e.text} streaming={e.streaming} />;
    case "perception": return <PerceptionContent text={e.text} />;
    case "system":     return <SystemContent text={e.text} tone={e.tone} />;
    default: return null;
  }
}

/* ── Grouping ────────────────────────────────────────────────────────── */
function speakerOf(e) {
  if (e.kind === "user" || e.kind === "voice") return "you";
  if (e.kind === "system") return "system";
  if (e.kind === "perception") return "perception";
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
function TurnBlock({ group, density, onConfirmAction, onCancelAction, onUndoAction }) {
  const tone = group.speaker === "system" ? "system" : group.speaker;
  return (
    <div className="hg-fade">
      <TurnHeader speaker={group.speaker} time={group.time} tone={tone} />
      <TurnBody density={density}>
        {group.events.map((e) => (
          <EventContent key={e.id} e={e} onConfirm={onConfirmAction} onCancel={onCancelAction} onUndo={onUndoAction} />
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
  ActionContent, HomeContent, SystemContent,
  groupEventsBySpeaker, speakerOf,
});
