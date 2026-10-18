/* Apartment unified prototype — shell: top bar, status, scene toolbar, layers, help, setup. */
const { useState: useStateS, useRef: useRefS } = React;

function statusRows(ctx) {
  const { site, scen, sceneInfo } = ctx;
  const scene = sceneInfo && sceneInfo.kind === "scan" ? ["ok", "Scan", `points.sim.ply · ${Math.round(sceneInfo.points / 1000)}k points`] : ["authored", site.scene.label, site.scene.note];
  if (site.id === "echo") {
    const model = scen === "draft" ? ["unsaved", "Local draft", "2 changes saved in this browser only. Publish after reviewing against the live model."]
      : scen === "loading" ? ["loading", "Loading model", "Fetching the Apartment model from Home Assistant."]
        : ["ok", "Live Home Assistant model", "Revision 42 · saved · authored for this prototype"];
    const ha = scen === "draft" ? ["disconnected", "Home Assistant offline", "Last contact 6 min ago. Light controls are read-only until it reconnects."]
      : scen === "loading" ? ["loading", "Connecting to Home Assistant", "Echo Park Home Assistant through the Home gateway."]
        : ["ok", "Home Assistant connected", "Echo Park Home Assistant · 13 lights placed. Authored state; the prototype connects to nothing."];
    return [{ k: "Model", s: model }, { k: "Lights", s: ha }, { k: "Scene", s: scen === "failed" ? ["blocked", "Scan didn't load", "The asset service didn't answer. Showing nothing rather than another home."] : scene },
      { k: "Presence", s: ["authored", "Tracker · room-level", "Someone in the living room. No position, no body."] },
      { k: "Cameras", s: ["recorded", "3 cameras · recorded stills", "Snapshots and detection only."] },
      { k: "Engineered", s: ["none", "Bench not reachable", "Loopback-only on the desktop app; a browser makes no loopback request."] }];
  }
  const p = window.VIC_SCEN[scen] || window.VIC_SCEN.current;
  return [{ k: "Model", s: ["authored", "Victoria Apartment model", "Authored stand-in for this prototype."] },
    { k: "Lights", s: ["none", "No light entities for Den fixtures", "Victoria's Home Assistant has no entities mapped to these sockets yet."] },
    { k: "Scene", s: scene }, { k: "Fixtures", s: ["modeled", "4 modeled · 0 commissioned", "Virtual fixtures at mapped sockets. Nothing physical moves."] },
    { k: "Perception", s: [p.badge[0], `Den camera · ${p.badge[1].toLowerCase()}`, scen === "none" ? "No session. Start one in Perception Lab." : "Authored sample; exact-frame viewing."] }];
}

function TopBar({ ctx }) {
  const { site, act, homes, scenOf } = ctx; const [pop, setPop] = useStateS(false); const ref = useRefS(null); const rows = statusRows(ctx);
  const short = site.id === "echo"
    ? (ctx.scen === "draft" ? [["unsaved", "Local draft · unsaved"], ["disconnected", "Home Assistant offline"]] : ctx.scen === "loading" ? [["loading", "Loading"], ["loading", "Connecting"]] : [["ok", "Live model · rev 42"], ["ok", "Home Assistant connected"], ["authored", "Seed geometry"]])
    : [["modeled", "Modeled fixtures"], [rows[4].s[0], rows[4].s[1]], ["authored", "Authored Den"]];
  return <header className="apt-top">
    <div className="apt-brand"><b>Home</b><button type="button" className="apt-proto" onClick={act.about} title="What is real in this prototype"><Icon name="info" size={13} />Prototype</button></div>
    <div className="apt-homes" role="group" aria-label="Home">
      {homes.map((h) => { const dirty = ctx.dirtyOf(h.id); return <button key={h.id} type="button" className="apt-home" aria-pressed={site.id === h.id} onClick={() => act.setSite(h.id)} title={dirty ? "Unsaved work kept for this home" : h.place}>
        <span className={`dot ${dirty ? "attn" : scenOf(h.id) === "draft" ? "attn" : "ok"}`} />{h.name}</button>; })}
    </div>
    <button type="button" ref={ref} className="apt-statusbtn" aria-expanded={pop} aria-haspopup="dialog" onClick={() => setPop(!pop)}>
      {short.map(([s, l], i) => <span key={i} className={`seg ${i > 1 ? "opt" : ""}`}><Badge state={s} label={l} className="plain" /></span>)}
    </button>
    {pop && <Popover anchor={ref} onClose={() => setPop(false)} width={380}>
      <div className="grp">{site.name} · what is connected</div>
      {rows.map((r) => <div key={r.k} className="row"><span style={{ paddingTop: 2 }}><Badge state={r.s[0]} label="" className="plain" /></span><div><div className="t">{r.s[1]}</div><div className="d">{r.s[2]}</div></div><span className="apt-meta">{r.k}</span></div>)}
    </Popover>}
    <div className="apt-top-r">
      <Btn icon="setup" pressed={ctx.setup} onClick={act.toggleSetup} title="Placement, aims, devices and Home Assistant links">{ctx.narrow ? null : "Setup"}</Btn>
    </div>
  </header>;
}

