/* eslint-disable */
/**
 * home-apartment-room-overlay.jsx — in-room aim overlay for Apartment lights:
 * fixture labels with hover and selected rings, the 44 px aim reticle and its
 * drag chip, the cyan proposal marker, the Set aim / Discard bar and the
 * Spot pending dot (SPEC › Gestures, Aim lifecycle, Spot or zones).
 *
 * Not loaded or mounted yet. The host passes an AimPresentation handle
 * (home-3d/apartment-aim-presentation.mjs), which places everything each
 * frame, and the HomeApartmentAimProposals store, which this file only reads.
 * Every change is an intent the host carries out: onPropose, onSet and
 * onDiscard. Nothing here moves an aim, sends or saves.
 */

const { useLayoutEffect, useRef } = React;

const APT_ROOM_OVERLAY_NO_SUBSCRIBE = () => () => {};
const APT_ROOM_OVERLAY_NO_STORE = Object.freeze({ revision: -1, proposals: Object.freeze([]) });
const APT_ROOM_OVERLAY_NO_VIEW = Object.freeze({ revision: -1, dragging: false, model: null });
const APT_ROOM_OVERLAY_NUDGE_PX = 10, APT_ROOM_OVERLAY_NUDGE_FAR_PX = 40;
const APT_ROOM_OVERLAY_FEW = 6;

const APT_ROOM_OVERLAY_CSS = `
.apt-room-overlay [hidden]{display:none!important}
.apt-room-overlay .apt-room-at{position:absolute;left:0;top:0;transform:translate(var(--apt-x),var(--apt-y)) translate(-50%,-50%)}
.apt-room-overlay .apt-room-label{pointer-events:auto;display:flex;align-items:center;gap:6px;height:24px;padding:0 9px 0 8px;border-radius:12px;background:rgba(8,9,11,.74);border:1px solid rgba(255,255,255,.12);color:#EDEDED;font:500 12px/1 "Geist","Inter",sans-serif;white-space:nowrap;cursor:pointer}
.apt-room-overlay .apt-room-label[data-state="hover"]{border-color:rgba(184,216,255,.5);color:#fff}
.apt-room-overlay .apt-room-label[data-state="selected"]{border-color:#B8D8FF;color:#fff;background:rgba(14,18,24,.9)}
.apt-room-overlay .apt-room-label .dot{width:6px;height:6px;border-radius:50%;background:#4b4b4b}
.apt-room-overlay .apt-room-label[data-on="true"] .dot{background:#ffddb0;box-shadow:0 0 6px rgba(255,221,176,.8)}
.apt-room-overlay .apt-room-ring{width:30px;height:30px;border-radius:50%;border:1.5px solid rgba(184,216,255,.45);pointer-events:none}
.apt-room-overlay .apt-room-ring[data-state="selected"]{border-color:#B8D8FF;box-shadow:0 0 0 3px rgba(0,0,0,.35),0 0 10px rgba(184,216,255,.4)}
.apt-room-overlay .apt-room-reticle{width:44px;height:44px;padding:0;border:0;border-radius:50%;background:transparent;pointer-events:auto;cursor:grab;touch-action:none}
.apt-room-overlay .apt-room-reticle:active{cursor:grabbing}
.apt-room-overlay .apt-room-reticle .ring{position:absolute;inset:10px;border-radius:50%;border:1.5px solid #ffddb0;box-shadow:0 0 0 3px rgba(0,0,0,.45),0 0 14px rgba(255,221,176,.35)}
.apt-room-overlay .apt-room-reticle .pip{position:absolute;left:50%;top:50%;width:5px;height:5px;margin:-2.5px;border-radius:50%;background:#fff}
.apt-room-overlay .apt-room-reticle[data-state="proposed"] .ring{border-color:#45dfff;box-shadow:0 0 0 3px rgba(0,0,0,.45),0 0 16px rgba(69,223,255,.45)}
.apt-room-overlay .apt-room-reticle[data-state="invalid"] .ring{border-color:#ff625f;border-style:dashed;box-shadow:0 0 0 3px rgba(0,0,0,.45)}
.apt-room-overlay .apt-room-chip{transform:translate(var(--apt-x),var(--apt-y));display:grid;gap:1px;max-width:240px;padding:7px 10px;border-radius:8px;background:rgba(8,9,11,.9);border:1px solid rgba(69,223,255,.55);color:#fff;font:12.5px/1.3 "Geist","Inter",sans-serif;pointer-events:none}
.apt-room-overlay .apt-room-chip[data-state="invalid"]{border-color:rgba(255,98,95,.7)}
.apt-room-overlay .apt-room-chip b{font-weight:560}
.apt-room-overlay .apt-room-chip .metrics{font:11px/1.3 "Geist Mono","JetBrains Mono",monospace;color:#A1A1A1}
.apt-room-overlay .apt-room-chip .note{font-size:11px;color:#ffb45f}
.apt-room-overlay .apt-room-chip .truth{font-size:11px;color:#A1A1A1;white-space:normal}
.apt-room-overlay .apt-room-mark{width:14px;height:14px;border-radius:50%;border:2px solid #45dfff;box-shadow:0 0 0 2px rgba(0,0,0,.45),0 0 12px rgba(69,223,255,.6);pointer-events:none}
.apt-room-overlay .apt-room-mark[data-state="invalid"]{border-color:#ff625f;border-style:dashed;box-shadow:0 0 0 2px rgba(0,0,0,.45)}
.apt-room-overlay .apt-room-blocker{width:10px;height:10px;border-radius:50%;background:#ff625f;box-shadow:0 0 0 2px rgba(0,0,0,.5);pointer-events:none}
.apt-room-overlay .apt-room-bar{transform:translate(var(--apt-x),var(--apt-y)) translate(-50%,0);pointer-events:auto;display:flex;align-items:center;gap:4px;padding:4px 4px 4px 11px;border-radius:10px;background:rgba(8,9,11,.9);border:1px solid rgba(69,223,255,.55);box-shadow:0 8px 24px rgba(0,0,0,.45);color:#EDEDED;font:540 12.5px/1.25 "Geist","Inter",sans-serif;white-space:nowrap;z-index:6}
.apt-room-overlay .apt-room-bar[data-state="blocked"]{border-color:rgba(255,98,95,.6)}
.apt-room-overlay .apt-room-bar[data-state="blocked"] [data-apt-aim-bar-target]{color:#ff8a87}
.apt-room-overlay .apt-room-bar [data-apt-aim-bar-target]{margin-right:6px;max-width:220px;overflow:hidden;text-overflow:ellipsis}
.apt-room-overlay .apt-room-bar button{height:26px;padding:0 10px;border-radius:7px;border:1px solid rgba(255,255,255,.16);background:transparent;color:#EDEDED;font:inherit;font-size:12px;cursor:pointer}
.apt-room-overlay .apt-room-bar button:hover{border-color:rgba(255,255,255,.4)}
.apt-room-overlay .apt-room-bar button[data-apt-aim-action="set"]{background:#45dfff;border-color:#45dfff;color:#04161b;font-weight:600}
@media (pointer:coarse){.apt-room-overlay .apt-room-bar button{height:36px}}
.apt-aim-pending{position:absolute;top:3px;right:4px;width:6px;height:6px;border-radius:50%;background:#45dfff;pointer-events:none}
`;

