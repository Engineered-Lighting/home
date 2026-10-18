/* Apartment unified prototype — inspector (Room / Light / Target) and command inspector. */
const { useState: useStateI, useMemo: useMemoI } = React;

const solveAim = (f, point) => {
  if (!f || !point) return null; const d = [point[0] - f.pos[0], point[1] - f.pos[1], point[2] - f.pos[2]]; const dist = Math.hypot(...d);
  return { dist, pan: Math.atan2(d[1], d[0]) * 180 / Math.PI, tilt: Math.acos(Math.max(-1, Math.min(1, -d[2] / dist))) * 180 / Math.PI };
};
const r2 = (v) => Math.round(v * 100) / 100;
const lightSummary = (f, st) => {
  if (!st) return "";
  if (f.kind === "cylinder") {
    const c = st.channels || {}; const parts = [];
    if (c.spot && c.spot.on) parts.push(`Spot ${c.spot.bri}%`); if (c.down && c.down.on) parts.push(`Down ${c.down.bri}%`);
    if (c.radial && c.radial.on) parts.push(`Radial ${Math.round(c.radial.levels.reduce((a, b) => a + b, 0) / 6)}%`);
    return parts.length ? parts.join(" · ") : "Off";
  }
  if (f.kind === "plug") return st.on ? "On" : "Off";
  return st.on ? `${st.bri}% · ${st.k} K` : "Off";
};
const isLit = (f, st) => !!(st && (st.on || (st.channels && Object.values(st.channels).some((c) => c && c.on && (c.bri > 0 || (c.levels && c.levels.some((v) => v > 0)))))));

function ProvenanceBadges({ ctx, f }) {
  const { site, scen } = ctx;
  if (f.kind === "cylinder") return <div className="apt-actions"><Badge state="modeled" label="Modeled fixture" /><Badge state="none" label="Not commissioned" title="No physical command path or reported state exists for this socket yet" /></div>;
  const ha = scen === "loading" ? <Badge state="loading" spin label="Connecting to Home Assistant" /> : scen === "draft" ? <Badge state="disconnected" label="Home Assistant offline" /> : <Badge state="connected" label="Home Assistant connected" title="Authored scenario. The prototype is not connected to anything." />;
  return <div className="apt-actions">{ha}{f.engineered && <Badge state="simulated" label="Engineered · simulated preview" />}</div>;
}

const SCENES = {
  victoria: [
    { id: "bright", name: "Bright", all: { spot: { on: true, bri: 80, k: 4000 }, down: { on: true, bri: 70, k: 4000 }, radial: { on: true, level: 70, k: 4000 } } },
    { id: "evening", name: "Evening", all: { spot: { on: true, bri: 35, k: 2700 }, down: { on: true, bri: 20, k: 2700 }, radial: { on: true, level: 30, k: 2400 } } },
    { id: "reading", name: "Reading", all: { spot: { on: false }, down: { on: false }, radial: { on: true, level: 15, k: 2400 } }, per: { "vic-s4": { spot: { on: true, bri: 70, k: 3500 } }, "vic-s3": { spot: { on: true, bri: 60, k: 3000 } } } },
    { id: "glow", name: "Glow", all: { spot: { on: false }, down: { on: false }, radial: { on: true, level: 20, k: 2200 } } },
    { id: "goodnight", name: "Goodnight", auto: true, note: "fades every light to off over 10 minutes", all: { spot: { on: false }, down: { on: false }, radial: { on: false } } },
  ],
  echo: [
    { id: "bright", name: "Bright", lights: { on: true, bri: 100, k: 4000 } },
    { id: "evening", name: "Evening", lights: { on: true, bri: 40, k: 2700 }, rooms: { kitchen: { on: true, bri: 30, k: 2700 } } },
    { id: "dinner", name: "Dinner", lights: { on: true, bri: 20, k: 2400 }, rooms: { dining_room: { on: true, bri: 70, k: 2700 } } },
    { id: "goodnight", name: "Goodnight", auto: true, note: "fades every light to off over 10 minutes", lights: { on: false } },
  ],
};
function QuickControls({ ctx }) {
  const { site, hs, act, scen } = ctx; const off = site.id === "echo" && (scen === "draft" || scen === "loading" || scen === "failed");
  return <div className="apt-sec">
    <div className="apt-row"><h3 className="apt-h grow">Quick</h3><Btn sm disabled={off} onClick={() => act.setAll(true)}>All on</Btn><Btn sm disabled={off} onClick={() => act.setAll(false)}>All off</Btn></div>
    <div className="apt-chips">{(SCENES[site.id] || []).map((sc) => <button key={sc.id} type="button" className="apt-chip" disabled={off} aria-pressed={hs.scene === sc.id} title={sc.note || `Apply ${sc.name}`} onClick={() => act.applyScene(sc)}>{sc.auto && <Icon name="play" size={11} />}{sc.name}</button>)}</div>
    <p className="apt-meta">{off ? "Home Assistant is offline. Quick controls return when it reconnects." : "Example scenes and one automation (▶). In Home these list the home's scenes and scripts."}</p>
  </div>;
}

