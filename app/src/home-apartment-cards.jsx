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

function aptCardViewport() {
  if (typeof window === "undefined") return { width: 1024, height: 768, mobile: false };
  const width = window.innerWidth || 1024;
  const height = window.innerHeight || 768;
  return { width, height, mobile: width < 700 };
}

function AptDeviceLabel({ device, screen, dim }) {
  if (!screen || !screen.visible) return null;
  const vp = aptCardViewport();
  const x = vp.mobile ? Math.min(Math.max(screen.x, 54), Math.max(54, vp.width - 54)) : screen.x;
  return (
    <div style={{
      position: "absolute", left: x, top: screen.y,
      transform: "translate(-50%, -130%)", pointerEvents: "none",
      fontFamily: CARD_FONT_MONO, fontSize: vp.mobile ? 8.5 : 9.5, letterSpacing: vp.mobile ? "0.06em" : "0.08em",
      color: "var(--hg-fg-2)", opacity: dim ? 0.55 : 1,
      textShadow: "0 1px 4px rgba(0,0,0,0.9)", whiteSpace: "nowrap",
      maxWidth: vp.mobile ? "46vw" : "none",
      overflow: "hidden",
      textOverflow: "ellipsis",
      textTransform: "lowercase",
    }}>{device.name}</div>
  );
}

function AptPersonLabel({ track, screen }) {
  if (!track || !screen || !screen.visible) return null;
  const vp = aptCardViewport();
  const x = vp.mobile ? Math.min(Math.max(screen.x, 86), Math.max(86, vp.width - 86)) : screen.x;
  const name = (track.person && (track.conf ?? 1) > 0.5) ? track.person : null;
  const act = track.activity && (track.activity_conf ?? 0) > 0.6 ? track.activity.replace(/_/g, " ") : null;
  const text = [name || "someone", act].filter(Boolean).join(" · ");
  return (
    <div style={{
      position: "absolute", left: x, top: screen.y,
      transform: vp.mobile ? "translate(-50%, -205%)" : "translate(-50%, -240%)", pointerEvents: "none",
      fontFamily: CARD_FONT_MONO, fontSize: vp.mobile ? 9 : 10.5, letterSpacing: vp.mobile ? "0.055em" : "0.08em",
      color: "var(--hg-ice)", textShadow: "0 1px 6px rgba(0,0,0,0.9)",
      whiteSpace: "nowrap",
      maxWidth: vp.mobile ? "58vw" : "none",
      overflow: "hidden",
      textOverflow: "ellipsis",
      textTransform: "lowercase",
    }}>{text}</div>
  );
}

/* room-level presence chip (pos unknown — the honesty ladder's middle rung) */
function AptRoomChip({ track }) {
  if (!track || track.pos || !track.room) return null;
  const vp = aptCardViewport();
  return (
    <div style={{
      position: "absolute", left: vp.mobile ? 12 : 18, bottom: vp.mobile ? "calc(78px + env(safe-area-inset-bottom, 0px))" : 92,
      maxWidth: vp.mobile ? "calc(100vw - 24px)" : "none",
      fontFamily: CARD_FONT_MONO, fontSize: vp.mobile ? 9 : 10, letterSpacing: vp.mobile ? "0.07em" : "0.1em",
      color: "var(--hg-ice)", border: "1px solid var(--hg-border-soft)",
      background: "rgba(10,12,16,0.75)", padding: "5px 10px",
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
      textTransform: "lowercase",
    }}>
      {(track.person || "someone")} · in {String(track.room).replace(/_/g, " ")}
      {track.activity && (track.activity_conf ?? 0) > 0.6 ? ` · ${track.activity.replace(/_/g, " ")}` : ""}
    </div>
  );
}

