/* Echo lights in the shared Light inspector, and the room's Quick controls.
 * Presentation only: the host passes Home Assistant state and its existing
 * guarded service call, which owns site, currentness and Travel Mode checks. */
(function () {
  "use strict";
  const { useState, useRef, useEffect } = React;

  function AptEchoIcon({ name }) {
    const paths = { back: "M10 3L5 8l5 5", close: "M4 4l8 8M12 4l-8 8" };
    return <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || ""} /></svg>;
  }

  /* Home Assistant gets one call per settled value, not one per input event. */
  function AptEchoSlider({ label, ariaLabel, value, min, max, step = 1, suffix, disabled, onCommit }) {
    const [draft, setDraft] = useState(null);
    const draftRef = useRef(null);
    useEffect(() => { draftRef.current = null; setDraft(null); }, [value]);
    const commit = () => {
      const next = draftRef.current;
      draftRef.current = null;
      setDraft(null);
      // The shown value returns to Home Assistant's reported state until readback.
      if (next != null && next !== value && !disabled) onCommit(next);
    };
    const shown = draft ?? value ?? min;
    return <label className="apt-ui-slider">
      <span className="apt-ui-row"><span className="grow">{label}</span><span className="apt-ui-value">{value == null && draft == null ? "—" : `${Math.round(shown)}${suffix}`}</span></span>
      <input className={`apt-ui-range${suffix.trim() === "K" ? " is-temperature" : ""}`} type="range" aria-label={ariaLabel || label}
        min={min} max={max} step={step} value={shown} disabled={disabled}
        onChange={event => { draftRef.current = Number(event.target.value); setDraft(draftRef.current); }}
        onPointerUp={commit} onKeyUp={commit} onTouchEnd={commit} onBlur={commit} />
    </label>;
  }

  function haBadge(status) {
    const Badge = window.HomeApartmentUI?.Badge;
    const [state, text] = { connected: ["connected", "Home Assistant connected"], connecting: ["loading", "Connecting"],
      simulation: ["simulated", "Simulation"], offline: ["disconnected", "Home Assistant offline"] }[status] || ["unavailable", "Home Assistant unavailable"];
    return Badge ? <Badge state={state}>{text}</Badge> : <span className="apt-ui-badge" data-state={state}>{text}</span>;
  }

  function AptEchoLightInspector({ homeName, device, state, roomName, connection, sim, engineered = false,
    onService, onBack, onAim, aimDisabledReason = "", onSetup, canSetup = false }) {
    const [tab, setTab] = useState("light");
    const [pending, setPending] = useState(false);
    useEffect(() => { setTab("light"); }, [device?.id]);
    const Q = window.HomeApartmentQuick;
    if (!device || !Q) return null;
    const entityId = device.ha_entity_id || null, domain = entityId ? entityId.split(".")[0] : "";
    const name = device.name || device.label || device.id;
    const block = Q.controlBlock({ connection, sim, state, entityId });
    const caps = Q.lightCapabilities(entityId, state);
    const on = state?.state === "on";
    const summary = entityId ? Q.lightSummary(entityId, state) : "Not linked";
    const disabled = block.disabled || pending;
    const Badge = window.HomeApartmentUI?.Badge;
    // Nothing is queued: a blocked control never reaches the service path.
    const send = async (service, data = {}) => {
      if (block.disabled || pending || !entityId || typeof onService !== "function") return;
      setPending(true);
      try { await onService(domain, service, { entity_id: entityId, ...data }); }
      finally { setPending(false); }
    };
    const kelvin = state?.attributes?.color_temp_kelvin;
    const light = <section className="apt-ui-section" id="apt-ui-echo-light" role="tabpanel" aria-label="Light controls">
      {block.disabled && <div className="apt-ui-callout" role="status" data-apt-echo-blocked={block.status}>
        <span className="apt-ui-copy">{block.reason}</span>
        {block.detail && <span className="apt-ui-meta" data-apt-echo-last-reported="1">{block.detail}{entityId && state ? ` · ${summary}` : ""}</span>}
      </div>}
      <section className="apt-ui-channel" aria-label={domain === "switch" ? "Power" : "Current light"}>
        <div className="apt-ui-channel-head"><div className="apt-ui-grow">
          <div className="apt-ui-channel-name">{domain === "switch" ? "Power" : "Current light"}</div>
          <div className="apt-ui-channel-desc apt-ui-mono" data-apt-echo-entity="1">{entityId || "Not linked"}</div></div>
          <label className="apt-ui-switch"><input type="checkbox" aria-label={`${name} power`} checked={on} disabled={disabled}
            onChange={event => send(event.target.checked ? "turn_on" : "turn_off")} /><span>{on ? "On" : "Off"}</span></label></div>
        {caps.brightness && <AptEchoSlider label="Brightness" ariaLabel={`${name} brightness`} min={1} max={100} suffix="%"
          value={on ? Q.brightnessPct(state) : null} disabled={disabled || !on}
          onCommit={value => send("turn_on", { brightness_pct: value })} />}
        {caps.colorTemp && <AptEchoSlider label="Color temp" ariaLabel={`${name} color temperature`} min={caps.colorTemp.min} max={caps.colorTemp.max}
          step={50} suffix=" K" value={on && state?.attributes?.color_mode === "color_temp" && Number.isFinite(kelvin) ? kelvin : null}
          disabled={disabled || !on} onCommit={value => send("turn_on", { color_temp_kelvin: value })} />}
      </section>
      <p className="apt-ui-meta" data-apt-echo-calls="1">{entityId ? `In Home this calls ${domain}.turn_on for ${entityId}.`
        : "Not linked to a Home Assistant light. Match it in Setup › Home Assistant."}</p>
    </section>;
    return <aside className="apt-ui-inspector" data-apt-echo-light-inspector="1" aria-label="Echo light inspector">
      <header className="apt-ui-header"><div className="apt-ui-header-row">
        <button type="button" className="apt-ui-button is-icon" aria-label="Back to room" onClick={onBack}><AptEchoIcon name="back" /></button>
        <div className="apt-ui-grow"><h2 className="apt-ui-title">{name}</h2>
          <div className="apt-ui-sub">{roomName || homeName} · {summary}</div></div>
        <button type="button" className="apt-ui-button is-icon" aria-label="Close light" onClick={onBack}><AptEchoIcon name="close" /></button></div>
        <div className="apt-ui-actions">{haBadge(block.status)}
          {engineered && (Badge ? <Badge state="simulated">Engineered · simulated preview</Badge>
            : <span className="apt-ui-badge" data-state="simulated">Engineered · simulated preview</span>)}</div>
        <div className="apt-ui-seg is-full" role="tablist" aria-label="Light sections">{[["light", "Light"], ["aim", "Aim"], ["setup", "Setup"]].map(([key, label]) =>
          <button key={key} type="button" role="tab" aria-selected={tab === key} aria-controls={`apt-ui-echo-${key}`} onClick={() => setTab(key)}>{label}</button>)}
        </div>
      </header>
      {tab === "light" && light}
      {tab === "aim" && <section className="apt-ui-section" id="apt-ui-echo-aim" role="tabpanel" aria-label="Light aim">
        {engineered ? <>
          <h3 className="apt-ui-heading">Engineered spotlight</h3>
          <p className="apt-ui-copy">This mount also carries a simulated Engineered profile. Auto, Current, Engineered and Both stay in the engineered aim panel; its execution remains blocked.</p>
          <button type="button" className="apt-ui-button" disabled={!!aimDisabledReason} title={aimDisabledReason || undefined} onClick={onAim}>Open engineered aim</button>
          {aimDisabledReason && <p className="apt-ui-meta">{aimDisabledReason}</p>}
        </> : <p className="apt-ui-copy">This light isn't aimable. Its Home Assistant controls are on Light.</p>}
      </section>}
      {tab === "setup" && <section className="apt-ui-section" id="apt-ui-echo-setup" role="tabpanel" aria-label="Light setup">
        <h3 className="apt-ui-heading">Identity</h3>
        <div className="apt-ui-row"><span className="apt-ui-meta grow">Device</span><span className="apt-ui-value">{device.id}</span></div>
        <div className="apt-ui-row"><span className="apt-ui-meta grow">Home Assistant</span><span className="apt-ui-value">{entityId || "Not linked"}</span></div>
        <button type="button" className="apt-ui-button" disabled={!canSetup} onClick={onSetup}>Open Setup</button>
      </section>}
    </aside>;
  }

  /* Quick lives in the room view. Echo chips are the home's own HA scenes and
   * scripts; the Den has no presets yet, so it shows none. */
  function AptQuickControls({ chips = [], block = { all: "", energizing: "" }, busy = false, onAll, onChip, modeledNote = "", children }) {
    const runs = chips.some(chip => chip.runs);
    const reason = block.all || block.energizing;
    return <section className="apt-ui-section" aria-label="Quick controls" data-apt-quick="1">
      <div className="apt-ui-row"><h3 className="apt-ui-heading grow">Quick</h3>
        {[true, false].map(on => <button type="button" key={String(on)} className="apt-ui-button is-small"
          disabled={busy || !!block.all || on && !!block.energizing} title={(on ? block.all || block.energizing : block.all) || undefined}
          onClick={() => onAll?.(on)}>All {on ? "on" : "off"}</button>)}</div>
      {chips.length > 0 && <div className="apt-ui-actions" role="group" aria-label="Scenes and scripts">{chips.map(chip =>
        <button type="button" key={chip.entity_id} className="apt-ui-button is-small" data-apt-quick-chip={chip.entity_id}
          aria-label={`${chip.runs ? "Run" : "Apply"} ${chip.name}`} aria-pressed={chip.runs ? chip.active : undefined}
          disabled={busy || !!reason || chip.unavailable} title={reason || (chip.unavailable ? "Unavailable in Home Assistant" : undefined)}
          onClick={() => onChip?.(chip.entity_id)}>{chip.runs && <span aria-hidden="true">▶</span>}{chip.name}</button>)}</div>}
      {runs && <p className="apt-ui-meta">▶ runs a Home Assistant script once.</p>}
      {reason && <p className="apt-ui-meta" data-apt-quick-reason="1">{reason}</p>}
      {modeledNote && <p className="apt-ui-meta">{modeledNote}</p>}
      {children}
    </section>;
  }

  window.AptEchoLightInspector = AptEchoLightInspector;
  window.AptQuickControls = AptQuickControls;
})();