function RoomInspector({ ctx }) {
  const { site, hs, act } = ctx; const fx = site.fixtures; const lit = fx.filter((f) => isLit(f, hs.fixtures[f.id])).length;
  const groups = site.rooms.map((r) => ({ r, items: fx.filter((f) => f.room === r.id) })).filter((g) => g.items.length);
  const targets = hs.targets;
  return <>
    <div className="apt-ihead"><div className="apt-ihead-row"><div className="grow" style={{ minWidth: 0 }}>
      <h2 className="apt-title">{site.rooms.length === 1 ? site.rooms[0].name : site.name}</h2>
      <div className="apt-sub">{fx.length} {site.id === "victoria" ? "fixtures at mapped sockets" : "lights"} · {lit} lit · {site.id === "victoria" ? "modeled, not commissioned" : "Home Assistant entities"}</div>
    </div></div></div>
    <QuickControls ctx={ctx} />
    <div className="apt-sec">
      <h3 className="apt-h">Lights</h3>
      {groups.map(({ r, items }) => <div key={r.id} style={{ display: "grid", gap: 2 }}>
        {groups.length > 1 && <div className="apt-row" style={{ padding: "6px 8px 2px" }}><span className="apt-meta grow">{r.name}</span><Switch checked={items.some((f) => isLit(f, hs.fixtures[f.id]))} label={`${r.name} lights`} onChange={(v) => act.setAll(v, r.id)} /></div>}
        <ul className="apt-list">{items.map((f) => { const st = hs.fixtures[f.id]; return <li key={f.id}>
          <button type="button" className="apt-li" onClick={() => act.select({ kind: "fixture", id: f.id })} onMouseEnter={() => act.hover({ kind: "fixture", id: f.id })} onMouseLeave={() => act.hover(null)}>
            <span className={`apt-lamp ${isLit(f, st) ? "on" : ""}`} /><span className="nm">{f.name}</span>{f.engineered && f.kind !== "cylinder" && <Badge state="simulated" label="Engineered" className="plain" />}<span className="vl">{lightSummary(f, st)}</span>
          </button></li>; })}</ul></div>)}
    </div>
    {targets.length > 0 && <div className="apt-sec">
      <div className="apt-row"><h3 className="apt-h grow">Named aims</h3><Btn sm kind="ghost" icon="plus" onClick={act.newTarget}>New</Btn></div>
      <ul className="apt-list">{targets.map((t) => { const users = fx.filter((f) => hs.fixtures[f.id] && hs.fixtures[f.id].aim && hs.fixtures[f.id].aim.targetId === t.id);
        return <li key={t.id}><button type="button" className="apt-li" onClick={() => act.select({ kind: "target", id: t.id })} onMouseEnter={() => act.hover({ kind: "target", id: t.id })} onMouseLeave={() => act.hover(null)}>
          <Icon name="target" size={14} style={{ color: "var(--hg-fg-3)" }} /><span className="nm">{t.name}</span><TargetState t={t} compact /><span className="vl">{users.map((u) => u.short).join(", ") || "—"}</span></button></li>; })}</ul>
    </div>}
    <div className="apt-sec"><p className="apt-p">{site.id === "victoria"
      ? "These four fixtures are modeled at the mapped sockets. Changes preview in the room and send nothing."
      : "Lights are Home Assistant entities placed from the seed model. Front left also carries an Engineered profile shown as a simulated preview."}</p></div>
  </>;
}

function TargetState({ t, compact }) {
  const m = { saved: ["saved", "Saved"], saving: ["saving", "Saving"], unsaved: ["unsaved", "Not saved"], conflict: ["conflict", "Conflict"], changed: ["changed", "Check point"], local: ["unsaved", "Saved locally"] }[t.state || "saved"];
  if (compact && (t.state || "saved") === "saved") return null;
  return <Badge state={m[0]} label={m[1]} spin={t.state === "saving"} className={compact ? "plain" : ""} />;
}