function AptControlCard({ device, state, screen, bounds, onClose, onService, onFlyTo, sim }) {
  const [briDraft, setBriDraft] = useState(null);
  const [volDraft, setVolDraft] = useState(null);
  const briCommitAtRef = useRef(0);
  const volCommitAtRef = useRef(0);
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

  const maxWidth = bounds?.width || window.innerWidth;
  const maxHeight = bounds?.height || window.innerHeight;
  const mobile = maxWidth < 700;
  const cardWidth = mobile ? Math.min(300, Math.max(240, maxWidth - 20)) : 260;
  const x = Math.min(Math.max(screen.x + (mobile ? 10 : 18), 10), Math.max(10, maxWidth - cardWidth - 10));
  const y = Math.min(Math.max(screen.y - 30, mobile ? 74 : 56), Math.max(mobile ? 74 : 56, maxHeight - (mobile ? 260 : 220)));

  const row = { display: "flex", alignItems: "center", gap: 8, marginTop: 8 };
  /* phones: the .hg-slider thumb/track hit is padding-based, so the slider
     rows only need to guarantee the 44px vertical touch box */
  const sliderRow = mobile ? { ...row, minHeight: 44 } : row;
  const stopCardEvent = (e) => { e?.stopPropagation?.(); };
  const commitBri = (e) => {
    stopCardEvent(e);
    const now = performance.now();
    if (now - briCommitAtRef.current < 180) return;
    briCommitAtRef.current = now;
    onService("light", "turn_on", { entity_id: device.ha_entity_id, brightness_pct: bri });
    setBriDraft(null);
  };
  const commitVol = (e) => {
    stopCardEvent(e);
    const now = performance.now();
    if (now - volCommitAtRef.current < 180) return;
    volCommitAtRef.current = now;
    onService("media_player", "volume_set", { entity_id: device.ha_entity_id, volume_level: vol / 100 });
    setVolDraft(null);
  };
  const btn = (label, onClick, primary) => (
    <button type="button"
      onPointerDown={stopCardEvent}
      onPointerUp={stopCardEvent}
      onTouchStart={stopCardEvent}
      onClick={(e) => { stopCardEvent(e); onClick?.(e); }}
      className="hg-focusable" style={{
      background: primary ? "var(--hg-ice)" : "transparent",
      border: "1px solid " + (primary ? "var(--hg-ice)" : "var(--hg-border-soft)"),
      color: primary ? "#0b0d11" : "var(--hg-fg-1)", cursor: "pointer",
      minHeight: mobile ? 38 : "auto",
      padding: mobile ? "7px 12px" : "4px 10px", fontFamily: CARD_FONT_MONO, fontSize: mobile ? 11 : 10,
      letterSpacing: "0.08em", textTransform: "lowercase",
    }}>{label}</button>
  );

  return (
    <div
      onPointerDown={stopCardEvent}
      onPointerUp={stopCardEvent}
      onClick={stopCardEvent}
      onDoubleClick={stopCardEvent}
      onTouchStart={stopCardEvent}
      onWheel={stopCardEvent}
      style={{
      position: "absolute", left: x, top: y, width: cardWidth,
      maxHeight: mobile ? "min(58dvh, 320px)" : "none",
      overflowY: mobile ? "auto" : "visible",
      background: "var(--hg-bg-1)", border: "1px solid var(--hg-border)",
      padding: mobile ? "10px 12px" : "12px 14px", zIndex: 5, boxShadow: "0 8px 28px rgba(0,0,0,0.5)",
    }}>
      <div style={{ display: "flex", alignItems: "baseline" }}>
        <span style={{ fontFamily: CARD_FONT_SANS, fontSize: 13, color: "var(--hg-fg-0)" }}>
          {device.name}
        </span>
        <span style={{ marginLeft: "auto", fontFamily: CARD_FONT_MONO, fontSize: 9,
                       color: on ? "var(--hg-ice)" : "var(--hg-fg-3)" }}>
          {state?.state || "unknown"}
        </span>
        <button type="button"
          aria-label="close card"
          onPointerDown={stopCardEvent}
          onPointerUp={stopCardEvent}
          onTouchStart={stopCardEvent}
          onClick={(e) => { stopCardEvent(e); onClose?.(); }}
          className="hg-focusable hg-mobile-touch" style={{
          marginLeft: 10, background: "transparent", border: "none",
          color: "var(--hg-fg-4)", cursor: "pointer", fontFamily: CARD_FONT_MONO,
        }}>×</button>
      </div>
      <div style={{ fontFamily: CARD_FONT_MONO, fontSize: 10.5, color: "var(--hg-fg-3)",
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
            <div style={sliderRow}>
              <span style={{ fontFamily: CARD_FONT_MONO, fontSize: 9, color: "var(--hg-fg-4)" }}>bri</span>
              <input type="range" min="1" max="100" value={bri}
                aria-label="brightness" className="hg-slider"
                onPointerDown={stopCardEvent}
                onPointerUp={commitBri}
                onTouchStart={stopCardEvent}
                onChange={(e) => setBriDraft(+e.target.value)}
                onMouseUp={commitBri}
                onTouchEnd={commitBri}
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
          <div style={sliderRow}>
            <span style={{ fontFamily: CARD_FONT_MONO, fontSize: 9, color: "var(--hg-fg-4)" }}>vol</span>
            <input type="range" min="0" max="100" value={vol}
              aria-label="volume" className="hg-slider"
              onPointerDown={stopCardEvent}
              onPointerUp={commitVol}
              onTouchStart={stopCardEvent}
              onChange={(e) => setVolDraft(+e.target.value)}
              onMouseUp={commitVol}
              onTouchEnd={commitVol}
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
          <div style={{ marginTop: 6, fontFamily: CARD_FONT_MONO, fontSize: 11.5, color: "var(--hg-fg-3)" }}>
            {device.camera?.extrinsics ? "calibrated pose" : "not calibrated — approximate pose"}
            {" · live feed needs go2rtc :1984 exposed"}
          </div>
        </>
      )}
    </div>
  );
}