function LayersPopover({ ctx, anchor, onClose }) {
  const { layers, act, site } = ctx; const L = layers; const set = (k, v) => act.setLayer(k, v); const body = site.capabilities.body; const active = ctx.scen !== "none";
  const cb = (k, label, d, dis, val) => <label className={`opt ${dis ? "is-dis" : ""}`}><input type="checkbox" disabled={dis} checked={dis ? !!val : !!L[k]} onChange={(e) => set(k, e.target.checked)} />{label}{d && <span className="d">{d}</span>}</label>;
  return <Popover anchor={anchor} onClose={onClose} width={330} align="right">
    <div className="grp">Light</div>
    {cb("pools", "Light pools", "where light lands")}
    <div style={{ padding: "4px 10px 8px" }}><Seg full value={L.beams} label="Beams" onChange={(v) => set("beams", v)} options={[{ value: "selected", label: "Selected beam" }, { value: "all", label: "All beams" }, { value: "off", label: "No beams" }]} /></div>
    {cb("targetsAlways", "Named aims always labeled", "else while aiming")}
    {cb("labelsAlways", "All fixture labels", site.fixtures.length > 6 ? "else on hover" : "")}
    {cb("cameras", "Camera positions", "")}
    <div className="grp">Body {body ? (active ? "· from analyzed frame" : "· no session") : "· not available here"}</div>
    {body ? <>{cb("bodyObserved", "Observed joints", "solid", !active)}{cb("bodyInferred", "Inferred completion", "dashed", !active)}{cb("bodyVolume", "Nominal volume", "not measured", !active)}{cb("bodyUncertainty", "Uncertainty", "", !active)}
      {cb("framePose", "2D pose on frame", "")}
      <label className="opt is-dis"><input type="checkbox" disabled />Follow<span className="d">needs continuous tracking · planned</span></label></>
      : <label className="opt is-dis"><input type="checkbox" disabled />Body layers<span className="d">need pose data</span></label>}
    <div className="grp">Shadows and occlusion · planned</div>
    <label className="opt is-dis"><input type="checkbox" disabled checked />Beam blocking by furniture<span className="d">boxes only</span></label>
    <label className="opt is-dis"><input type="checkbox" disabled />Furniture shadows<span className="d">planned</span></label>
    <label className="opt is-dis"><input type="checkbox" disabled />Body shadows<span className="d">planned</span></label>
  </Popover>;
}

function SceneToolbar({ ctx }) {
  const { act, mode, site, detent } = ctx; const [lp, setLp] = useStateS(false); const ref = useRefS(null);
  const scan = ctx.sceneInfo && ctx.sceneInfo.kind === "scan";
  return <div className="apt-toolbar" role="toolbar" aria-label="Room view">
    <button type="button" className="src hide-sm" onClick={act.about} title={site.scene.note}><Badge state={scan ? "ok" : "authored"} label="" className="plain" />{scan ? "Scan" : site.id === "victoria" ? "Authored Den" : "Seed geometry"}</button>
    <span className="sep hide-sm" />
    <Seg value={mode} label="Scene" onChange={act.setMode} options={[{ value: "cloud", label: "Cloud" }, { value: "photo", label: "Photo" }, { value: "mesh", label: "Mesh" }, { value: "lit", label: "Lit", title: "Simulated light with real-time shadows from furniture and the body. Materials are assumed; not photometric." }]} />
    <span className="sep" />
    <Btn sm icon="chevL" label="Orbit left" title="Orbit left (←)" onClick={() => act.orbit(-1)} className="orbit" />
    <span className="apt-meta hide-sm mono" style={{ minWidth: 34, textAlign: "center" }} aria-live="polite">{detent + 1}/8</span>
    <Btn sm icon="chevR" label="Orbit right" title="Orbit right (→)" onClick={() => act.orbit(1)} className="orbit" />
    <Btn sm icon="fit" label="Fit room" title="Fit room (H)" onClick={act.fit} className="hide-sm" />
    <span className="sep" />
    <button type="button" ref={ref} className="apt-btn sm" aria-label="Layers" title="Layers" aria-expanded={lp} onClick={() => setLp(!lp)}><Icon name="layers" size={14} /><span className="lbl">Layers</span></button>
    <Btn sm icon="help" label="Gestures and keys" title="Gestures and keys (?)" onClick={act.help} />
    {lp && <LayersPopover ctx={ctx} anchor={ref} onClose={() => setLp(false)} />}
  </div>;
}

