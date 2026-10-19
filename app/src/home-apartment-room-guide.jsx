/* Room guide for the unified Apartment toolbar: Layers rows, legend, help and
 * About. Presentation only. Owners supply every fact and every setter; this
 * module reads no household data, starts no session and sends no command.
 *
 * Keys: KEY_MAP lists what the room actually binds. The room handler in
 * home-apartment.jsx owns every "room" key, HomeOverlay owns Escape and this
 * module owns "?". tools/test-apartment-room-guide.cjs compares KEY_MAP with
 * the bound handler in both directions, so help can only list working keys.
 *
 * Layers: a row is enabled only when its owner passes a seam ({ value, set }).
 * Fixture visibility has exactly one seam, the room's virtual spotlight
 * setter; Den's desk-preview withdrawal binds to that same setter. Renderer
 * seams for pools, beams, labels and cameras belong to Repair and arrive as
 * more seams. Choices owned here live for this page session only, shared by
 * every home; cross-home persistence waits for the shared-preference
 * agreement (APT-F05, #281). */
(function () {
  "use strict";

  const { useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } = React;

  const KEY_MAP = Object.freeze([
    { id: "orbit", owner: "room", keys: ["ArrowLeft", "ArrowRight"], label: "← →", action: "Orbit the room one step" },
    { id: "tilt", owner: "room", keys: ["ArrowUp", "ArrowDown"], label: "↑ ↓", action: "Tilt the view" },
    { id: "zoom", owner: "room", keys: ["+", "=", "-"], label: "+ −", action: "Zoom in or out" },
    { id: "home", owner: "room", keys: ["h", "Home"], label: "H / Home", action: "Return to the starting view" },
    { id: "cycle", owner: "room", keys: ["n", "p"], label: "N / P", action: "Select the next or previous light or device" },
    { id: "aim-mode", owner: "room", keys: ["a", "z"], label: "A / Z", homes: ["victoria"],
      action: "Spot aiming or radial zones for the selected light", when: "With a modeled light selected" },
    { id: "escape", owner: "overlay", keys: ["Escape"], label: "Esc",
      action: "Cancel the current step, then close the open card or clear the selection" },
    { id: "help", owner: "guide", keys: ["?"], label: "?", action: "Show these gestures and keys" },
  ].map(Object.freeze));

  /* Pointer input in home-3d/rig.js and the room's pick and aim handlers.
   * Pinch and pan are not bound, so they are not listed. */
  const GESTURES = Object.freeze([
    { id: "drag-orbit", label: "Drag empty space", action: "Orbit the room; one finger on touch" },
    { id: "wheel-zoom", label: "Scroll wheel", action: "Zoom" },
    { id: "select", label: "Click a light", action: "Select it and open its controls" },
    { id: "aim-handle", label: "Drag the aim handle", homes: ["victoria"],
      action: "Propose where the selected modeled light points; nothing physical moves" },
  ].map(Object.freeze));

  /* Each colour is the renderer's own constant in `source`; the test checks it. */
  const LEGEND = Object.freeze([
    { id: "light-on", homes: ["echo"], color: "#ffe2a8", line: "solid", label: "Light on now",
      detail: "Glow in its colour temperature", source: "home-3d/markers.js" },
    { id: "light-off", homes: ["echo"], color: "#444a55", line: "solid", label: "Off",
      detail: "Dark marker, no glow", source: "home-3d/markers.js" },
    { id: "preview-beam", homes: ["echo"], color: "#45dfff", line: "solid", label: "Preview or simulated beam",
      detail: "Not the light's reported state", source: "home-3d/markers.js" },
    { id: "stale-beam", homes: ["echo"], color: "#a6adb7", line: "solid", label: "State stale or unknown",
      detail: "Shown without a current report", source: "home-3d/markers.js" },
    { id: "modeled-light", homes: ["victoria"], color: "#ffdfab", line: "solid", label: "Modeled light",
      detail: "Where light lands, modeled from the fixture, not measured", source: "home-3d/equipment-virtual-fixtures.mjs" },
    { id: "aim-handle", homes: ["victoria"], when: "selected", color: "#b8dcff", line: "solid", label: "Aim handle",
      detail: "Drag it to propose an aim for the selected light", source: "home-3d/equipment-virtual-fixtures.mjs" },
    { id: "body-joints", homes: ["victoria"], when: "perception", color: "#76dce7", line: "dotted", label: "Body joints",
      detail: "Approximate room placement from an analyzed frame", source: "home-3d/apartment-perception-layer.mjs" },
  ].map(Object.freeze));

  const forHome = (homeId) => (item) => !item.homes || item.homes.includes(homeId);

  function helpRows(homeId) {
    return { gestures: GESTURES.filter(forHome(homeId)), keys: KEY_MAP.filter(forHome(homeId)) };
  }

  function legendItems({ homeId, selected = false, hasPerception = false } = {}) {
    return LEGEND.filter(forHome(homeId)).filter((item) =>
      item.when === "selected" ? selected : item.when === "perception" ? hasPerception : true);
  }

  /* Session-only choices owned here, shared across homes for this page. */
  let choices = Object.freeze({ legend: false });
  const subscribers = new Set();
  function setChoice(key, value) {
    if (!Object.prototype.hasOwnProperty.call(choices, key) || choices[key] === value) return;
    choices = Object.freeze({ ...choices, [key]: value });
    subscribers.forEach((notify) => notify());
  }
  const subscribe = (notify) => { subscribers.add(notify); return () => subscribers.delete(notify); };
  const readChoices = () => choices;
  const useChoices = () => useSyncExternalStore(subscribe, readChoices, readChoices);

  const LEGEND_MIN_WIDTH = 1180;
  function useWideEnough() {
    const query = `(min-width: ${LEGEND_MIN_WIDTH}px)`;
    const [wide, setWide] = useState(() => !!window.matchMedia?.(query).matches);
    useEffect(() => {
      const list = window.matchMedia?.(query);
      if (!list) return undefined;
      const changed = () => setWide(list.matches);
      changed(); list.addEventListener("change", changed);
      return () => list.removeEventListener("change", changed);
    }, [query]);
    return wide;
  }

  const NO_SEAM = "Not separable in this view yet";

  /* Rows are listed whether or not they work; an unavailable row says why. */
  function layerGroups({ homeId, seams = {}, hasPerception = false, fixtureCount = 0, wide = true, legend = false } = {}) {
    const bound = (id, row, reason) => {
      const seam = seams[id];
      return seam && typeof seam.set === "function"
        ? { id, ...row, available: true, value: seam.value, set: seam.set }
        : { id, ...row, available: false, reason };
    };
    const victoria = homeId === "victoria";
    const fixtures = victoria
      ? bound("fixtures", { label: "Virtual spotlights", ariaLabel: "Show virtual spotlights layer", detail: "modeled" },
        "The modeled fixtures are not loaded for this scene")
      : { id: "fixtures", label: "Light markers", available: false, reason: "Mapped lights follow the current scene" };
    const body = !victoria || !hasPerception ? [{ id: "body", label: "Body layers", available: false, reason: "Need pose data" }] : [
      { id: "body-observed", label: "Observed joints", detail: "solid", available: false, reason: "Shown with the analyzed frame in Camera · JEV" },
      { id: "body-inferred", label: "Inferred completion", detail: "dashed", available: false, reason: "Shown with the analyzed frame in Camera · JEV" },
      { id: "body-volume", label: "Nominal volume", detail: "not measured", available: false, reason: "Needs a body-shape record" },
      { id: "body-uncertainty", label: "Uncertainty", available: false, reason: "Needs a body-shape record" },
      { id: "frame-pose", label: "2D pose on frame", available: false, reason: "Shown with the analyzed frame in Camera · JEV" },
      { id: "follow", label: "Follow", available: false, reason: "Needs continuous tracking · planned" },
    ];
    return [
      { id: "guide", title: "Guide", rows: [wide
        ? { id: "legend", label: "Legend", detail: "what the room's marks mean", available: true, value: legend, set: (value) => setChoice("legend", value) }
        : { id: "legend", label: "Legend", available: false, reason: `Shown in windows ${LEGEND_MIN_WIDTH} px and wider; Help lists the marks` }] },
      { id: "light", title: "Light", rows: [
        fixtures,
        bound("pools", { label: "Light pools", detail: "where light lands" },
          victoria ? "Drawn with each modeled light; hide them with Virtual spotlights" : "Drawn with each light marker"),
        bound("beams", { label: "Beams", control: "segment",
          options: [["selected", "Selected beam"], ["all", "All beams"], ["off", "No beams"]] }, NO_SEAM),
        bound("named-aims", { label: "Named aims always labeled", detail: "else while aiming" }, "Named aims are planned"),
        bound("labels", { label: "All fixture labels", detail: fixtureCount > 6 ? "else on hover" : undefined }, NO_SEAM),
        bound("cameras", { label: "Camera positions" }, NO_SEAM),
      ] },
      { id: "body", title: !victoria || !hasPerception ? "Body · not available here" : "Body · approximate room placement", rows: body },
      { id: "shadows", title: "Shadows and occlusion · planned", rows: [
        { id: "beam-blocking", label: "Beam blocking by furniture", available: false, reason: "Planned" },
        { id: "furniture-shadows", label: "Furniture shadows", available: false, reason: "Planned" },
        { id: "body-shadows", label: "Body shadows", available: false, reason: "Planned" },
      ] },
    ];
  }

  function LayerRow({ row }) {
    const reasonId = useId();
    if (row.control === "segment") {
      return <div className="apt-room-guide-row" data-apt-layer={row.id} data-available={row.available ? "1" : "0"}>
        <span className="apt-ui-copy">{row.label}</span>
        <div className="apt-ui-seg" role="group" aria-label={row.label} aria-describedby={row.available ? undefined : reasonId}>
          {row.options.map(([value, label]) => <button type="button" key={value} disabled={!row.available}
            aria-pressed={row.available ? row.value === value : false} onClick={() => row.set(value)}>{label}</button>)}
        </div>
        {!row.available && <span id={reasonId} className="apt-ui-meta">{row.reason}</span>}
      </div>;
    }
    return <div className="apt-room-guide-row" data-apt-layer={row.id} data-available={row.available ? "1" : "0"}>
      <label className="apt-ui-row apt-ui-copy">
        <input type="checkbox" disabled={!row.available} checked={row.available ? !!row.value : false}
          aria-label={row.ariaLabel} aria-describedby={row.available ? undefined : reasonId}
          onChange={(event) => row.set(event.target.checked)} />
        <span>{row.label}{row.detail && <span className="apt-ui-meta"> · {row.detail}</span>}</span>
      </label>
      {!row.available && <span id={reasonId} className="apt-ui-meta">{row.reason}</span>}
    </div>;
  }

  /* Rendered inside the toolbar's existing "Room layers" dialog. */
  function LayersPanel({ homeId, seams, hasPerception, fixtureCount }) {
    const { legend } = useChoices();
    const wide = useWideEnough();
    return <div className="apt-room-guide-layers">
      {layerGroups({ homeId, seams, hasPerception, fixtureCount, wide, legend }).map((group) =>
        <section key={group.id} aria-label={group.title}>
          <h3 className="apt-ui-heading">{group.title}</h3>
          {group.rows.map((row) => <LayerRow key={row.id} row={row} />)}
        </section>)}
    </div>;
  }

  function Swatch({ item }) {
    return <i className="apt-room-guide-swatch" aria-hidden="true" style={{ borderTopColor: item.color, borderTopStyle: item.line }} />;
  }

  /* Shown on demand from Layers, in wide windows, while nothing else is open
   * over the toolbar. It sits at the top left of the room stage. */
  function Legend({ homeId, selected, hasPerception, suppressed }) {
    const { legend } = useChoices();
    const wide = useWideEnough();
    const anchorRef = useRef(null);
    const [stage, setStage] = useState(null);
    useLayoutEffect(() => { setStage(anchorRef.current?.parentElement?.parentElement || null); }, []);
    const items = legendItems({ homeId, selected, hasPerception });
    const show = legend && wide && !suppressed && items.length > 0;
    return <>
      <span ref={anchorRef} hidden />
      {show && stage && ReactDOM.createPortal(<div className="apt-room-guide-legend" role="group" aria-label="Room legend">
        {items.map((item) => <span key={item.id} data-apt-legend={item.id} title={item.detail}><Swatch item={item} />{item.label}</span>)}
      </div>, stage)}
    </>;
  }

  function GuideDialog({ overlayKey, title, closeLabel, onClose, children, footer }) {
    const anchorRef = useRef(null), dialogRef = useRef(null), titleId = useId();
    const [host, setHost] = useState(null);
    useLayoutEffect(() => { setHost(anchorRef.current?.closest(".apt-home-shell") || document.body); }, []);
    window.HomeOverlay.useOverlayLayer({ key: overlayKey, active: !!host, rootRef: dialogRef, trap: true,
      initialFocus: "first", onEscape: () => { onClose(); return true; } });
    return <>
      <span ref={anchorRef} hidden />
      {host && ReactDOM.createPortal(<div className="apt-room-guide-backdrop"
        onPointerDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
        <div ref={dialogRef} className="apt-room-guide-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}>
          <div className="apt-room-guide-head">
            <h2 id={titleId} className="apt-ui-title">{title}</h2>
            <button type="button" className="apt-ui-button is-small" onClick={onClose}>{closeLabel}</button>
          </div>
          {children}
          {footer && <div className="apt-ui-actions">{footer}</div>}
        </div>
      </div>, host)}
    </>;
  }

  function HelpDialog({ homeId, hasPerception, onClose, onAbout, children }) {
    const { gestures, keys } = helpRows(homeId);
    const marks = legendItems({ homeId, selected: true, hasPerception });
    return <GuideDialog overlayKey="apartment-room-help" title="Gestures and keys" closeLabel="Close help" onClose={onClose}
      footer={onAbout && <button type="button" className="apt-ui-button is-small" onClick={onAbout}>About this home</button>}>
      <h3 className="apt-ui-heading">Gestures</h3>
      <dl className="apt-ui-key-values">{gestures.map((row) => <React.Fragment key={row.id}>
        <dt data-apt-help-gesture={row.id}>{row.label}</dt><dd>{row.action}</dd></React.Fragment>)}</dl>
      <h3 className="apt-ui-heading">Keys</h3>
      <dl className="apt-ui-key-values">{keys.map((row) => <React.Fragment key={row.id}>
        <dt data-apt-help-key={row.id}><kbd>{row.label}</kbd></dt>
        <dd>{row.action}{row.when && <span className="apt-ui-meta"> · {row.when}</span>}</dd></React.Fragment>)}</dl>
      <h3 className="apt-ui-heading">Reading the room</h3>
      <dl className="apt-ui-key-values">{marks.map((item) => <React.Fragment key={item.id}>
        <dt data-apt-help-mark={item.id}><Swatch item={item} />{item.label}</dt><dd>{item.detail}</dd></React.Fragment>)}</dl>
      {children}
    </GuideDialog>;
  }

  /* Only facts the room already shows elsewhere; no capability object exists. */
  const MODEL_GROUP = {
    live_ha_model: "live", tracker_live: "live", cached_live_model: "recorded", live_shadow: "recorded",
    local_draft: "authored", seed_model: "authored", equipment_registration: "authored", simulation: "modeled",
  };
  const ABOUT_GROUPS = [["live", "Live"], ["modeled", "Modeled"], ["authored", "Authored"], ["recorded", "Recorded"], ["unavailable", "Not available here"]];

  function aboutFacts({ homeId, source = {}, revision = null, liveShadow = false, simActive = false,
    trackerStatus = null, hasVirtualFixtures = false, fixtureCount = 0, hasPerception = false } = {}) {
    const facts = [];
    const add = (group, id, title, detail) => facts.push({ group, id, title, detail });
    const rev = Number.isInteger(revision) ? ` · rev ${revision}` : "";
    add(MODEL_GROUP[source.kind] || "unavailable", "model", `${source.label || "No Apartment model"}${rev}`,
      liveShadow ? "Read-only room inspection · physical controls unavailable"
        : source.live ? "Read from this home's live source" : source.writable === false ? "Read-only here" : null);
    add("recorded", "scene", "Scene imagery", "Cloud, Photo and Mesh use this home's existing scene assets.");
    if (simActive) add("modeled", "simulation", "Simulation", "Device states are simulated; nothing is sent to hardware.");
    if (homeId === "victoria" && hasVirtualFixtures) {
      add("modeled", "fixtures", `${fixtureCount || "Virtual"} modeled fixture${fixtureCount === 1 ? "" : "s"} · not commissioned`,
        "Modeled channels · nothing is sent to hardware.");
      add("modeled", "pools", "Light pools and beams", "Modeled from fixture geometry, not measured illumination.");
    }
    if (liveShadow) add("recorded", "observations", "Observations", "Context journal");
    else if (trackerStatus === "live") add("live", "tracker", "Presence tracker", "Room-level presence, no body position");
    else if (trackerStatus) add("unavailable", "tracker", "Presence tracker", `Not live · ${trackerStatus}`);
    if (homeId === "victoria") {
      if (hasPerception) add("recorded", "perception", "Den camera results · viewing only",
        "Exact analyzed frames with approximate room placement. Start and stop sessions in Camera · JEV.");
      else add("unavailable", "perception", "Body and camera results", "Need pose data");
    }
    return facts;
  }

  function AboutDialog({ homeName, facts, onClose }) {
    const Badge = window.HomeApartmentUI.Badge;
    return <GuideDialog overlayKey="apartment-room-about" title={`What's real in ${homeName || "this home"}`}
      closeLabel="Close About" onClose={onClose}>
      {ABOUT_GROUPS.map(([group, heading]) => {
        const rows = facts.filter((fact) => fact.group === group);
        return rows.length > 0 && <section key={group} data-apt-about-group={group} aria-label={heading}>
          <h3 className="apt-ui-heading"><Badge state={group}>{heading}</Badge></h3>
          <ul className="apt-ui-list">{rows.map((fact) => <li key={fact.id} data-apt-about={fact.id}>
            <span className="apt-ui-copy">{fact.title}</span>{fact.detail && <p className="apt-ui-meta">{fact.detail}</p>}</li>)}</ul>
        </section>;
      })}
    </GuideDialog>;
  }

  /* Keeps an open toolbar popover inside the room stage. On a phone the stage
   * is the top half of the screen and the Layers list is taller than it. */
  function useFitPopover(ref, open) {
    useLayoutEffect(() => {
      const node = ref.current;
      if (!open || !node) return undefined;
      const fit = () => {
        node.style.maxHeight = ""; node.style.translate = "";
        const toolbar = node.closest('[role="toolbar"]'), stage = toolbar?.parentElement;
        if (!stage) return;
        const room = stage.getBoundingClientRect(), bar = toolbar.getBoundingClientRect(), margin = 8;
        const cap = parseFloat(getComputedStyle(node).maxHeight) || Infinity;
        node.style.maxHeight = `${Math.max(120, Math.min(cap, Math.floor(bar.top - room.top - 10 - margin)))}px`;
        const box = node.getBoundingClientRect();
        const shift = box.left < room.left + margin ? room.left + margin - box.left
          : box.right > room.right - margin ? room.right - margin - box.right : 0;
        if (shift) node.style.translate = `${Math.round(shift)}px 0`;
      };
      fit();
      window.addEventListener("resize", fit);
      return () => window.removeEventListener("resize", fit);
    }, [ref, open]);
  }

  /* "?" opens help while the room is the top surface. Editors keep the key. */
  function useHelpShortcut(enabled, openHelp) {
    const openRef = useRef(openHelp);
    openRef.current = openHelp;
    useEffect(() => {
      if (!enabled) return undefined;
      const onKey = (event) => {
        if (event.defaultPrevented || event.key !== "?" || event.ctrlKey || event.metaKey || event.altKey) return;
        const target = event.target;
        if (target?.isContentEditable || (target && /INPUT|TEXTAREA|SELECT/.test(target.tagName))) return;
        const top = window.HomeOverlay?.topKey?.();
        if (top && top !== "apartment" && top !== "apartment-embedded") return;
        event.preventDefault();
        openRef.current();
      };
      window.addEventListener("keydown", onKey);
      return () => window.removeEventListener("keydown", onKey);
    }, [enabled]);
  }

  const STYLE = `
.apt-room-guide-layers section + section { margin-top: 10px; }
.apt-room-guide-row { display: grid; gap: 2px; padding: 3px 0; }
.apt-room-guide-row[data-available="0"] .apt-ui-copy { color: var(--hg-fg-3, #a1a1a1); }
.apt-room-guide-row > .apt-ui-meta { padding-left: 22px; }
.apt-room-guide-row .apt-ui-seg button { flex: 1; }
.apt-room-guide-legend { position: absolute; top: 12px; left: 12px; z-index: 5; display: flex; flex-wrap: wrap; gap: 4px 14px;
  max-width: min(560px, calc(100% - 24px)); padding: 6px 10px; border: 1px solid var(--apt-border); border-radius: 10px;
  background: var(--apt-glass); color: #ededed; font-size: 11.5px; line-height: 1.5; pointer-events: none; }
.apt-room-guide-swatch { display: inline-block; width: 16px; height: 0; margin: 0 6px 0 0; border-top-width: 2px; vertical-align: middle; }
.apt-room-guide-backdrop { position: absolute; inset: 0; z-index: 50; display: grid; place-items: center; padding: 16px; background: rgba(0,0,0,.5); }
.apt-room-guide-dialog { width: min(540px, 100%); max-height: min(680px, 100%); overflow-y: auto; padding: 16px 18px;
  border: 1px solid var(--apt-border); border-radius: 12px; background: var(--apt-surface-raised, #111); color: var(--apt-text, #ededed);
  box-shadow: 0 16px 40px rgba(0,0,0,.45); font-size: 13px; line-height: 1.5; }
.apt-room-guide-dialog .apt-ui-heading { margin: 14px 0 6px; }
.apt-room-guide-dialog .apt-ui-key-values dd { text-align: left; }
.apt-room-guide-dialog kbd { font: 500 12px/1.4 'Geist Mono', ui-monospace, monospace; }
.apt-room-guide-dialog .apt-ui-list { margin: 0; padding: 0; list-style: none; display: grid; gap: 6px; }
.apt-room-guide-dialog .apt-ui-list .apt-ui-meta { margin: 0; }
.apt-room-guide-dialog .apt-ui-heading .apt-ui-badge { font-size: 11px; }
.apt-room-guide-dialog > .apt-ui-actions { margin-top: 14px; }
.apt-room-guide-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.apt-room-guide-head .apt-ui-title { margin: 0; font-size: 15px; font-weight: 600; }
`;
  if (typeof document !== "undefined" && !document.querySelector("style[data-apt-room-guide]")) {
    const style = document.createElement("style");
    style.setAttribute("data-apt-room-guide", "1");
    style.textContent = STYLE;
    document.head.appendChild(style);
  }

  window.HomeApartmentRoomGuide = Object.freeze({
    KEY_MAP, GESTURES, LEGEND, LEGEND_MIN_WIDTH, helpRows, legendItems, layerGroups, aboutFacts,
    LayersPanel, Legend, HelpDialog, AboutDialog, useHelpShortcut, useFitPopover,
  });
})();
