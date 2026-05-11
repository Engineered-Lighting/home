/* Home — scripted demo conversation.
 *
 * Each entry is one event to render, with a `dt` delay (ms) before it appears.
 * A few "pending" actions transition to "success" — those carry a `becomes`
 * payload the player will apply after `dt + becomesAfter` ms.
 *
 * The player rewinds and loops on its own (controlled by HomeApp).
 */

const SCRIPT = [
  /* ── Exchange 1 ─────────────────────────────────────────────────────── */
  {
    dt: 200,
    event: { kind: "user", text: "Make the living room lights cooler." },
  },
  {
    dt: 700,
    event: { kind: "thinking", text: "Interpreting request as a lighting color temperature change." },
  },
  {
    dt: 800,
    event: { kind: "thinking", text: "Resolved target area: living_room (3 fixtures, all support kelvin)." },
  },
  {
    dt: 600,
    event: {
      kind: "tool",
      name: "get_entity_state",
      args: { target: "light.living_room_lamps" },
      status: "success",
      latency: "12ms",
    },
  },
  {
    dt: 500,
    event: {
      kind: "action",
      id: "act-1",
      title: "Adjusting living room lights",
      service: "light.turn_on",
      target: "area.living_room",
      attrs: { color_temp_kelvin: 5000, brightness: "unchanged" },
      status: "pending",
    },
    becomesAfter: 1100,
    becomes: { status: "success", latency: "83ms" },
  },
  {
    dt: 800,
    event: {
      kind: "home",
      stream: "Done. I shifted the three living room fixtures to 5000 K. The kitchen island is unchanged — want me to match it?",
    },
  },

  /* ── Exchange 2 (voice) ─────────────────────────────────────────────── */
  {
    dt: 3200,
    event: { kind: "voice", text: "Yeah, and drop them to 3000 after sunset." },
  },
  {
    dt: 700,
    event: { kind: "thinking", text: "Two requests. Matching island temperature, then scheduling an evening warm-down." },
  },
  {
    dt: 900,
    event: {
      kind: "action",
      id: "act-2",
      title: "Matching kitchen island to living room",
      service: "light.turn_on",
      target: "light.kitchen_island",
      attrs: { color_temp_kelvin: 5000 },
      status: "pending",
    },
    becomesAfter: 900,
    becomes: { status: "success", latency: "61ms" },
  },
  {
    dt: 700,
    event: {
      kind: "action",
      id: "act-3",
      title: "Created automation · evening warm-down",
      service: "automation.create",
      target: "lr_evening_warm",
      attrs: {
        trigger: "sun.sunset",
        action: "light.turn_on",
        color_temp_kelvin: 3000,
        area: "living_room",
      },
      status: "pending",
    },
    becomesAfter: 1300,
    becomes: { status: "success", latency: "124ms" },
  },
  {
    dt: 700,
    event: {
      kind: "home",
      stream: "Set. Island matches at 5000 K, and the living room will warm to 3000 K every day after sunset. I'll surface any fixture that pushes back.",
    },
  },
];

/* Simple deterministic responder for free-form user input.
 * Matches a few keywords and returns a {thinking, action?, response} pair.
 * Falls back to a polite uncertain response. */
const FALLBACK_REPLIES = [
  "I'm running locally only, so I don't have an answer for that yet — but I logged the request.",
  "I didn't catch a clear command in that. Want to rephrase, or should I leave it?",
  "Not sure how to act on that one. Nothing changed.",
];

function pickReply(userText) {
  const t = userText.toLowerCase();

  if (/(unlock|open).*(door|gate|lock)|disarm|security off|alarm off/.test(t)) {
    return {
      thinking: "Detected a security-sensitive action. Requires explicit confirmation.",
      action: {
        title: "Unlock front door",
        service: "lock.unlock",
        target: "lock.front_door",
        attrs: { actor: "voice", confirm: "required" },
        status: "pending-confirm",
      },
      response: "Holding on the front door — confirm above to proceed.",
    };
  }

  if (/(dim|dimmer|lower|darker|softer)/.test(t) && /light|lamp/.test(t)) {
    return {
      thinking: "Interpreting as a brightness reduction. Defaulting to the active area.",
      action: {
        title: "Dimming lights in the active area",
        service: "light.turn_on",
        target: "area.active",
        attrs: { brightness_pct: 35 },
        latency: "71ms",
      },
      response: "Brought the active area down to 35%. Let me know if you want a different floor.",
    };
  }
  if (/(off|turn off|kill)/.test(t) && /light|lamp/.test(t)) {
    return {
      thinking: "Interpreting as turn off lights in the active area.",
      action: {
        title: "Turning off lights",
        service: "light.turn_off",
        target: "area.active",
        attrs: {},
        latency: "52ms",
      },
      response: "Off. Tell me when to bring them back.",
    };
  }
  if (/warm|warmer/.test(t) && /light|lamp|kelvin/.test(t)) {
    return {
      thinking: "Color temperature decrease. Resolving area.",
      action: {
        title: "Warming lights",
        service: "light.turn_on",
        target: "area.living_room",
        attrs: { color_temp_kelvin: 2700 },
        latency: "79ms",
      },
      response: "Shifted living room to 2700 K. Cozy.",
    };
  }
  if (/cool|cooler/.test(t) && /light|lamp|kelvin/.test(t)) {
    return {
      thinking: "Color temperature increase. Resolving area.",
      action: {
        title: "Cooling lights",
        service: "light.turn_on",
        target: "area.living_room",
        attrs: { color_temp_kelvin: 5000 },
        latency: "83ms",
      },
      response: "Living room is at 5000 K.",
    };
  }
  if (/(temperature|temp|degrees|thermo)/.test(t)) {
    return {
      thinking: "Reading thermostat state for the main floor.",
      response: "Main floor is sitting at 21.4 °C. Setpoint is 21.0. Nothing's running right now.",
    };
  }
  if (/(who|what).*you/.test(t) || /(hello|hi|hey)/.test(t)) {
    return {
      response: "Local. Listening. Nothing pending.",
    };
  }
  if (/(status|how|metrics|gpu|vram)/.test(t)) {
    return {
      response: "All good. Model is qwen3-32b, running on the RTX 6000. Last response took 412 ms.",
    };
  }
  return {
    response: FALLBACK_REPLIES[Math.floor(Math.random() * FALLBACK_REPLIES.length)],
  };
}

Object.assign(window, { SCRIPT, pickReply });