function Legend({ ctx }) {
  const { hs, site } = ctx; const sel = hs.selected; const f = sel && sel.kind === "fixture" && site.fixtures.find((x) => x.id === sel.id);
  const items = [["#ffddb0", "solid", "Light on now"]];
  if (f && f.engineered) items.push(["#45dfff", "dashed", "Proposed aim"], ["#8a8a8a", "dashed", f.kind === "cylinder" ? "Off · last aim" : "Simulated / off"]);
  if (ctx.bodyVisible) items.push(["#B8D8FF", "dashed", "Inferred joints"]);
  return <div className="apt-legend" aria-hidden="true">{items.map(([c, s, l]) => <span key={l}><i style={{ borderTopColor: c, borderTopStyle: s }} />{l}</span>)}</div>;
}

function HelpModal({ onClose }) {
  const rows = [["Drag empty space", "Orbit the room"], ["Shift-drag or right-drag", "Pan"], ["Wheel or pinch", "Zoom"], ["Click a light or its label", "Select it and open its controls"],
    ["Drag the reticle", "Aim the selected light at a surface; the socket stays fixed"], ["Tap to aim (Aim tab)", "Touch alternative: tap a surface"], ["Esc", "Cancel the current step, then clear the selection. It never closes Apartment"],
    ["← → / ↑ ↓", "Orbit by one detent / tilt"], ["+ −", "Zoom"], ["H", "Fit the room"], ["N / P", "Next / previous light"], ["A / Z", "Spot aiming / radial zones for the selected light"], ["Enter", "Set the proposed aim"], ["⌘Z / ⇧⌘Z", "Undo / redo an aim proposal"], ["Arrow keys on the reticle", "Nudge the proposed point (Shift for larger steps)"]];
  return <Modal title="Gestures and keys" onClose={onClose}>
    <dl className="apt-kv" style={{ gridTemplateColumns: "minmax(140px,auto) 1fr" }}>{rows.map(([k, v]) => <React.Fragment key={k}><dt className="mono" style={{ fontSize: 12 }}>{k}</dt><dd style={{ textAlign: "left" }}>{v}</dd></React.Fragment>)}</dl>
    <h3 className="apt-h">Reading the room</h3>
    <dl className="apt-kv" style={{ gridTemplateColumns: "minmax(140px,auto) 1fr" }}>
      <dt>Warm pool</dt><dd style={{ textAlign: "left" }}>Light that is on now, in its colour temperature</dd><dt>Cyan ring</dt><dd style={{ textAlign: "left" }}>A proposed aim that hasn't been set</dd>
      <dt>Grey dashed</dt><dd style={{ textAlign: "left" }}>Off, simulated, or inferred</dd><dt>Red dashed</dt><dd style={{ textAlign: "left" }}>Can't aim there: out of reach or blocked</dd><dt>Orange</dt><dd style={{ textAlign: "left" }}>Tape measurements in Setup</dd></dl>
  </Modal>;
}

function AboutModal({ onClose, sceneInfo }) {
  return <Modal title="What's real in this prototype" onClose={onClose}>
    <p className="apt-p">This is a design prototype. It connects to nothing, sends no commands and starts no camera or model sessions.</p>
    <h3 className="apt-h">From the Home repository</h3>
    <ul className="apt-p" style={{ margin: 0, paddingLeft: 18 }}><li>Echo Park rooms, light positions, cameras and named targets (seed-model.json)</li><li>Echo Park camera stills (app/src/simulation/cameras)</li><li>Home tokens, Geist fonts, Apartment contracts and inspector conventions</li>{sceneInfo && sceneInfo.kind === "scan" && <li>Echo Park point cloud loaded from your checkout</li>}</ul>
    <h3 className="apt-h">Authored for the prototype</h3>
    <ul className="apt-p" style={{ margin: 0, paddingLeft: 18 }}><li>Furniture boxes, light states and the Engineered preview on Front left</li><li>Victoria's Den, socket positions, fixtures and named aims</li><li>The Den camera frame (rendered from the room model), pose, detector, JEV and its activity labels</li></ul>
    <h3 className="apt-h">Not readable in this session</h3>
    <p className="apt-p">The Victoria scan, socket map, perception recordings and the Oct 15–17 plans are in the local checkout. Swap them in using the handoff notes.</p>
  </Modal>;
}

const SETUP_TOOLS = [["sockets", "Devices and sockets", "setup"], ["names", "Names", "pencil"], ["targets", "Named aims", "target"], ["zones", "Zones", "fit"], ["position", "Fixture position", "ruler"], ["links", "Home Assistant", "lock"], ["cameras", "Cameras and sensors", "camera"]];
function SetupRail({ ctx }) {
  return <nav className="apt-rail" aria-label="Setup tools"><div className="grp">Setup</div>
    {SETUP_TOOLS.map(([k, l, ic]) => <button key={k} type="button" aria-pressed={ctx.setupTool === k} onClick={() => ctx.act.setSetupTool(k)}><Icon name={ic} size={15} />{l}</button>)}</nav>;
}

