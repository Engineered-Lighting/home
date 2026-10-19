/* Chat bound to the room in the Apartment Home view (APT-C02, APT-C03).
 *
 * Pure and deterministic. Nothing here sends a message, calls a service or
 * opens a socket. It reads the room snapshot the Apartment view publishes
 * (schema below) and either names lights and named aims for chips, or claims
 * a message that carries the "About <selection>" chip:
 *   normalizeRoom(snapshot)            the current home's inventory, or null
 *   findReferences(text, room)         lights and named aims a reply names
 *   resolveActionTarget(target, room)  the light an action card names, or null
 *   bindEvent(event, room)             refs frozen to the home a reply arrived in
 *   availability(ref, binding, room)   "" when a chip may act, else the reason
 *   parseIntent(text, label)           brighter, dimmer, warmer, cooler, N%
 *   resolveAbout(text, about, room, bridge)
 * resolveAbout always claims its message. It returns a modeled change, an
 * unchanged note, a refusal or a question, and never routes anywhere else: a
 * chip-bearing message must not reach the model, Home Assistant or an
 * external provider. Only a Victoria light changes, and only in the room
 * model through the bridge the Apartment view supplies. Los Angeles stays
 * refused until a positive Chat origin contract exists (#287 keeps refusing
 * Chat output).
 */
