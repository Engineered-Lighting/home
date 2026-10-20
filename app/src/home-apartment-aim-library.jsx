/* eslint-disable */
/**
 * home-apartment-aim-library.jsx — named aims in both homes' Aim hosts and the
 * room (APT-A05, A07, A10, R04): the Aim rows with Set aim, Save as named
 * aim and Discard; recall chips with their attention flags and per-aim
 * state; the named-aim inspector; and the room's named-aim list.
 *
 * Presentation only. Every action is a callback the host carries out
 * through its existing guarded paths: proposals go to the shared store, Set
 * goes through the host's one Set, and saves through the host's existing
 * save. Copy comes from HomeApartmentNamedAims.COPY.
 */

const APT_NAMED_AIM_CSS = `
.apt-named-aim .apt-na-chips{display:flex;flex-wrap:wrap;gap:6px}
.apt-named-aim .apt-na-chip{position:relative;min-height:32px;padding:0 11px;border-radius:16px;border:1px solid var(--hg-border-soft,rgba(255,255,255,.16));background:var(--apt-fill,transparent);color:var(--hg-fg-1,#EDEDED);font:500 12px/1 "Geist","Inter",sans-serif;cursor:pointer}
.apt-named-aim .apt-na-chip[aria-current="true"]{border-color:var(--apt-warm,#ffddb0);color:var(--apt-warm,#ffddb0)}
.apt-named-aim .apt-na-chip[data-proposed="true"]{border-color:var(--apt-proposed,#45dfff);color:var(--apt-proposed,#45dfff)}
.apt-named-aim .apt-na-chip[disabled]{opacity:.55;cursor:default}
.apt-named-aim .apt-na-chip .flag{position:absolute;top:3px;right:4px;width:6px;height:6px;border-radius:50%;background:var(--apt-attn,#ffb45f)}
.apt-named-aim .apt-na-flag{display:flex;align-items:center;flex-wrap:wrap;gap:6px;padding:7px 9px;border-radius:8px;border:1px solid color-mix(in srgb,var(--apt-attn,#ffb45f) 45%,transparent);color:var(--hg-fg-2,#cfcfcf);font-size:12px}
.apt-named-aim .apt-na-flag[data-state="conflict"],.apt-named-aim .apt-na-flag[data-state="not_saved"]{border-color:color-mix(in srgb,var(--apt-blocked,#ff625f) 50%,transparent)}
.apt-named-aim .apt-na-flag .grow{flex:1 1 160px}
.apt-named-aim .apt-na-row{display:flex;align-items:flex-start;gap:8px}
.apt-named-aim .apt-na-swatch{flex:none;width:9px;height:9px;margin-top:4px;border-radius:50%;border:2px solid currentColor}
.apt-named-aim .apt-na-field{display:flex;gap:6px;align-items:center}
.apt-named-aim .apt-na-field input{flex:1;min-width:0;min-height:32px;padding:0 9px;border-radius:8px;border:1px solid var(--hg-border-soft,rgba(255,255,255,.16));background:var(--hg-input-bg,transparent);color:var(--hg-fg-1,#EDEDED);font:inherit}
.apt-named-aim .apt-na-ref{display:flex;align-items:center;gap:8px;font:12px/1.3 "Geist Mono","JetBrains Mono",monospace;overflow-wrap:anywhere}
.apt-named-aim dl{display:grid;grid-template-columns:auto 1fr;gap:3px 10px;margin:0;font-size:12px}
.apt-named-aim dd{margin:0;font-family:"Geist Mono","JetBrains Mono",monospace;overflow-wrap:anywhere}
`;

const aptNamedAimCopy = () => window.HomeApartmentNamedAims?.COPY;
const APT_NAMED_AIM_BADGE = { saving: "loading", saved: "saved", local: "unsaved", conflict: "conflict", changed: "changed", not_saved: "unsaved" };

function AptNamedAimStyle() { return <style>{APT_NAMED_AIM_CSS}</style>; }

function AptNamedAimBadge({ status, compact = false }) {
  const Badge = window.HomeApartmentUI?.Badge;
  if (!status || !Badge || compact && status.state === "saved") return null;
  return <Badge state={APT_NAMED_AIM_BADGE[status.state] || "unavailable"} className={compact ? "plain" : ""}>
    <span data-apt-named-aim-state={status.state}>{status.badge}</span></Badge>;
}

/* Save as named aim: a button that opens an inline naming field. The field
 * claims Escape for itself and closes only the field (SPEC › Cancel, Return
 * and Escape). */