const TARGET_TYPES = [{ value: "table", label: "Table" }, { value: "island", label: "Island" }, { value: "art", label: "Art" }, { value: "custom", label: "Custom" }];
function TargetMapper({ ctx }) {
  const { site, hs, act, tdraft: d } = ctx;
  if (!d) {
    const used = (id) => site.fixtures.filter((f) => hs.fixtures[f.id] && hs.fixtures[f.id].aim && hs.fixtures[f.id].aim.targetId === id).map((f) => f.short);
    return <div className="apt-sec">
      <div className="apt-row"><span className="apt-p grow">Surfaces lights can be pointed at by name.</span><Btn sm kind="primary" icon="plus" onClick={act.newTarget}>New</Btn></div>
      <ul className="apt-list">{hs.targets.map((t) => { const u = used(t.id); return <li key={t.id}><button type="button" className="apt-li" onClick={() => act.editTarget(t.id)}>
        <Icon name="target" size={14} style={{ color: "var(--hg-fg-3)" }} /><span className="nm">{t.name}</span><span className="vl">{((TARGET_TYPES.find((x) => x.value === t.category) || {}).label || t.category) + (Math.abs(t.normal[2]) > 0.8 ? " · flat" : " · wall") + (u.length ? " · " + u.join(", ") : "")}</span></button></li>; })}</ul>
    </div>;
  }
  if (d.mode === "placing") return <div className="apt-sec">
    <div className="apt-row"><h3 className="apt-h grow">{d.pos ? `Move “${d.name || "new aim"}”` : "New named aim"}</h3><Btn sm kind="ghost" onClick={d.pos ? () => act.updateDraft({ mode: "editing" }) : act.cancelDraft}>Cancel</Btn></div>
    <p className="apt-p" style={{ color: "var(--hg-fg-0)" }}>Click the surface you want to light: a desk top, a wall, the painting or the floor.</p>
    <p className="apt-meta">Furniture tops and art fit their own size. Dragging still orbits; Esc cancels.</p>
  </div>;
  const nm = (d.name || "").trim(); const dup = !!nm && hs.targets.some((t) => t.id !== d.id && t.name.trim().toLowerCase() === nm.toLowerCase());
  const bad = d.category === "art" && d.horizontal ? "Art goes on a wall. Pick a vertical surface or change the type." : (d.category === "table" || d.category === "island") && !d.horizontal ? "Tables and islands are flat. Pick a top surface or change the type." : null;
  const aimers = site.fixtures.filter((f) => f.engineered); const reach = aimers.map((f) => ({ f, r: ctx.checkAim(f.id, d.pos) }));
  const ok = reach.filter((x) => x.r && x.r.valid), no = reach.filter((x) => x.r && !x.r.valid);
  const nudge = (dx, dy) => { const st = 0.05, p = d.pos.slice(); if (d.horizontal) { p[0] += dx * st; p[1] += dy * st; } else { const t = [-d.normal[1], d.normal[0]], L = Math.hypot(t[0], t[1]) || 1; p[0] += (t[0] / L) * dx * st; p[1] += (t[1] / L) * dx * st; p[2] += dy * st; } act.updateDraft({ pos: p.map((v) => Math.round(v * 1000) / 1000) }); };
  const sz = (i, v) => { const s = d.size.slice(); s[i] = Math.max(0.05, Math.min(6, +v || 0)); act.updateDraft({ size: s }); };
  return <>
    <div className="apt-sec">
      <h3 className="apt-h">{d.id ? "Edit named aim" : "New named aim"}</h3>
      <label className="apt-field"><span>Name</span><input className="apt-input" autoFocus={!d.id} value={d.name || ""} placeholder="Desk reading" onChange={(e) => act.updateDraft({ name: e.target.value })} /></label>
      {dup && <div className="apt-flag blocked"><span className="grow">Another named aim is already called “{nm}”.</span></div>}
      <div className="apt-field"><span>Surface</span><div className="apt-row"><span className="grow" style={{ fontSize: 13, minWidth: 0 }}>{d.surface}{d.face && d.face !== "wall" ? ` · ${d.face}` : ""}<span className="apt-meta"> · {d.horizontal ? "flat" : "vertical"}</span></span><Btn sm onClick={act.pickAgain}>Pick again</Btn></div></div>
      <div className="apt-field"><span>Type</span><Seg full value={d.category} label="Type" onChange={(v) => act.updateDraft({ category: v })} options={TARGET_TYPES} /></div>
      {bad && <div className="apt-flag"><span className="grow">{bad}</span></div>}
      <div className="apt-field"><span>Shape</span><Seg full value={d.shape} label="Shape" onChange={(v) => act.updateDraft({ shape: v })} options={[{ value: "surface", label: "Area" }, { value: "point", label: "Point" }]} /></div>
      <p className="apt-meta">{d.shape === "surface" ? `In the room, drag a corner to resize, the center to move${d.horizontal ? " and the arrow to raise or lower" : ""}.` : `In the room, drag the point to move it${d.horizontal ? " and the arrow to raise or lower" : ""}.`}</p>
      {d.shape === "surface" && <div className="apt-field"><span>Size · {d.horizontal ? "width × depth" : "width × height"}</span><div className="apt-row">
        <input className="apt-input mono" style={{ width: 72 }} type="number" step="0.05" min="0.05" value={d.size[0]} aria-label="Width" onChange={(e) => sz(0, e.target.value)} /><span className="apt-meta">×</span>
        <input className="apt-input mono" style={{ width: 72 }} type="number" step="0.05" min="0.05" value={d.size[1]} aria-label={d.horizontal ? "Depth" : "Height"} onChange={(e) => sz(1, e.target.value)} /><span className="apt-meta grow">m</span>
        {d.fit && <Btn sm onClick={() => act.updateDraft({ pos: d.fit.center.slice(), size: d.fit.size.map((v) => Math.round(v * 100) / 100), rot: 0 })}>Fit to {d.fitName}</Btn>}</div></div>}
      <Slider label={d.horizontal ? "Height" : "Center height"} min={0} max={Math.max(2.4, ...site.rooms.map((x) => x.h || 0))} step={0.01} value={d.pos[2]} fmt={(v) => `${(+v).toFixed(2)} m`} onChange={(v) => act.updateDraft({ pos: [d.pos[0], d.pos[1], +v] })} />
      <div className="apt-field"><span>Position · 5 cm steps</span><div className="apt-row">
        <span className="apt-meta mono grow">{d.pos.map((v) => v.toFixed(2)).join(", ")} m</span>
        <div className="apt-nudge" role="group" aria-label="Nudge 5 cm"><Btn sm icon="chevL" label="Nudge left" onClick={() => nudge(-1, 0)} /><Btn sm icon="chevR" label="Nudge right" onClick={() => nudge(1, 0)} /><Btn sm icon="chevU" label={d.horizontal ? "Nudge away" : "Nudge up"} onClick={() => nudge(0, 1)} /><Btn sm icon="chevD" label={d.horizontal ? "Nudge toward" : "Nudge down"} onClick={() => nudge(0, -1)} /></div>
        {d.horizontal && d.shape === "surface" && <Btn sm label={`Rotate, now ${d.rot || 0}°`} title="Rotate 15°" onClick={() => act.updateDraft({ rot: ((d.rot || 0) + 15) % 180 })}>{`${d.rot || 0}°`}</Btn>}</div></div>
    </div>
    <div className="apt-sec">
      <h3 className="apt-h">Reach</h3>
      <p className="apt-p">{ok.length ? `${ok.map((x) => x.f.short).join(", ")} can reach it.` : "No light can reach it."}{no.length ? " " + no.map((x) => `${x.f.short} ${x.r.reason === "blocked" ? "is blocked" : "is out of reach"}`).join(", ") + "." : ""}</p>
      <div className="apt-chips">{aimers.map((f) => <button key={f.id} type="button" className={`apt-chip ${d.test === f.id ? "is-proposed" : ""}`} aria-pressed={d.test === f.id} onClick={() => act.testDraft(f.id)}>Preview from {f.short}</button>)}</div>
    </div>
    <div className="apt-sec"><div className="apt-actions">
      <Btn kind="primary" disabled={!nm || dup || !!bad} onClick={act.saveDraft}>{d.id ? "Save changes" : "Save named aim"}</Btn>
      <Btn kind="ghost" onClick={act.cancelDraft}>Cancel</Btn>
      {d.id && <Btn kind="ghost" style={{ marginLeft: "auto", color: "var(--apt-blocked)" }} onClick={() => act.deleteTarget(d.id)}>Delete</Btn>}</div>
      <p className="apt-meta">Saved to the {site.name} Apartment model. Changes geometry only; nothing is sent to a light.</p>
    </div>
  </>;
}

