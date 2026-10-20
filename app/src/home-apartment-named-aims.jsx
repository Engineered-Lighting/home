/* eslint-disable */
/**
 * home-apartment-named-aims.jsx — APT-U04/U05 Setup › Named aims mapper.
 *
 * New (or Pick again) arms the editor's existing click-to-place gesture: a
 * click picks the exact mesh face, a drag still orbits. The draft fits a
 * furniture top or small vertical plane to its own size, suggests a name and
 * type, lists which aimable lights can reach it, previews a light's beam, and
 * has corner, centre and height handles on its own plane in the room.
 * Save writes the existing apartment_model target through the editor's
 * mutation and save path. A named aim is geometry only: nothing here sends a
 * light command. Victoria stays unavailable until the Den named-aim store
 * exists.
 */
(function () {
  "use strict";
  const { useState, useEffect, useRef, useCallback, useMemo } = React;
  const N = () => window.AptNamedAims;
  const TYPES = [["table", "Table"], ["island", "Island"], ["art", "Art"], ["custom", "Custom"]];
  const HANDLE_TITLES = { c0: "Drag to resize", c1: "Drag to resize", c2: "Drag to resize", c3: "Drag to resize",
    mid: "Drag to move within its plane", lift: "Drag to raise or lower" };

  function slug(value) { return String(value).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "aim"; }

  /* Apartment-frame camera ray through a screen point, from the engine's own
     picking raycaster (the same one every edit pick uses). */
  function apartmentRay(engine, clientX, clientY) {
    try {
      engine.picking.pick([], clientX, clientY);
      const ray = engine.picking.raycaster.ray;
      engine.apartmentRoot.updateMatrixWorld(true);
      const o = engine.apartmentRoot.worldToLocal(ray.origin.clone());
      const p = engine.apartmentRoot.worldToLocal(ray.origin.clone().add(ray.direction));
      const d = [p.x - o.x, p.y - o.y, p.z - o.z], l = Math.hypot(...d) || 1;
      return { origin: [o.x, o.y, o.z], direction: d.map((v) => v / l) };
    } catch (_) { return null; }
  }

  /* Exact mesh pick with its face, the planar region around it and a draft. */
  function pickDraft(engine, clientX, clientY, targets) {
    const mesh = engine?.modes?.getMesh?.();
    if (!mesh) return { error: "The room mesh is still loading. Try again in a moment." };
    const surface = engine.picking.surfaceHit(engine.apartmentRoot, [mesh], clientX, clientY);
    if (!surface) return { error: "No surface under the pointer. Drag to orbit, then click a surface." };
    const hit = engine.picking.pick([mesh], clientX, clientY).find((candidate) => candidate.point && candidate.face
      && candidate.object === surface.object && Number.isInteger(candidate.faceIndex));
    let region = null;
    const geometry = hit?.object?.geometry, position = geometry?.attributes?.position;
    if (hit && position?.array) {
      engine.apartmentRoot.updateMatrixWorld(true); hit.object.updateMatrixWorld(true);
      const matrix = engine.apartmentRoot.matrixWorld.clone().invert().multiply(hit.object.matrixWorld).elements;
      region = N().planarRegion({ positions: position.array, index: geometry.index?.array || null, faceIndex: hit.faceIndex,
        matrix: Array.from(matrix), cacheKey: geometry });
    }
    const name = surface.object?.name || surface.object?.parent?.name || "";
    return { draft: N().draftFromPick({ point: surface.point, normal: surface.normal, region, surfaceName: name, targets }) };
  }

  /* In-room outline and handles. Positions come from the F01 overlay layer's
     projection of Apartment-frame points; drags intersect the camera ray with
     the draft's own plane (the height arrow uses a vertical plane facing the
     camera), so handles never leave the real surface plane. */
  function AptNamedAimHandles({ engine, draft, preview, roomTop, onDraftEdit }) {
    const host = engine?.renderer?.domElement?.parentElement || null;
    const layerRef = useRef(null), dragRef = useRef(null), svgRef = useRef(null), handleRefs = useRef({});
    const draftRef = useRef(draft); draftRef.current = draft;
    useEffect(() => {
      if (!engine?.createUiOverlayLayer || !host) return undefined;
      const controller = new AbortController(); let alive = true;
      engine.createUiOverlayLayer({ host, isCurrent: () => alive, signal: controller.signal }).then((layer) => {
        if (!alive) { layer?.dispose?.(); return; }
        layerRef.current = layer;
      });
      return () => { alive = false; controller.abort(); layerRef.current?.dispose?.(); layerRef.current = null; };
    }, [engine, host]);
    useEffect(() => {
      let frame = 0;
      const place = () => {
        frame = requestAnimationFrame(place);
        const layer = layerRef.current, d = draftRef.current, svg = svgRef.current;
        if (!layer || !d?.pos || !svg) return;
        const points = N().handlePoints(d), project = (p) => layer.project(p);
        const screen = Object.fromEntries(Object.entries(points).map(([key, point]) => [key, project(point)]));
        const outline = d.shape === "surface" ? ["c0", "c1", "c2", "c3"].map((key) => screen[key]) : [];
        const poly = svg.querySelector("[data-draft-outline]"), drop = svg.querySelector("[data-draft-drop]");
        if (poly) {
          const ok = outline.length === 4 && outline.every(Boolean);
          poly.setAttribute("points", ok ? outline.map((p) => `${p.x},${p.y}`).join(" ") : "");
        }
        if (drop) {
          const ok = screen.mid && screen.floor;
          drop.setAttribute("visibility", ok ? "visible" : "hidden");
          if (ok) { drop.setAttribute("x1", screen.mid.x); drop.setAttribute("y1", screen.mid.y); drop.setAttribute("x2", screen.floor.x); drop.setAttribute("y2", screen.floor.y); }
        }
        for (const [key, el] of Object.entries(handleRefs.current)) {
          if (!el) continue;
          const p = screen[key];
          el.style.display = p ? "" : "none";
          if (p) el.style.transform = `translate(${Math.round(p.x)}px, ${Math.round(p.y)}px) translate(-50%, -50%)`;
        }
      };
      frame = requestAnimationFrame(place);
      return () => cancelAnimationFrame(frame);
    }, []);
    // Native listeners: the room's orbit listens on the canvas host, which a
    // React handler (dispatched at the app root) would only reach afterwards.
    const onEditRef = useRef(onDraftEdit); onEditRef.current = onDraftEdit;
    const roomTopRef = useRef(roomTop); roomTopRef.current = roomTop;
    const begin = (key) => (event) => {
      event.preventDefault(); event.stopPropagation();
      const d0 = { ...draftRef.current }, ray = apartmentRay(engine, event.clientX, event.clientY);
      if (!ray) return;
      const plane = N().dragPlane(d0, key, ray.direction);
      const start = N().rayPlane(ray.origin, ray.direction, plane.point, plane.normal);
      if (!start) return;
      try { event.currentTarget.setPointerCapture(event.pointerId); } catch (_) { /* optional */ }
      dragRef.current = { key, pointerId: event.pointerId, d0, plane, start };
      host.classList.add("is-dragging-named-aim");
    };
    const move = (event) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      event.preventDefault(); event.stopPropagation();
      const ray = apartmentRay(engine, event.clientX, event.clientY);
      const point = ray && N().rayPlane(ray.origin, ray.direction, drag.plane.point, drag.plane.normal);
      const patch = point && N().dragHandle(drag.d0, drag.key, drag.start, point, { roomTop: roomTopRef.current });
      if (patch) onEditRef.current(patch);
    };
    const end = (event) => {
      if (dragRef.current?.pointerId !== event.pointerId) return;
      event.stopPropagation(); dragRef.current = null; host?.classList.remove("is-dragging-named-aim");
    };
    const keys = draft?.pos ? Object.keys(N().handlePoints(draft)).filter((key) => key !== "floor") : [];
    useEffect(() => {
      const bound = [];
      for (const key of keys) {
        const el = handleRefs.current[key];
        if (!el) continue;
        const down = begin(key);
        for (const [type, fn] of [["pointerdown", down], ["pointermove", move], ["pointerup", end], ["pointercancel", end]]) {
          el.addEventListener(type, fn); bound.push([el, type, fn]);
        }
      }
      return () => { for (const [el, type, fn] of bound) el.removeEventListener(type, fn); };
    }, [keys.join(",")]);
    if (!host || !draft?.pos) return null;
    return ReactDOM.createPortal(<div className="apt-named-aim-layer" data-apt-edit-ui="named-aim-handles" data-preview={preview ? "true" : undefined}>
      <svg ref={svgRef} className="apt-named-aim-svg" aria-hidden="true">
        <polygon data-draft-outline="1" points="" />
        {draft.horizontal && <line data-draft-drop="1" visibility="hidden" />}
      </svg>
      {keys.map((key) => <div key={key} ref={(el) => { handleRefs.current[key] = el; }} role="presentation"
        className={`apt-named-aim-handle is-${key === "mid" ? "mid" : key === "lift" ? "lift" : "corner"}`}
        data-handle={key} title={HANDLE_TITLES[key]} style={{ display: "none" }}>
        {key === "lift" && <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M6 1.5v9M3.5 4L6 1.5 8.5 4M3.5 8L6 10.5 8.5 8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>}
      </div>)}
    </div>, host);
  }

  function Seg({ label, value, options, onChange }) {
    return <div className="apt-ui-seg apt-named-aim-seg" role="group" aria-label={label}>
      {options.map(([key, text]) => <button key={key} type="button" aria-pressed={value === key} onClick={() => onChange(key)}>{text}</button>)}
    </div>;
  }

  /**
   * Props from HomeApartmentEdit (rail mode): engine, model, homeName, sim,
   * saveStatus, saving, mutate, onSave, roomAt, placing, beginPlacing,
   * endPlacing, registerPick, selectedTargetId, onSelectTarget.
   */
  function AptNamedAimMapper(props) {
    const { engine, model, homeName, saveStatus, saving, mutate, onSave, roomAt, placing, beginPlacing, endPlacing,
      registerPick, selectedTargetId, onSelectTarget } = props;
    const targets = model.targets || [];
    const [draft, setDraft] = useState(null);
    const [message, setMessage] = useState("");
    const [previewId, setPreviewId] = useState(null);
    const [pendingSave, setPendingSave] = useState(null);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [saveNote, setSaveNote] = useState(null);
    const rootRef = useRef(null), nameRef = useRef(null), newRef = useRef(null);
    const roomTop = Math.max(2.4, ...(model.zones || []).map((zone) => +zone.ceiling_height_m || 0));
    const fixtures = useMemo(() => (model.devices || []).filter((device) => device.fixture_kind === window.HomeApartmentAiming?.ENGINEERED_KIND), [model.devices]);

    // The editor routes a placement click here instead of its legacy presets.
    useEffect(() => {
      registerPick((clientX, clientY) => {
        const result = pickDraft(engine, clientX, clientY, targets);
        if (result.error) { setMessage(result.error); return; }
        setDraft((current) => {
          const next = { ...result.draft, mode: "editing" };
          // Pick again keeps what the owner already chose for an existing aim.
          return current && (current.id || current.edited) ? { ...next, id: current.id, name: current.name, category: current.edited ? current.category : next.category, edited: current.edited } : next;
        });
        setMessage(""); setConfirmDelete(false); endPlacing();
        requestAnimationFrame(() => nameRef.current?.focus());
      });
      return () => registerPick(null);
    }, [engine, targets, registerPick, endPlacing]);

    // Clicking an aim in the room (or the Room inspector's Edit) opens it.
    useEffect(() => {
      if (!selectedTargetId || (draft && draft.id === selectedTargetId)) return;
      const target = targets.find((candidate) => candidate.id === selectedTargetId);
      if (target && Array.isArray(target.pos)) { setDraft({ ...N().draftFromTarget(target), mode: "editing" }); setMessage(""); setConfirmDelete(false); }
    }, [selectedTargetId, targets]);

    // Placing without a spot yet ends with the draft when placing is cancelled.
    useEffect(() => {
      if (!placing && draft && !draft.pos) setDraft(null);
    }, [placing]);

    const edit = useCallback((patch) => setDraft((current) => current ? { ...current, ...patch } : current), []);
    const check = draft ? N().validate(draft, targets) : null;
    const reach = useMemo(() => {
      if (!draft?.pos) return [];
      const collision = engine?.modes?.getCollision?.() || engine?.modes?.getMesh?.();
      const trace = collision && engine?.picking?.beamTrace ? (device, solved) => {
        const fwhm = device.spotlight?.optic_profile?.configured_fwhm_deg || 20;
        const result = engine.picking.beamTrace(engine.apartmentRoot, [collision], device.pos, N().basis(solved.delta).normal, fwhm, solved.distance_m);
        return !!result?.blocked;
      } : null;
      return N().reach({ draft, devices: fixtures, aiming: window.HomeApartmentAiming, trace });
    }, [draft?.pos?.join(","), fixtures, engine]);

    // Preview from a light draws its beam onto the draft (presentation only).
    useEffect(() => {
      const overlay = engine?.overlay;
      if (!overlay?.setAimBeams) return undefined;
      const row = previewId && reach.find((candidate) => candidate.id === previewId && candidate.direction);
      const fixture = row && fixtures.find((candidate) => candidate.id === row.id);
      if (!row || !fixture) { overlay.setAimBeams([]); return undefined; }
      const spec = { fixture_id: fixture.id, origin: fixture.pos, preview: { origin: fixture.pos, direction: row.direction,
        distance_m: row.distance_m, full_fwhm_deg: fixture.spotlight?.optic_profile?.configured_fwhm_deg || 20 } };
      overlay.setAimBeams([spec], fixture.id);
      // The host clears beams when its own inputs change; reassert after it.
      const frame = requestAnimationFrame(() => overlay.setAimBeams([spec], fixture.id));
      return () => { cancelAnimationFrame(frame); overlay.setAimBeams([]); };
    }, [previewId, reach, fixtures, engine, model]);

    // Save is the editor's model save, after the target lands in the model.
    useEffect(() => {
      if (!pendingSave || !targets.some((target) => target.id === pendingSave.id && target.updated_at === pendingSave.stamp)) return;
      setPendingSave(null); setSaveNote({ id: pendingSave.id, kind: pendingSave.kind });
      onSave();
    }, [pendingSave, targets, onSave]);
    useEffect(() => {
      if (!pendingSave || pendingSave.kind !== "delete" || targets.some((target) => target.id === pendingSave.id)) return;
      setPendingSave(null); setSaveNote({ id: pendingSave.id, kind: "delete" }); onSave();
    }, [pendingSave, targets, onSave]);

    const save = () => {
      if (!draft?.pos || !check?.ok || saving) return;
      const id = draft.id || `target-${slug(draft.name)}-${Date.now().toString(36)}`, stamp = new Date().toISOString();
      const target = N().toTarget(draft, { id, roomId: roomAt(draft.pos[0], draft.pos[1]), now: stamp });
      mutate((m) => {
        m.targets = m.targets || [];
        const index = m.targets.findIndex((candidate) => candidate.id === id);
        if (index >= 0) m.targets[index] = target; else m.targets.push(target);
      });
      setPendingSave({ id, stamp, kind: draft.id ? "update" : "add" });
      setDraft(null); setPreviewId(null); onSelectTarget(null);
      requestAnimationFrame(() => newRef.current?.focus());
    };
    const remove = () => {
      if (!draft?.id) return;
      const id = draft.id;
      // Lights keep their current aim; only the name and its references go.
      mutate((m) => {
        m.targets = (m.targets || []).filter((target) => target.id !== id);
        for (const device of m.devices || []) if (device?.gimbal?.default_target_id === id) device.gimbal.default_target_id = null;
      });
      setPendingSave({ id, kind: "delete" }); setDraft(null); setConfirmDelete(false); setPreviewId(null); onSelectTarget(null);
    };
    const cancel = () => { setDraft(null); setPreviewId(null); setConfirmDelete(false); setMessage(""); onSelectTarget(null); if (placing) endPlacing(); };

    // Escape: leave placing first (the editor's placement layer), then close
    // the editor with nothing saved.
    window.HomeOverlay.useOverlayLayer({
      key: "apartment-named-aim-draft", active: !!(draft && draft.pos && !placing), rootRef,
      onEscape: () => { cancel(); requestAnimationFrame(() => newRef.current?.focus()); }, initialFocus: "none",
    });

    const status = saveStatus?.state;
    const note = saveNote && (pendingSave ? "Saving…" : saving || status === "saving" ? "Saving…"
      : status === "saved" ? `${saveNote.kind === "delete" ? "Deleted from" : "Saved to"} the ${homeName || "home"} Apartment model. Changes geometry only; nothing is sent to a light.`
        : status === "offline" ? "Saved locally. Home Assistant is offline, so the Apartment model publishes when you save online."
          : status === "conflict" ? "Not published: the model changed elsewhere. Your change is kept here as a local draft."
            : status === "simulation" ? "Simulation cannot save; the live model is untouched." : null);

    if (!draft) {
      const used = (id) => (model.devices || []).filter((device) => device.gimbal?.default_target_id === id).map((device) => device.name);
      return <div ref={rootRef} className="apt-named-aims" data-named-aims="list">
        <section className="apt-ui-section">
          <div className="apt-ui-row"><p className="apt-ui-copy grow">Surfaces lights can be pointed at by name.</p>
            <button ref={newRef} type="button" className="apt-ui-button is-primary" onClick={() => {
              setMessage(""); setSaveNote(null); setDraft({ id: null, pos: null, mode: "placing" }); beginPlacing();
            }}>New</button></div>
          {message && <p className="apt-ui-meta" role="status">{message}</p>}
          {note && <p className="apt-ui-meta" role="status" data-named-aim-save={pendingSave ? "saving" : status}>{note}</p>}
        </section>
        <section className="apt-ui-section" aria-label="Named aims">
          {!targets.length && <p className="apt-ui-copy">No named aims yet.</p>}
          <ul className="apt-ui-list">{targets.map((target) => { const u = used(target.id); const flat = N().isFlat(target.normal || [0, 0, 1]);
            return <li key={target.id}><button type="button" className="apt-ui-list-button" data-named-aim={target.id}
              onClick={() => onSelectTarget(target.id)}>
              <span className="nm">{target.name}</span>
              <span className="vl">{(N().CATEGORIES[target.category]?.label || target.category) + (flat ? " · flat" : " · wall") + (u.length ? " · " + u.join(", ") : "")}</span>
            </button></li>; })}</ul>
        </section>
      </div>;
    }

    if (!draft.pos) return <div ref={rootRef} className="apt-named-aims" data-named-aims="placing">
      <section className="apt-ui-section">
        <div className="apt-ui-row"><h3 className="apt-ui-heading grow">New named aim</h3>
          <button type="button" className="apt-ui-button is-small" onClick={cancel}>Cancel</button></div>
        <p className="apt-ui-copy">Click the surface you want to light: a desk top, a wall, the painting or the floor.</p>
        <p className="apt-ui-meta">Furniture tops and art fit their own size. Dragging still orbits; Esc cancels.</p>
        {message && <p className="apt-ui-meta" role="status">{message}</p>}
      </section>
    </div>;

    const flat = draft.horizontal, size = draft.size_m || [0.6, 0.6];
    const setSize = (i, raw) => { const v = Math.min(8, Math.max(0.05, +raw || 0)); const next = size.slice(); next[i] = Math.round(v * 100) / 100; edit({ size_m: next }); };
    const ok = reach.filter((row) => row.ok);
    return <div ref={rootRef} className="apt-named-aims" data-named-aims="editing"
      data-draft={JSON.stringify({ pos: draft.pos, normal: draft.normal, up: draft.up, size_m: draft.size_m, shape: draft.shape, category: draft.category })}>
      <section className="apt-ui-section">
        <div className="apt-ui-row"><h3 className="apt-ui-heading grow">{placing ? `Move “${draft.name}”` : draft.id ? "Edit named aim" : "New named aim"}</h3></div>
        <label className="apt-setup-field"><span className="apt-ui-meta">Name</span>
          <input ref={nameRef} className="apt-ui-input" aria-label="Named aim name" value={draft.name || ""} placeholder="Desk reading"
            onChange={(event) => edit({ name: event.target.value })} /></label>
        {check.nameError && draft.name !== "" && <p className="apt-ui-meta apt-named-aim-flag" data-check="name">{check.nameError}</p>}
        <div className="apt-ui-row"><span className="apt-ui-meta grow">Surface · {draft.surface || "—"} · {flat ? "flat" : "vertical"}</span>
          <button type="button" className="apt-ui-button is-small" aria-pressed={placing} onClick={() => { if (placing) endPlacing(); else { setDraft({ ...draft, edited: true }); beginPlacing(); } }}>{placing ? "Cancel pick" : "Pick again"}</button></div>
        <div className="apt-setup-field"><span className="apt-ui-meta">Type</span>
          <Seg label="Type" value={draft.category} options={TYPES} onChange={(category) => edit({ category, edited: true })} /></div>
        {check.typeError && <p className="apt-ui-meta apt-named-aim-flag" data-check="type">{check.typeError}</p>}
        <div className="apt-setup-field"><span className="apt-ui-meta">Shape</span>
          <Seg label="Shape" value={draft.shape} options={[["surface", "Area"], ["point", "Point"]]} onChange={(shape) => edit({ shape })} /></div>
        <p className="apt-ui-meta">{draft.shape === "surface" ? `In the room, drag a corner to resize, the center to move${flat ? " and the arrow to raise or lower" : ""}.` : `In the room, drag the point to move it${flat ? " and the arrow to raise or lower" : ""}.`}</p>
        {draft.shape === "surface" && <div className="apt-setup-field"><span className="apt-ui-meta">Size · {flat ? "width × depth" : "width × height"} (m)</span>
          <div className="apt-ui-row">
            <input className="apt-ui-input" type="number" step="0.05" min="0.05" max="8" aria-label="Width" value={size[0]} onChange={(event) => setSize(0, event.target.value)} />
            <span className="apt-ui-meta">×</span>
            <input className="apt-ui-input" type="number" step="0.05" min="0.05" max="8" aria-label={flat ? "Depth" : "Height"} value={size[1]} onChange={(event) => setSize(1, event.target.value)} />
            {draft.fit && <button type="button" className="apt-ui-button is-small" onClick={() => edit({ pos: draft.fit.center.slice(), size_m: draft.fit.size.slice(), up: draft.fit.up.slice() })}>Fit</button>}
          </div></div>}
        <label className="apt-ui-slider"><span className="apt-ui-row"><span className="grow">{flat ? "Height" : "Center height"}</span><span className="apt-ui-value">{(+draft.pos[2]).toFixed(2)} m</span></span>
          <input className="apt-ui-range" type="range" min="0" max={roomTop} step="0.01" aria-label={flat ? "Aim height" : "Aim center height"} value={draft.pos[2]}
            onChange={(event) => edit({ pos: [draft.pos[0], draft.pos[1], +event.target.value] })} /></label>
        <div className="apt-setup-field"><span className="apt-ui-meta">Position · 5 cm steps · {draft.pos.map((v) => (+v).toFixed(2)).join(", ")} m</span>
          <div className="apt-ui-row apt-named-aim-nudges" role="group" aria-label="Nudge 5 cm">
            <button type="button" className="apt-ui-button is-small" aria-label="Nudge left" onClick={() => edit(N().nudge(draft, -1, 0))}>←</button>
            <button type="button" className="apt-ui-button is-small" aria-label="Nudge right" onClick={() => edit(N().nudge(draft, 1, 0))}>→</button>
            <button type="button" className="apt-ui-button is-small" aria-label={flat ? "Nudge away" : "Nudge up"} onClick={() => edit(N().nudge(draft, 0, 1))}>↑</button>
            <button type="button" className="apt-ui-button is-small" aria-label={flat ? "Nudge toward" : "Nudge down"} onClick={() => edit(N().nudge(draft, 0, -1))}>↓</button>
            {flat && draft.shape === "surface" && <button type="button" className="apt-ui-button is-small" aria-label={`Rotate 15°, now ${N().flatRotation(draft)}°`}
              onClick={() => edit(N().rotateFlat(draft, 15))}>{N().flatRotation(draft)}°</button>}
          </div></div>
        <details className="apt-named-aim-exact"><summary className="apt-ui-meta">Exact position</summary>
          <div className="apt-ui-row">{["x", "y", "z"].map((axis, i) => <label key={axis} className="apt-setup-field grow"><span className="apt-ui-meta">{axis} (m)</span>
            <input className="apt-ui-input" type="number" step="0.01" aria-label={`Aim ${axis}`} value={draft.pos[i]}
              onChange={(event) => { const patch = N().exactPosition(draft, i, event.target.value); if (patch) edit(patch); }} /></label>)}</div>
          <p className="apt-ui-meta">Values stay on the aim's plane.</p>
        </details>
      </section>
      <section className="apt-ui-section" aria-label="Reach">
        <h3 className="apt-ui-heading">Reach</h3>
        <p className="apt-ui-copy" data-named-aim-reach="1">{N().reachSentence(reach)}</p>
        {ok.length > 0 && <div className="apt-ui-actions">{ok.map((row) => <button key={row.id} type="button"
          className={`apt-ui-button is-small${previewId === row.id ? " is-proposed" : ""}`} aria-pressed={previewId === row.id}
          onClick={() => setPreviewId((current) => current === row.id ? null : row.id)}>Preview from {row.name}</button>)}</div>}
        <p className="apt-ui-meta">From each light's position and its pan and tilt limits; Preview draws the beam only.</p>
      </section>
      <section className="apt-ui-section">
        <div className="apt-ui-actions">
          <button type="button" className="apt-ui-button is-primary" disabled={!check.ok || !!saving || placing} onClick={save}>{draft.id ? "Save changes" : "Save named aim"}</button>
          <button type="button" className="apt-ui-button" onClick={cancel}>Cancel</button>
          {draft.id && !confirmDelete && <button type="button" className="apt-ui-button apt-named-aim-delete" onClick={() => setConfirmDelete(true)}>Delete</button>}
        </div>
        {confirmDelete && <div className="apt-ui-row" role="group" aria-label={`Delete ${draft.name}`}>
          <span className="apt-ui-meta grow">Delete “{draft.name}”? Lights keep their current aim; only the name goes.</span>
          <button type="button" className="apt-ui-button is-small apt-named-aim-delete" onClick={remove}>Delete aim</button>
          <button type="button" className="apt-ui-button is-small" onClick={() => setConfirmDelete(false)}>Keep</button></div>}
        <p className="apt-ui-meta">Saved to the {homeName || "home"} Apartment model. Changes geometry only; nothing is sent to a light.</p>
        {message && <p className="apt-ui-meta" role="status">{message}</p>}
      </section>
      {!placing && <AptNamedAimHandles engine={engine} draft={draft} preview={!!previewId} roomTop={roomTop} onDraftEdit={edit} />}
    </div>;
  }

  const CSS = `
.apt-named-aim-layer { position: absolute; inset: 0; z-index: 6; pointer-events: none; overflow: hidden; }
.apt-named-aim-svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.apt-named-aim-svg polygon { fill: rgba(69,223,255,.10); stroke: #45dfff; stroke-width: 1.5; }
.apt-named-aim-svg line { stroke: #45dfff; stroke-width: 1.2; stroke-dasharray: 4 4; opacity: .85; }
.apt-named-aim-handle { position: absolute; left: 0; top: 0; pointer-events: auto; touch-action: none; cursor: grab; }
.apt-named-aim-handle::after { content: ""; position: absolute; inset: -12px; }
.apt-named-aim-handle.is-corner { width: 12px; height: 12px; border-radius: 3px; background: #0b0d10; border: 2px solid #45dfff; box-shadow: 0 0 0 3px rgba(69,223,255,.16); }
.apt-named-aim-handle.is-mid { width: 16px; height: 16px; border-radius: 50%; background: rgba(69,223,255,.2); border: 2px solid #45dfff; cursor: move; }
.apt-named-aim-handle.is-lift { width: 22px; height: 22px; border-radius: 50%; background: #0b0d10; border: 1.5px solid #45dfff; color: #45dfff; display: grid; place-items: center; cursor: ns-resize; }
.apt-named-aim-handle:hover { box-shadow: 0 0 0 4px rgba(69,223,255,.3); }
.is-dragging-named-aim .apt-named-aim-handle { cursor: grabbing; }
@media (pointer: coarse) { .apt-named-aim-handle::after { inset: -16px; } .apt-named-aim-handle.is-corner { width: 14px; height: 14px; } }
.apt-named-aims .apt-named-aim-seg { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; gap: 2px; padding: 3px; border: 1px solid var(--apt-border-soft); border-radius: 9px; }
.apt-named-aims .apt-named-aim-seg > button { min-height: 28px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--hg-fg-2); font: inherit; font-size: 12.5px; cursor: pointer; }
.apt-named-aims .apt-named-aim-seg > button[aria-pressed="true"] { border-color: var(--apt-border); background: var(--hg-bg-3); color: var(--hg-fg-0); }
.apt-named-aims .apt-named-aim-flag { color: var(--apt-attn); }
.apt-named-aims .apt-named-aim-delete { color: var(--apt-blocked); border-color: color-mix(in srgb, var(--apt-blocked) 50%, transparent); }
.apt-named-aims .apt-named-aim-nudges .apt-ui-button { min-width: 32px; }
@media (pointer: coarse) { .apt-named-aims .apt-named-aim-seg > button, .apt-named-aims .apt-named-aim-nudges .apt-ui-button { min-height: 44px; min-width: 44px; } }
`;
  function installCss() {
    if (typeof document === "undefined" || document.querySelector("style[data-source='home-apartment-named-aims']")) return;
    const style = document.createElement("style"); style.setAttribute("data-source", "home-apartment-named-aims"); style.textContent = CSS;
    document.head.appendChild(style);
  }
  installCss();

  window.AptNamedAimMapper = AptNamedAimMapper;
})();