function AptSaveNamedAim({ suggested = "", disabledReason = "", onSave }) {
  const C = aptNamedAimCopy();
  const [naming, setNaming] = React.useState(false), [name, setName] = React.useState("");
  const inputRef = React.useRef(null);
  React.useEffect(() => { if (naming) inputRef.current?.focus(); }, [naming]);
  if (!C) return null;
  if (!naming) return <button type="button" className="apt-ui-button" data-apt-named-aim-action="save-as"
    disabled={!!disabledReason} title={disabledReason || undefined} onClick={() => { setName(suggested || ""); setNaming(true); }}>{C.action.save}</button>;
  return <form className="apt-na-field" data-apt-named-aim-naming="" onSubmit={event => {
    event.preventDefault(); const clean = name.trim(); if (!clean) return; setNaming(false); onSave?.(clean); }}>
    <input ref={inputRef} value={name} onChange={event => setName(event.target.value)} aria-label={C.naming.label}
      placeholder={C.naming.placeholder} maxLength={80}
      onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setNaming(false); } }} />
    <button type="submit" className="apt-ui-button" disabled={!name.trim()}>{C.naming.save}</button>
  </form>;
}

/* The Aim tab's rows for a light whose host owns the shared proposal path:
 * where it points now (warm), what is proposed (cyan) or why that spot
 * can't be used with a suggestion, then Set aim, Save as named aim and
 * Discard (prototype AimTab). */
function AptNamedAimRows({ label, current, proposal, measure, suggestion, onSet, onDiscard, onUndo, onRedo, canUndo, canRedo,
  saveSuggested, saveDisabledReason, onSaveAs, saveState = null, tapAim = null, onTapAim, notice }) {
  const C = aptNamedAimCopy(), target = proposal?.target, valid = target?.valid === true;
  const reason = { blocked: "Blocked", out_of_reach: "Out of reach", no_surface: "No surface here", not_aimable: "Can’t aim here" };
  const origin = proposal?.origin === "chat" ? "from chat" : proposal?.origin === "perception" ? "from perception" : null;
  return <section className="apt-ui-section apt-named-aim" data-apt-named-aim-rows="" aria-label="Aim">
    <AptNamedAimStyle />
    <div className="apt-ui-row"><h3 className="apt-ui-heading" style={{ flex: 1 }}>Aim</h3>
      <span className="apt-ui-meta" data-apt-aim-truth="">{label}</span>
      {onUndo && <button type="button" className="apt-ui-button is-small" aria-label="Undo aim" title="Undo (⌘Z)" disabled={!canUndo} onClick={onUndo}>Undo</button>}
      {onRedo && <button type="button" className="apt-ui-button is-small" aria-label="Redo aim" title="Redo (⇧⌘Z)" disabled={!canRedo} onClick={onRedo}>Redo</button>}
    </div>
    <div className="apt-na-row" data-apt-aim-row="current" style={{ color: "var(--apt-warm,#ffddb0)" }}>
      <span className="apt-na-swatch" aria-hidden="true" />
      <span className="apt-ui-grow" style={{ color: "var(--hg-fg-1)" }}><span className="apt-ui-channel-name">Now</span>
        <span className="apt-ui-meta" style={{ display: "block" }}>{current ? `${current.name || "Room surface"} · ${measure?.(current.point_m) || ""}` : "No aim yet"}</span></span>
    </div>
    {target ? <div className="apt-na-row" data-apt-aim-row="proposed" data-state={valid ? "valid" : "invalid"}
      style={{ color: valid ? "var(--apt-proposed,#45dfff)" : "var(--apt-blocked,#ff625f)" }}>
      <span className="apt-na-swatch" aria-hidden="true" />
      <span className="apt-ui-grow" style={{ color: "var(--hg-fg-1)" }}><span className="apt-ui-channel-name">Proposed</span>
        {origin && <span className="apt-ui-meta" data-apt-aim-origin={proposal.origin}> · {origin}</span>}
        <span className="apt-ui-meta" style={{ display: "block" }}>{valid ? `${target.surface || "This spot"} · ${measure?.(target.point_m) || ""}`
          : `${reason[target.reason] || reason.not_aimable} · only Discard is available`}</span>
        {!valid && suggestion && <span className="apt-ui-meta" style={{ display: "block" }} data-apt-aim-suggestion="">{suggestion}</span>}</span>
    </div> : <p className="apt-ui-copy" data-apt-aim-empty="">{tapAim ? "Tap a surface in the room." : "Drag the reticle onto a surface."}</p>}
    {target ? <div className="apt-ui-actions" role="group" aria-label="Aim proposal">
      {valid && <button type="button" className="apt-ui-button" data-apt-aim-tab-action="set" onClick={onSet}
        style={{ background: "var(--apt-proposed,#45dfff)", borderColor: "var(--apt-proposed,#45dfff)", color: "#04161b" }}>Set aim</button>}
      {valid && onSaveAs && C && <AptSaveNamedAim key={proposal.id} suggested={saveSuggested} disabledReason={saveDisabledReason} onSave={onSaveAs} />}
      <button type="button" className="apt-ui-button" data-apt-aim-tab-action="discard" onClick={onDiscard}>Discard</button>
    </div> : onTapAim && <div className="apt-ui-actions"><button type="button" className="apt-ui-button" aria-pressed={!!tapAim} onClick={onTapAim}>
      {tapAim ? "Tap a surface…" : "Tap to aim"}</button></div>}
    {saveState && <p className="apt-ui-row" role="status" data-apt-named-aim-save-state={saveState.status.state}>
      <span className="apt-ui-grow apt-ui-meta">“{saveState.name}”</span><AptNamedAimBadge status={saveState.status} /></p>}
    {notice && <p className="apt-ui-meta" role="status" data-apt-aim-notice="">{notice}</p>}
  </section>;
}