function ChannelCard({ title, desc, ch, open, onOpen, onChange, children, disabledReason }) {
  const head = <><div className="apt-ch-name">{title}</div><div className="apt-ch-desc">{desc}</div></>;
  return <div className="apt-ch">
    <div className="apt-ch-top">
      {onOpen ? <button type="button" className="apt-ch-head" onClick={onOpen} aria-expanded={open}>{head}</button> : <div className="apt-ch-head">{head}</div>}
      {!open && ch.on && <span className="apt-val">{ch.bri != null ? `${ch.bri}%` : ""}</span>}
      <Switch checked={ch.on} label={`${title} power`} onChange={(v) => onChange({ on: v })} disabled={!!disabledReason} />
    </div>
    {open && <div style={{ display: "grid", gap: 0, marginTop: 4 }}>{children}</div>}
  </div>;
}

function ToneSliders({ name, ch, onChange, min = 1800 }) {
  return <>
    <Slider label="Brightness" min={1} max={100} value={ch.bri} fmt={fmtPct} onChange={(v) => onChange({ bri: v, on: true })} />
    <Slider label="Color temp" min={min} max={6500} step={50} temp value={ch.k} fmt={fmtK} onChange={(v) => onChange({ k: v })} />
  </>;
}

function RadialRing({ radial, zone, setZone, onChange, facing = [], anchor = 0, onToward, onFocus }) {
  const cx = 59, cy = 59, ro = 55, ri = 32; const arc = (i) => { const cA = -((anchor || 0) + i * 60), a0 = (cA - 28) * Math.PI / 180, a1 = (cA + 28) * Math.PI / 180;
    const p = (r, a) => `${cx + r * Math.cos(a)} ${cy + r * Math.sin(a)}`; return `M${p(ro, a0)} A${ro} ${ro} 0 0 1 ${p(ro, a1)} L${p(ri, a1)} A${ri} ${ri} 0 0 0 ${p(ri, a0)} Z`; };
  const col = kelvinCss(radial.k); const avg = Math.round(radial.levels.reduce((a, b) => a + b, 0) / 6);
  return <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr)", gap: 4, minWidth: 0 }}>
    <div className="apt-ring">
      <svg viewBox="0 0 118 118" role="group" aria-label="Radial zones">
        {radial.levels.map((lv, i) => <path key={i} d={arc(i)} tabIndex={0} role="button" aria-label={`Zone ${i + 1}, faces ${(facing[i] || {}).label || "open space"}, ${lv}%`} aria-pressed={zone === i}
          onClick={() => setZone(zone === i ? null : i)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setZone(zone === i ? null : i); } }}
          style={{ fill: radial.on && lv > 0 ? col : "var(--apt-fill-2)", fillOpacity: radial.on && lv > 0 ? 0.25 + lv / 130 : 1, stroke: zone === i ? "var(--hg-ice)" : undefined, strokeWidth: zone === i ? 2 : undefined }} />)}
        <text x="59" y="56" textAnchor="middle" style={{ fill: "var(--hg-fg-0)", font: "560 13px Geist" }}>{zone == null ? `${avg}%` : `${radial.levels[zone]}%`}</text>
        <text x="59" y="71" textAnchor="middle" style={{ fill: "var(--hg-fg-3)", font: "500 10.5px Geist" }}>{zone == null ? "all zones" : `zone ${zone + 1}`}</text>
      </svg>
      <div style={{ display: "grid", gap: 4, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 540 }}>{zone == null ? "All zones" : `Zone ${zone + 1}`}</div>
        <div className="apt-meta">{zone == null ? "Plan view, north up. In the room, press on the light and drag: outward brightens the zone under the cursor, inward dims it." : `Faces ${(facing[zone] || {}).label || "open space"}${(facing[zone] || {}).dist ? ` · ${facing[zone].dist.toFixed(1)} m` : ""}`}</div>
        {onFocus && <Btn sm icon="eye" onClick={onFocus}>Show in room</Btn>}
      </div>
    </div>
    <div className="apt-row"><span className="apt-meta grow">All six zones</span><div className="apt-quick">{[0, 25, 50, 100].map((v) => <button key={v} type="button" className="apt-chip sm" aria-pressed={v === 0 ? !radial.on : radial.on && radial.levels.every((x) => x === v)} onClick={() => { setZone(null); onChange(v ? { on: true, levels: radial.levels.map(() => v) } : { on: false }); }}>{v ? `${v}%` : "Off"}</button>)}</div></div>
    {onToward && <div className="apt-row"><span className="apt-meta grow">Light only</span><div className="apt-quick"><button type="button" className="apt-chip sm" onClick={() => { setZone(null); onToward("walls"); }}>Toward walls</button><button type="button" className="apt-chip sm" onClick={() => { setZone(null); onToward("room"); }}>Into the room</button></div></div>}
    <Slider label="Brightness" min={0} max={100} value={zone == null ? avg : radial.levels[zone]} fmt={fmtPct}
      onChange={(v) => onChange({ on: true, levels: radial.levels.map((x, i) => (zone == null || zone === i ? v : x)) })} />
    <Slider label="Color temp" min={1800} max={6500} step={50} temp value={radial.k} fmt={fmtK} onChange={(v) => onChange({ k: v })} />
  </div>;
}

