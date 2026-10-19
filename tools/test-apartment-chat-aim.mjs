#!/usr/bin/env node
// Chat aim proposals (APT-C04): the UI-side parser, its refusals, and that a
// Chat card's Set and the room bar's Set are one Set. Real proposal store,
// named-aim logic and Echo aim host; an authored Echo room and an authored
// Simulation Mode runtime. No DOM, network, agent, service or hardware.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const P = require('../app/src/home-apartment-aim-proposals.js');
const N = require('../app/src/home-apartment-named-aims.js');
const E = require('../app/src/home-apartment-echo-aim.js');
const C = require('../app/src/home-apartment-chat-aim.js');

// Fixed before the first run (2026-10-19); never raised to make a run pass.
const DEADLINE_MS = 5000;
const SPOT = [2, 2.404, 2.453], PAINTING = [2, 3.99, 1.5];
const model = () => ({ schema_version: 1, revision: 7, zones: [],
  devices: [{ id: 'authored-echo-spot', type: 'light', name: 'Authored spot', pos: SPOT, fixture_kind: 'engineered_gimbal_v1',
    gimbal: { limits: { pan_min_deg: -180, pan_max_deg: 180, tilt_min_deg: -90, tilt_max_deg: 90 }, product_profile_sha256: 'a'.repeat(64), default_target_id: 'authored-floor-spot' } },
    { id: 'authored-lamp', type: 'light', name: 'Floor lamp', pos: [1, 1, 1.5] }],
  targets: [{ id: 'authored-painting', name: 'Painting', pos: PAINTING, normal: [0, -1, 0], size_m: [0.9, 0.65] },
    { id: 'authored-floor-spot', name: 'Floor spot', pos: [2, 2, 0], normal: [0, 0, 1] },
    { id: 'authored-coffee-table', name: 'coffee table', pos: [3, 1, 0.45], normal: [0, 0, 1] }] });

// The bridge the Apartment publishes, built from the same parts.
function room({ sim = true, siteId = 'echo' } = {}) {
  const store = P.createAimProposalStore({ now: () => 1 }), current = model();
  const state = { destination: { kind: 'named', id: 'authored-floor-spot', name: 'Floor spot', pos: [2, 2, 0] }, calls: [] };
  const runtime = { setDestination(id, destination) { state.calls.push(destination); state.destination = { ...destination }; return { current_destination: { ...destination } }; } };
  const toasts = [], shown = [];
  const host = E.createEchoAimHost({ store, runtime, simulated: () => sim, onToast: text => toasts.push(text), now: () => 2,
    getContext: id => N.echoAimContext({ model: current, fixtureId: id, runtime: { current_destination: state.destination }, contentRevision: 'authored-echo-r1', sim }) });
  const engineered = current.devices.filter(device => device.fixture_kind === N.ENGINEERED_KIND);
  const pendingOf = (id, proposalId) => { const value = host.held(id)?.proposal; return value?.id === proposalId ? value : null; };
  const bridge = {
    view: () => siteId === 'victoria' ? { siteId, available: false, reason: N.COPY.den.unavailable, aims: [], lights: [] }
      : { siteId, available: sim, reason: sim ? null : N.COPY.chatPhysical, truth: 'simulated',
        aims: current.targets.map(target => ({ id: target.id, name: target.name })), lights: engineered.map(device => ({ id: device.id, name: device.name })) },
    propose({ fixture_id, aim_id, origin }) {
      const target = current.targets.find(item => item.id === aim_id), device = engineered.find(item => item.id === fixture_id);
      const reach = N.reachOfAim(target, device, { siteId: 'echo' });
      const proposal = host.recall({ fixture_id, origin, reach, aim: { id: target.id, name: target.name, point_m: target.pos, normal_m: target.normal } });
      shown.push(fixture_id);
      return proposal ? { ok: true, proposal } : { ok: false };
    },
    live: (id, proposalId) => ({ pending: !!pendingOf(id, proposalId) }),
    set: (id, proposalId) => host.set({ fixture_id: id, proposal: pendingOf(id, proposalId) }),
    discard: (id, proposalId) => host.discard({ fixture_id: id, proposal: pendingOf(id, proposalId) }),
  };
  return { store, host, state, toasts, shown, bridge };
}
const chat = (text, bridge) => { const events = []; const handled = C.run(text, { addEvent: event => events.push(event), bridge, now: () => 3 }); return { handled, events }; };