const aptRoomOverlayHidden = { position: "absolute", width: 1, height: 1, margin: -1, padding: 0, overflow: "hidden", clip: "rect(0 0 0 0)", whiteSpace: "nowrap", border: 0 };

function useAptAimStore(store) {
  return React.useSyncExternalStore(store?.subscribe || APT_ROOM_OVERLAY_NO_SUBSCRIBE,
    store?.getSnapshot || (() => APT_ROOM_OVERLAY_NO_STORE), store?.getSnapshot || (() => APT_ROOM_OVERLAY_NO_STORE));
}

// The store's own read path; a scope it does not admit has no proposal.
function aptAimPendingFor(store, siteId, descriptor) {
  if (!store || !descriptor) return null;
  const scope = store.scopeFromDescriptor(siteId, descriptor);
  return scope ? { scope, proposal: store.current(scope) } : null;
}

/* Cyan dot for the Spot button of the room tool pill while an aim waits for
 * Set or Discard. On the Spot tool the bar itself is showing, so the dot
 * stays hidden there. It adds "(aim pending)" to the button's name; the
 * parentheses read the same whether or not the name gains a space. */
function AptSpotPendingDot({ store, siteId, descriptor, tool }) {
  useAptAimStore(store);
  const pending = aptAimPendingFor(store, siteId, descriptor);
  if (!pending?.proposal || tool === "spot") return null;
  return <span className="apt-aim-pending" data-apt-aim-pending="">
    <span style={aptRoomOverlayHidden}> (aim pending)</span>
  </span>;
}