const DEV_STATUS = { approximate: ["attn", "Approximate pose"], placed: ["attn", "Placed by hand · not calibrated"], authored: ["authored", "Authored pose"], calibrated: ["ok", "Calibrated"], planned: ["none", "Ordered · not connected"] };
function DevicePlacer({ ctx }) {
  const { hs, act, ddraft: d } = ctx; const deg = (r) => Math.round((r * 180) / Math.PI);
  if (!d) return <>
    <div className="apt-sec">
      <div className="apt-actions"><Btn sm kind="primary" icon="plus" onClick={() => act.newDevice("camera")}>Camera</Btn><Btn sm icon="plus" onClick={() => act.newDevice("mmwave")}>mmWave sensor</Btn></div>
      <ul className="apt-list">{hs.cams.map((c) => { const st = DEV_STATUS[c.status] || DEV_STATUS.placed; return <li key={c.id}><button type="button" className="apt-li" onClick={() => act.editDevice(c.id)}>
        <Icon name={c.kind === "camera" ? "camera" : "spark"} size={14} style={{ color: "var(--hg-fg-3)" }} /><span className="nm">{c.name}</span><Badge state={st[0]} label={st[1]} className="plain" /></button></li>; })}</ul>
    </div>
    <div className="apt-sec"><p className="apt-p">Placing sets where a device sits and what it covers. Camera calibration, which matches pixels to the mesh, refines a camera's pose on the desktop app.</p></div>
  </>;
  const cam = d.kind === "camera", what = cam ? "camera" : "sensor";
  if (d.mode === "placing" || d.mode === "looking") return <div className="apt-sec">
    <div className="apt-row"><h3 className="apt-h grow">{d.mode === "looking" ? `Point ${d.name}` : d.pos ? `Move ${d.name}` : cam ? "New camera" : "New mmWave sensor"}</h3><Btn sm kind="ghost" onClick={d.pos ? () => act.updateDdraft({ mode: "editing" }) : act.cancelDdraft}>Cancel</Btn></div>
    {d.mode === "placing" && <div className="apt-field"><span>Mounted on</span><Seg full value={d.mount} label="Mount" onChange={(v) => act.updateDdraft({ mount: v })} options={[{ value: "wall", label: "Wall" }, { value: "ceiling", label: "Ceiling" }, { value: "shelf", label: "Shelf" }]} /></div>}
    <p className="apt-p" style={{ color: "var(--hg-fg-0)" }}>{d.mode === "looking" ? `Click the spot the ${what} should face.` : d.mount === "ceiling" ? `Click the floor directly below the ${what}.` : d.mount === "wall" ? `Click the wall where the ${what} is mounted, at its height.` : "Click the shelf or furniture top it sits on."}</p>
    <p className="apt-meta">Dragging still orbits; Esc cancels.</p>
  </div>;
  const cover = ctx.coverage(d); const st = DEV_STATUS[cam ? "placed" : "planned"];
  return <>
    <div className="apt-sec">
      <div className="apt-row"><h3 className="apt-h grow">{d.id ? (cam ? "Edit camera" : "Edit sensor") : cam ? "New camera" : "New mmWave sensor"}</h3><Badge state={st[0]} label={st[1]} /></div>
      <label className="apt-field"><span>Name</span><input className="apt-input" value={d.name || ""} onChange={(e) => act.updateDdraft({ name: e.target.value })} /></label>
      <div className="apt-field"><span>Mounted on</span><div className="apt-row"><span className="grow" style={{ fontSize: 13, minWidth: 0 }}>{d.surface || "—"}<span className="apt-meta mono"> · {d.pos.map((v) => v.toFixed(2)).join(", ")} m</span></span><Btn sm onClick={() => act.updateDdraft({ mode: "placing" })}>Move</Btn></div></div>
      <Slider label="Height" min={0.2} max={d.roomH || 2.6} step={0.05} value={d.pos[2]} fmt={(v) => `${(+v).toFixed(2)} m`} onChange={(v) => act.updateDdraft({ pos: [d.pos[0], d.pos[1], v] })} />
      <div className="apt-row"><span className="apt-meta grow">Facing</span><Btn sm icon="target" onClick={() => act.updateDdraft({ mode: "looking" })}>Point at a spot</Btn></div>
      <Slider label="Pan" min={-180} max={180} value={deg(d.yaw)} fmt={(v) => `${v}°`} onChange={(v) => act.updateDdraft({ yaw: (v * Math.PI) / 180 })} />
      <Slider label="Tilt" min={-90} max={30} value={deg(d.pitch)} fmt={(v) => `${v}°`} onChange={(v) => act.updateDdraft({ pitch: (v * Math.PI) / 180 })} />
      <Slider label={cam ? "Lens" : "Field"} min={30} max={150} value={d.hfov} fmt={(v) => `${v}°`} onChange={(v) => act.updateDdraft({ hfov: v, vfov: cam ? Math.round((2 * Math.atan(Math.tan((v * Math.PI) / 360) * 9 / 16) * 180) / Math.PI) : d.vfov })} />
      {!cam && <Slider label="Range" min={1} max={12} step={0.5} value={d.range} fmt={(v) => `${v} m`} onChange={(v) => act.updateDdraft({ range: v })} />}
      {!cam && <p className="apt-meta">Set field and range from the sensor's spec sheet. You can place it before it arrives; it connects once it's paired with Home Assistant.</p>}
    </div>
    <div className="apt-sec"><h3 className="apt-h">Covers</h3><p className="apt-p">{cover.length ? cover.join(", ") : "No furniture in view yet. Point it toward the room."}</p>
      <p className="apt-meta">From field of view and range. Line of sight through walls and furniture isn't checked yet.</p></div>
    <div className="apt-sec"><div className="apt-actions"><Btn kind="primary" onClick={act.saveDevice}>{d.id ? "Save changes" : cam ? "Save camera" : "Save sensor"}</Btn><Btn kind="ghost" onClick={act.cancelDdraft}>Cancel</Btn>
      {d.id && <Btn kind="ghost" style={{ marginLeft: "auto", color: "var(--apt-blocked)" }} onClick={() => act.deleteDevice(d.id)}>Remove</Btn>}</div>
      {cam && d.id && <p className="apt-meta">After moving a camera, recalibrate it on the desktop app.</p>}</div>
  </>;
}