// Room membership comes from the admitted model, never from display positions.
function aptRoomLightGroups({ rooms = [], lights = [] } = {}) {
  const roomById = new Map(rooms.filter(room => typeof room?.id === "string" && room.id)
    .map(room => [room.id, room]));
  const groups = new Map(), seen = new Set();
  for (const light of lights) {
    if (typeof light?.id !== "string" || !light.id || seen.has(light.id)) continue;
    seen.add(light.id);
    const room = roomById.get(light.room_id), id = room ? room.id : null;
    if (!groups.has(id)) groups.set(id, { id, label: room?.name || room?.label || room?.id || "Unassigned", lights: [] });
    groups.get(id).lights.push(light);
  }
  return [...groups.values()].map(group => {
    const states = group.lights.map(light => light.power);
    const power = states.some(state => state !== "on" && state !== "off") ? "unknown"
      : states.every(state => state === "on") ? "on" : states.every(state => state === "off") ? "off" : "mixed";
    return { ...group, power };
  });
}

function AptRoomLightGroups({ groups = [], onSelect, onPower, disabledReason = "", onHover }) {
  return <>{groups.map(group => {
    const reason = disabledReason || (group.power === "unknown" ? "Light state unavailable" : "");
    return <section className="apt-ui-section" key={group.id ?? "unassigned"} aria-label={`${group.label} lights`}>
      <div className="apt-ui-row"><h3 className="apt-ui-heading grow">{group.label}</h3>
        <label className="apt-ui-switch"><input type="checkbox" aria-label={`${group.label} room power`}
          checked={group.power === "on"} disabled={!!reason}
          ref={node => { if (node) node.indeterminate = group.power === "mixed" || group.power === "unknown"; }}
          onChange={event => onPower(group.id, event.target.checked)} />
          <span>{({ on: "On", off: "Off", mixed: "Mixed", unknown: "Unknown" })[group.power]}</span></label>
      </div>
      {reason && <p className="apt-ui-meta">{reason}</p>}
      <ul className="apt-ui-list">{group.lights.map(light => <li key={light.id}>
        <button type="button" className="apt-ui-list-button" aria-label={light.ariaLabel}
          aria-current={light.selected ? "true" : undefined} onClick={() => onSelect(light.id)}
          onMouseEnter={onHover && (() => onHover(light.id))} onMouseLeave={onHover && (() => onHover(null))}
          onFocus={onHover && (() => onHover(light.id))} onBlur={onHover && (() => onHover(null))}>
          <span className={`apt-ui-lamp${light.power === "on" ? " is-on" : ""}`} aria-hidden="true" />
          <span className="nm">{light.label || light.id}</span><span className="vl">{light.summary
            || (light.power === "on" ? "On" : light.power === "off" ? "Off" : "Unavailable")}</span>
        </button></li>)}</ul>
    </section>;
  })}</>;
}