function LightTab({ ctx, f, st }) {
  const { act } = ctx; const [zone, setZone] = useStateI(null);
  const ent = (ctx.hs.links || {})[f.id] !== undefined ? ctx.hs.links[f.id] : f.entity;
  const sent = f.kind === "cylinder" ? "Preview only · modeled fixture, nothing is sent." : ent ? `Prototype · not sent. In Home this calls light.turn_on for ${ent}.` : "Not linked to a Home Assistant light. Match it in Setup › Home Assistant.";
  if (f.kind === "cylinder") {
    const c = st.channels; const ch = (k) => (p) => act.setChannel(f.id, k, p);
    return <div className="apt-sec">
      <div className="apt-row"><span className="apt-meta grow">All channels</span><Btn sm onClick={() => act.setFixtureAll(f.id, true)}>All on</Btn><Btn sm onClick={() => act.setFixtureAll(f.id, false)}>All off</Btn></div>
      <ChannelCard title="Spot" desc={`Hidden robotic spotlight · aimed at ${st.aim ? st.aim.surface : "nothing yet"}`} ch={c.spot} open onChange={ch("spot")}>
        <ToneSliders ch={c.spot} onChange={ch("spot")} />
        <div className="apt-row"><span className="apt-meta grow">Where it points is set in Aim.</span><Btn sm icon="target" onClick={() => act.setTab("aim")}>Aim</Btn></div>
      </ChannelCard>
      <ChannelCard title="Down" desc="Downward LED · soft pool below the socket" ch={c.down} open onChange={ch("down")}>
        <ToneSliders ch={c.down} onChange={ch("down")} />
      </ChannelCard>
      <ChannelCard title="Radial" desc="Six outward zones behind the diffuser film" ch={{ ...c.radial, bri: Math.round(c.radial.levels.reduce((a, b) => a + b, 0) / 6) }} open onChange={ch("radial")}>
        <RadialRing radial={c.radial} zone={zone} setZone={setZone} onChange={ch("radial")} facing={ctx.zoneFacing(f.id)} anchor={f.radialAnchor || 0} onToward={(m) => act.zonesToward(f.id, m)} onFocus={() => act.focusFixture(f.id)} />
      </ChannelCard>
      <p className="apt-p apt-meta">{sent}</p>
    </div>;
  }
  const off = ctx.scen === "draft" || ctx.scen === "loading" || ctx.scen === "failed";
  return <>
    <div className="apt-sec">
      {off && <div className="apt-callout attn"><div className="apt-row"><Badge state="held" label="Last reported 20:58" /></div><p className="apt-p">Home Assistant is offline, so this shows the last reported state. Controls come back when it reconnects; nothing is queued.</p></div>}
      <div className="apt-row"><div className="grow"><div className="apt-ch-name">{f.kind === "plug" ? "Power" : "Current light"}</div><div className="apt-ch-desc mono">{ent || "Not linked"}</div></div>
        <Switch checked={st.on} label="Power" disabled={off} onChange={(v) => act.setLight(f.id, { on: v })} /></div>
      {f.kind !== "plug" && <><Slider label="Brightness" min={1} max={100} value={st.bri} fmt={fmtPct} disabled={!st.on || off} onChange={(v) => act.setLight(f.id, { bri: v })} />
        <Slider label="Color temp" min={2000} max={6500} step={50} temp value={st.k} fmt={fmtK} disabled={!st.on || off} onChange={(v) => act.setLight(f.id, { k: v })} /></>}
      {st.manual && !off && <div className="apt-callout attn"><div className="apt-row"><Badge state="held" label="Manual" /><span className="apt-p grow">Changed by hand {st.manual}. Adaptive lighting is paused for this light.</span></div>
        <div className="apt-actions"><Btn sm onClick={() => act.returnToAuto(f.id)}>Return to Auto</Btn></div></div>}
      <p className="apt-p apt-meta">{sent}</p>
    </div>
    {f.engineered && st.channels && <div className="apt-sec">
      <h3 className="apt-h">Engineered spot<span className="r">simulated</span></h3>
      <div className="apt-row"><span className="apt-p grow">Aimed at {st.aim ? st.aim.surface : "nothing"}.</span><Switch checked={st.channels.spot.on} label="Engineered spot power" onChange={(v) => act.setChannel(f.id, "spot", { on: v })} /></div>
      <ToneSliders ch={st.channels.spot} onChange={(p) => act.setChannel(f.id, "spot", p)} />
      <p className="apt-p apt-meta">{f.engineered.note}</p>
    </div>}
  </>;
}