/* Recall chips (prototype AimTab › Named aims): every named aim of this home,
 * the current one marked, a proposed one cyan, and an attention dot when its
 * state needs a decision. Tapping a chip proposes it for the light; nothing
 * moves until Set. Flags below the chips carry the decision. */
function AptNamedAimChips({ aims = [], statuses = {}, currentId = null, proposedId = null, unavailableReason = "", onRecall, onResolve, onOpen,
  heading = "Named aims", saveRow = null, pendingFlag = null }) {
  const C = aptNamedAimCopy();
  if (!C) return null;
  const attention = aims.filter(aim => statuses[aim.id]?.attention);
  return <section className="apt-ui-section apt-named-aim" data-apt-named-aim-chips="" aria-label={heading}>
    <AptNamedAimStyle />
    <h3 className="apt-ui-heading">{heading}</h3>
    {unavailableReason ? <p className="apt-ui-meta" data-apt-named-aim-unavailable="">{unavailableReason}</p>
      : aims.length ? <div className="apt-na-chips">{aims.map(aim => {
        const current = aim.id === currentId, status = statuses[aim.id];
        return <button key={aim.id} type="button" className="apt-na-chip hg-focusable" data-apt-named-aim-chip={aim.id}
          aria-current={current ? "true" : undefined} data-proposed={aim.id === proposedId ? "true" : undefined}
          title={current ? C.action.current : C.action.recall} disabled={!onRecall}
          onClick={() => onRecall?.(aim)}>{aim.name}{aim.read_only && <span className="apt-ui-meta"> · read only</span>}
          {status?.attention && <span className="flag" aria-label="needs attention" />}</button>;
      })}</div> : <p className="apt-ui-meta">No named aims saved for this home.</p>}
    {saveRow}
    {pendingFlag && <AptNamedAimFlag aim={pendingFlag.aim} status={pendingFlag.status} onResolve={onResolve} />}
    {attention.map(aim => <AptNamedAimFlag key={aim.id} aim={aim} status={statuses[aim.id]} onResolve={onResolve} onOpen={onOpen} />)}
  </section>;
}

function AptNamedAimFlag({ aim, status, onResolve }) {
  const C = aptNamedAimCopy();
  if (!status?.flag || !C) return null;
  const label = { replace: C.action.replace, keep_both: C.action.keepBoth, preview: C.action.preview, keep: C.action.keep };
  return <div className="apt-na-flag" data-apt-named-aim-flag={aim.id} data-state={status.state} role="status">
    <span className="grow">{status.flag}</span>
    {status.actions.map(action => <button key={action} type="button" className="apt-ui-button is-small"
      data-apt-named-aim-resolve={action} onClick={() => onResolve?.(aim, action)}>{label[action]}</button>)}
  </div>;
}

/* The room's named-aim list (prototype RoomInspector): each aim with its
 * state badge and the lights using it. A row opens the aim's inspector. */
function AptNamedAimRoomList({ aims = [], statuses = {}, usersOf = () => [], onOpen, onEdit, editDisabledReason = "" }) {
  return <section className="apt-ui-section apt-named-aim" aria-label="Named aims" data-apt-named-aim-list="">
    <AptNamedAimStyle />
    <h3 className="apt-ui-heading">Named aims</h3>
    {aims.length ? <ul className="apt-ui-list">{aims.map(aim => {
      const users = usersOf(aim);
      return <li key={aim.id} className="apt-ui-row">
        <button type="button" className="apt-ui-list-button" style={{ flex: 1 }} data-apt-named-aim-open={aim.id} onClick={() => onOpen?.(aim.id)}>
          <span className="nm">{aim.name || aim.id}</span><AptNamedAimBadge status={statuses[aim.id]} compact />
          <span className="vl">{users.length ? users.map(user => user.name || user.id).join(", ") : "—"}</span></button>
        {onEdit && <button type="button" className="apt-ui-button is-small" aria-label={`Edit ${aim.name || aim.id}`}
          disabled={!!editDisabledReason} onClick={() => onEdit(aim.id)}>Edit</button>}
      </li>;
    })}</ul> : <p className="apt-ui-meta">No named aims saved for this home.</p>}
    {editDisabledReason && aims.length > 0 && <p className="apt-ui-meta">{editDisabledReason}</p>}
  </section>;
}

/* The named-aim inspector (APT-R04, prototype TargetInspector), the same from
 * the room, the list and a Chat chip: which lights use it, which can reach
 * it, Aim a light here (a proposal only), Edit in Setup, the automation
 * reference (APT-A10) and its geometry. */