(function (root) {
  "use strict";
  const SCHEMA = "home-apartment-chat-room-1";
  const SITES = Object.freeze({ echo: "Los Angeles", victoria: "Victoria" });
  const CHANNELS = Object.freeze({ spotlight: "spot", downward: "down" });
  const STEP_BRIGHTNESS = 0.2, MIN_DIMMED = 0.05, STEP_KELVIN = 400;
  const MAX_ITEMS = 64, MAX_LABEL = 80, MAX_TEXT = 2000;
  const ENTITY = /^light\.[a-z0-9_]{1,64}$/;

  const clean = value => typeof value === "string" ? value.normalize("NFKC").replace(/\s+/g, " ").trim() : "";
  const fold = value => clean(value).toLowerCase().replace(/[‘’]/g, "'");
  const isWordChar = ch => !!ch && /[\p{L}\p{N}_]/u.test(ch);
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const round2 = value => Math.round(value * 100) / 100;

  function items(list, kind) {
    if (!Array.isArray(list)) return [];
    const out = [], ids = new Set();
    for (const raw of list.slice(0, MAX_ITEMS)) {
      const id = clean(raw && raw.id), label = clean(raw && raw.label).slice(0, MAX_LABEL);
      if (!id || !label || ids.has(id)) continue;
      ids.add(id);
      const entityId = kind === "light" && typeof raw.entityId === "string" && ENTITY.test(raw.entityId) ? raw.entityId : null;
      out.push({ kind, id, label, entityId });
    }
    return out;
  }

  /* A snapshot that is missing, foreign or malformed yields null: chips stay
   * inert and the About chip is not offered. */
  function normalizeRoom(snapshot) {
    try {
      if (!snapshot || snapshot.schema !== SCHEMA || !Object.prototype.hasOwnProperty.call(SITES, snapshot.siteId)) return null;
      const key = clean(snapshot.key);
      if (!key) return null;
      const lights = items(snapshot.lights, "light"), namedAims = items(snapshot.namedAims, "aim");
      // A name two items share is ambiguous; it never becomes a chip.
      const counts = new Map();
      for (const item of [...lights, ...namedAims]) counts.set(fold(item.label), (counts.get(fold(item.label)) || 0) + 1);
      const freeze = item => Object.freeze({ ...item, ambiguous: counts.get(fold(item.label)) > 1 });
      const room = { siteId: snapshot.siteId, siteLabel: SITES[snapshot.siteId], key,
        lights: Object.freeze(lights.map(freeze)), namedAims: Object.freeze(namedAims.map(freeze)) };
      const raw = snapshot.selection;
      const pool = raw && raw.kind === "light" ? room.lights : raw && raw.kind === "aim" ? room.namedAims : [];
      const selected = raw && pool.find(item => item.id === raw.id);
      room.selection = selected ? Object.freeze({ kind: selected.kind, id: selected.id, label: selected.label }) : null;
      return Object.freeze(room);
    } catch (_) { return null; }
  }

  const ref = (item, room) => Object.freeze({ kind: item.kind, id: item.id, label: item.label, siteId: room.siteId, key: room.key });

  function occurrences(hay, needle) {
    const found = [];
    if (needle.length < 2) return found;
    for (let at = hay.indexOf(needle); at >= 0; at = hay.indexOf(needle, at + 1)) {
      if (!isWordChar(hay[at - 1]) && !isWordChar(hay[at + needle.length])) found.push([at, at + needle.length]);
    }
    return found;
  }

  /* Names the current home's lights and named aims in reply order. Longer
   * names win over names they contain ("Socket 4 spot" over "Socket 4"). */
  function findReferences(text, room) {
    if (!room || typeof text !== "string" || !text) return [];
    const hay = fold(text.slice(0, MAX_TEXT)), matches = [];
    for (const item of [...room.lights, ...room.namedAims]) {
      if (item.ambiguous) continue;
      for (const term of [fold(item.label), item.entityId].filter(Boolean)) {
        for (const [start, end] of occurrences(hay, term)) matches.push({ start, end, item });
      }
    }
    matches.sort((a, b) => a.start - b.start || (b.end - b.start) - (a.end - a.start));
    const out = [], seen = new Set();
    let reach = -1;
    for (const match of matches) {
      if (match.start < reach) continue;
      reach = match.end;
      const id = match.item.kind + ":" + match.item.id;
      if (!seen.has(id)) { seen.add(id); out.push(ref(match.item, room)); }
    }
    return out;
  }

  function resolveActionTarget(target, room) {
    if (!room || typeof target !== "string") return null;
    const wanted = fold(target);
    if (!wanted) return null;
    const exact = room.lights.filter(item => item.entityId === wanted || (!item.ambiguous && fold(item.label) === wanted));
    return exact.length === 1 ? ref(exact[0], room) : null;
  }

  /* A reply is bound to the home and room revision it completed in. Its refs
   * are resolved then and kept, so a later visit to another home shows them
   * unavailable instead of re-reading the words against the wrong inventory. */
  function bindEvent(event, room) {
    if (!room || !event) return null;
    const refs = event.kind === "home" || event.kind === "assistant" ? findReferences(event.text, room) : [];
    const action = event.kind === "action" ? resolveActionTarget(event.target, room) : null;
    return Object.freeze({ siteId: room.siteId, key: room.key, refs: Object.freeze(refs), action });
  }

  function availability(item, binding, room) {
    if (!room) return "The room isn't open.";
    if (!binding || !item) return "This reply came before this room view was open.";
    if (binding.siteId !== room.siteId || item.siteId !== room.siteId) {
      return `This reply was about ${SITES[binding.siteId] || "another home"}; you're viewing ${room.siteLabel}.`;
    }
    if (binding.key !== room.key || item.key !== room.key) return "The room changed since this reply.";
    const pool = item.kind === "light" ? room.lights : room.namedAims;
    return pool.some(entry => entry.id === item.id) ? "" : "This is no longer in the room.";
  }

  const FILLER = new Set(["make", "it", "its", "it's", "a", "an", "bit", "little", "tad", "touch", "please", "the", "set",
    "to", "light", "more", "slightly", "much", "lot", "this", "that", "one", "can", "you", "could", "would", "go", "be",
    "and", "just", "now", "here", "for", "me", "at", "is", "let's", "lets", "get", "put", "brightness", "level"]);
  const WORDS = {
    brighter: ["brightness", "up"], brighten: ["brightness", "up"], dimmer: ["brightness", "down"], dim: ["brightness", "down"],
    darker: ["brightness", "down"], warmer: ["kelvin", "warmer"], warm: ["kelvin", "warmer"], cooler: ["kelvin", "cooler"],
    cool: ["kelvin", "cooler"], colder: ["kelvin", "cooler"],
    spot: ["channel", "spotlight"], spotlight: ["channel", "spotlight"], down: ["channel", "downward"],
    downlight: ["channel", "downward"], downward: ["channel", "downward"],
  };

  /* Only these are representable; anything else asks instead of guessing. */
  function parseIntent(text, label) {
    let s = fold(text).slice(0, 200);
    if (!s) return { ok: false, reason: "empty" };
    const name = fold(label);
    if (name) s = s.split(name).join(" ");
    let level = null;
    s = s.replace(/(\d{1,3}(?:\.\d+)?)\s*(?:%|percent\b)/g, (_, value) => {
      level = level === null ? Number(value) : NaN; return " ";
    });
    const found = { brightness: [], kelvin: [], channel: [] };
    for (const word of s.replace(/[?.!,;:]+/g, " ").split(/\s+/).filter(Boolean)) {
      if (WORDS[word]) { found[WORDS[word][0]].push(WORDS[word][1]); continue; }
      if (!FILLER.has(word)) return { ok: false, reason: "unsupported" };
    }
    if (level !== null) {
      if (!(level >= 1 && level <= 100)) return { ok: false, reason: "level" };
      found.brightness.push("set");
    }
    const one = list => new Set(list).size <= 1 && list.length <= 1;
    if (!one(found.brightness) || !one(found.kelvin) || new Set(found.channel).size > 1) return { ok: false, reason: "conflict" };
    if (!found.brightness.length && !found.kelvin.length) return { ok: false, reason: "no_change" };
    return { ok: true, channel: found.channel[0] || "spotlight", brightness: found.brightness[0] || null,
      level: found.brightness[0] === "set" ? level : null, kelvin: found.kelvin[0] || null };
  }

  function computeChange(intent, state, range) {
    if (!state || typeof state.on !== "boolean" || !finite(state.brightness) || !finite(state.color_temp_kelvin)
        || !Array.isArray(range) || !finite(range[0]) || !finite(range[1])) return null;
    const patch = {};
    if (intent.brightness === "set") { patch.on = true; patch.brightness = round2(intent.level / 100); }
    else if (intent.brightness === "up") { patch.on = true; patch.brightness = round2(Math.min(1, (state.on ? state.brightness : 0) + STEP_BRIGHTNESS)); }
    else if (intent.brightness === "down") {
      if (!state.on || state.brightness <= MIN_DIMMED) return { patch: null, note: "off_or_lowest" };
      patch.brightness = round2(Math.max(MIN_DIMMED, state.brightness - STEP_BRIGHTNESS));
    }
    if (intent.kelvin === "warmer") patch.color_temp_kelvin = Math.max(range[0], state.color_temp_kelvin - STEP_KELVIN);
    if (intent.kelvin === "cooler") patch.color_temp_kelvin = Math.min(range[1], state.color_temp_kelvin + STEP_KELVIN);
    for (const key of Object.keys(patch)) if (patch[key] === state[key]) delete patch[key];
    return Object.keys(patch).length ? { patch } : { patch: null, note: "unchanged" };
  }

  const describe = channel => `${channel.on ? Math.round(channel.brightness * 100) + "%" : "off"} · ${channel.color_temp_kelvin} K`;
  const outcome = (kind, text) => Object.freeze({ outcome: kind, text });

  /* Claim-first: every return is the final word on this message. */
  function resolveAbout(text, about, room, bridge) {
    try {
      const label = about && clean(about.label) || "the selection";
      if (!about || !room) return outcome("refused", `The room isn't ready, so nothing changed for ${label}.`);
      const selection = room.selection;
      if (about.siteId !== room.siteId || about.key !== room.key || !selection
          || selection.kind !== about.kind || selection.id !== about.id) {
        return outcome("refused", "The selection changed before this was sent, so nothing changed. Select the light again and resend.");
      }
      if (room.siteId !== "victoria") {
        return outcome("refused", `Chat can't change ${label} in ${room.siteLabel} yet, so nothing was sent. Use the Light tab to adjust it.`);
      }
      if (about.kind !== "light") {
        return outcome("clarify", `What should change about ${label}? From Chat, a selected Den light can get brighter, dimmer, warmer or cooler, or go to a level like 50%.`);
      }
      const intent = parseIntent(text, label);
      if (!intent.ok) {
        return outcome("clarify", `I can make ${label} brighter, dimmer, warmer or cooler, or set a level like 50% (the spot, unless you say down). Nothing changed.`);
      }
      const current = bridge && typeof bridge.readModeledLight === "function" ? bridge.readModeledLight(about) : null;
      const channelName = CHANNELS[intent.channel];
      const state = current && current.state && current.state[intent.channel];
      const range = current && current.color_temp_ranges && current.color_temp_ranges[intent.channel];
      const change = computeChange(intent, state, range);
      if (!change) return outcome("refused", `${label} isn't available in the room model right now, so nothing changed.`);
      if (!change.patch) {
        return outcome("unchanged", change.note === "off_or_lowest"
          ? `${label} ${channelName} is ${state.on ? "already at its lowest" : "off"}, so nothing changed.`
          : `${label} ${channelName} is already ${describe(state)}, so nothing changed.`);
      }
      const patch = { [intent.channel]: change.patch };
      const applied = typeof bridge.applyModeledPatch === "function" ? bridge.applyModeledPatch(about, patch) : null;
      const after = applied && applied.ok === true && applied.state && applied.state[intent.channel];
      if (!after || Object.keys(change.patch).some(key => after[key] !== change.patch[key])) {
        return outcome("refused", `The room model didn't accept that change for ${label}, so nothing changed.`);
      }
      return outcome("modeled", `${label} ${channelName} is now ${describe(after)} in the room model. Nothing was sent to a light.`);
    } catch (_) {
      return outcome("refused", "That couldn't be resolved against the selection, so nothing changed.");
    }
  }

  root.HomeApartmentChatContext = Object.freeze({ SCHEMA, normalizeRoom, findReferences, resolveActionTarget, bindEvent,
    availability, parseIntent, computeChange, resolveAbout });
})(typeof window !== "undefined" ? window : globalThis);