function AimRow({ kind, title, meta, sub }) {
  return <div className={`apt-aimrow ${kind}`}><span className="mk" aria-hidden="true" /><span className="nm">{title}{sub && <span className="sub">{sub}</span>}</span>{meta && <span className="vl">{meta}</span>}</div>;
}

function ShadowNote({ ctx, f, pr }) {
  const alt = ctx.site.fixtures.filter((x) => x.engineered && x.id !== f.id).map((x) => ({ x, r: ctx.checkAim(x.id, pr.point) })).filter((a) => a.r && a.r.valid && !a.r.shadow);
  return <div className="apt-flag"><span className="grow">Your {pr.shadow.part} is in the beam, so it shadows this spot while you're there{pr.shadow.inferred ? " (inferred limb)" : ""}.{alt.length ? "" : " No other light reaches it clear of you."}</span>
    {alt.slice(0, 2).map((a) => <Btn key={a.x.id} sm kind="ghost" onClick={() => ctx.act.proposeOn(a.x.id, pr)}>Use {a.x.short}</Btn>)}</div>;
}

function AimTab({ ctx, f, st }) {
  const { act, hs } = ctx; const pr = hs.proposed[f.id]; const hist = hs.history[f.id] || { past: [], future: [] };
  const [name, setName] = useStateI(""); const [naming, setNaming] = useStateI(false);
  if (!f.engineered) return <div className="apt-sec"><h3 className="apt-h">Aim</h3><p className="apt-p">{f.name} is an ordinary light and can't be aimed. Aiming needs an Engineered profile at this mount.</p><div className="apt-actions"><Btn sm onClick={() => act.setTab("setup")}>Open Setup</Btn></div></div>;
  const ok = !!(pr && pr.valid !== false); const meta = (p) => { const s = solveAim(f, p); return s ? `${r2(s.dist)} m · ${Math.round(s.tilt)}°` : ""; };
  const attention = hs.targets.filter((t) => ["changed", "conflict", "local"].includes(t.state));
  return <>
    <div className="apt-sec">
      <div className="apt-row"><h3 className="apt-h grow">Aim</h3><span className="apt-meta">{f.kind === "cylinder" ? "Modeled" : "Simulated"}</span>
        <Btn sm kind="ghost" icon="undo" label="Undo" title="Undo (⌘Z)" disabled={!hist.past.length} onClick={() => act.undo(f.id)} />
        <Btn sm kind="ghost" icon="redo" label="Redo" title="Redo (⇧⌘Z)" disabled={!hist.future.length} onClick={() => act.redo(f.id)} /></div>
      <AimRow kind="current" title={st.aim ? ((hs.targets.find((t) => t.id === st.aim.targetId) || {}).name || st.aim.surface) : "No aim yet"} meta={st.aim ? meta(st.aim.point) : ""} />
      {pr && (ok ? <AimRow kind="proposed" title={pr.snapName || pr.surface} sub={pr.origin === "perception" ? "from perception" : pr.origin === "chat" ? "from chat" : null} meta={meta(pr.point) + (pr.grazing ? " · shallow" : "")} />
        : <AimRow kind="invalid" title={pr.message} />)}
      {ok && pr.shadow && <ShadowNote ctx={ctx} f={f} pr={pr} />}
      {pr ? (naming
        ? <form className="apt-row" onSubmit={(e) => { e.preventDefault(); if (name.trim()) { act.saveTarget(f.id, name.trim()); setNaming(false); } }}>
            <input className="apt-input grow" autoFocus value={name} onChange={(e) => setName(e.target.value)} aria-label="Name for this aim" placeholder="Desk reading" onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); setNaming(false); } }} />
            <Btn sm kind="primary" type="submit" disabled={!name.trim()}>Save</Btn></form>
        : <div className="apt-actions">
            {ok && <Btn sm kind="primary" onClick={() => act.applyAim(f.id)}>Set aim</Btn>}
            {ok && !pr.snapId && <Btn sm onClick={() => { setName(pr.surface || ""); setNaming(true); }}>Save as named aim</Btn>}
            <Btn sm kind="ghost" onClick={() => act.discard(f.id)}>Discard</Btn></div>)
        : <div className="apt-row"><span className="apt-meta grow">Drag the reticle onto a surface.</span>
            <Btn sm icon="target" pressed={ctx.tapAim} onClick={() => (ctx.tapAim ? act.cancelTapAim() : act.startTapAim(f.id))}>{ctx.tapAim ? "Tap a surface…" : "Tap to aim"}</Btn></div>}
    </div>
    <div className="apt-sec">
      <h3 className="apt-h">Named aims</h3>
      <div className="apt-chips">{hs.targets.map((t) => { const isCur = !!(st.aim && st.aim.targetId === t.id); const isPr = !!(ok && pr.snapId === t.id);
        return <button key={t.id} type="button" className={`apt-chip ${isPr ? "is-proposed" : ""}`} aria-current={isCur} title={isCur ? "Current aim" : "Propose this aim"} onClick={() => act.recallTarget(f.id, t.id)}>
          {t.name}{attention.includes(t) && <span className="flag" aria-label="needs attention" />}</button>; })}</div>
      {attention.map((t) => <div key={t.id} className={`apt-flag ${t.state === "conflict" ? "blocked" : ""}`}>
        <span className="grow">{t.state === "changed" ? `“${t.name}” predates the latest scan.` : t.state === "conflict" ? `“${t.name}” already exists.` : `“${t.name}” is saved in this browser only.`}</span>
        {t.state === "changed" && <><Btn sm kind="ghost" onClick={() => act.recallTarget(f.id, t.id)}>Preview</Btn><Btn sm kind="ghost" onClick={() => act.confirmTarget(t.id)}>Keep</Btn></>}
        {t.state === "conflict" && <><Btn sm kind="ghost" onClick={() => act.resolveTarget(t.id, "replace")}>Replace</Btn><Btn sm kind="ghost" onClick={() => act.resolveTarget(t.id, "keep")}>Keep both</Btn></>}
      </div>)}
    </div>
    <CommandInspector ctx={ctx} f={f} st={st} />
  </>;
}

