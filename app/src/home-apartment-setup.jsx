/* eslint-disable */
/**
 * home-apartment-setup.jsx — APT-U01 Setup rail and tool inspector chrome.
 *
 * Presentation only. HomeApartmentEdit keeps every editor state, mutation,
 * save and Escape layer; this module draws the left rail over the canvas,
 * the inspector header in the unified right column, and the per-home tool
 * availability. It reads no household data and dispatches no command.
 *
 * While the inspector is mounted, CSS hides the Controls/Chat tabs, the chat
 * panel and the camera dock. Their React state is never touched, so the dock
 * returns exactly as it was when Setup closes.
 */
(function () {
  "use strict";
  const { useEffect } = React;

  const SETUP_TOOLS = Object.freeze([
    Object.freeze({ id: "sockets", label: "Devices and sockets", icon: "sockets" }),
    Object.freeze({ id: "names", label: "Names", icon: "names" }),
    Object.freeze({ id: "targets", label: "Named aims", icon: "targets" }),
    Object.freeze({ id: "zones", label: "Zones", icon: "zones" }),
    Object.freeze({ id: "position", label: "Fixture position", icon: "position" }),
    Object.freeze({ id: "links", label: "Home Assistant", icon: "links" }),
    Object.freeze({ id: "cameras", label: "Cameras and sensors", icon: "cameras" }),
  ]);
  const TOOL_IDS = new Set(SETUP_TOOLS.map((tool) => tool.id));

  /* The closed equipment registration is the only Victoria record the UI may
     write. Tools without a contract stay listed with the reason, instead of
     switching to a different editor layout. */
  const REGISTRATION_UNAVAILABLE = Object.freeze({
    names: "Victoria's fixture names and short labels wait on the registration label schema. Socket and camera labels stay editable in Devices and sockets.",
    targets: "Victoria named aims wait on the Den named-aim store. The Den desk target stays managed by Den and is not changed here.",
    zones: "Victoria has no room-zone record yet, so there is nothing to reshape.",
  });

  function setupToolAvailability({ registration = false } = {}) {
    const result = {};
    for (const tool of SETUP_TOOLS) {
      const reason = registration ? REGISTRATION_UNAVAILABLE[tool.id] || "" : "";
      result[tool.id] = Object.freeze({ available: !reason, reason });
    }
    return Object.freeze(result);
  }

  function setupToolLabel(id) {
    return (SETUP_TOOLS.find((tool) => tool.id === id) || SETUP_TOOLS[0]).label;
  }

  const ICON_PATHS = {
    sockets: "M8 2v3M5 2v3M11 2v3M3.5 5h9v3a4.5 4.5 0 0 1-9 0V5zM8 12.5V15",
    names: "M3 13l.6-2.7L10.8 3a1.4 1.4 0 0 1 2 2l-7.3 7.2L3 13zM9.6 4.2l2 2",
    targets: "M8 1.8v2.4M8 11.8v2.4M1.8 8h2.4M11.8 8h2.4M8 4.2a3.8 3.8 0 1 1 0 7.6 3.8 3.8 0 0 1 0-7.6zM8 7.2a.8.8 0 1 1 0 1.6.8.8 0 0 1 0-1.6z",
    zones: "M2.5 4.5V2.5h2M11.5 2.5h2v2M13.5 11.5v2h-2M4.5 13.5h-2v-2M7 2.5h2M7 13.5h2M2.5 7v2M13.5 7v2",
    position: "M2 10.5l8.5-8.5 3.5 3.5L5.5 14 2 10.5zM5 7.5l1.5 1.5M7 5.5l1.5 1.5M9 3.5L10.5 5",
    links: "M6.6 9.4l2.8-2.8M7.3 4.4l1-1a2.6 2.6 0 0 1 3.7 3.7l-1 1M8.7 11.6l-1 1a2.6 2.6 0 0 1-3.7-3.7l1-1",
    cameras: "M2 5.2h2.4L5.6 3.6h4.8l1.2 1.6H14v7.2H2V5.2zM8 6.6a2.2 2.2 0 1 1 0 4.4 2.2 2.2 0 0 1 0-4.4z",
  };
  function SetupIcon({ name }) {
    return <svg className="apt-setup-icon" width="15" height="15" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d={ICON_PATHS[name] || ICON_PATHS.sockets} fill="none" stroke="currentColor" strokeWidth="1.3"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>;
  }

  function AptSetupRail({ tool, onTool, availability, railRef }) {
    return <nav ref={railRef} className="apt-setup-rail" aria-label="Setup tools" data-apt-edit-ui="rail">
      <div className="apt-setup-rail-group" aria-hidden="true">Setup</div>
      {SETUP_TOOLS.map((entry) => {
        const state = availability?.[entry.id] || { available: true, reason: "" };
        return <button key={entry.id} type="button" aria-pressed={tool === entry.id}
          data-setup-tool={entry.id} data-available={state.available ? "true" : "false"}
          title={state.reason || undefined} onClick={() => onTool(entry.id)}>
          <SetupIcon name={entry.icon} />
          <span className="apt-setup-rail-label">{entry.label}
            {!state.available && <span className="apt-setup-rail-note">Unavailable</span>}</span>
        </button>;
      })}
    </nav>;
  }

  /* The header is the editor toolbar in rail form: the same undo, redo, save
     and done controls, names and handlers, so a rail save writes exactly what
     the legacy toolbar wrote. Their accessible names stay the legacy ones;
     CSS shows them in sentence case. */
  function AptSetupHeader({ tool, homeName, dirty, saveState, sourceLabel, sourceTone, detail, notice, toolbarRef, onDone, children }) {
    const Badge = window.HomeApartmentUI?.Badge;
    const status = saveState === "conflict" ? "conflict" : dirty ? "unsaved" : "saved";
    return <header ref={toolbarRef} tabIndex={-1} className="apt-ui-header apt-setup-header" data-apt-edit-ui="toolbar">
      <div className="apt-ui-header-row">
        <div className="grow">
          <h2 className="apt-ui-title">{setupToolLabel(tool)}</h2>
          <div className="apt-ui-sub">Setup · {homeName || "this home"} · {status === "saved" ? "saved" : status === "conflict" ? "save conflict" : "unsaved changes"}</div>
        </div>
        <button type="button" className="apt-ui-button is-primary apt-setup-done" onClick={onDone}><span className="apt-setup-caps">done</span></button>
      </div>
      <div className="apt-ui-actions apt-setup-badges">
        {Badge && <Badge state={status} />}
      </div>
      {(sourceLabel || detail) && <details className="apt-ui-details apt-setup-source">
        <summary>Source details</summary>
        {Badge && sourceLabel && <Badge state={sourceTone || "modeled"}>{sourceLabel}</Badge>}
        {detail && <p className="apt-ui-meta" data-setup-detail="1">{detail}</p>}
      </details>}
      {notice && <p className="apt-ui-meta apt-setup-proposal" role="note" data-setup-read-only="1">{notice}</p>}
      <div className="apt-ui-actions apt-setup-toolbar">{children}</div>
    </header>;
  }

  function AptSetupUnavailable({ homeName, reason }) {
    return <section className="apt-ui-section" data-setup-unavailable="1">
      <h3 className="apt-ui-heading">Not available for {homeName || "this home"}</h3>
      <p className="apt-ui-copy">{reason}</p>
    </section>;
  }

  /* Every light grouped by room. Name is the existing per-device name field,
     edited live exactly as the device inspector edits it; "Was …", Reset,
     the duplicate flag and the room highlight are presentation only. The
     short Label (proposed `devices[].label`) shows only with the Setup
     proposal flag, pending the owner's approval. */
  function AptSetupNames({ devices = [], zones = [], originalNames = {}, originalLabels = {}, onRename, onLabel, onFocusDevice,
    onHoverDevice, onFindDevice, selectedId, proposal = false }) {
    const Fields = window.AptSetupDevices;
    const lights = devices.filter((device) => device && device.type === "light");
    const groups = zones.map((zone) => [zone, lights.filter((device) => device.room_id === zone.id)]).filter(([, list]) => list.length);
    const rest = lights.filter((device) => !zones.some((zone) => zone.id === device.room_id));
    if (rest.length) groups.push([{ id: "_unassigned", name: "Unassigned" }, rest]);
    if (!lights.length) return <section className="apt-ui-section"><p className="apt-ui-copy">No lights are placed in this home yet.</p></section>;
    const duplicates = Fields ? Fields.duplicateNames(lights) : new Set();
    return <>
      <section className="apt-ui-section">
        <p className="apt-ui-copy">The name shows in lists, the inspector and messages.{proposal ? " The label is the short tag over each light in the room." : ""} Renames stay in Apartment; Home Assistant keeps its own names.</p>
        {proposal && <p className="apt-ui-meta apt-setup-proposal" data-setup-proposal="names">Proposed field · pending the owner's approval: the room label is saved as <code>devices[].label</code>.</p>}
      </section>
      {groups.map(([zone, list]) => <section key={zone.id} className="apt-ui-section" aria-label={`${zone.name} names`}>
        {groups.length > 1 && <h3 className="apt-ui-heading">{zone.name}</h3>}
        {list.map((device) => {
          const original = originalNames[device.id] ?? device.name ?? device.id, originalLabel = originalLabels[device.id] ?? "";
          const changed = (device.name || "") !== (originalNames[device.id] ?? device.name ?? "") || (proposal && (device.label || "") !== originalLabel);
          const duplicate = duplicates.has(device.id), labelError = proposal && Fields ? Fields.labelError(device.label) : "";
          return <div key={device.id} className="apt-setup-name-row" data-setup-name={device.id} data-selected={selectedId === device.id ? "true" : undefined}
            onMouseEnter={() => onHoverDevice?.(device.id)} onMouseLeave={() => onHoverDevice?.(null)}>
            <div className="apt-ui-row">
              <label className="apt-setup-field grow"><span className="apt-ui-meta">{device.ha_entity_id || "Not linked"}</span>
                <input className="apt-ui-input" value={device.name || ""} maxLength={48} aria-label={`Name for ${original}`}
                  onFocus={() => { onFocusDevice?.(device.id); onHoverDevice?.(device.id); }}
                  onChange={(event) => onRename(device.id, event.target.value)}
                  onBlur={(event) => { onHoverDevice?.(null); if (!event.target.value.trim() && originalNames[device.id]) onRename(device.id, originalNames[device.id]); }} /></label>
              {proposal && <label className="apt-setup-field apt-setup-label-field"><span className="apt-ui-meta">Label</span>
                <input className="apt-ui-input" value={device.label || ""} maxLength={Fields?.LABEL_MAX || 10} aria-label={`Room label for ${original}`}
                  onFocus={() => onHoverDevice?.(device.id)} onChange={(event) => onLabel?.(device.id, event.target.value)}
                  onBlur={() => onHoverDevice?.(null)} /></label>}
              <button type="button" className="apt-ui-button is-small is-icon apt-setup-find" aria-label={`Find ${device.name || original} in the room`}
                title="Find in the room" onClick={() => onFindDevice?.(device.id)}>⌖</button>
            </div>
            {(changed || duplicate || labelError) && <div className="apt-ui-row">
              <span className={`apt-ui-meta grow${duplicate || labelError ? " apt-setup-flag" : ""}`} data-name-note={duplicate ? "duplicate" : labelError ? "label" : "changed"}>
                {duplicate ? "Another light has this name." : labelError || `Was “${original}”${proposal && originalLabel ? ` · ${originalLabel}` : ""}`}</span>
              {changed && <button type="button" className="apt-ui-button is-small" aria-label={`Reset ${original}`}
                onClick={() => { onRename(device.id, originalNames[device.id] ?? device.name); if (proposal) onLabel?.(device.id, originalLabel); }}>Reset</button>}
            </div>}
          </div>;
        })}
      </section>)}
    </>;
  }

  /* Calibration moves here from the camera-view HUD. It reuses the existing
     calibration overlay and its persistence; this list only starts it. */
  function AptSetupCameras({ cameras = [], selectedId, onSelect, onCalibrate, calibrateReasonFor }) {
    return <section className="apt-ui-section" aria-label="Cameras">
      <h3 className="apt-ui-heading">Cameras</h3>
      {!cameras.length && <p className="apt-ui-copy">No cameras are placed in this home yet.</p>}
      <ul className="apt-ui-list">{cameras.map((camera) => {
        const calibrated = !!camera.camera?.extrinsics;
        const reason = (calibrateReasonFor && calibrateReasonFor(camera)) || (onCalibrate ? "" : "Calibration is unavailable here.");
        const name = camera.name || camera.id;
        return <li key={camera.id} className="apt-setup-camera-row" data-setup-camera={camera.id}>
          <button type="button" className="apt-ui-list-button" aria-current={selectedId === camera.id ? "true" : undefined}
            onClick={() => onSelect(camera.id)}>
            <span className="nm">{name}</span>
            <span className="vl">{calibrated ? "Calibrated" : "Placed by hand · not calibrated"}</span>
          </button>
          <div className="apt-ui-row">
            <button type="button" className="apt-ui-button is-small" disabled={!!reason}
              aria-label={`Calibrate ${name}`} onClick={() => { if (!reason) onCalibrate(camera); }}>Calibrate</button>
            {reason && <span className="apt-ui-meta grow">{reason}</span>}
          </div>
        </li>;
      })}</ul>
      <p className="apt-ui-meta">Placing sets where a camera sits and what it covers. Calibration matches pixels to the mesh and is stored separately; placing never changes it.</p>
    </section>;
  }

  /* Travel Mode as Home last observed it; the guarded dispatch re-reads it
     from Home Assistant at the moment of output. */
  function useTravelState() {
    const read = () => window.AptSetupLinks?.travelState(window) ?? null;
    const [state, setState] = React.useState(read);
    useEffect(() => {
      const update = () => setState(read());
      window.addEventListener("home-travel-mode-change", update);
      return () => window.removeEventListener("home-travel-mode-change", update);
    }, []);
    return state;
  }

  const LINK_BADGE = { linked: ["current", "Linked"], shared: ["changed", "Shared"], not_linked: ["unavailable", "Not linked"] };

  /* APT-U03, formerly "Fixture links". Matching changes only the link; Flash
     is the only output and is refused unless Home Assistant is connected and
     Travel Mode is known to be off. A registration home lists its sockets and
     fixtures as Not linked with no picker. */
  function AptSetupLinks({ rows = [], registration = false, homeName, siteId, sim, connection, onLink, onFlash, onFrame }) {
    const Badge = window.HomeApartmentUI?.Badge;
    const travel = useTravelState();
    const [confirm, setConfirm] = React.useState(null);
    const [status, setStatus] = React.useState(null);
    const [busy, setBusy] = React.useState(null);
    const offline = !registration && connection !== "online";
    const flash = async (row) => {
      setBusy(row.id); setStatus(null); onFrame?.(row.id);
      const result = await onFlash(row);
      setBusy(null);
      setStatus({ id: row.id, ok: !!result?.ok, text: result?.ok ? `Flashed ${row.name}. Check that the right light blinked.` : result?.reason || "Flash was refused; nothing was sent." });
    };
    return <>
      <section className="apt-ui-section">
        <p className="apt-ui-copy">Match each light in the model to the Home Assistant light that controls it. That's how a slider here reaches the real bulb.</p>
        {registration && <p className="apt-ui-meta" data-setup-links-note="registration">{homeName || "This home"}'s sockets don't have Home Assistant lights yet. Each is matched when its fixture is commissioned; until then their controls only change the model.</p>}
        {offline && <p className="apt-ui-meta" id="apt-setup-flash-global" data-setup-links-note="offline">Home Assistant is offline. You can change matches; Flash needs a connection.</p>}
        {!registration && !offline && !sim && travel !== "off" && <p className="apt-ui-meta" id="apt-setup-flash-global" data-setup-links-note="travel">{travel === "on" ? "Travel Mode is on, so Flash is refused." : "Travel Mode's state is unknown, so Flash is refused."}</p>}
      </section>
      <section className="apt-ui-section" aria-label="Home Assistant links">
        {!rows.length && <p className="apt-ui-copy">No lights are placed in this home yet.</p>}
        <ul className="apt-ui-list apt-setup-links">{rows.map((row) => {
          const [state, label] = LINK_BADGE[row.state] || LINK_BADGE.not_linked;
          const refusal = registration ? "" : window.AptSetupLinks.flashRefusal({ siteId, sim, connection, entityId: row.entity, travel });
          // Offline and Travel Mode are explained once above; a row only
          // explains what is particular to it (no link, simulation).
          const global = offline || (!sim && travel !== "off");
          return <li key={row.id} className="apt-setup-link" data-setup-link={row.id} data-link-state={row.state}>
            <div className="apt-ui-row">
              <span className="grow"><span className="apt-setup-link-name">{row.name}</span>{row.room && <span className="apt-ui-meta apt-setup-link-room">{row.room}</span>}</span>
              {Badge ? <Badge state={state}>{label}</Badge> : <span>{label}</span>}
            </div>
            {!registration && <div className="apt-ui-row">
              <select className="apt-ui-input grow" value={row.entity || ""} aria-label={`Home Assistant light for ${row.name}`}
                onChange={(event) => {
                  const next = event.target.value || null;
                  setStatus(null);
                  if (!next && row.entity) { setConfirm(row.id); return; }
                  setConfirm(null); onLink(row.id, next);
                }}>
                <option value="">Not linked</option>
                {row.options.map((option) => <option key={option.entity_id} value={option.entity_id}>
                  {option.missing ? `${option.entity_id} · not in the registry` : `${option.name} · ${option.entity_id}`}</option>)}
              </select>
              <button type="button" className="apt-ui-button is-small" disabled={!!refusal || busy === row.id}
                aria-label={`Flash ${row.name}`} aria-describedby={refusal ? (global ? "apt-setup-flash-global" : `apt-flash-why-${row.id}`) : undefined}
                onClick={() => flash(row)}>{busy === row.id ? "Flashing…" : "Flash"}</button>
            </div>}
            {refusal && !global && <p className="apt-ui-meta" id={`apt-flash-why-${row.id}`}>{refusal}</p>}
            {row.sharedWith.length > 0 && <p className="apt-ui-meta">Also controls {row.sharedWith.join(", ")}.</p>}
            {confirm === row.id && <div className="apt-ui-row" role="group" aria-label={`Remove the link for ${row.name}`}>
              <span className="apt-ui-meta grow">Remove the link? Position and calibration are kept.</span>
              <button type="button" className="apt-ui-button is-small" onClick={() => { setConfirm(null); onLink(row.id, null); }}>Remove link</button>
              <button type="button" className="apt-ui-button is-small" onClick={() => setConfirm(null)}>Keep</button>
            </div>}
            {status?.id === row.id && <p className="apt-ui-meta" role="status" data-flash-result={status.ok ? "sent" : "refused"}>{status.text}</p>}
          </li>;
        })}</ul>
        <p className="apt-ui-meta">{registration ? "Nothing here sends output." : "Changing a match moves no geometry or tape measurement and sends nothing. Flash is the only output and respects Travel Mode."}</p>
      </section>
    </>;
  }

  /* Portal into the unified right column. data-apt-setup-inspector lets CSS
     hide the other column content while Setup is open. */
  function AptSetupInspector({ host, children }) {
    if (!host) return null;
    return ReactDOM.createPortal(<section className="apt-ui-inspector apt-setup-inspector"
      data-apt-setup-inspector="1" aria-label="Setup">{children}</section>, host);
  }

  const SETUP_CSS = `
.apt-home-shell:has([data-apt-setup-inspector]) .apt-home-tabs { display: none !important; }
.apt-home-shell:has([data-apt-setup-inspector]) .apt-home-camera { display: none !important; }
.apt-home-shell:has([data-apt-setup-inspector]) .apt-home-chat { display: none !important; }
.apt-home-shell .apt-home-controls:has([data-apt-setup-inspector]) > :not([data-apt-setup-inspector]) { display: none !important; }
.apt-home-shell .apt-home-controls:has([data-apt-setup-inspector]) { grid-column: 1 / -1 !important; }
/* The rail floats on the room's dark glass in both themes, like the room
   toolbar, so its text uses fixed light-on-glass colors. */
.apt-setup-rail { position: absolute; left: 12px; top: 12px; bottom: 76px; width: 208px; z-index: 5; display: flex; flex-direction: column; gap: 2px;
  padding: 8px; overflow: auto; overscroll-behavior: contain; border: 1px solid rgba(255,255,255,.1); border-radius: 12px;
  background: var(--apt-glass, rgba(8,9,11,.78)); -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px); color: #ededed;
  font: 400 13px/1.4 'Geist', ui-sans-serif, system-ui, sans-serif; }
.apt-setup-rail-group { padding: 8px 8px 4px; font-size: 12px; color: #a1a1a1; }
.apt-setup-rail button { display: flex; align-items: center; gap: 9px; min-height: 36px; padding: 0 10px; border: 1px solid transparent; border-radius: 8px;
  background: transparent; color: #ededed; font: inherit; text-align: left; cursor: pointer; }
.apt-setup-rail button:hover { background: rgba(255,255,255,.05); }
.apt-setup-rail button[aria-pressed="true"] { background: rgba(255,255,255,.09); border-color: rgba(255,255,255,.14); color: #fff; }
.apt-setup-rail button[data-available="false"] { color: #b4b4b4; }
.apt-setup-rail-label { flex: 1 1 auto; min-width: 0; padding: 4px 0; }
.apt-setup-rail-note { display: block; font-size: 11.5px; line-height: 1.3; color: #a1a1a1; }
.apt-setup-icon { flex: 0 0 auto; color: #a1a1a1; }
.apt-setup-rail button[aria-pressed="true"] .apt-setup-icon { color: #ededed; }
@media (pointer: coarse) { .apt-setup-rail button { min-height: 44px; } .apt-setup-inspector :is(button, select, input:not([type="checkbox"]):not([type="range"])) { min-height: 44px; } }
@container apartment-home (width < 900px) { .apt-setup-rail { bottom: auto; max-height: 46%; width: 180px; } }
.apt-setup-header { outline: none; }
.apt-setup-header .apt-setup-toolbar:empty { display: none; }
.apt-setup-caps { display: inline-block; }
.apt-setup-caps::first-letter { text-transform: uppercase; }
.apt-setup-body [data-apt-edit-ui] :is([style*="--ed-heading"], [role="status"], label:not(.apt-setup-field) > span:first-child)::first-letter { text-transform: uppercase !important; }
.apt-setup-field { display: grid; gap: 4px; min-width: 0; }
.apt-setup-field > .apt-ui-meta { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.apt-setup-field[data-selected="true"] .apt-ui-input { border-color: var(--hg-border-strong); }
.apt-setup-inspector .apt-ui-input { width: 100%; min-height: 32px; padding: 5px 8px; border: 1px solid var(--apt-border, var(--hg-border)); border-radius: 7px;
  background: var(--hg-bg-0); color: var(--hg-fg-0); font: 400 13px/1.4 'Geist', ui-sans-serif, sans-serif; }
.apt-setup-camera-row { display: grid; gap: 4px; padding-bottom: 6px; }
.apt-setup-name-row { display: grid; gap: 4px; }
.apt-setup-name-row .apt-setup-label-field { width: 92px; flex: 0 0 92px; }
.apt-setup-name-row .apt-setup-find { align-self: end; }
.apt-setup-flag { color: var(--apt-attn) !important; }
.apt-setup-proposal { padding: 6px 8px; border: 1px dashed var(--apt-attn); border-radius: 7px; color: var(--hg-fg-2); }
.apt-setup-links { display: grid; gap: 4px; }
.apt-setup-link { display: grid; gap: 6px; padding: 10px 0; border-bottom: 1px solid var(--apt-border-soft, var(--hg-border-soft)); }
.apt-setup-link:last-child { border-bottom: 0; }
.apt-setup-link-name { display: block; font-size: 13px; font-weight: 520; color: var(--hg-fg-0); }
.apt-setup-link-room { display: block; }
.apt-setup-body { display: grid; gap: 0; padding: 4px 16px 16px; }
.apt-setup-body > [data-apt-edit-ui] { position: static !important; width: auto !important; max-width: none !important; padding: 0 !important;
  margin: 0 !important; border: 0 !important; background: transparent !important; backdrop-filter: none !important; overflow: visible !important;
  font-family: 'Geist', ui-sans-serif, sans-serif !important; color: var(--apt-text, var(--hg-fg-1)); }
.apt-setup-body > [data-apt-edit-ui="inspector"] { border-top: 1px solid var(--apt-border-soft, var(--hg-border-soft)) !important; margin-top: 12px !important; padding-top: 4px !important; }
.apt-setup-body > [data-apt-edit-ui="inspector"]:empty { display: none; }
.apt-setup-body [data-apt-edit-ui] :is(div, span, label, button, input, select, summary, fieldset, p, option) {
  font-size: max(12px, 1em) !important; letter-spacing: normal !important; text-transform: none !important; line-height: 1.45 !important; font-family: inherit !important; }
.apt-setup-body [data-apt-edit-ui] :is(input, select):not(.apt-ui-input) { font-family: 'Geist Mono', ui-monospace, monospace !important; min-height: 32px; border-radius: 7px; }
.apt-setup-body [data-apt-edit-ui] [data-setup-heading] { font-size: 12.5px !important; font-weight: 560; color: var(--hg-fg-2) !important; margin: 16px 0 6px !important; }
.apt-setup-body [data-apt-edit-ui] button { border-radius: 7px; }
.apt-setup-source { margin: 0; }
.apt-setup-source > summary { color: var(--apt-muted); font-size: 12px; }
@media (pointer: coarse) { .apt-setup-body [data-apt-edit-ui] :is(button, input:not([type="checkbox"]):not([type="radio"]), select) { min-height: 44px!important; } }
`;
  function installSetupCss() {
    if (typeof document === "undefined" || document.querySelector("style[data-source='home-apartment-setup']")) return;
    const style = document.createElement("style");
    style.setAttribute("data-source", "home-apartment-setup");
    style.textContent = SETUP_CSS;
    document.head.appendChild(style);
  }

  function useSetupCss() { useEffect(() => { installSetupCss(); }, []); }

  window.AptSetup = Object.freeze({
    TOOLS: SETUP_TOOLS, isTool: (id) => TOOL_IDS.has(id), availability: setupToolAvailability, label: setupToolLabel,
    Rail: AptSetupRail, Header: AptSetupHeader, Inspector: AptSetupInspector, Unavailable: AptSetupUnavailable,
    Names: AptSetupNames, Cameras: AptSetupCameras, Links: AptSetupLinks, useCss: useSetupCss, installCss: installSetupCss,
  });
})();
