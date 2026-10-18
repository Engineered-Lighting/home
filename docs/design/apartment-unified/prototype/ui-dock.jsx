/* Apartment unified prototype — perception dock (camera frame, inference, JEV, outcome). */
const { useState: useStateD } = React;

const VIC_SCEN = {
  current: { badge: ["current", "Analyzed 4 s ago"], age: 4.2, clock: "21:04:12", note: null },
  held: { badge: ["held", "Holding · 38 s"], age: 38, clock: "21:03:38", note: "Analysis paused. Home is holding the last result and won't propose lighting from it." },
  stale: { badge: ["stale", "Stale · 6 min"], age: 360, clock: "20:58:10", note: "Older than the 2-minute limit. Shown for reference; not used for lighting." },
  replay: { badge: ["replay", "Replay · Oct 16"], age: null, clock: "2026-10-16 21:04:12", note: "Recorded session. Nothing here is live." },
  abstain: { badge: ["current", "Analyzed 3 s ago"], age: 3, clock: "21:04:13", note: null },
  none: { badge: ["none", "No session"], age: null, clock: null, note: null },
};

function FrameOverlay({ kp, det, inferred, dim, detailed, showPose }) {
  const D = window.APT_DATA; const idx = (k) => D.COCO.indexOf(k); const inf = new Set(inferred || []);
  const P = (k) => kp && kp[idx(k)] ? [kp[idx(k)][0] * 960, kp[idx(k)][1] * 540] : null;
  return <svg viewBox="0 0 960 540" preserveAspectRatio="none" aria-hidden="true" style={{ opacity: dim ? 0.55 : 1 }}>
    {det && det.map((d, i) => { const [x0, y0, x1, y1] = d.box; return <g key={i}>
      <rect x={x0 * 960} y={y0 * 540} width={(x1 - x0) * 960} height={(y1 - y0) * 540} fill="none" stroke="#B8D8FF" strokeWidth="2.5" strokeDasharray={dim ? "8 6" : "none"} rx="4" />
      <rect x={x0 * 960} y={y0 * 540 - 26} width={detailed ? 118 : 70} height="22" rx="4" fill="rgba(0,0,0,.72)" />
      <text x={x0 * 960 + 7} y={y0 * 540 - 10} style={{ fill: "#DDEAFF", font: "500 15px Geist" }}>{detailed ? `person · ${d.score.toFixed(2)}` : "Person"}</text></g>; })}
    {showPose && kp && D.BONES.map(([a, b], i) => { const A = P(a), B = P(b); if (!A || !B) return null; const isInf = inf.has(a) || inf.has(b);
      return <line key={i} x1={A[0]} y1={A[1]} x2={B[0]} y2={B[1]} stroke={isInf ? "#B8D8FF" : "#F4F8FF"} strokeOpacity={isInf ? 0.7 : 0.95} strokeWidth={isInf ? 2 : 3} strokeDasharray={isInf ? "6 6" : "none"} strokeLinecap="round" />; })}
    {showPose && kp && D.COCO.map((k, i) => { const p = P(k); if (!p) return null; const isInf = inf.has(k);
      return <circle key={k} cx={p[0]} cy={p[1]} r={isInf ? 6 : 5} fill={isInf ? "rgba(0,0,0,.4)" : "#F4F8FF"} stroke={isInf ? "#B8D8FF" : "#0A0A0A"} strokeWidth="2" />; })}
  </svg>;
}

function Trace({ ctx, steps }) {
  return <div className="apt-trace" role="group" aria-label="Evidence to outcome">
    {steps.map((s) => <button key={s.k} type="button" aria-pressed={ctx.hs.trace === s.k} onClick={() => ctx.act.trace(s.k)} disabled={s.disabled} title={s.title}>
      <span className="k">{s.label}</span><span className={`v ${s.tone || ""}`}>{s.value}</span></button>)}
  </div>;
}

function DetailSeg({ ctx }) {
  return <Seg className="sm" value={ctx.detail} label="Detail level" onChange={ctx.act.setDetail} options={[{ value: "simple", label: "Simple" }, { value: "detailed", label: "Detailed" }]} />;
}

