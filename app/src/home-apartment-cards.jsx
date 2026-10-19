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

function AptRoomLightGroups({ groups = [], onSelect, onPower, disabledReason = "" }) {
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
          aria-current={light.selected ? "true" : undefined} onClick={() => onSelect(light.id)}>
          <span className={`apt-ui-lamp${light.power === "on" ? " is-on" : ""}`} aria-hidden="true" />
          <span className="nm">{light.label || light.id}</span><span className="vl">{light.power === "on" ? "On" : light.power === "off" ? "Off" : "Unavailable"}</span>
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
  const mix = Number.isFinite(kelvin) ? Math.max(0, Math.min(1, (kelvin - 2200) / 2800)) : .3;
  return `rgb(255, ${Math.round(193 + mix * 57)}, ${Math.round(120 + mix * 126)})`;
}

function AptDirectionalRing({ radials = [], zone = null, onZone, onShow }) {
  const frame = { project: ([x, y]) => ({ x: 100 + x * 72, y: 100 - y * 72, visible: true }) };
  const geometry = window.HomeApartmentDirectional?.radialSectorPaths?.({ radials, frame, innerRadius: .47, outerRadius: 1 });
  const selected = zone == null ? radials : radials.filter(item => item.number === zone);
  const level = selected.length ? selected.reduce((sum, item) => sum + (item.on ? item.brightness : 0), 0) / selected.length : 0;
  const activate = number => onZone?.(number === zone ? null : number);
  const keyActivate = (event, number) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault(); if (!event.repeat) activate(number);
    }
  };
  return <div className="apt-directional-ring" data-aim-ignore="true" style={{ width: "100%", fontFamily: CARD_FONT_SANS }}>
    {geometry ? <svg viewBox="0 0 200 200" role="group" aria-label="Radial zones"
      style={{ display: "block", width: 208, maxWidth: "100%", height: "auto", margin: "0 auto", overflow: "visible" }}>
      <path d={geometry.outer} fill="none" stroke="var(--hg-border)" strokeDasharray="2 5" pointerEvents="none" />
      <path d={geometry.inner} fill="var(--hg-bg-2)" stroke="var(--hg-border-soft)" pointerEvents="none" />
      {geometry.zones.map(item => {
        const state = radials.find(radial => radial.number === item.number);
        return <g key={item.number} role="button" tabIndex={0} className="hg-focusable"
          aria-label={`Radial zone ${item.number}`} aria-pressed={zone === item.number}
          onClick={() => activate(item.number)} onKeyDown={event => keyActivate(event, item.number)} style={{ cursor: "pointer" }}>
          <title>{`Zone ${item.number}: ${item.level > 0 ? `${Math.round(item.level * 100)}%` : "Off"}, ${state.color_temp_kelvin} kelvin`}</title>
          <path d={item.path} fill={aptDirectionalTone(state.color_temp_kelvin)} fillOpacity={.12 + item.level * .58}
            stroke={zone === item.number ? "var(--hg-fg-0)" : "var(--hg-border)"} strokeWidth={zone === item.number ? 2 : 1} />
          <circle cx={item.tick.x} cy={item.tick.y} r="22" fill="transparent" pointerEvents="all" />
          <circle cx={item.tick.x} cy={item.tick.y} r="14" fill="var(--hg-bg-1)"
            stroke={zone === item.number ? "var(--hg-fg-0)" : "var(--hg-border)"} strokeWidth={zone === item.number ? 2 : 1} pointerEvents="none" />
          <text x={item.tick.x} y={item.tick.y + 4} textAnchor="middle" fill="var(--hg-fg-0)"
            style={{ fontFamily: CARD_FONT_MONO, fontSize: 12 }} pointerEvents="none">{item.number}</text>
        </g>;
      })}
      <g role="button" tabIndex={0} className="hg-focusable" aria-label="All radial zones" aria-pressed={zone == null}
        onClick={() => onZone?.(null)} onKeyDown={event => {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); if (!event.repeat) onZone?.(null); }
        }} style={{ cursor: "pointer" }}>
        <circle cx="100" cy="100" r="31" fill="var(--hg-bg-1)" stroke={zone == null ? "var(--hg-fg-3)" : "var(--hg-border-soft)"} />
        <text x="100" y="100" textAnchor="middle" fill="var(--hg-fg-0)" style={{ fontFamily: CARD_FONT_SANS, fontSize: 20, fontWeight: 550 }} pointerEvents="none">
          {Math.round(level * 100)}%</text>
        <text x="100" y="116" textAnchor="middle" fill="var(--hg-fg-3)" style={{ fontFamily: CARD_FONT_SANS, fontSize: 10.5 }} pointerEvents="none">
          {zone == null ? "All zones" : `Zone ${zone}`}</text>
      </g>
    </svg> : <p className="apt-ui-meta">Facing unavailable</p>}
    <div style={{ display: "flex", alignItems: "center", gap: 10, justifyContent: "space-between", marginTop: 8 }}>
      <p className="apt-ui-meta" style={{ margin: 0, fontSize: 11, lineHeight: 1.5 }}>Modeled orientation · not commissioned</p>
      <button type="button" className="apt-ui-button is-small" disabled={!geometry || typeof onShow !== "function"}
        onClick={() => onShow?.()} style={{ minHeight: 44, flexShrink: 0 }}>Show in room</button>
    </div>
  </div>;
}

