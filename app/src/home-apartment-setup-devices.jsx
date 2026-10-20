/* eslint-disable */
/**
 * home-apartment-setup-devices.jsx — PROPOSED, pending the owner's approval.
 *
 * Setup › Cameras and sensors placer (APT-U06, APT-U07), shown only with the
 * Setup proposal flag (?setupProposal=1). It writes the proposed
 * `devices[].placement` and `presence_sensor` records described in
 * docs/design/apartment-unified/setup-stored-fields-proposal.md, through the
 * editor's own mutation and save. Calibrated intrinsics and extrinsics are
 * never written. A sensor reads "Ordered · not connected" until Home
 * Assistant has its entities. Nothing here sends a command.
 */
(function () {
  "use strict";
  const { useState, useEffect, useRef, useMemo } = React;
  const F = () => window.AptSetupDevices;
  const MOUNTS = [["wall", "Wall"], ["ceiling", "Ceiling"], ["shelf", "Shelf"]];

  function slug(value) { return String(value).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "sensor"; }
  const deg = (rad) => Math.round((rad || 0) / Math.PI * 180);

  function surfacePick(engine, clientX, clientY) {
    const mesh = engine?.modes?.getMesh?.();
    if (!mesh) return null;
    return engine.picking.surfaceHit(engine.apartmentRoot, [mesh], clientX, clientY);
  }

  /* The draft's coverage, projected through the F01 overlay layer: solid for
     cameras, dashed for sensors. Presentation only. */
  function AptSetupFrustum({ engine, draft }) {
    const host = engine?.renderer?.domElement?.parentElement || null;
    const layerRef = useRef(null), svgRef = useRef(null), draftRef = useRef(draft); draftRef.current = draft;
    useEffect(() => {
      if (!engine?.createUiOverlayLayer || !host) return undefined;
      const controller = new AbortController(); let alive = true;
      engine.createUiOverlayLayer({ host, isCurrent: () => alive, signal: controller.signal })
        .then((layer) => { if (!alive) layer?.dispose?.(); else layerRef.current = layer; });
      return () => { alive = false; controller.abort(); layerRef.current?.dispose?.(); layerRef.current = null; };
    }, [engine, host]);
    useEffect(() => {
      let frame = 0;
      const draw = () => {
        frame = requestAnimationFrame(draw);
        const layer = layerRef.current, d = draftRef.current, svg = svgRef.current;
        if (!layer || !svg || !d?.pos) return;
        const shape = F().frustum({ pos: d.pos, yaw_rad: d.yaw_rad, placement: { pitch_deg: d.pitch_deg, hfov_deg: d.hfov_deg, vfov_deg: d.vfov_deg, range_m: d.kind === "sensor" ? d.range_m : 2.5 } });
        const apex = layer.project(shape.apex), corners = shape.corners.map((point) => layer.project(point));
        const path = apex && corners.every(Boolean)
          ? corners.map((c) => `M${apex.x},${apex.y}L${c.x},${c.y}`).join("") + `M${corners.map((c) => `${c.x},${c.y}`).join("L")}Z` : "";
        svg.querySelector("path").setAttribute("d", path);
      };
      frame = requestAnimationFrame(draw);
      return () => cancelAnimationFrame(frame);
    }, []);
    if (!host || !draft?.pos) return null;
    return ReactDOM.createPortal(<svg ref={svgRef} className="apt-setup-frustum" data-apt-edit-ui="device-frustum" data-kind={draft.kind} aria-hidden="true"><path d="" /></svg>, host);
  }

  function Slider({ label, value, min, max, step = 1, suffix, onChange, aria }) {
    return <label className="apt-ui-slider"><span className="apt-ui-row"><span className="grow">{label}</span><span className="apt-ui-value">{value}{suffix}</span></span>
      <input className="apt-ui-range" type="range" min={min} max={max} step={step} value={value} aria-label={aria || label} onChange={(event) => onChange(+event.target.value)} /></label>;
  }

  function AptSetupDevicePlacer(props) {
    const { engine, model, homeName, registryIds = [], mutate, onSave, saveStatus, saving, roomAt, placing, beginPlacing, endPlacing,
      registerPick } = props;
    const Badge = window.HomeApartmentUI?.Badge;
    const [draft, setDraft] = useState(null);
    const [message, setMessage] = useState("");
    const [pendingSave, setPendingSave] = useState(null);
    const rootRef = useRef(null);
    const devices = model.devices || [];
    const cameras = devices.filter((device) => device.type === "camera");
    const sensors = devices.filter((device) => device.type === "presence_sensor");
    const roomHeight = Math.max(2.4, ...(model.zones || []).map((zone) => +zone.ceiling_height_m || 0));
    const center = useMemo(() => {
      const points = (model.zones || []).flatMap((zone) => zone.floor_polygon || []);
      return points.length ? [points.reduce((s, p) => s + p[0], 0) / points.length, points.reduce((s, p) => s + p[1], 0) / points.length] : [2, 2];
    }, [model.zones]);
    const registry = useMemo(() => Object.fromEntries(registryIds.map((id) => [id, true])), [registryIds.join(",")]);
    const edit = (patch) => setDraft((current) => current ? { ...current, ...patch } : current);

    useEffect(() => {
      registerPick((clientX, clientY) => {
        const hit = surfacePick(engine, clientX, clientY);
        if (!hit) { setMessage("No surface under the pointer. Drag to orbit, then click."); return; }
        setDraft((current) => {
          if (!current) return current;
          if (current.mode === "looking") {
            const aim = current.pos && F().aimAt(current.pos, hit.point);
            return aim ? { ...current, ...aim, mode: "editing" } : { ...current, mode: "editing" };
          }
          const pose = F().poseFromPick({ mount: current.mount, point: hit.point, normal: hit.normal, roomHeight, roomCenter: center });
          return { ...current, ...pose, surface: hit.object?.name || null, mode: "editing" };
        });
        setMessage(""); endPlacing();
      });
      return () => registerPick(null);
    }, [engine, registerPick, endPlacing, roomHeight, center]);

    useEffect(() => { if (!placing && draft && draft.mode !== "editing") setDraft((current) => current?.pos ? { ...current, mode: "editing" } : null); }, [placing]);

    useEffect(() => {
      if (!pendingSave) return;
      const device = devices.find((candidate) => candidate.id === pendingSave.id);
      const landed = pendingSave.kind === "remove" ? !device : device?.updated_at === pendingSave.stamp;
      if (!landed) return;
      setPendingSave(null); onSave();
    }, [pendingSave, devices, onSave]);

    window.HomeOverlay.useOverlayLayer({
      key: "apartment-device-placer", active: !!(draft && draft.pos && !placing), rootRef,
      onEscape: () => setDraft(null), initialFocus: "none",
    });

    const startCamera = (camera) => {
      const p = camera.placement || {};
      setDraft({ id: camera.id, kind: "camera", name: camera.name || camera.id, pos: camera.pos?.slice() || null, yaw_rad: camera.yaw_rad || 0,
        pitch_deg: p.pitch_deg ?? -15, hfov_deg: p.hfov_deg ?? 90, vfov_deg: p.vfov_deg ?? F().cameraVfov(p.hfov_deg ?? 90),
        mount: p.mount || "wall", mode: "editing" });
      setMessage("");
    };
    const startSensor = (sensor) => {
      const p = sensor?.placement || {};
      setDraft(sensor ? { id: sensor.id, kind: "sensor", name: sensor.name, pos: sensor.pos.slice(), yaw_rad: sensor.yaw_rad || 0,
        pitch_deg: p.pitch_deg ?? -15, hfov_deg: p.hfov_deg ?? 100, vfov_deg: p.vfov_deg ?? 90, range_m: p.range_m ?? 6, mount: p.mount || "wall", mode: "editing" }
        : { id: null, kind: "sensor", name: `mmWave sensor ${sensors.length + 1}`, pos: null, yaw_rad: 0, pitch_deg: -15, hfov_deg: 100, vfov_deg: 90,
          range_m: 6, mount: "wall", mode: "placing" });
      if (!sensor) beginPlacing(); setMessage("");
    };
    const save = () => {
      if (!draft?.pos || saving) return;
      const stamp = new Date().toISOString(), id = draft.id || `sensor-${slug(draft.name)}-${Date.now().toString(36)}`;
      mutate((m) => {
        const room = roomAt(draft.pos[0], draft.pos[1]);
        if (draft.kind === "camera") {
          const device = (m.devices || []).find((candidate) => candidate.id === id);
          if (!device) return;
          // Body pose and the proposed placement only; calibration is untouched.
          device.pos = draft.pos.slice(); device.yaw_rad = draft.yaw_rad; device.room_id = room; device.updated_at = stamp;
          device.placement = F().placementRecord({ ...draft, now: stamp, extrinsics: device.camera?.extrinsics || null });
          return;
        }
        const record = { id, type: "presence_sensor", name: String(draft.name).trim() || "mmWave sensor", pos: draft.pos.slice(), yaw_rad: draft.yaw_rad,
          room_id: room, placement: F().placementRecord({ ...draft, now: stamp, sensor: true }), ordered: true, ha_entity_ids: [],
          source: "manual", confidence: 1, updated_at: stamp };
        const index = (m.devices || []).findIndex((candidate) => candidate.id === id);
        if (index >= 0) m.devices[index] = { ...m.devices[index], ...record, ha_entity_ids: m.devices[index].ha_entity_ids || [] };
        else (m.devices = m.devices || []).push(record);
      });
      setPendingSave({ id, stamp, kind: "save" }); setDraft(null);
    };
    const remove = () => {
      if (draft?.kind !== "sensor" || !draft.id) return;
      const id = draft.id;
      mutate((m) => { m.devices = (m.devices || []).filter((device) => device.id !== id); });
      setPendingSave({ id, kind: "remove" }); setDraft(null);
    };

    const status = (device) => device.type === "camera" ? F().cameraStatus(device) : F().sensorStatus(device, registry);
    const tone = (state) => state === "calibrated" || state === "paired" ? "current" : state === "moved" ? "changed" : state === "ordered" ? "unavailable" : "proposed";
    const note = saveStatus?.state === "saved" ? "Saved to the Apartment model." : saveStatus?.state === "offline" ? "Saved locally. Home Assistant is offline." : null;

    const banner = <p className="apt-ui-meta apt-setup-proposal" data-setup-proposal="devices">Proposed fields · pending the owner's approval: hand-placed pose (<code>placement</code>) and the mmWave sensor record. Calibration is never changed here.</p>;
    if (!draft) return <section ref={rootRef} className="apt-ui-section" aria-label="Place cameras and sensors" data-device-placer="list">
      <h3 className="apt-ui-heading">Placement</h3>
      {banner}
      <ul className="apt-ui-list">
        {cameras.map((camera) => { const s = status(camera); return <li key={camera.id} className="apt-ui-row" data-placer-device={camera.id}>
          <span className="grow">{camera.name}</span>{Badge && <Badge state={tone(s.state)}>{s.label}</Badge>}
          <button type="button" className="apt-ui-button is-small" aria-label={`Place ${camera.name} by hand`} onClick={() => startCamera(camera)}>Place</button></li>; })}
        {sensors.map((sensor) => { const s = status(sensor); return <li key={sensor.id} className="apt-ui-row" data-placer-device={sensor.id}>
          <span className="grow">{sensor.name}</span>{Badge && <Badge state={tone(s.state)}>{s.label}</Badge>}
          <button type="button" className="apt-ui-button is-small" aria-label={`Edit ${sensor.name}`} onClick={() => startSensor(sensor)}>Edit</button></li>; })}
      </ul>
      <div className="apt-ui-actions"><button type="button" className="apt-ui-button" onClick={() => startSensor(null)}>+ mmWave sensor</button></div>
      {(message || (note && pendingSave === null)) && <p className="apt-ui-meta" role="status">{message || note}</p>}
      <p className="apt-ui-meta">Placing sets where a device sits and what it covers. Camera calibration, which matches pixels to the mesh, is separate.</p>
    </section>;

    const cam = draft.kind === "camera", what = cam ? "camera" : "sensor";
    if (draft.mode !== "editing" || !draft.pos) return <section ref={rootRef} className="apt-ui-section" data-device-placer={draft.mode}>
      <div className="apt-ui-row"><h3 className="apt-ui-heading grow">{draft.mode === "looking" ? `Point ${draft.name}` : draft.pos ? `Move ${draft.name}` : cam ? "Place camera" : "New mmWave sensor"}</h3>
        <button type="button" className="apt-ui-button is-small" onClick={() => { endPlacing(); setDraft(draft.pos ? { ...draft, mode: "editing" } : null); }}>Cancel</button></div>
      {draft.mode === "placing" && <div className="apt-ui-seg apt-named-aim-seg" role="group" aria-label="Mounted on">
        {MOUNTS.map(([key, label]) => <button key={key} type="button" aria-pressed={draft.mount === key} onClick={() => edit({ mount: key })}>{label}</button>)}</div>}
      <p className="apt-ui-copy">{draft.mode === "looking" ? `Click the spot the ${what} should face.` : draft.mount === "ceiling" ? `Click the floor directly below the ${what}.`
        : draft.mount === "wall" ? `Click the wall where the ${what} is mounted, at its height.` : "Click the shelf or furniture top it sits on."}</p>
      <p className="apt-ui-meta">Dragging still orbits; Esc cancels.</p>
      {message && <p className="apt-ui-meta" role="status">{message}</p>}
    </section>;

    const coverList = F().covers({ pos: draft.pos, yaw_rad: draft.yaw_rad, placement: { pitch_deg: draft.pitch_deg, hfov_deg: draft.hfov_deg,
      vfov_deg: draft.vfov_deg, range_m: cam ? 6 : draft.range_m } }, { targets: model.targets || [], devices });
    const stored = devices.find((device) => device.id === draft.id);
    const s = stored ? status(stored) : { state: "ordered", label: "Ordered · not connected" };
    return <section ref={rootRef} className="apt-ui-section" aria-label={cam ? "Place camera" : "Place sensor"} data-device-placer="editing"
      data-draft={JSON.stringify({ pos: draft.pos, yaw_rad: draft.yaw_rad, pitch_deg: draft.pitch_deg, hfov_deg: draft.hfov_deg, vfov_deg: draft.vfov_deg, range_m: draft.range_m, mount: draft.mount })}>
      <div className="apt-ui-row"><h3 className="apt-ui-heading grow">{draft.id ? (cam ? "Edit camera" : "Edit sensor") : "New mmWave sensor"}</h3>
        {Badge && <Badge state={tone(s.state)}>{s.label}</Badge>}</div>
      {banner}
      {!cam && <label className="apt-setup-field"><span className="apt-ui-meta">Name</span>
        <input className="apt-ui-input" aria-label="Sensor name" value={draft.name} onChange={(event) => edit({ name: event.target.value })} /></label>}
      <div className="apt-ui-row"><span className="apt-ui-meta grow">Mounted on {draft.mount} · {draft.pos.map((v) => (+v).toFixed(2)).join(", ")} m</span>
        <button type="button" className="apt-ui-button is-small" onClick={() => { edit({ mode: "placing" }); beginPlacing(); }}>Move</button></div>
      <Slider label="Height" value={+(+draft.pos[2]).toFixed(2)} min={0.2} max={roomHeight} step={0.05} suffix=" m" aria="Device height" onChange={(v) => edit({ pos: [draft.pos[0], draft.pos[1], v] })} />
      <div className="apt-ui-row"><span className="apt-ui-meta grow">Facing</span>
        <button type="button" className="apt-ui-button is-small" onClick={() => { edit({ mode: "looking" }); beginPlacing(); }}>Point at a spot</button></div>
      <Slider label="Pan" value={deg(draft.yaw_rad)} min={-180} max={180} suffix="°" onChange={(v) => edit({ yaw_rad: Math.round(v * Math.PI / 180 * 1000) / 1000 })} />
      <Slider label="Tilt" value={draft.pitch_deg} min={-90} max={30} suffix="°" onChange={(v) => edit({ pitch_deg: v })} />
      <Slider label={cam ? "Lens" : "Field"} value={draft.hfov_deg} min={30} max={150} suffix="°" onChange={(v) => edit({ hfov_deg: v, vfov_deg: cam ? F().cameraVfov(v) : draft.vfov_deg })} />
      {!cam && <Slider label="Range" value={draft.range_m} min={1} max={12} step={0.5} suffix=" m" onChange={(v) => edit({ range_m: v })} />}
      {!cam && <p className="apt-ui-meta">Set field and range from the sensor's spec sheet. You can place it before it arrives; it connects once it's paired with Home Assistant.</p>}
      <h3 className="apt-ui-heading">Covers</h3>
      <p className="apt-ui-copy" data-placer-covers="1">{coverList.length ? coverList.join(", ") : "Nothing in view yet. Point it toward the room."}</p>
      <p className="apt-ui-meta">From field of view and range. Line of sight through walls and furniture isn't checked yet.</p>
      <div className="apt-ui-actions">
        <button type="button" className="apt-ui-button is-primary" disabled={!!saving} onClick={save}>{draft.id ? "Save changes" : "Save sensor"}</button>
        <button type="button" className="apt-ui-button" onClick={() => setDraft(null)}>Cancel</button>
        {!cam && draft.id && <button type="button" className="apt-ui-button apt-named-aim-delete" onClick={remove}>Remove</button>}
      </div>
      {cam && <p className="apt-ui-meta">After moving a camera, recalibrate it on the desktop app.</p>}
      <AptSetupFrustum engine={engine} draft={draft} />
    </section>;
  }

  const CSS = `
.apt-setup-frustum { position: absolute; inset: 0; width: 100%; height: 100%; z-index: 6; pointer-events: none; overflow: visible; }
.apt-setup-frustum path { fill: rgba(69,223,255,.04); stroke: rgba(69,223,255,.75); stroke-width: 1.2; }
.apt-setup-frustum[data-kind="sensor"] path { stroke-dasharray: 5 4; fill: rgba(255,180,95,.05); stroke: #ffb45f; }
`;
  if (typeof document !== "undefined" && !document.querySelector("style[data-source='home-apartment-setup-devices']")) {
    const style = document.createElement("style"); style.setAttribute("data-source", "home-apartment-setup-devices"); style.textContent = CSS; document.head.appendChild(style);
  }
  window.AptSetupDevicePlacer = AptSetupDevicePlacer;
})();
