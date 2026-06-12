/* eslint-disable */
/**
 * home-apartment-cards.jsx — hover labels + control cards for the /apartment
 * 3D view. Anchored DOM (Geist + --hg tokens render crisp at any DPI; the
 * caller positions via engine.picking.projectToScreen each frame).
 *
 * Cards follow home-lights.jsx idioms: optimistic local state, service calls
 * through the shared HAClient (window.__hav_haClient), reconcile via the
 * state_changed binding upstream.
 */

const { useState, useEffect, useRef, useCallback } = React;

const CARD_FONT_MONO = '"Geist Mono", "JetBrains Mono", monospace';
const CARD_FONT_SANS = '"Geist", "Inter", sans-serif';

function AptDeviceLabel({ device, screen, dim }) {
  if (!screen || !screen.visible) return null;
  return (
    <div style={{
      position: "absolute", left: screen.x, top: screen.y,
      transform: "translate(-50%, -130%)", pointerEvents: "none",
      fontFamily: CARD_FONT_MONO, fontSize: 9.5, letterSpacing: "0.08em",
      color: "var(--hg-fg-2)", opacity: dim ? 0.55 : 1,
      textShadow: "0 1px 4px rgba(0,0,0,0.9)", whiteSpace: "nowrap",
      textTransform: "lowercase",
    }}>{device.name}</div>
  );
}

function AptPersonLabel({ track, screen }) {
  if (!track || !screen || !screen.visible) return null;
  const name = (track.person && (track.conf ?? 1) > 0.5) ? track.person : null;
  const act = track.activity && (track.activity_conf ?? 0) > 0.6 ? track.activity.replace(/_/g, " ") : null;
  const text = [name || "someone", act].filter(Boolean).join(" · ");
  return (
    <div style={{
      position: "absolute", left: screen.x, top: screen.y,
      transform: "translate(-50%, -240%)", pointerEvents: "none",
      fontFamily: CARD_FONT_MONO, fontSize: 10.5, letterSpacing: "0.08em",
      color: "var(--hg-ice)", textShadow: "0 1px 6px rgba(0,0,0,0.9)",
      whiteSpace: "nowrap", textTransform: "lowercase",
    }}>{text}</div>
  );
}

/* room-level presence chip (pos unknown — the honesty ladder's middle rung) */
function AptRoomChip({ track }) {
  if (!track || track.pos || !track.room) return null;
  return (
    <div style={{
      position: "absolute", left: 18, bottom: 92,
      fontFamily: CARD_FONT_MONO, fontSize: 10, letterSpacing: "0.1em",
      color: "var(--hg-ice)", border: "1px solid var(--hg-border-soft)",
      background: "rgba(10,12,16,0.75)", padding: "5px 10px",
      textTransform: "lowercase",
    }}>
      {(track.person || "someone")} · in {String(track.room).replace(/_/g, " ")}
      {track.activity && (track.activity_conf ?? 0) > 0.6 ? ` · ${track.activity.replace(/_/g, " ")}` : ""}
    </div>
  );
}