function AptDirectionalComposer({ projection, descriptor, geometry: projectedGeometry, mode = "zones", onMode, readout, ownedGesture = false }) {
  const gradientId = React.useId();
  const radials = descriptor?.state?.radials || [];
  const geometry = projectedGeometry || window.HomeApartmentDirectional?.radialSectorPaths?.({ radials, frame: projection });
  if (!geometry || geometry.hub?.visible !== true) return null;
  const hub = geometry.hub;
  const pillButton = (value, label) => <button type="button" className="hg-focusable" data-aim-ignore="true"
    data-apt-directional-mode={value} aria-keyshortcuts={value === "zones" ? "Z" : "A"} aria-pressed={mode === value}
    onPointerDownCapture={event => event.stopPropagation()} onClick={() => onMode?.(value)} style={{
      height: 44, minHeight: 44, boxSizing: "border-box", minWidth: 62, padding: "7px 12px", border: 0, borderRadius: 22,
      color: mode === value ? "var(--hg-bg-0)" : "var(--hg-fg-2)", background: mode === value ? "var(--hg-fg-0)" : "transparent",
      fontFamily: CARD_FONT_SANS, fontSize: 12, fontWeight: 550, cursor: "pointer",
    }}>{label}</button>;
  return <div data-apt-directional-composer="1" style={{ position: "absolute", inset: 0, pointerEvents: "none", overflow: "hidden", zIndex: 4 }}>
    <svg aria-hidden="true" width="100%" height="100%" style={{ display: "block", overflow: "hidden", pointerEvents: "none" }}>
      {mode === "zones" && <>
        <defs>{geometry.zones.map(item => {
          const state = radials.find(radial => radial.number === item.number), edge = item.node || item.tick;
          return <linearGradient key={item.number} id={`${gradientId}-zone-${item.number}`} gradientUnits="userSpaceOnUse"
            x1={hub.x} y1={hub.y} x2={edge.x} y2={edge.y}>
            <stop offset="0" stopColor={aptDirectionalTone(state.color_temp_kelvin)} stopOpacity=".6" />
            <stop offset=".55" stopColor={aptDirectionalTone(state.color_temp_kelvin)} stopOpacity=".3" />
            <stop offset="1" stopColor={aptDirectionalTone(state.color_temp_kelvin)} stopOpacity="0" />
          </linearGradient>;
        })}</defs>
        <path d={geometry.outer} fill="none" stroke="var(--hg-fg-3)" strokeOpacity=".5" strokeDasharray="2 7" />
        {geometry.zones.map(item => {
          const state = radials.find(radial => radial.number === item.number);
          const tone = aptDirectionalTone(state.color_temp_kelvin), dx = item.tick.x - hub.x, dy = item.tick.y - hub.y;
          const length = Math.hypot(dx, dy) || 1;
          return <g key={item.number}>
            <path d={item.path} fill={`url(#${gradientId}-zone-${item.number})`} fillOpacity={item.level > 0 ? .55 + item.level * .45 : .04}
              stroke={tone} strokeOpacity={item.level > 0 ? .72 : .2} strokeWidth="1.2"
              data-apt-radial-start={item.level > 0 ? `zone:${item.number}` : undefined}
              style={{ pointerEvents: item.level > 0 ? "fill" : "none", cursor: item.level > 0 ? "crosshair" : undefined }} />
            <line x1={item.tick.x - dx / length * 3} y1={item.tick.y - dy / length * 3}
              x2={item.tick.x + dx / length * 3} y2={item.tick.y + dy / length * 3} stroke="var(--hg-fg-2)" strokeOpacity=".7" />
            {item.node?.visible && <circle cx={item.node.x} cy={item.node.y} r="5"
              fill={ownedGesture && readout?.zone === item.number ? "#fff" : tone}
              stroke={ownedGesture && readout?.zone === item.number ? "rgba(20,20,20,.8)" : "var(--hg-bg-1)"} strokeWidth="1.5" />}
          </g>;
        })}
        <path d={geometry.inner} fill="var(--hg-bg-1)" fillOpacity=".3" stroke="var(--hg-fg-2)" strokeOpacity=".55" />
      </>}
      <circle cx={hub.x} cy={hub.y} r="22" fill="transparent" data-apt-radial-start={mode === "zones" ? "hub" : undefined}
        style={{ pointerEvents: mode === "zones" ? "all" : "none", cursor: mode === "zones" ? "crosshair" : undefined }} />
      <circle cx={hub.x} cy={hub.y} r="7" fill="var(--hg-fg-0)" stroke="var(--hg-bg-1)" strokeWidth="2" />
    </svg>
    <div style={{ position: "absolute", left: hub.x, top: hub.y - 65, transform: "translateX(-50%)", maxWidth: 240,
      padding: "6px 10px", background: "var(--hg-bg-1)", border: "1px solid var(--hg-border-soft)", borderRadius: 7,
      fontFamily: CARD_FONT_SANS, textAlign: "center", color: "var(--hg-fg-0)", boxShadow: "0 2px 12px rgba(0,0,0,.12)" }}>
      <div style={{ fontSize: 12, fontWeight: 550, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{descriptor.label || descriptor.fixture_id}</div>
      <div style={{ fontSize: 10.5, lineHeight: 1.5, marginTop: 2, color: "var(--hg-fg-3)", whiteSpace: "nowrap" }}>Modeled orientation · not commissioned</div>
    </div>
    {!ownedGesture && <div role="group" aria-label="Room lighting tool" data-aim-ignore="true" style={{ position: "absolute", left: hub.x, top: hub.y - 124,
      transform: "translateX(-50%)", display: "flex", gap: 2, padding: 3, borderRadius: 26, pointerEvents: "auto",
      background: "var(--hg-bg-1)", border: "1px solid var(--hg-border)", boxShadow: "0 3px 14px rgba(0,0,0,.15)" }}>
      {pillButton("zones", "Zones")}{pillButton("spot", "Spot")}
    </div>}
    {mode === "zones" && readout && <div role="status" aria-live="off" style={{ position: "absolute",
      left: Number.isFinite(readout.x) ? readout.x + 14 : hub.x + 26, top: Number.isFinite(readout.y) ? readout.y - 36 : hub.y - 18,
      padding: "6px 9px", borderRadius: 6, background: "var(--hg-bg-1)", border: "1px solid var(--hg-border)",
      color: "var(--hg-fg-0)", fontFamily: CARD_FONT_MONO, fontSize: 11, whiteSpace: "nowrap" }}>
      {readout.zone == null ? "All zones" : `Zone ${readout.zone}`} · {Number.isFinite(readout.level) ? `${Math.round(readout.level * 100)}%` : "Unavailable"}
    </div>}
  </div>;
}

Object.assign(window, { AptDeviceLabel, AptPersonLabel, AptRoomChip, AptControlCard,
  aptRoomLightGroups, AptRoomLightGroups, AptNamedAimList, AptDirectionalRing, AptDirectionalComposer });