function DockHeader({ ctx, title, badge, cams }) {
  const { act, dock, detail, site, hs } = ctx; const open = dock === "default" || dock === "expanded";
  return <div className="apt-dock-head">
    {cams && cams.length > 1 ? <select className="apt-input" style={{ width: "auto", height: 28, padding: "0 6px", fontSize: 13, fontWeight: 560, border: "1px solid transparent", background: "transparent" }} aria-label="Camera"
      value={hs.camId} onChange={(e) => act.setCamera(e.target.value)}>{cams.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
      : <span className="ttl">{title}</span>}
    {badge && <Badge state={badge[0]} label={badge[1]} />}
    <span style={{ flex: "1 1 auto" }} />
    {open && <Btn sm icon="info" label="Details" pressed={detail === "detailed"} onClick={() => act.setDetail(detail === "detailed" ? "simple" : "detailed")} title={detail === "detailed" ? "Hide details" : "Details: detector, pose and JEV values, frame metadata, raw result"} />}
    <Btn sm icon={dock === "expanded" ? "collapse" : "expand"} label={dock === "expanded" ? "Shrink camera dock" : "Expand camera dock"} title={dock === "expanded" ? "Shrink" : "Expand"} onClick={() => act.setDock(dock === "expanded" ? "default" : "expanded")} />
    <Btn sm icon="chevD" label={open ? "Collapse camera dock" : "Open camera dock"} title={open ? "Collapse" : "Open"} onClick={() => act.setDock(open ? "collapsed" : "default")} style={{ transform: open ? "none" : "rotate(180deg)" }} />
  </div>;
}

function VictoriaDock({ ctx }) {
  const { site, hs, act, dock, detail, scen, frameUrl, kp2d } = ctx; const S = site.perception.sample; const sc = VIC_SCEN[scen] || VIC_SCEN.current;
  const open = dock === "default" || dock === "expanded"; const wide = dock === "expanded"; const det = detail === "detailed";
  const prop = site.fixtures.find((f) => f.id === S.proposal.fixtureId); const tgt = hs.targets.find((t) => t.id === S.proposal.targetId);
  const abstain = scen === "abstain"; const unusable = scen === "stale" || scen === "held"; const none = scen === "none";
  const top = S.jev.dist[0]; const [view, setView] = useStateD("analyzed");
  const steps = [
    { k: "seen", label: "Seen", value: none ? "—" : `Person · #${S.frameId}`, tone: none ? "muted" : "" },
    { k: "interpreted", label: "Interpreted", value: none ? "—" : abstain ? "Abstained" : `${top[0]} · ${Math.round(top[1] * 100)}%`, tone: none || abstain ? "muted" : "" },
    { k: "proposed", label: "Proposed", value: none || abstain || unusable ? "Nothing" : `${tgt ? tgt.name : "—"} · ${prop.short}`, tone: none || abstain || unusable ? "muted" : "proposed", disabled: none || abstain || unusable },
    { k: "did", label: "Did", value: none ? "—" : hs.didPerception ? "You set it · modeled" : "Nothing yet", tone: "muted" },
  ];
  const summary = none ? "Not analyzing the Den camera right now." : abstain ? "JEV abstained: not enough usable frames." : unusable ? `Last seen: ${top[0]}. Not used for lighting.` : `${top[0][0].toUpperCase() + top[0].slice(1)} at the desk · proposes ${tgt ? tgt.name : "—"} on ${prop.short}`;
  return <section className={`apt-dock ${wide ? "is-expanded" : ""}`} aria-label="Camera and perception">
    <DockHeader ctx={ctx} title="Den camera" badge={sc.badge} />
    {!open && <div style={{ padding: "0 16px 12px" }} className="apt-mini"><span className="grow" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{summary}</span></div>}
    {open && <div className="apt-dock-body">
      {none ? <div className="apt-callout"><h3 className="apt-h">No perception session</h3><p className="apt-p">Home isn't analyzing the Den camera. Sessions are started and reviewed in Perception Lab; Apartment only shows their results.</p>
        <div className="apt-actions"><Btn sm icon="external" onClick={act.openLab}>Open Perception Lab</Btn></div></div> : <>
        <div className={`apt-frame ${unusable ? "is-stale" : ""}`} onClick={() => act.trace("seen")} style={{ cursor: "pointer" }}>
          {frameUrl ? <img src={frameUrl} alt={`Den camera, analyzed frame ${S.frameId}`} /> : <div className="veil">Rendering frame…</div>}
          <FrameOverlay kp={kp2d} inferred={site.perception.body.inferred} dim={unusable} detailed={det} showPose={ctx.layers.framePose !== false} />
          <div className="onsrc" onClick={(e) => e.stopPropagation()}><Seg className="sm" value={view} label="Frame source" onChange={setView} options={[{ value: "analyzed", label: "Analyzed" }, { value: "live", label: "Live", disabled: true, title: "Live video isn't part of this prototype" }]} /></div>
          <button type="button" className="onframe" title="See the room from this camera" onClick={(e) => { e.stopPropagation(); act.cameraView("den"); }}><Icon name="camera" size={13} />From camera</button>
          <div className="stamp"><span>#{S.frameId}</span><span>{sc.clock}</span><span>authored stand-in frame</span></div>
        </div>
        {sc.note && <div className="apt-callout attn"><p className="apt-p">{sc.note}</p></div>}
        {scen === "replay" && <div className="apt-row"><Btn sm icon="play" label="Play replay" /><input type="range" className="apt-range grow" min="18228" max="18240" defaultValue="18234" aria-label="Replay frame" /><span className="apt-val">#18234</span></div>}
        <Trace ctx={ctx} steps={steps} />
        <div style={{ display: "grid", gap: 8 }}>
          <div className="apt-mini"><Icon name="person" size={14} /><span className="grow">Detector found <b>1 person</b>{det ? <> · detector confidence <span className="mono">{S.detector.score.toFixed(2)}</span></> : ""}</span></div>
          <div className="apt-mini"><Icon name="spark" size={14} /><span className="grow">Pose: <b>{S.pose.observed} of {S.pose.total} joints observed</b>, {S.pose.total - S.pose.observed} inferred behind the desk</span></div>
          {abstain ? <div className="apt-mini"><Icon name="info" size={14} /><span className="grow">JEV <b>abstained</b>: 2 usable frames in the 8 s window (needs {S.jev.minUsable}). Person partly out of frame.</span></div>
            : <div className="apt-mini"><Icon name="eye" size={14} /><span className="grow">JEV reads <b>{top[0]}</b> over the last {S.jev.window_s} s{det ? <> · activity probability <span className="mono">{top[1].toFixed(2)}</span></> : ""}</span></div>}
        </div>
        {(wide || det) && !abstain && <div className="apt-sec" style={{ padding: 0, border: 0 }}>
          <h3 className="apt-h">JEV activity<span className="r">{S.jev.frames} frames · {S.jev.window_s} s window</span></h3>
          <div className="apt-dist">{S.jev.dist.map(([l, p], i) => <div key={l} className={`apt-dist-row ${i === 0 ? "top" : ""}`}><span>{l}</span><span className="bar"><i style={{ width: `${p * 100}%` }} /></span><span className="p">{p.toFixed(2)}</span></div>)}</div>
          <ul className="apt-p" style={{ margin: 0, paddingLeft: 18 }}>{S.jev.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          <p className="apt-meta">Activity labels here are authored examples. Probabilities describe JEV's judgment over the window; they are not detector or pose scores.</p>
        </div>}
        {det && <Disc summary="Frame, models and registration">
          <dl className="apt-kv">
            <dt>Frame</dt><dd className="mono">#{S.frameId}</dd><dt>Captured</dt><dd className="mono">{scen === "replay" ? S.recordedAt : sc.clock}</dd>
            <dt>Analysis age</dt><dd className="mono">{sc.age == null ? "replay" : fmtAge(sc.age)}</dd><dt>Overlay</dt><dd>Bound to frame #{S.frameId}</dd>
            <dt>Detector</dt><dd>person · {S.detector.score.toFixed(2)}</dd><dt>Pose</dt><dd>{S.pose.model} · {S.pose.observed}/{S.pose.total}</dd>
            <dt>JEV window</dt><dd className="mono">{S.jev.window_s} s · {S.jev.usable}/{S.jev.frames} usable</dd>
            <dt>Registration</dt><dd>{S.registration.camera} · {S.registration.status}</dd><dt>Source</dt><dd>Authored sample</dd>
          </dl>
          <Disc summary="Raw result (authored)"><pre className="apt-code">{JSON.stringify({ frame_id: S.frameId, camera: S.cameraId, detections: [{ label: "person", score: S.detector.score }], pose: { format: "coco17", observed: S.pose.observed, inferred: site.perception.body.inferred }, jev: abstain ? { abstained: true, usable_frames: 2, min_usable: S.jev.minUsable } : { window_s: S.jev.window_s, distribution: Object.fromEntries(S.jev.dist) } }, null, 2)}</pre></Disc>
        </Disc>}
        <div className="apt-row"><span className="apt-meta grow">Lab A/B and the full pipeline live in Perception Lab.</span><Btn sm icon="external" onClick={act.openLab}>Open in Lab</Btn></div>
      </>}
    </div>}
  </section>;
}

function EchoDock({ ctx }) {
  const { site, hs, act, dock, detail, scen } = ctx; const open = dock === "default" || dock === "expanded"; const cam = site.cameras.find((c) => c.id === hs.camId) || site.cameras[0];
  const fr = site.perception.frames[cam.id]; const offline = scen === "draft", loading = scen === "loading"; const det = detail === "detailed";
  const badge = offline ? ["unavailable", "Unavailable"] : loading ? ["loading", "Connecting"] : ["recorded", "Recorded still"];
  const n = fr.detections.length;
  return <section className={`apt-dock ${dock === "expanded" ? "is-expanded" : ""}`} aria-label="Camera and perception">
    <DockHeader ctx={ctx} cams={site.cameras} badge={badge} />
    {!open && <div style={{ padding: "0 16px 12px" }} className="apt-mini"><span>{offline ? "Snapshots unavailable while Home Assistant is offline." : `${site.perception.tracker.label} · ${n ? "person in this view" : "no person in this view"}`}</span></div>}
    {open && <div className="apt-dock-body">
      <div className="apt-frame">
        {offline || loading ? <div className="veil">{offline ? "Snapshot unavailable while Home Assistant is offline." : "Connecting to Home Assistant…"}</div>
          : <><img src={cam.media} alt={`${cam.name} snapshot`} /><FrameOverlay det={fr.detections} detailed={det} /><div className="stamp"><span>snapshot</span><span>recorded still · repo asset</span></div>
            <div className="onsrc"><Seg className="sm" value="analyzed" label="Frame source" onChange={() => {}} options={[{ value: "analyzed", label: "Snapshot" }, { value: "live", label: "Live", disabled: true, title: "Live feed needs go2rtc :1984 exposed" }]} /></div>
            <button type="button" className="onframe" title="See the room from this camera" onClick={() => act.cameraView(cam.id)}><Icon name="camera" size={13} />From camera</button></>}
      </div>
      <Trace ctx={ctx} steps={[
        { k: "seen", label: "Seen", value: offline ? "—" : n ? "Person" : "Nobody", tone: offline ? "muted" : "" },
        { k: "interpreted", label: "Interpreted", value: "Not available", tone: "muted", disabled: true, title: "No pose or JEV for Echo Park cameras" },
        { k: "proposed", label: "Proposed", value: "Nothing", tone: "muted", disabled: true },
        { k: "did", label: "Did", value: "Nothing", tone: "muted", disabled: true }]} />
      <div className="apt-mini"><Icon name="person" size={14} /><span className="grow">Tracker: <b>{site.perception.tracker.label}</b> · room-level, no position</span></div>
      <p className="apt-p apt-meta">{site.perception.summary}</p>
      {det && <dl className="apt-kv"><dt>Camera</dt><dd>{cam.name}</dd><dt>Calibration</dt><dd>Not calibrated · approximate pose</dd><dt>Detections</dt><dd>{n ? `person · ${fr.detections[0].score.toFixed(2)} (authored box)` : "none"}</dd><dt>Tracker</dt><dd className="mono">{site.perception.tracker.source}</dd></dl>}
    </div>}
  </section>;
}

function PerceptionDock({ ctx }) {
  if (ctx.dock === "hidden") return null;
  return ctx.site.id === "victoria" ? <VictoriaDock ctx={ctx} /> : <EchoDock ctx={ctx} />;
}

Object.assign(window, { PerceptionDock, VIC_SCEN });