function CommandInspector({ ctx, f, st }) {
  const { hs, site, scen } = ctx; const pr = hs.proposed[f.id]; const usePr = pr && pr.valid !== false && pr.point; const point = usePr ? pr.point : st.aim && st.aim.point;
  const sol = solveAim(f, point); const spot = st.channels.spot; const surface = usePr ? (pr.snapName || pr.surface) : st.aim && st.aim.surface;
  const cyl = f.kind === "cylinder";
  const intent = `${spot.on ? "Point" : "Keep off, aimed"} ${cyl ? f.name.split(" · ")[0] : f.name}'s spotlight at ${surface || "—"}${spot.on ? `, ${spot.bri}% and ${spot.k} K` : ""}.`;
  const modeled = JSON.stringify({ frame: site.frame, fixture_id: f.id, origin_m: f.pos.map(r2), destination_m: point ? point.map(r2) : null, surface: surface || null,
    pan_deg: sol ? Math.round(sol.pan * 10) / 10 : null, tilt_deg: sol ? Math.round(sol.tilt * 10) / 10 : null, full_fwhm_deg: f.engineered.fwhm }, null, 2);
  const steps = [
    { k: usePr ? "proposed" : "ok", t: usePr ? "Draft target" : "Current aim", d: usePr ? "Proposed in this view. Nothing has changed yet." : `${cyl ? "Modeled" : "Simulated"} aim in this view.` },
    { k: sol && sol.tilt <= f.engineered.tiltLimit ? "ok" : "blocked", t: "Modeled command", d: sol ? `Solved in the Apartment frame · pan ${Math.round(sol.pan)}°, tilt ${Math.round(sol.tilt)}° · within the ${f.engineered.tiltLimit}° limit` : "No destination yet." },
    cyl ? { k: "none", t: "Accepted execution", d: "Not available · this socket isn't commissioned, so there is no command path." }
      : { k: "blocked", t: "Accepted execution", d: "Blocked · no movement authority and no target-plane binding for this frame." },
    cyl ? { k: "none", t: "Reported state", d: "None · nothing physical reports back for this fixture." }
      : { k: "none", t: "Reported state", d: "No telemetry · the bench is loopback-only and not open here." },
  ];
  const status = cyl ? "Modeled · nothing sent" : "Simulated · execution blocked";
  return <div className="apt-sec">
    <details className="apt-disc apt-cmd"><summary><span className="apt-cmd-t">Command</span><span className="apt-cmd-s">{status}</span></summary><div className="body">
    <p className="apt-p" style={{ color: "var(--hg-fg-0)", fontSize: 13.5 }}>{intent}</p>
    <ol className="apt-ladder">{steps.map((s, i) => <li key={i}><span className={`mk ${s.k}`}>{s.k === "ok" && <Icon name="check" size={11} style={{ color: "var(--apt-ok)" }} />}</span><div><div className="t">{s.t}</div><div className="d">{s.d}</div></div></li>)}</ol>
    <Disc summary="Exact output">
      <div className="apt-meta">Modeled solve · visualization only</div>
      <pre className="apt-code">{modeled}</pre>
      {cyl ? <><div className="apt-meta">Command</div><pre className="apt-code"><span className="c">// No canonical command exists for an uncommissioned socket.{"\n"}// Nothing here is a device packet.</span></pre></>
        : <><div className="apt-meta">Product Aim request · docs/APARTMENT-LIGHT-AIMING.md</div>
          <pre className="apt-code"><span className="k">POST</span> /api/group/aim{"\n"}{"{ "}<span className="n">"target_x_m"</span>: null, <span className="n">"target_y_m"</span>: null, <span className="n">"post_dwell_ms"</span>: null {"}"}{"\n"}<span className="c">// blocked · no Apartment→Product-plane transform{"\n"}// blocked · no foreground owner authority</span></pre></>}
      <div className="apt-actions"><Btn sm icon="copy" onClick={() => ctx.act.copy(intent)}>Copy intent</Btn><Btn sm icon="copy" onClick={() => ctx.act.copy(modeled)}>Copy modeled JSON</Btn><Btn sm icon="copy" disabled title="Blocked: no executable request exists">Copy request</Btn></div>
    </Disc>
    <Disc summary="Provenance">
      <dl className="apt-kv">
        <dt>Model</dt><dd>{site.model.source}{site.model.revision ? ` · rev ${site.model.revision}` : ""}</dd>
        <dt>Frame</dt><dd className="mono">{site.frame}</dd>
        <dt>Scene</dt><dd>{site.scene.label}</dd>
        {site.id === "echo" && <><dt>Collision</dt><dd className="mono">collision.glb · 320cd1d6</dd></>}
        <dt>Optic</dt><dd>{f.engineered.optic}</dd>
        <dt>Origin</dt><dd>{cyl ? "Fixture bottom · socket position authored" : "Fixture bottom · position proposed (no tape yet)"}</dd>
        <dt>Data</dt><dd>{cyl ? "Authored for this prototype" : "Seed geometry · authored states"}</dd>
      </dl>
    </Disc>
    </div></details>
  </div>;
}

function SetupTab({ ctx, f, st }) {
  const cyl = f.kind === "cylinder";
  return <>
    <div className="apt-sec"><h3 className="apt-h">Identity</h3>
      <dl className="apt-kv">
        <dt>Fixture</dt><dd className="mono">{f.id}</dd>
        {cyl ? <><dt>Socket</dt><dd>Socket {f.socket} · mapped</dd><dt>Entity</dt><dd>Not commissioned</dd><dt>Channels</dt><dd>Spot · Down · Radial 1–6</dd></>
          : <><dt>Entity</dt><dd className="mono">{f.entity}</dd><dt>Profile</dt><dd>{f.engineered ? "Current + Engineered at one mount" : "Current light only"}</dd></>}
      </dl>
      {!cyl && !f.engineered && <div className="apt-callout"><p className="apt-p">Adding an Engineered profile reuses this exact ceiling position and its measurements. The current light stays linked; no second fixture is placed.</p><div className="apt-actions"><Btn sm onClick={() => ctx.act.toast("Prototype: profile adoption is shown in Setup → Fixture links")}>Add Engineered profile…</Btn></div></div>}
    </div>
    <div className="apt-sec"><h3 className="apt-h">Fixture position<span className="r">{cyl ? "authored" : f.calibration}</span></h3>
      <p className="apt-p">{cyl ? "Socket positions come from the Victoria socket map. Tape checks are optional." : "Two wall tapes and the ceiling height set the fixture-bottom aiming origin. None entered yet."}</p>
      <div className="apt-actions"><Btn sm icon="ruler" onClick={() => ctx.act.openSetup("position", f.id)}>Open Fixture position</Btn></div>
    </div>
    {f.engineered && <div className="apt-sec"><h3 className="apt-h">Optics and limits</h3>
      <dl className="apt-kv"><dt>Optic</dt><dd>{f.engineered.optic}</dd><dt>Beam</dt><dd>{f.engineered.fwhm}° full width at half maximum</dd><dt>Reach</dt><dd>Up to {f.engineered.tiltLimit}° from straight down{cyl ? " · authored" : ""}</dd></dl>
      <Disc summary="Rendering and physics limits"><ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 4 }} className="apt-p">
        <li>The pool edge is the half-maximum contour, not a photometric map.</li><li>Beam blocking checks furniture boxes only; people don't block yet.</li>
        <li>Shadows from furniture and bodies are planned.</li><li>Colour on screen is qualitative.</li></ul></Disc>
    </div>}
  </>;
}