function AptNamedAimInspector({ aim, status, users = [], aimers = [], onBack, onEdit, editDisabledReason = "", onAimHere, onCopy }) {
  const C = aptNamedAimCopy(), N = window.HomeApartmentNamedAims;
  const [copied, setCopied] = React.useState(false);
  if (!aim || !C || !N) return null;
  const I = C.inspector, id = N.automationReference(aim), r2 = value => Math.round(value * 100) / 100;
  const copy = async () => {
    let ok = false;
    try { ok = (await onCopy?.(id)) !== false; } catch (_) { ok = false; }
    setCopied(ok);
  };
  return <section className="apt-ui-inspector apt-named-aim" aria-label="Named aim inspector" data-apt-named-aim-inspector={aim.id}>
    <AptNamedAimStyle />
    <header className="apt-ui-header">
      <div className="apt-ui-row">
        <button type="button" className="apt-ui-button is-small" onClick={onBack} aria-label={I.back}>Back</button>
        <div className="apt-ui-grow"><h2 className="apt-ui-title">{aim.name}</h2>
          <div className="apt-ui-sub">{I.kind} · {aim.category || "custom"} · {N.surfaceKind(aim)}</div></div>
        <button type="button" className="apt-ui-button is-small" data-apt-named-aim-edit="" disabled={!!editDisabledReason || !onEdit}
          title={editDisabledReason || undefined} onClick={() => onEdit?.(aim.id)}>{I.edit}</button>
      </div>
      <div className="apt-ui-actions"><AptNamedAimBadge status={status} />
        {window.HomeApartmentUI?.Badge && <window.HomeApartmentUI.Badge state={aim.source === "configured_desk" ? "authored" : "saved"}>{N.sourceLabel(aim)}</window.HomeApartmentUI.Badge>}</div>
      {editDisabledReason && <p className="apt-ui-meta">{editDisabledReason}</p>}
    </header>
    {status?.flag && <div className="apt-ui-section"><AptNamedAimFlag aim={aim} status={status} /></div>}
    <section className="apt-ui-section" data-apt-named-aim-users=""><h3 className="apt-ui-heading">{I.litBy}</h3>
      <p className="apt-ui-copy">{users.length ? users.map(user => user.name || user.id).join(", ") : I.nobody}</p></section>
    <section className="apt-ui-section" data-apt-named-aim-reach=""><h3 className="apt-ui-heading">{I.reach}</h3>
      {aimers.length ? <ul className="apt-ui-list">{aimers.map(entry => <li key={entry.id} className="apt-ui-row" data-apt-named-aim-reach-row={entry.id}
        data-state={entry.reach.state}><span className="apt-ui-grow">{entry.name}</span><span className="apt-ui-meta">{entry.reach.text}</span></li>)}</ul>
        : <p className="apt-ui-meta">No aimable light in this home.</p>}</section>
    <section className="apt-ui-section"><h3 className="apt-ui-heading">{I.aimHere}</h3>
      <div className="apt-ui-actions">{aimers.map(entry => <button key={entry.id} type="button" className="apt-ui-button"
        data-apt-named-aim-aim-here={entry.id} disabled={!onAimHere || !entry.canPropose} title={entry.disabledReason || undefined}
        onClick={() => onAimHere?.(entry.id, aim)}>{entry.short || entry.name}{entry.truth ? ` · ${entry.truth}` : ""}</button>)}</div>
      <p className="apt-ui-meta">{I.aimHereNote}</p></section>
    <section className="apt-ui-section">
      <details data-apt-named-aim-automation=""><summary className="apt-ui-heading">{I.automation}</summary>
        <p className="apt-ui-copy">{I.automationNote}</p>
        <div className="apt-na-ref"><span data-apt-named-aim-id="">{id}</span>
          <button type="button" className="apt-ui-button is-small" data-apt-named-aim-copy="" onClick={copy}>{copied ? I.copied : I.copyId}</button></div>
        <p className="apt-ui-meta">{I.automationProposed}</p>
        <p className="apt-ui-meta">{I.automationUsers}</p>
      </details>
      <details data-apt-named-aim-geometry=""><summary className="apt-ui-heading">{I.geometry}</summary>
        <dl><dt>Id</dt><dd>{aim.id}</dd>
          <dt>Centre</dt><dd>{(aim.pos || aim.point_m || []).map(r2).join(", ")} m</dd>
          <dt>Normal</dt><dd>{(aim.normal || aim.normal_m || []).join(", ")}</dd>
          {Array.isArray(aim.size_m) && aim.size_m.some(value => value > 0) && <><dt>Size</dt><dd>{aim.size_m.map(r2).join(" × ")} m</dd></>}</dl>
      </details>
    </section>
  </section>;
}

/* Borrows an engine's admitted UI overlay layer for an aim presentation while
 * `wanted` holds, as the Victoria wiring does, for any home. `admitted()` is
 * the host's own currentness check; the lease retires the moment it fails. */