function AptRoomOverlay({ presentation, store, siteId, fixtures = [], selectedId = null, hoverId = null,
  pillFixtureId = null, aim = null, onSelect, onHover, onAimStart, onPropose, onSet, onDiscard }) {
  const rootRef = useRef(null), reticleRef = useRef(null);
  useAptAimStore(store);
  const view = React.useSyncExternalStore(presentation?.subscribe || APT_ROOM_OVERLAY_NO_SUBSCRIBE,
    presentation?.getSnapshot || (() => APT_ROOM_OVERLAY_NO_VIEW), presentation?.getSnapshot || (() => APT_ROOM_OVERLAY_NO_VIEW));
  const descriptor = aim?.descriptor || null;
  const aimable = !!(presentation && descriptor && descriptor.fixture_id === selectedId);
  const pending = aimable ? aptAimPendingFor(store, siteId, descriptor) : null;
  const scope = pending?.scope || null, proposal = pending?.proposal || null;
  const bar = proposal && !view.dragging ? presentation.describeProposalBar(proposal) : null;
  const words = presentation?.vocabulary?.aria;
  const fixtureKey = fixtures.map(item => item.fixture_id).join("\n");
  const few = fixtures.length <= APT_ROOM_OVERLAY_FEW;

  useLayoutEffect(() => {
    if (presentation && rootRef.current) return presentation.attach(rootRef.current);
  }, [presentation, fixtureKey]);
  useLayoutEffect(() => {
    if (!presentation) return;
    presentation.sync({ fixtures, selectedId, hoverId, pillFixtureId, aim: aimable && scope ? aim : null, proposal });
    presentation.frame();
  });

  const fixtureId = descriptor?.fixture_id;
  const propose = model => {
    const target = model && presentation.proposalTarget(model);
    if (target && scope) onPropose?.({ fixture_id: fixtureId, scope, origin: "manual", target });
  };
  const set = () => { if (proposal && scope && proposal.target.valid === true) onSet?.({ fixture_id: fixtureId, scope, proposal }); };
  const discard = () => { if (proposal && scope) onDiscard?.({ fixture_id: fixtureId, scope, proposal }); };
  const act = intent => {
    if (intent === "set") set();
    else if (intent === "discard") discard();
    else if (intent === "cancel_drag") presentation.cancelDrag();
    return !!intent;
  };

  // Capture-phase handlers: the host's orbit listener sits on the viewport
  // below this overlay, so a press that stopped only in the bubble phase
  // would already have started an orbit there.
  const reticleDown = event => {
    if (!aimable || !scope || event.button !== 0 || event.isPrimary === false) return;
    event.preventDefault(); event.stopPropagation();
    event.currentTarget.focus({ preventScroll: true });
    if (!presentation.beginDrag({ pointerId: event.pointerId, clientX: event.clientX, clientY: event.clientY })) return;
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch (_) { /* detached */ }
    onAimStart?.({ fixture_id: fixtureId, source: "drag" });
    presentation.moveDrag({ pointerId: event.pointerId, clientX: event.clientX, clientY: event.clientY });
  };
  const reticleMove = event => {
    if (!view.dragging) return;
    event.stopPropagation();
    // A second finger is a pinch, not part of the aim.
    if (event.isPrimary === false) { presentation.cancelDrag(); return; }
    presentation.moveDrag({ pointerId: event.pointerId, clientX: event.clientX, clientY: event.clientY });
  };
  const reticleUp = event => {
    if (view.dragging) event.stopPropagation();
    const model = presentation?.endDrag({ pointerId: event.pointerId });
    try { event.currentTarget.releasePointerCapture(event.pointerId); } catch (_) { /* released */ }
    propose(model);
  };
  const reticleCancel = () => { presentation?.cancelDrag(); };
  const reticleKey = event => {
    if (!aimable || !scope) return;
    const arrow = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
    if (arrow) {
      if (view.dragging) return;
      event.preventDefault(); event.stopPropagation();
      const step = event.shiftKey ? APT_ROOM_OVERLAY_NUDGE_FAR_PX : APT_ROOM_OVERLAY_NUDGE_PX;
      const model = presentation.nudge(arrow[0] * step, arrow[1] * step);
      if (model) { onAimStart?.({ fixture_id: fixtureId, source: "keys" }); propose(model); }
      return;
    }
    if (event.key !== "Enter" && event.key !== "Escape") return;
    const intent = presentation.keyIntent({ key: event.key, dragging: view.dragging, proposal });
    // Enter mid-drag releases where the pointer is; the pointerup proposes.
    if (intent === "release") { event.preventDefault(); return; }
    if (act(intent)) { event.preventDefault(); event.stopPropagation(); }
  };
  const barKey = event => {
    // Enter on a bar button is that button's own click.
    if (event.key === "Enter" && event.target?.closest?.("button")) return;
    if (act(presentation.keyIntent({ key: event.key, dragging: false, proposal }))) { event.preventDefault(); event.stopPropagation(); }
  };

  if (!presentation) return null;
  return <div ref={rootRef} className="apt-room-overlay" data-apt-room-overlay=""
    style={{ position: "absolute", inset: 0, pointerEvents: "none", overflow: "hidden", zIndex: 5 }}>
    <style>{APT_ROOM_OVERLAY_CSS}</style>
    {fixtures.map(fixture => {
      const id = fixture.fixture_id, full = id === selectedId || id === hoverId || !few;
      return <React.Fragment key={id}>
        <span className="apt-room-at apt-room-ring" data-apt-fixture-ring={id} data-state="idle" aria-hidden="true" hidden />
        <button type="button" tabIndex={-1} hidden className="apt-room-at apt-room-label hg-focusable"
          data-apt-fixture-label={id} data-state="idle" data-on={fixture.on ? "true" : "false"} data-aim-ignore="true"
          aria-label={fixture.label || id} aria-current={id === selectedId ? "true" : undefined}
          onPointerDownCapture={event => event.stopPropagation()}
          onClick={event => { event.stopPropagation(); onSelect?.(id); }}
          onPointerEnter={() => onHover?.(id)} onPointerLeave={() => onHover?.(null)}>
          <span className="dot" aria-hidden="true" />
          <span>{full ? fixture.label || id : fixture.short_label || fixture.label || id}</span>
        </button>
      </React.Fragment>;
    })}
    <span className="apt-room-at apt-room-mark" data-apt-aim-proposal="" aria-hidden="true" hidden
      data-state={proposal?.target?.valid === false ? "invalid" : "valid"} />
    <span className="apt-room-at apt-room-blocker" data-apt-aim-blocker="" aria-hidden="true" hidden />
    <button ref={reticleRef} type="button" hidden className="apt-room-at apt-room-reticle hg-focusable"
      data-apt-aim-reticle="" data-state="current" data-aim-ignore="true" aria-label={words?.reticle}
      onPointerDownCapture={reticleDown} onPointerMoveCapture={reticleMove} onPointerUpCapture={reticleUp}
      onPointerCancel={reticleCancel} onLostPointerCapture={reticleCancel} onKeyDown={reticleKey}
      onClick={event => event.stopPropagation()}>
      <span className="ring" aria-hidden="true" /><span className="pip" aria-hidden="true" />
    </button>
    <div className="apt-room-at apt-room-chip" data-apt-aim-chip="" role="status" aria-live="off" hidden
      data-state={view.model?.valid === false ? "invalid" : "valid"}>
      {view.model && <>
        <b data-apt-aim-chip-title="">{view.model.title}</b>
        {view.model.metrics && <span className="metrics" data-apt-aim-chip-metrics="">{view.model.metrics}</span>}
        {view.model.note && <span className="note" data-apt-aim-chip-note="">{view.model.note}</span>}
        {view.model.disclosures.length > 0 && <span className="truth">
          {view.model.disclosures.map((item, index) => <React.Fragment key={item.id}>
            {index > 0 && " · "}<span data-apt-aim-disclosure={item.id}>{item.text}</span>
          </React.Fragment>)}
        </span>}
      </>}
    </div>
    <div className="apt-room-at apt-room-bar" data-apt-aim-bar="" role="group" aria-label={words?.bar} hidden
      data-state={bar?.state || "valid"} data-aim-ignore="true" onKeyDown={barKey}
      onPointerDownCapture={event => event.stopPropagation()} onClick={event => event.stopPropagation()}>
      {bar && <>
        <span data-apt-aim-bar-target="">{bar.title}</span>
        {bar.actions.includes("set") && <button type="button" className="hg-focusable" data-apt-aim-action="set" onClick={set}>{words?.set}</button>}
        <button type="button" className="hg-focusable" data-apt-aim-action="discard" onClick={discard}>{words?.discard}</button>
      </>}
    </div>
  </div>;
}

Object.assign(window, { AptRoomOverlay, AptSpotPendingDot, aptAimPendingFor });