function AptNamedAimList({ targets = [], onEdit, disabledReason = "" }) {
  return <section className="apt-ui-section" aria-label="Named aims"><h3 className="apt-ui-heading">Named aims</h3>
    {targets.length ? <ul className="apt-ui-list">{targets.map(target => <li key={target.id} className="apt-ui-row">
      <span className="grow">{target.name || target.label || target.id}</span>
      <button type="button" className="apt-ui-button is-small" aria-label={`Edit ${target.name || target.label || target.id}`}
        disabled={!!disabledReason} onClick={() => onEdit(target.id)}>Edit</button>
    </li>)}</ul> : <p className="apt-ui-meta">No named aims saved for this home.</p>}
    {disabledReason && targets.length > 0 && <p className="apt-ui-meta">{disabledReason}</p>}
  </section>;
}

// Temperature is a display cue, not a prediction of the room's measured color.
function aptDirectionalTone(kelvin) {
  const t = Math.max(1000, Math.min(12000, Number.isFinite(kelvin) ? kelvin : 2700)) / 100;
  const r = t <= 66 ? 255 : 329.7 * Math.pow(t - 60, -.1332);
  const g = t <= 66 ? 99.47 * Math.log(t) - 161.12 : 288.12 * Math.pow(t - 60, -.0755);
  const b = t <= 19 ? 0 : t <= 66 ? 138.52 * Math.log(t - 10) - 305.04 : 255;
  return `rgb(${[r, g, b].map(value => Math.round(Math.max(0, Math.min(255, value)))).join(",")})`;
}