function useAptAimPresentationLease({ engine, host, wanted, admitted, traceBeam }) {
  const [presentation, setPresentation] = React.useState(null);
  const admittedRef = React.useRef(admitted);
  admittedRef.current = admitted;
  React.useEffect(() => {
    if (!wanted || !engine || !host || typeof engine.createUiOverlayLayer !== "function") return undefined;
    let lease = null, closed = false;
    const ok = () => { try { return !closed && host.isConnected && document.visibilityState !== "hidden" && admittedRef.current?.() === true; } catch (_) { return false; } };
    const retire = owned => {
      if (!owned || owned.retired) return;
      owned.retired = true;
      try { owned.presentation?.dispose(); } finally {
        owned.controller.abort(); owned.layer?.dispose();
        setPresentation(previous => previous === owned.presentation ? null : previous);
      }
      if (lease === owned) lease = null;
    };
    const start = async () => {
      if (lease || !ok()) return;
      const owned = { controller: new AbortController(), layer: null, presentation: null, retired: false };
      lease = owned;
      const current = () => lease === owned && !owned.retired && !owned.controller.signal.aborted && ok();
      try {
        const module = await window.Home3D?.ready;
        if (!current() || typeof module?.loadAimPresentation !== "function") { retire(owned); return; }
        const api = await module.loadAimPresentation();
        if (!current()) { retire(owned); return; }
        const layer = await engine.createUiOverlayLayer({ host, signal: owned.controller.signal, isCurrent: current,
          onPresentationFrame: () => owned.presentation?.frame(), onPresentationDispose: () => retire(owned) });
        if (!layer || !current()) { layer?.dispose(); retire(owned); return; }
        owned.layer = layer;
        owned.presentation = api.createAimPresentation({ layer, host, isCurrent: current, onRetire: () => retire(owned),
          traceBeam: typeof traceBeam === "function" ? traceBeam : undefined });
        if (!current()) { retire(owned); return; }
        setPresentation(owned.presentation);
      } catch (_) { retire(owned); }
    };
    const onVisibility = () => { retire(lease); if (document.visibilityState !== "hidden") void start(); };
    document.addEventListener("visibilitychange", onVisibility);
    const revive = setInterval(() => { if (!lease) void start(); }, 300);
    void start();
    return () => { closed = true; clearInterval(revive); document.removeEventListener("visibilitychange", onVisibility); retire(lease); };
  }, [engine, host, wanted]);
  return wanted ? presentation : null;
}

const APT_ECHO_NAMED_AIMS_NONE = Object.freeze({ overlay: null, aimTab: null, roomList: null, inspector: null,
  escape: () => false, openNamedAim: () => false, recall: () => null, host: null });

/* Echo's engineered light on the shared aim path, and Echo's named aims, for
 * HomeApartmentLiveView. The host passes its existing state and guarded
 * callbacks; this hook returns the elements it mounts and the Escape steps
 * it owns. It never saves except through `saveModel`, the existing
 * apartment_model save with its revision check. */