function AptControlCard({ device, state, screen, onClose, onService, onFlyTo, sim }) {
  const [briDraft, setBriDraft] = useState(null);
  const [volDraft, setVolDraft] = useState(null);
  if (!device || !screen) return null;
  const attrs = state?.attributes || {};
  const isLight = device.type === "light" || device.ha_entity_id?.startsWith("light.");
  const isSwitch = device.ha_entity_id?.startsWith("switch.");
  const isMedia = ["speaker", "tv", "amp"].includes(device.type)
    || device.ha_entity_id?.startsWith("media_player.");
  const on = state?.state === "on" || state?.state === "playing";
  const bri = briDraft ?? Math.round(((attrs.brightness ?? 0) / 255) * 100);
  const vol = volDraft ?? Math.round((attrs.volume_level ?? 0) * 100);
  const mediaOff = ["off", "standby", "unavailable", "unknown"].includes(state?.state);

  const x = Math.min(Math.max(screen.x + 18, 10), window.innerWidth - 280);
  const y = Math.min(Math.max(screen.y - 30, 56), window.innerHeight - 220);

  const row = { display: "flex", alignItems: "center", gap: 8, marginTop: 8 };
  const btn = (label, onClick, primary) => (
    <button onClick={onClick} className="hg-focusable" style={{
      background: primary ? "var(--hg-ice)" : "transparent",
      border: "1px solid " + (primary ? "var(--hg-ice)" : "var(--hg-border-soft)"),
      color: primary ? "#0b0d11" : "var(--hg-fg-1)", cursor: "pointer",
      padding: "4px 10px", fontFamily: CARD_FONT_MONO, fontSize: 10,
      letterSpacing: "0.08em", textTransform: "lowercase",
    }}>{label}</button>
  );

  return (
    <div style={{
      position: "absolute", left: x, top: y, width: 260,
      background: "var(--hg-bg-1)", border: "1px solid var(--hg-border)",
      padding: "12px 14px", zIndex: 5, boxShadow: "0 8px 28px rgba(0,0,0,0.5)",
    }}>
      <div style={{ display: "flex", alignItems: "baseline" }}>
        <span style={{ fontFamily: CARD_FONT_SANS, fontSize: 13, color: "var(--hg-fg-0)" }}>
          {device.name}
        </span>
        <span style={{ marginLeft: "auto", fontFamily: CARD_FONT_MONO, fontSize: 9,
                       color: on ? "var(--hg-ice)" : "var(--hg-fg-5)" }}>
          {state?.state || "unknown"}
        </span>
        <button onClick={onClose} className="hg-focusable" style={{
          marginLeft: 10, background: "transparent", border: "none",
          color: "var(--hg-fg-4)", cursor: "pointer", fontFamily: CARD_FONT_MONO,
        }}>×</button>
      </div>
      <div style={{ fontFamily: CARD_FONT_MONO, fontSize: 8.5, color: "var(--hg-fg-5)",
                    letterSpacing: "0.1em", marginTop: 2 }}>
        {device.ha_entity_id || "unbound"}
      </div>

      {sim && (
        <div style={{ marginTop: 8, fontFamily: CARD_FONT_MONO, fontSize: 9.5,
                      color: "var(--hg-warn)" }}>sim mode — controls disabled</div>
      )}

      {!sim && (isLight || isSwitch) && device.ha_entity_id && (
        <>
          <div style={row}>
            {btn(on ? "turn off" : "turn on", () =>
              onService(device.ha_entity_id.split(".")[0], on ? "turn_off" : "turn_on",
                { entity_id: device.ha_entity_id }), true)}
          </div>
          {isLight && on && (
            <div style={row}>
              <span style={{ fontFamily: CARD_FONT_MONO, fontSize: 9, color: "var(--hg-fg-4)" }}>bri</span>
              <input type="range" min="1" max="100" value={bri}
                onChange={(e) => setBriDraft(+e.target.value)}
                onMouseUp={() => { onService("light", "turn_on",
                  { entity_id: device.ha_entity_id, brightness_pct: bri }); setBriDraft(null); }}
                style={{ flex: 1 }} />
              <span style={{ fontFamily: CARD_FONT_MONO, fontSize: 9.5, color: "var(--hg-fg-2)", width: 30 }}>
                {bri}%
              </span>
            </div>
          )}
        </>
      )}

      {!sim && isMedia && device.ha_entity_id && (
        <>
          <div style={row}>
            {btn(state?.state === "playing" ? "pause" : "play", () =>
              onService("media_player", "media_play_pause", { entity_id: device.ha_entity_id }), true)}
            {btn("next", () => onService("media_player", "media_next_track", { entity_id: device.ha_entity_id }))}
            {btn(mediaOff ? "turn on" : "turn off", () =>
              onService("media_player", mediaOff ? "turn_on" : "turn_off",
                { entity_id: device.ha_entity_id }))}
          </div>
          <div style={row}>
            <span style={{ fontFamily: CARD_FONT_MONO, fontSize: 9, color: "var(--hg-fg-4)" }}>vol</span>
            <input type="range" min="0" max="100" value={vol}
              onChange={(e) => setVolDraft(+e.target.value)}
              onMouseUp={() => { onService("media_player", "volume_set",
                { entity_id: device.ha_entity_id, volume_level: vol / 100 }); setVolDraft(null); }}
              style={{ flex: 1 }} />
            <span style={{ fontFamily: CARD_FONT_MONO, fontSize: 9.5, color: "var(--hg-fg-2)", width: 30 }}>
              {vol}%
            </span>
          </div>
          {attrs.media_title && (
            <div style={{ marginTop: 8, fontFamily: CARD_FONT_MONO, fontSize: 9.5,
                          color: "var(--hg-fg-3)", overflow: "hidden", whiteSpace: "nowrap",
                          textOverflow: "ellipsis" }}>
              ♪ {attrs.media_title}{attrs.media_artist ? ` — ${attrs.media_artist}` : ""}
            </div>
          )}
        </>
      )}

      {device.type === "camera" && (
        <>
          <div style={row}>
            {btn("fly to view", () => { onFlyTo && onFlyTo(device); onClose(); }, true)}
          </div>
          <div style={{ marginTop: 6, fontFamily: CARD_FONT_MONO, fontSize: 8.5, color: "var(--hg-fg-5)" }}>
            {device.camera?.extrinsics ? "calibrated pose" : "not calibrated — approximate pose"}
            {" · live feed needs go2rtc :1984 exposed"}
          </div>
        </>
      )}
    </div>
  );
}

Object.assign(window, { AptDeviceLabel, AptPersonLabel, AptRoomChip, AptControlCard });