function AptDirectionalRing({ radials = [], zone = null, onZone, onShow, view, power }) {
  const planRef = useRef(null);
  const zones = [...radials].sort((a, b) => a.number - b.number);
  const available = zones.length === 6 && zones.every((item, index) => item.number === index + 1 && Number.isFinite(item.azimuth_deg));
  React.useLayoutEffect(() => {
    if (view && planRef.current) return view.attachPlan(planRef.current);
  }, [view, available]);
  const selected = zone == null ? zones : zones.filter(item => item.number === zone);
  const level = selected.length ? selected.reduce((sum, item) => sum + item.brightness, 0) / selected.length : 0;
  const sector = azimuth => {
    const start = (-azimuth - 28) * Math.PI / 180, end = (-azimuth + 28) * Math.PI / 180;
    const point = (radius, angle) => `${59 + radius * Math.cos(angle)} ${59 + radius * Math.sin(angle)}`;
    return `M${point(55, start)} A55 55 0 0 1 ${point(55, end)} L${point(32, end)} A32 32 0 0 0 ${point(32, start)} Z`;
  };
  const activate = number => onZone?.(number === zone ? null : number);
  const keyActivate = (event, number) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault(); if (!event.repeat) activate(number);
    }
  };
  return <div className="apt-directional-ring" data-aim-ignore="true" style={{ display: "grid", gridTemplateColumns: "118px minmax(0,1fr)", gap: 14, alignItems: "center", fontFamily: CARD_FONT_SANS }}>
    <style>{`.apt-directional-ring .radial-sector:focus-visible{outline:none;stroke:var(--hg-blue);stroke-width:2}`}</style>
    {available ? <svg ref={planRef} viewBox="0 0 118 118" role="group" aria-label="Radial zones" style={{ display: "block", width: 118, height: 118 }}>
      {zones.map(item => <path key={`base-${item.number}`} d={sector(item.azimuth_deg)} fill="var(--apt-fill-2)" pointerEvents="none" />)}
      {zones.map(item => <path key={item.number} role="button" tabIndex={0} className="radial-sector"
          aria-label={`Radial zone ${item.number}`} aria-pressed={zone === item.number}
          data-apt-radial-plan-zone={item.number} d={sector(item.azimuth_deg)}
          fill={view ? "var(--apt-warm)" : item.on && item.brightness > 0 ? aptDirectionalTone(item.color_temp_kelvin) : "var(--apt-fill-2)"}
          fillOpacity={view ? 0 : item.on && item.brightness > 0 ? .25 + item.brightness / 1.3 : 1}
          stroke={zone === item.number ? "var(--hg-ice)" : "var(--hg-bg-1)"} strokeWidth="2"
          onClick={() => activate(item.number)} onKeyDown={event => keyActivate(event, item.number)} style={{ cursor: "pointer" }} />)}
      <g role="button" tabIndex={0} className="hg-focusable" aria-label="All radial zones" aria-pressed={zone == null}
        onClick={() => onZone?.(null)} onKeyDown={event => {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); if (!event.repeat) onZone?.(null); }
        }} style={{ cursor: "pointer" }}>
        <circle cx="59" cy="59" r="31" fill="transparent" />
        <text x="59" y="56" textAnchor="middle" fill="var(--hg-fg-0)" style={{ fontFamily: CARD_FONT_SANS, fontSize: 13, fontWeight: 560 }} pointerEvents="none">
          {Math.round(level * 100)}%</text>
        <text x="59" y="71" textAnchor="middle" fill="var(--hg-fg-3)" style={{ fontFamily: CARD_FONT_SANS, fontSize: 10.5, fontWeight: 500 }} pointerEvents="none">
          {zone == null ? "all zones" : `zone ${zone}`}</text>
      </g>
    </svg> : <p className="apt-ui-meta">Facing unavailable</p>}
    <div style={{ display: "grid", gap: 4, minWidth: 0, justifyItems: "start" }}>
      <div style={{ fontSize: 13, fontWeight: 540 }}>{zone == null ? "All zones" : `Zone ${zone}`}</div>
      {power}
      <p className="apt-ui-meta" title="Directions are modeled; facing is unavailable until radial heading commissioning."
        style={{ margin: 0, lineHeight: 1.5 }}>Facing unverified. Drag outward to brighten, inward to dim.</p>
      <button type="button" className="apt-ui-button is-small" disabled={!available || typeof onShow !== "function"}
        onClick={() => onShow?.()} style={{ minHeight: 44, flexShrink: 0 }}>Show in room</button>
    </div>
  </div>;
}

const aptDirectionalEmptySnapshot = Object.freeze({ descriptor: null, mode: "spot", active: false, readout: null });
const aptDirectionalEmptySubscribe = () => () => {};