function useAptEchoNamedAims(input) {
  const { open, siteId, engine, host, model, saveStatus, sim, store, aimMode, aimFixtureId,
    setAimMode, setAimFixtureId, editing, sceneEnabled, placementEnabled, contentRevision, openNamedAimEditor } = input;
  const N = window.HomeApartmentNamedAims, E = window.HomeApartmentEchoAim;
  const echo = siteId === "echo" && !!N && !!E && !!store;
  const live = React.useRef(input); live.current = input;
  const [notice, setNotice] = React.useState("");
  const [pending, setPending] = React.useState(null);
  const [kept, setKept] = React.useState([]);
  const [inspectId, setInspectId] = React.useState(null);
  const [, setTick] = React.useState(0);
  const manifest = () => window.HomeContentPacks?.get?.("echo", live.current.contentRevision || undefined)?.manifest || null;
  const runtimeApi = () => live.current.sim ? window.__SIM_APARTMENT_AIM_RUNTIME : null;
  const contextFor = id => N?.echoAimContext({ model: live.current.model, fixtureId: id, runtime: runtimeApi()?.read?.(id) || null,
    contentRevision: live.current.contentRevision, manifest: manifest(), sim: !!live.current.sim }) || null;
  const hostRef = React.useRef(null);
  if (echo && !hostRef.current) {
    hostRef.current = E.createEchoAimHost({ store, siteId: "echo", getContext: contextFor,
      runtime: { setDestination: (id, destination) => runtimeApi()?.setDestination?.(id, destination) || null },
      simulated: () => !!live.current.sim && !!window.__SIM_APARTMENT_AIM_RUNTIME,
      reducedMotion: () => window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true,
      sweep: ({ fixture, from_point_m, to_point_m }) => {
        const overlay = live.current.engine?.overlay, origin = fixture.pos;
        const unit = point => { const ray = point.map((value, axis) => value - origin[axis]), length = Math.hypot(...ray); return [ray.map(value => value / length), length]; };
        const [from, fromLength] = unit(from_point_m), [to, toLength] = unit(to_point_m);
        overlay?.animateAimTransition?.({ origin, from_direction: from, to_direction: to, from_distance_m: fromLength, to_distance_m: toLength,
          full_fwhm_deg: fixture.spotlight?.optic_profile?.configured_fwhm_deg || 20, duration_ms: 650 }, () => {});
      },
      onNotice: setNotice, onToast: text => live.current.showToast?.(text), onChange: () => { setTick(value => value + 1); live.current.onAimChange?.(); },
      shortcut: /Mac|iPhone|iPad/.test(navigator.platform || "") ? "⌘Z" : "Ctrl+Z" });
  }
  const aimHost = echo ? hostRef.current : null;
  React.useSyncExternalStore(aimHost?.subscribe || aptNamedAimNoSubscribe, aimHost?.getSnapshot || aptNamedAimNoSnapshot, aimHost?.getSnapshot || aptNamedAimNoSnapshot);

  const targets = echo ? (model?.targets || []).filter(target => target && typeof target.id === "string") : [];
  const engineered = echo ? (model?.devices || []).filter(device => device?.fixture_kind === N.ENGINEERED_KIND) : [];
  const fixture = engineered.find(device => device.id === aimFixtureId) || null;
  const context = fixture ? contextFor(fixture.id) : null;
  const held = context && aimHost ? aimHost.held(fixture.id) : null;
  const proposal = held?.proposal || null;
  const collision = manifest()?.assets?.["collision.glb"]?.sha256 || null;
  const basisFor = target => N?.aimBasis({ contentRevision, collisionDigest: collision,
    fixture: engineered.find(device => device.id === target?.aim_basis?.fixture_id) || null });
  const statuses = echo ? Object.fromEntries(targets.map(target => [target.id, N.namedAimState(target, { pending, save: saveStatus, basis: basisFor(target), kept })])) : {};
  const aimOf = device => { const current = contextFor(device.id)?.current; return current ? { target_id: current.target_id } : null; };

  // The overlay's lease follows Echo's aim mode for an engineered light.
  const wanted = echo && open && aimMode && !!fixture && !editing && sceneEnabled;
  const presentation = useAptAimPresentationLease({ engine: echo ? engine : null, host, wanted,
    admitted: () => live.current.open && live.current.siteId === "echo" && live.current.engine?.siteId === "echo" && live.current.sceneEnabled && !live.current.editing });

  // Proposals retire with the scope their fence names.
  React.useEffect(() => {
    if (!echo) return;
    store.retainScopes("echo", engineered.map(device => { const value = contextFor(device.id); return value && store.scopeFromDescriptor("echo", value.descriptor); }).filter(Boolean));
  }, [echo, model, contentRevision, sim]);
  React.useEffect(() => { if (!echo) setInspectId(null); }, [echo]);

  // Save as named aim goes through the existing model save once the new
  // record is in the model this view renders.
  React.useEffect(() => {
    if (!pending?.awaiting || !(model?.targets || []).some(target => target.id === pending.target_id)) return;
    setPending(previous => previous && { ...previous, awaiting: false });
    void live.current.saveModel?.();
  }, [model, pending]);
  const saveDisabled = !placementEnabled ? N?.COPY.echoUnavailable : "";
  function saveAs(name, resolution = null, recordOverride = null) {
    const current = live.current;
    const built = recordOverride ? { ok: true, record: recordOverride } : N.namedAimRecord({ proposal, name, roomId: fixture?.room_id || null,
      basis: N.aimBasis({ contentRevision: current.contentRevision, collisionDigest: collision, fixture }), nowMs: Date.now() });
    if (!built.ok) { setNotice("Only a valid proposed spot can be saved as a named aim."); return false; }
    const applied = N.applyNamedAimSave(current.model, built.record, resolution);
    if (applied.conflict) { setPending({ target_id: built.record.id, name: built.record.name, conflict: true, record: built.record }); return false; }
    if (!applied.ok) return false;
    current.setModel(applied.model); current.markModelDirty?.();
    setPending({ target_id: applied.target_id, name: built.record.name, awaiting: true });
    return true;
  }
  function keepAsIs(target) {
    const current = live.current, basis = basisFor(target);
    current.setModel(previous => ({ ...previous, targets: (previous.targets || []).map(item => item.id === target.id
      ? { ...item, aim_basis: { ...basis }, updated_at: new Date().toISOString() } : item) }));
    current.markModelDirty?.(); setKept(list => [...new Set([...list, target.id])]);
    setPending({ target_id: target.id, name: target.name, awaiting: true });
  }
  const recall = (fixtureId, aim, origin = "manual") => {
    const device = engineered.find(item => item.id === fixtureId);
    if (!device || !aimHost) return null;
    const target = targets.find(item => item.id === aim.id) || { pos: aim.point_m };
    return aimHost.recall({ fixture_id: fixtureId, origin, reach: N.reachOfAim(target, device, { siteId: "echo" }),
      aim: { id: aim.id, name: aim.name, point_m: aim.point_m || aim.pos, normal_m: aim.normal_m || aim.normal } });
  };
  const resolve = (aim, action) => {
    if ((action === "replace" || action === "keep_both") && pending?.conflict) { saveAs(pending.name, action, pending.record); return; }
    const target = targets.find(item => item.id === aim.id);
    if (action === "preview" && target && fixture) recall(fixture.id, { id: target.id, name: target.name, point_m: target.pos, normal_m: target.normal });
    else if (action === "keep" && target) keepAsIs(target);
  };
  const openNamedAim = id => {
    if (!echo || !(live.current.model?.targets || []).some(target => target?.id === id)) return false;
    setInspectId(id); live.current.onShowControls?.(); return true;
  };
  const aimHere = (fixtureId, aim) => {
    setInspectId(null); setAimFixtureId(fixtureId); setAimMode(true); live.current.onShowAim?.();
    return recall(fixtureId, { id: aim.id, name: aim.name, point_m: aim.pos, normal_m: aim.normal });
  };

  // From the room: a named aim's marker selects it, or proposes it while an
  // engineered light is being aimed (SPEC › Selection precedence 3).
  React.useEffect(() => {
    if (!echo || !open || editing || !host || !engine?.picking || !sceneEnabled) return undefined;
    let press = null;
    const down = event => { press = { x: event.clientX, y: event.clientY }; };
    const click = event => {
      const start = press; press = null;
      if (!start || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 8) return;
      if (event.target?.closest?.("[data-aim-ignore], button, a, input")) return;
      try {
        if (engine.picking.pick(engine.overlay.pickObjects(), event.clientX, event.clientY).length) return;
        const id = engine.picking.pick(engine.overlay.targetPickObjects?.() || [], event.clientX, event.clientY)[0]?.object.userData.targetId;
        const target = id && (live.current.model?.targets || []).find(item => item.id === id);
        if (!target) return;
        const now = live.current, devices = (now.model?.devices || []).filter(device => device?.fixture_kind === N.ENGINEERED_KIND);
        if (now.aimMode && devices.some(device => device.id === now.aimFixtureId)) recall(now.aimFixtureId, { id: target.id, name: target.name, point_m: target.pos, normal_m: target.normal });
        else openNamedAim(target.id);
      } catch (_) { /* a missed pick selects nothing */ }
    };
    host.addEventListener("pointerdown", down, true); host.addEventListener("click", click, true);
    return () => { host.removeEventListener("pointerdown", down, true); host.removeEventListener("click", click, true); };
  }, [echo, open, editing, host, engine, sceneEnabled, model]);

  // Cmd/Ctrl+Z, Shift+Cmd/Ctrl+Z and Enter for the Echo light being aimed.
  React.useEffect(() => {
    if (!echo || !open) return undefined;
    const key = event => {
      const now = live.current, target = event.target;
      if (event.defaultPrevented || event.altKey || event.isComposing || !now.aimMode || now.editing) return;
      if (target?.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target?.tagName || "")) return;
      const top = window.HomeOverlay?.topKey?.();
      if (top && top !== "apartment" && top !== "apartment-embedded") return;
      const id = now.aimFixtureId, hostNow = hostRef.current;
      if (!hostNow || !(now.model?.devices || []).some(device => device?.id === id && device.fixture_kind === N.ENGINEERED_KIND)) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
        if (event.shiftKey ? hostNow.redo(id) : hostNow.undo(id)) event.preventDefault();
        return;
      }
      if (event.key === "Enter" && !event.metaKey && !event.ctrlKey && !event.shiftKey
          && !target?.closest?.('button, a[href], summary, [role="button"], [role="tab"], [role="switch"], [role="slider"], [role="option"]')) {
        const value = hostNow.held(id)?.proposal;
        if (value?.target.valid === true && hostNow.set({ fixture_id: id, proposal: value }).ok) event.preventDefault();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [echo, open]);

  if (!echo) return APT_ECHO_NAMED_AIMS_NONE;
  const measure = point => context ? N.measure(context.descriptor.optical_origin_m, point)?.text || "" : "";
  const suggestion = proposal && proposal.target.valid === false ? N.COPY.suggest?.[proposal.target.reason] || null : null;
  const pendingFlag = pending?.conflict ? { aim: { id: pending.target_id, name: pending.name },
    status: N.namedAimState({ id: pending.target_id, name: pending.name }, { pending }) } : null;
  const proposedId = proposal?.target.valid ? targets.find(target => Array.isArray(target.pos)
    && proposal.target.point_m.every((value, axis) => Math.abs(value - target.pos[axis]) < .0015))?.id || null : null;
  const chips = <AptNamedAimChips aims={targets.map(target => ({ id: target.id, name: target.name || target.id, point_m: target.pos, normal_m: target.normal }))}
    statuses={statuses} currentId={context?.current?.target_id || null} proposedId={proposedId}
    onRecall={fixture ? aim => recall(fixture.id, aim) : null} onResolve={resolve} pendingFlag={pendingFlag} />;
  const aimTab = fixture && context ? <>
    <AptNamedAimRows label={context.label} current={context.current} proposal={proposal} measure={measure} suggestion={suggestion}
      onSet={() => proposal && aimHost.set({ fixture_id: fixture.id, proposal })}
      onDiscard={() => proposal && aimHost.discard({ fixture_id: fixture.id, proposal })}
      onUndo={() => aimHost.undo(fixture.id)} onRedo={() => aimHost.redo(fixture.id)}
      canUndo={aimHost.historyLength("undo", fixture.id) > 0} canRedo={aimHost.historyLength("redo", fixture.id) > 0}
      saveSuggested={proposal?.target.surface && proposal.target.surface !== "Scan surface" ? proposal.target.surface : ""}
      saveDisabledReason={saveDisabled} onSaveAs={name => saveAs(name)} notice={notice}
      saveState={pending && !pending.conflict && statuses[pending.target_id] ? { name: targets.find(target => target.id === pending.target_id)?.name || pending.name,
        status: statuses[pending.target_id] } : null} />
    {chips}
  </> : null;
  const overlay = presentation && host && window.AptRoomOverlay && fixture && context ? ReactDOM.createPortal(
    <window.AptRoomOverlay presentation={presentation} store={store} siteId="echo" fixtures={[]} selectedId={fixture.id}
      aim={{ descriptor: context.descriptor, limits: context.limits, namedAims: context.namedAims }}
      onSelect={() => {}} onHover={() => {}} onAimStart={() => live.current.onShowAim?.()}
      onPropose={value => aimHost.propose(value)} onSet={value => aimHost.set(value)} onDiscard={value => aimHost.discard(value)} />, host) : null;
  const usersOf = aim => N.lightsUsingAim(aim.id, engineered, aimOf);
  const setupReason = !placementEnabled || !sceneEnabled ? "Setup is unavailable for this room." : "";
  const roomList = <AptNamedAimRoomList aims={targets} statuses={statuses} usersOf={usersOf} onOpen={openNamedAim}
    onEdit={openNamedAimEditor} editDisabledReason={setupReason} />;
  const inspected = inspectId ? targets.find(target => target.id === inspectId) : null;
  const inspector = inspected ? <AptNamedAimInspector aim={inspected} status={statuses[inspected.id]} users={usersOf(inspected)}
    aimers={engineered.map(device => ({ id: device.id, name: device.name || device.id, short: device.name || device.id,
      truth: sim ? "simulated" : "engineered profile", reach: N.reachOfAim(inspected, device, { siteId: "echo" }), canPropose: true }))}
    onBack={() => setInspectId(null)} onEdit={openNamedAimEditor} onAimHere={aimHere} editDisabledReason={setupReason}
    onCopy={async id => { if (!navigator.clipboard?.writeText) return false; await navigator.clipboard.writeText(id); return true; }} /> : null;
  return { overlay, aimTab, roomList, inspector, host: aimHost, openNamedAim, recall,
    // SPEC › Cancel, Return and Escape: 1 a reticle drag, 4 the light's
    // proposal, 5 the named-aim inspector's selection.
    escape(step) {
      if (step === "drag") return !!presentation?.readback().dragging && presentation.cancelDrag();
      if (step === "proposal") return !!(live.current.aimMode && fixture && proposal && aimHost.discard({ fixture_id: fixture.id, proposal }));
      if (step === "selection" && inspectId) { setInspectId(null); return true; }
      return false;
    } };
}
const aptNamedAimNoSubscribe = () => () => {};
const aptNamedAimNoSnapshot = () => null;

/* The Den's named aims in Victoria's Aim tab. Until Den's store contract
 * lands, the production binding has no aims and says why; Save as named aim
 * is shown disabled with the same reason. */
function AptDenNamedAims({ store, registrationRevision }) {
  const N = window.HomeApartmentNamedAims;
  const den = store || N?.denNamedAimsUnavailable();
  if (!den || !N) return null;
  const view = den.view(registrationRevision);
  return <AptNamedAimChips aims={view.aims} unavailableReason={view.available ? "" : view.reason}
    saveRow={<div className="apt-ui-actions"><button type="button" className="apt-ui-button" data-apt-named-aim-action="save-as" disabled
      title={view.reason || N.COPY.den.unavailable}>{N.COPY.action.save}</button></div>} />;
}


Object.assign(window, { useAptAimPresentationLease, useAptEchoNamedAims, AptDenNamedAims, AptNamedAimRows, AptNamedAimChips, AptNamedAimRoomList, AptNamedAimInspector, AptSaveNamedAim, AptNamedAimBadge });