function LightInspector({ ctx, f }) {
  const { hs, act } = ctx; const st = hs.fixtures[f.id]; const tab = hs.tab;
  return <>
    <div className="apt-ihead">
      <div className="apt-ihead-row">
        <Btn sm icon="back" label="Back to room" title="Back to room" onClick={() => act.select(null)} />
        <div className="grow" style={{ minWidth: 0 }}><h2 className="apt-title">{f.name}</h2><div className="apt-sub">{ctx.site.rooms.find((r) => r.id === f.room).name} · {lightSummary(f, st)}</div></div>
        <Btn sm icon="close" label="Close light" kind="ghost" onClick={() => act.select(null)} />
      </div>
      <ProvenanceBadges ctx={ctx} f={f} />
      <div className="apt-tabs" role="tablist" aria-label="Light sections">
        {[["light", "Light"], ["aim", "Aim"], ["setup", "Setup"]].map(([k, l]) => <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => act.setTab(k)}>{l}</button>)}
      </div>
    </div>
    {tab === "light" && <LightTab key={f.id} ctx={ctx} f={f} st={st} />}
    {tab === "aim" && <AimTab key={f.id} ctx={ctx} f={f} st={st} />}
    {tab === "setup" && <SetupTab ctx={ctx} f={f} st={st} />}
  </>;
}