function AptDirectionalComposer({ view, onMode, spotBadge = null, spotName = null }) {
  const rootRef = useRef(null), pillRef = useRef(null), readoutRef = useRef(null);
  const snapshot = React.useSyncExternalStore(view?.subscribe || aptDirectionalEmptySubscribe,
    view?.getSnapshot || (() => aptDirectionalEmptySnapshot), view?.getSnapshot || (() => aptDirectionalEmptySnapshot));
  React.useLayoutEffect(() => {
    if (view && rootRef.current && pillRef.current && readoutRef.current)
      return view.attach({ root: rootRef.current, pill: pillRef.current, readout: readoutRef.current });
  }, [view]);
  const descriptor = snapshot.descriptor;
  if (!descriptor) return null;
  const pillButton = (value, label, key) => <button type="button" className="hg-focusable"
    data-apt-directional-mode={value} aria-keyshortcuts={key} aria-pressed={snapshot.mode === value}
    aria-label={value === "spot" && spotName || undefined}
    title={value === "zones" ? "Drag around the light to set its radial zones (Z)" : "Drag the reticle to aim the spot (A)"}
    onClick={() => onMode?.(value)}>{label}{value === "spot" && spotBadge}</button>;
  return <div ref={rootRef} hidden data-apt-directional-composer="1"
    style={{ position: "absolute", inset: 0, pointerEvents: "none", overflow: "hidden", zIndex: 4 }}>
    <style>{`
      .apt-directional-hits [data-apt-radial-number],.apt-directional-hits [data-apt-radial-hub="hit"]{pointer-events:none}
      .apt-directional-hits [data-apt-radial-number][data-apt-radial-start]{pointer-events:fill;cursor:crosshair}
      .apt-directional-hits [data-apt-radial-hub="hit"][data-apt-radial-start]{pointer-events:all;cursor:crosshair}
      .apt-directional-toolpill{position:absolute;left:0;top:0;pointer-events:auto;display:flex;align-items:center;gap:2px;padding:3px;border-radius:999px;background:rgba(8,9,11,.84);border:1px solid rgba(255,255,255,.16);font-size:12px;white-space:nowrap;z-index:5}
      .apt-directional-toolpill[hidden]{display:none}
      .apt-directional-toolpill .nm{padding:0 6px 0 8px;color:#A1A1A1;font-weight:560;font-size:11.5px;max-width:180px;overflow:hidden;text-overflow:ellipsis}
      .apt-directional-toolpill button{position:relative;height:24px;padding:0 11px;border-radius:999px;border:0;background:transparent;color:#C9C9C9;font:inherit;font-weight:520;cursor:pointer}
      .apt-directional-toolpill button::after{content:"";position:absolute;left:0;right:0;top:50%;height:44px;transform:translateY(-50%)}
      .apt-directional-toolpill button:hover{color:#fff}
      .apt-directional-toolpill button[aria-pressed="true"]{background:rgba(255,255,255,.15);color:#fff}
      @media(pointer:coarse){.apt-directional-toolpill button{height:36px}}
    `}</style>
    <svg className="apt-directional-hits" aria-hidden="true" width="100%" height="100%"
      style={{ display: "block", overflow: "hidden", pointerEvents: "none" }}>
      {[1, 2, 3, 4, 5, 6].map(number => <path key={number} data-apt-radial-number={number} fill="transparent" />)}
      <circle data-apt-radial-hub="hit" r="34" fill="transparent" />
      <circle data-apt-radial-hub="dot" r="7" fill="transparent" pointerEvents="none" />
    </svg>
    <div ref={pillRef} hidden className="apt-directional-toolpill" role="group" aria-label="Room lighting tool" data-aim-ignore="true" data-apt-label-avoid=""
      onPointerDownCapture={event => event.stopPropagation()} onClick={event => event.stopPropagation()}
      style={{ fontFamily: CARD_FONT_SANS }}>
      <span className="nm">{descriptor.label || descriptor.fixture_id}</span>
      {pillButton("zones", "Zones", "Z")}{pillButton("spot", "Spot", "A")}
    </div>
    <div ref={readoutRef} hidden role="status" aria-live="off" data-aim-ignore="true"
      style={{ position: "absolute", left: 0, top: 0, padding: "6px 9px", borderRadius: 6,
        background: "rgba(8,9,11,.84)", border: "1px solid rgba(255,255,255,.16)", color: "#fff",
        fontFamily: CARD_FONT_MONO, fontSize: 11, whiteSpace: "nowrap" }} />
  </div>;
}

Object.assign(window, { AptDeviceLabel, AptPersonLabel, AptRoomChip, AptControlCard,
  aptRoomLightGroups, AptRoomLightGroups, AptNamedAimList, AptDirectionalRing, AptDirectionalComposer });