function HaLinks({ ctx }) {
  const { site, hs, act } = ctx; const links = hs.links || {};
  const ents = [...new Set(site.fixtures.map((x) => x.entity).filter(Boolean))];
  const ent = (f) => (links[f.id] !== undefined ? links[f.id] : f.entity || null);
  const roomOf = (f) => (site.rooms.find((r) => r.id === f.room) || {}).name;
  const none = !ents.length, off = site.id === "echo" && ["draft", "loading", "failed"].includes(ctx.scen);
  return <>
    <div className="apt-sec">
      <p className="apt-p">Match each light in the model to the Home Assistant light that controls it. That's how a slider here reaches the real bulb.</p>
      {none && <p className="apt-meta">{site.name}'s sockets don't have Home Assistant lights yet. Each is matched when its fixture is commissioned; until then their controls only change the model.</p>}
      {off && <p className="apt-meta">Home Assistant is offline. You can change matches; Flash needs a connection.</p>}
    </div>
    <div className="apt-sec">
      <ul className="apt-list">{site.fixtures.map((f) => { const e = ent(f), others = e ? site.fixtures.filter((x) => x.id !== f.id && ent(x) === e) : [];
        return <li key={f.id} className="apt-link">
          <div className="apt-row"><span className="grow" style={{ minWidth: 0 }}><span className="apt-ch-name">{f.name}</span>{roomOf(f) && <span className="apt-meta"> · {roomOf(f)}</span>}</span>
            <Badge state={e ? (others.length ? "attn" : "ok") : "none"} label={e ? (others.length ? "Shared" : "Linked") : "Not linked"} /></div>
          {!none && <div className="apt-row">
            <select className="apt-input mono grow" style={{ minWidth: 0 }} value={e || ""} aria-label={`Home Assistant light for ${f.name}`} onChange={(ev) => act.setLink(f.id, ev.target.value || null)}>
              <option value="">Not linked</option>{ents.map((x) => <option key={x} value={x}>{x}</option>)}</select>
            <Btn sm disabled={!e || off} title="Blink the real light so you can check it's the right one" onClick={() => act.flashLight(f.id, e)}>Flash</Btn></div>}
          {others.length > 0 && <p className="apt-meta">Also controls {others.map((x) => x.name).join(", ")}.</p>}
        </li>; })}</ul>
    </div>
  </>;
}