function TargetInspector({ ctx, t }) {
  const { site, hs, act } = ctx; const users = site.fixtures.filter((f) => hs.fixtures[f.id] && hs.fixtures[f.id].aim && hs.fixtures[f.id].aim.targetId === t.id);
  const aimers = site.fixtures.filter((f) => f.engineered);
  return <>
    <div className="apt-ihead"><div className="apt-ihead-row"><Btn sm icon="back" label="Back to room" onClick={() => act.select(null)} />
      <div className="grow"><h2 className="apt-title">{t.name}</h2><div className="apt-sub">Named aim · {t.category} · {t.normal[2] > 0.9 ? "horizontal surface" : "wall surface"}</div></div><Btn sm icon="setup" onClick={() => act.editTarget(t.id)}>Edit</Btn></div>
      <div className="apt-actions"><TargetState t={t} /><Badge state={t.source === "proposed" ? "proposed" : t.source === "authored" ? "authored" : "ok"} label={t.source === "tape_and_scan" ? "Tape and scan" : t.source === "proposed" ? "Proposed placement" : t.source === "aimed" ? "Saved from an aim" : t.source === "mapped" ? "Mapped in Setup" : "Authored"} /></div></div>
    <div className="apt-sec"><h3 className="apt-h">Lit by</h3><p className="apt-p">{users.length ? users.map((u) => u.name).join(", ") : "No fixture is aimed here now."}</p></div>
    <div className="apt-sec"><h3 className="apt-h">Aim a light here</h3><div className="apt-actions">{aimers.map((f) => <Btn key={f.id} sm onClick={() => { act.select({ kind: "fixture", id: f.id }, "aim"); act.recallTarget(f.id, t.id); }}>{f.short}{f.kind === "cylinder" ? "" : " · simulated"}</Btn>)}</div>
      <p className="apt-meta">Creates a proposal on that light; nothing changes until you set it.</p></div>
    <div className="apt-sec"><Disc summary="Use in an automation"><p className="apt-p">Automations refer to this aim by its id, so renaming it keeps them working.</p><div className="apt-ref"><span>{t.id}</span><Btn sm icon="copy" label="Copy id" onClick={() => act.copy(t.id)} /></div><p className="apt-meta">targets[].id is the existing schema field; how automations reference it is proposed.</p></Disc>
      <Disc summary="Geometry"><dl className="apt-kv"><dt>Id</dt><dd className="mono">{t.id}</dd><dt>Centre</dt><dd className="mono">{t.pos.map(r2).join(", ")} m</dd><dt>Normal</dt><dd className="mono">{t.normal.join(", ")}</dd>{t.size && <><dt>Size</dt><dd className="mono">{t.size.map(r2).join(" × ")} m</dd></>}</dl></Disc></div>
  </>;
}

function Inspector({ ctx }) {
  const { site, hs } = ctx; const sel = hs.selected;
  if (sel && sel.kind === "fixture") { const f = site.fixtures.find((x) => x.id === sel.id); if (f) return <LightInspector ctx={ctx} f={f} />; }
  if (sel && sel.kind === "target") { const t = hs.targets.find((x) => x.id === sel.id); if (t) return <TargetInspector ctx={ctx} t={t} />; }
  return <RoomInspector ctx={ctx} />;
}

Object.assign(window, { Inspector, solveAim, lightSummary, isLit, r2 });
