/* Chat aim proposals (APT-C04): an agreed UI-side parser that turns "aim the
 * spot at the painting" into a proposal with origin "chat" on the same
 * shared proposal path as a drag, plus the reply and card it adds to the
 * feed. It runs before personal memory, lighting control and the agent, and
 * a message it handles goes nowhere else: no HomeLightingControl dispatch,
 * no agent or WebSocket send and no service call. Set and Discard on the
 * card are the room's own Set and Discard (the Apartment's aim bridge).
 * Only modeled and simulated lights can take a Chat proposal; a physical
 * light is refused with its reason. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.HomeApartmentChatAim = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  // Aim verbs only, so "turn on the coffee table light" stays a lighting command.
  const AIM_VERB = /\b(?:aim|point|shine|focus|spotlight|light up)\b/i;
  const COPY = Object.freeze({
    proposed: (light, aim) => `I'd light ${aim} with ${light}. It's previewed in the room; Set aim or Discard it here or in the room.`,
    unreachable: (light, aim, why) => `${light} can't reach ${aim}: ${why}. Discard it, or choose another named aim.`,
    noAim: names => names.length ? `I don't know that named aim. This home has ${names.join(', ')}.` : 'This home has no named aims yet. Save one from a proposed aim first.',
    noLight: 'There is no aimable light in this home to point there.',
    unavailable: reason => reason,
    card: Object.freeze({ open: 'Proposed', set: 'Aim set', discarded: 'Discarded', gone: 'No longer proposed' }),
  });
  const text = value => typeof value === 'string' ? value.trim() : '';
  const lower = value => text(value).toLocaleLowerCase();
  const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const mentions = (message, name) => {
    const clean = lower(name);
    return clean.length > 1 && new RegExp(`(?:^|[^\\p{L}\\p{N}])${escape(clean)}(?:$|[^\\p{L}\\p{N}])`, 'u').test(message);
  };
  const longest = (message, items) => (items || []).filter(item => mentions(message, item.name))
    .sort((a, b) => text(b.name).length - text(a.name).length)[0] || null;

  /* null when the message is not an aim request for this home; otherwise
   * what to propose, or why not. `room` is the Apartment's aim bridge view:
   * { aims: [{ id, name }], lights: [{ id, name, short? }], selectedId, available, reason }. */
  function parseAimRequest(message, room) {
    const said = lower(message);
    if (!said || said.startsWith('/') || !AIM_VERB.test(said) || !room) return null;
    const aims = Array.isArray(room.aims) ? room.aims : [];
    const aimed = /\b(?:at|on|to)\b/.test(said);
    // A home that can't take a Chat aim says why rather than passing it on.
    if (room.available === false && aimed) return { kind: 'reply', text: COPY.unavailable(room.reason) };
    const aim = longest(said, aims);
    // Without a named aim this is not ours: an aim verb alone stays with the agent.
    if (!aim) return aimed && aims.length ? { kind: 'reply', text: COPY.noAim(aims.map(item => item.name)) } : null;
    if (room.available === false) return { kind: 'reply', text: COPY.unavailable(room.reason) };
    const lights = Array.isArray(room.lights) ? room.lights : [];
    const named = longest(said, lights) || longest(said, lights.map(light => ({ ...light, name: light.short || '' })).filter(light => light.name));
    const light = named ? lights.find(item => item.id === named.id) : lights.find(item => item.id === room.selectedId) || lights[0] || null;
    if (!light) return { kind: 'reply', text: COPY.noLight };
    return { kind: 'propose', fixture_id: light.id, light, aim };
  }

  /* Handles one submitted message. Returns true when it was an aim request
   * for this home, so the caller routes it nowhere else. */
  function run(message, { addEvent, bridge, now = Date.now } = {}) {
    let room = null;
    try { room = bridge?.view?.() || null; } catch (_) { room = null; }
    const parsed = parseAimRequest(message, room);
    if (!parsed || typeof addEvent !== 'function') return false;
    addEvent({ kind: 'user', text: text(message) });
    if (parsed.kind === 'reply') { addEvent({ kind: 'home', text: parsed.text, chatAim: true }); return true; }
    let result = null;
    try { result = bridge.propose({ fixture_id: parsed.fixture_id, aim_id: parsed.aim.id, origin: 'chat' }); } catch (_) { result = null; }
    if (!result?.ok) {
      addEvent({ kind: 'home', text: result?.reason || COPY.unavailable(room.reason || 'That aim is unavailable right now.'), chatAim: true });
      return true;
    }
    const lightName = parsed.light.name || parsed.light.id, aimName = parsed.aim.name;
    addEvent({ kind: 'home', text: result.proposal.target.valid ? COPY.proposed(lightName, aimName)
      : COPY.unreachable(lightName, aimName, result.why || 'the spot is outside its limits'), chatAim: true });
    addEvent({ kind: 'apartment-aim-proposal', site_id: room.siteId, fixture_id: parsed.fixture_id, proposal_id: result.proposal.id,
      light_name: lightName, aim_id: parsed.aim.id, aim_name: aimName, origin: 'chat', truth: room.truth || null, created_at: now() });
    return true;
  }

  // The card's state from the live proposal path: open while its proposal is
  // the light's pending one, then whatever the person did, or gone.
  function cardState(card, live) {
    if (card?.outcome === 'set' || card?.outcome === 'discarded') return card.outcome;
    return live?.pending ? 'open' : 'gone';
  }

  return Object.freeze({ COPY, AIM_VERB, parseAimRequest, run, cardState });
});