function NamesTool({ ctx }) {
  const { site, act } = ctx; const groups = site.rooms.map((r) => [r, site.fixtures.filter((f) => f.room === r.id)]).filter(([, l]) => l.length);
  const rest = site.fixtures.filter((f) => !site.rooms.some((r) => r.id === f.room)); if (rest.length) groups.push([{ id: "_other", name: "Other" }, rest]);
  const dup = (f) => !!f.name.trim() && site.fixtures.some((x) => x.id !== f.id && x.name.trim().toLowerCase() === f.name.trim().toLowerCase());
  const field = (f, key, extra) => <input className={`apt-input${key === "short" ? " mono" : ""}`} value={f[key]} maxLength={key === "short" ? 10 : 48} aria-label={`${key === "short" ? "Room label" : "Name"} for ${f.orig.name}`}
    onFocus={() => act.hoverFixture(f.id)} onChange={(e) => act.renameFixture(f.id, { [key]: e.target.value })}
    onBlur={(e) => { act.hoverFixture(null); if (!e.target.value.trim()) act.renameFixture(f.id, { [key]: f.orig[key] }); }} {...extra} />;
  return <>
    <div className="apt-sec"><p className="apt-p">The name shows in lists, the inspector and messages. The label is the short tag over each light in the room.</p>
      <p className="apt-meta">Renames in Apartment only; Home Assistant keeps its own names. Saved in this browser.</p></div>
    {groups.map(([r, list]) => <div key={r.id} className="apt-sec">
      {groups.length > 1 && <h3 className="apt-h">{r.name}</h3>}
      <div className="apt-names-row head" aria-hidden="true"><span>Name</span><span>Label</span><span /></div>
      {list.map((f) => { const changed = f.name !== f.orig.name || f.short !== f.orig.short, d = dup(f);
        return <div key={f.id} className="apt-names-item" onMouseEnter={() => act.hoverFixture(f.id)} onMouseLeave={() => act.hoverFixture(null)}>
          <div className="apt-names-row">{field(f, "name")}{field(f, "short")}<Btn sm kind="ghost" icon="target" label={`Find ${f.name} in the room`} title="Find in the room" onClick={() => act.focusFixture(f.id)} /></div>
          {(changed || d) && <div className="apt-row">
            <span className="apt-meta grow" style={d ? { color: "var(--apt-attn)" } : undefined}>{d ? "Another light has this name." : `Was “${f.orig.name}” · ${f.orig.short}`}</span>
            {changed && <Btn sm kind="ghost" onClick={() => act.renameFixture(f.id, { name: f.orig.name, short: f.orig.short })}>Reset</Btn>}</div>}
        </div>; })}
    </div>)}
  </>;
}