test('only aim requests that name a named aim are Chat aim proposals', { timeout: DEADLINE_MS }, () => {
  const view = room().bridge.view();
  assert.equal(C.parseAimRequest('turn on the coffee table light', view), null, 'a lighting command stays a lighting command');
  assert.equal(C.parseAimRequest('what is the painting?', view), null);
  assert.equal(C.parseAimRequest('/aim painting', view), null, 'slash commands keep their own path');
  assert.equal(C.parseAimRequest('aim the light somewhere nice', view), null, 'an aim verb alone stays with the agent');
  const parsed = C.parseAimRequest('Aim the authored spot at the painting', view);
  assert.deepEqual([parsed.kind, parsed.fixture_id, parsed.aim.id], ['propose', 'authored-echo-spot', 'authored-painting']);
  assert.equal(C.parseAimRequest('point a light at the coffee table', view).aim.id, 'authored-coffee-table');
  assert.match(C.parseAimRequest('point it at the bookshelf', view).text, /I don't know that named aim\. This home has Painting, Floor spot, coffee table\./);
});

test('a Chat aim proposes with origin chat, previews in the room and moves nothing', { timeout: DEADLINE_MS }, () => {
  const { bridge, store, state, shown } = room();
  const { handled, events } = chat('shine the spot on the painting', bridge);
  assert.equal(handled, true);
  assert.deepEqual(events.map(event => event.kind), ['user', 'home', 'apartment-aim-proposal']);
  const [proposal] = store.getSnapshot().proposals;
  assert.deepEqual([proposal.origin, proposal.target.point_m, proposal.target.valid], ['chat', PAINTING, true]);
  assert.deepEqual(events[2], { kind: 'apartment-aim-proposal', site_id: 'echo', fixture_id: 'authored-echo-spot', proposal_id: proposal.id,
    light_name: 'Authored spot', aim_id: 'authored-painting', aim_name: 'Painting', origin: 'chat', truth: 'simulated', created_at: 3 });
  assert.deepEqual(shown, ['authored-echo-spot'], 'the light opens on the Aim tab to preview');
  assert.equal(state.calls.length, 0, 'nothing moves until Set');
  assert.equal(C.cardState({}, bridge.live('authored-echo-spot', proposal.id)), 'open');
});

test('card Set and bar Set give the same simulated aim, history and toast', { timeout: DEADLINE_MS }, () => {
  const outcome = via => {
    const r = room(), { events } = chat('aim the authored spot at the painting', r.bridge), card = events.at(-1);
    const result = via === 'card' ? r.bridge.set(card.fixture_id, card.proposal_id)
      : r.host.set({ fixture_id: card.fixture_id, proposal: r.host.held(card.fixture_id).proposal }); // the room bar's onSet
    return { ok: result.ok, destination: r.state.destination, toasts: r.toasts, undo: r.host.historyLength('undo', card.fixture_id),
      pending: r.store.getSnapshot().proposals.length, card: C.cardState({ outcome: result.ok ? 'set' : null }, r.bridge.live(card.fixture_id, card.proposal_id)) };
  };
  const card = outcome('card'), bar = outcome('bar');
  assert.deepEqual(card, bar);
  assert.equal(card.destination.id, 'authored-painting');
  assert.deepEqual(card.toasts, ['Aim set · simulated, no hardware command sent']);
  // Discard from the bar leaves the card with nothing to act on.
  const r = room(), { events } = chat('aim the authored spot at the painting', r.bridge), event = events.at(-1);
  assert.equal(r.host.discard({ fixture_id: event.fixture_id, proposal: r.host.held(event.fixture_id).proposal }), true);
  assert.equal(C.cardState({}, r.bridge.live(event.fixture_id, event.proposal_id)), 'gone', 'No longer proposed');
  assert.equal(r.bridge.set(event.fixture_id, event.proposal_id).ok, false);
});

test('physical lights and the Den are refused with their reason; nothing else is called', { timeout: DEADLINE_MS }, () => {
  const physical = room({ sim: false });
  const refused = chat('aim the authored spot at the painting', physical.bridge);
  assert.equal(refused.handled, true, 'the message is answered here, not passed to lighting control or the agent');
  assert.deepEqual(refused.events.map(event => [event.kind, event.text]), [['user', 'aim the authored spot at the painting'], ['home', N.COPY.chatPhysical]]);
  assert.equal(physical.store.getSnapshot().proposals.length, 0); assert.equal(physical.state.calls.length, 0);
  const den = chat('point the spotlight at the desk', room({ siteId: 'victoria' }).bridge);
  assert.deepEqual(den.events.at(-1), { kind: 'home', text: N.COPY.den.unavailable, chatAim: true });
  // Outside the Apartment there is no bridge: the message is not ours.
  assert.deepEqual(chat('aim the spot at the painting', null), { handled: false, events: [] });
});