function SetupInspector({ ctx }) {
  const { site, act, setupTool, hs } = ctx; const [tape, setTape] = useStateS({ a: "", b: "", h: "", d: "", v: "" }); const sel = hs.selected && hs.selected.kind === "fixture" ? site.fixtures.find((f) => f.id === hs.selected.id) : null;
  const f = sel || site.fixtures[0]; const n = Object.values(tape).filter(Boolean).length; const status = n === 0 ? "proposed" : tape.a && tape.b && tape.h && tape.d ? (tape.v ? "verified" : "calibrated") : "measured";
  const title = (SETUP_TOOLS.find((t) => t[0] === setupTool) || [])[1];
  return <>
    <div className="apt-ihead"><div className="apt-ihead-row"><div className="grow"><h2 className="apt-title">{title}</h2><div className="apt-sub">Setup · {site.name} · {ctx.dirtyOf(site.id) ? "unsaved changes" : "saved"}</div></div><Btn sm kind="primary" onClick={act.toggleSetup}>Done</Btn></div>
      <div className="apt-actions">{ctx.dirtyOf(site.id) ? <Badge state="unsaved" label="Unsaved" /> : <Badge state="saved" label="Saved" />}<Badge state={site.id === "victoria" ? "authored" : "ok"} label={site.model.source} /></div></div>
    {setupTool === "position" && <div className="apt-sec">
      <div className="apt-row"><span className="grow" style={{ fontSize: 14, fontWeight: 540 }}>{f.name}</span><Badge state={status === "proposed" ? "proposed" : status === "measured" ? "attn" : "ok"} label={status[0].toUpperCase() + status.slice(1)} /></div>
      <p className="apt-p">Measure from two perpendicular walls, then the ceiling height and the drop to the fixture bottom. Accepts 7' 4", 2.24 m, 224 cm.</p>
      {[["a", "From west wall"], ["b", "From south wall"], ["h", "Floor to ceiling"], ["d", "Ceiling to fixture bottom"], ["v", "Check: floor to bottom (optional)"]].map(([k, l]) =>
        <label key={k} className="apt-row" style={{ gap: 12 }}><span className="apt-meta grow">{l}</span><input className="apt-input mono apt-survey" style={{ width: 120 }} value={tape[k]} placeholder="—" onChange={(e) => { setTape({ ...tape, [k]: e.target.value }); act.markDirty(); }} /></label>)}
      <p className="apt-meta">Only entered tapes are drawn, in orange. Seed positions are never shown as measurements. Changes geometry only; sends no light or gimbal command.</p>
    </div>}
    {setupTool === "links" && <HaLinks ctx={ctx} />}
    {setupTool === "names" && <NamesTool ctx={ctx} />}
    {setupTool === "sockets" && <div className="apt-sec"><p className="apt-p">{site.id === "victoria" ? "Four mapped sockets in the Den. Fixtures are placed at the socket; moving a socket here changes the model only." : "Ceiling fixtures and plugs placed from the seed model. Drag to move in the room; exact values below."}</p>
      <ul className="apt-list">{site.fixtures.map((x) => <li key={x.id}><button type="button" className="apt-li" onClick={() => act.select({ kind: "fixture", id: x.id })} aria-current={sel && sel.id === x.id}><span className="nm">{x.name}</span><span className="vl">{x.pos.map((v) => v.toFixed(2)).join(", ")}</span></button></li>)}</ul></div>}
    {setupTool === "targets" && <TargetMapper ctx={ctx} />}
    {setupTool === "zones" && <div className="apt-sec"><ul className="apt-list">{site.rooms.map((r) => <li key={r.id}><div className="apt-li"><span className="nm">{r.name}</span><span className="vl">{r.poly.length} corners · {r.h} m</span></div></li>)}</ul><p className="apt-meta">Drag a corner to reshape, or inside the shade to move. Membership of lights and aims updates before saving.</p></div>}
    {setupTool === "cameras" && <DevicePlacer ctx={ctx} />}
  </>;
}

Object.assign(window, { TopBar, SceneToolbar, Legend, HelpModal, AboutModal, SetupRail, SetupInspector, statusRows });
