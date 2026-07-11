/* home-proactive.jsx — the proactive-assistant coordinator.
 *
 * A home that feels alive: it greets you when you actually walk in, can
 * notice you entering a room and offer help, and keeps the conversation
 * open for a beat — without ever being spammy.
 *
 * HA detects low-level events (person left/arrived, room occupancy, face
 * recognition) and does the reliable physical actions (lights). THIS module
 * owns the conversational POLICY: the two-stage arrival confirmation, should
 * we speak, what to say, the follow-up window, suppression/cooldowns, the UI.
 *
 * The KEY product rule — GPS says "probably home"; it is NOT trusted for
 * "just walked in". The spoken welcome requires a two-stage confirm:
 *   1. HA location → person entered the home zone  → arms a pending window
 *   2. Frigate face-rec confirms it's the arriving person → only then speak
 *
 * Render-stability discipline (the bug class that hung the boot sequence and
 * churned the camera streams): exactly ONE useState lives in HomeApp; all
 * coordinator machinery is closure state in a once-constructed object; every
 * returned callback is stable; the only React effect is the HA-WS
 * subscription, keyed on primitives. No timer-bearing effect.
 *
 * Loaded via index.html AFTER home-events.jsx, BEFORE the simulation block
 * (simulation-data.jsx references window.PROACTIVE_CONSTANTS).
 */

(function () {
  // ── Config — EDIT THESE for the real install (see docs/RUNBOOK.md) ──────

  // The wall-mounted Voice PE puck's assist_satellite entity_id.
  // (Resolved from ha_config_steps.md + scripts/listen_pipeline.py.)
  const VOICE_PE_SATELLITE = "assist_satellite.home_assistant_voice_0a92e0_assist_satellite";

  // Optional per-room satellite override for room-entry prompts. Falls back
  // to VOICE_PE_SATELLITE. e.g. { kitchen: "assist_satellite.kitchen" }
  const ROOM_SATELLITE_MAP = {};

  // Interior entry camera(s). A high-confidence face match on ANY interior
  // camera confirms an arrival; this list gates only the WEAK "person seen
  // but not identified" corroboration signal.
  const ARRIVAL_CONFIRM_CAMERAS = ["living_room"];

  // The HA service used to speak + open the follow-up mic. start_conversation
  // (HA 2025.x) speaks an opener AND opens the conversation. If the install's
  // HA is older, set service:"announce" — the follow-up window then won't
  // open, which degrades gracefully (the coordinator detects it via the
  // observed satellite state and just doesn't enter listening_for_followup).
  const ANNOUNCE_SERVICE = { domain: "assist_satellite", service: "start_conversation" };

  // Templated opener strings. {name} / {room} are substituted. The follow-up
  // REPLY is what reaches the LLM — these openers are deterministic on purpose.
  const PROACTIVE_MESSAGES = {
    welcome: "Welcome home, {name}.",
    welcomeSoft: "Welcome home.",
    room: "Hey — the {room} lights are ready. Need anything?",
  };

  // When Frigate is offline and an arrival window times out with no sighting
  // at all, fall back to a GPS-only spoken welcome (best signal available).
  const ARRIVAL_GPS_ONLY_FALLBACK = true;

  // Assumed HA assist_satellite entity states. Verified against the install's
  // HA version in impl step 1 — this is the one spot to adjust if they differ.
  const SAT = { IDLE: "idle", LISTENING: "listening", PROCESSING: "processing", RESPONDING: "responding" };

  // ── Tunable constants ───────────────────────────────────────────────────
  const PROACTIVE_CONSTANTS = {
    minConfidence: 0.6,             // rule 2 — below this, ignore
    confirmHighConf: 0.85,          // identity_confirmed threshold for a STRONG confirm
    quietHoursStart: 23,            // rule 3 — 23:00..07:00: room prompts off, welcome → silent
    quietHoursEnd: 7,
    loudVolume: 0.5,                // rule 8 — media at/above this in the room = "loud"
    assistantQuietMs: 20000,        // rule 9
    globalProactiveCooldownMs: 90000,   // rule 10
    perRoomCooldownMs: 600000,      // rule 11 — 10 min
    meaningfulAbsenceMs: 1800000,   // rule 13 reset gate — 30 min away from a room
    postWelcomeQuietMs: 120000,     // rule 14 — no room prompts for 2 min after a welcome
    welcomeHomeCooldownMs: 1800000, // stage-1 gate — 30 min between spoken welcomes
    multiRoomWindowMs: 2500,        // room_entered buffer — 2+ rooms in this window → suppress all
    dedupeWindowMs: 180000,         // cross-transport dedupe — absorbs WS-vs-SSE `for:` skew
    coordinatorDebounceMs: 20000,   // SSE-path GPS-flap debounce
    arrivalConfirmWindowMs: 300000, // stage-1 → stage-2 wait — 5 min
    followupWindowMs: 12000,        // how long we expect a follow-up after the puck opens its mic
    announceWatchdogMs: 12000,      // announce fired but satellite never moved → give up
    processingGuardMs: 30000,       // follow-up reply processing safety net
    transientHoldMs: 4000,          // how long `suppressed`/`followup_timeout` linger before idle
  };

  const IDLE_STATE = {
    phase: "idle", reason: null, room: null, person: null,
    away: false, sightingLevel: "none", sinceTs: 0,
  };

  // ── normalizeProactiveEvent — pure ──────────────────────────────────────
  // Accepts either the HA-custom-event payload or the bridge SSE payload and
  // produces the one internal shape. `transport` ("ha_ws" | "sse") is set here.
  function normalizeProactiveEvent(raw, transport) {
    raw = raw || {};
    let ts = (typeof raw.timestamp === "number" ? raw.timestamp
      : typeof raw.ts === "number" ? raw.ts
      : Date.now() / 1000);
    if (ts > 1e12) ts = ts / 1000;  // ms → s
    return {
      type: raw.type || null,
      person: raw.person || null,
      display_name: raw.display_name || raw.displayName || null,
      room: raw.room || null,
      confidence: typeof raw.confidence === "number" ? raw.confidence : 1.0,
      timestamp: ts,
      source: transport === "sse" ? "sse" : "ha_ws",
      raw_source: raw.raw_source || raw.source || "home_assistant",
      // `test: true` events (the HA test-harness buttons + /proactive test)
      // bypass the time-based rate-limits (cooldowns, dedupe, once-per-
      // session) so the pipeline can be re-tested repeatedly. They still
      // respect the context modes (sleep/DND/movie/quiet-hours) so you can
      // still test suppression with /proactive <mode> on.
      test: raw.test === true,
    };
  }

  // ── evaluateProactive — pure predicate, the entire suppression rulebook ──
  // Returns { allow, reason } — the FIRST failing rule's reason, else "ok".
  // `now` is injected so the function has no clock of its own (testable).
  function evaluateProactive(event, ctx, now) {
    const C = PROACTIVE_CONSTANTS;
    // 1 — malformed
    if (!event || !event.type) return { allow: false, reason: "malformed" };
    if (event.type === "room_entered" && !event.room) return { allow: false, reason: "malformed" };
    if ((event.type === "person_arrived_home" || event.type === "identity_confirmed")
        && !event.person && !event.display_name) return { allow: false, reason: "malformed" };
    const isRoom = event.type === "room_entered";
    // test events bypass the time-based rate-limits (see normalizeProactiveEvent)
    const skipRateLimits = !!event.test;
    // 2 — confidence floor
    if (typeof event.confidence === "number" && event.confidence < C.minConfidence) {
      return { allow: false, reason: "low-confidence" };
    }
    // 3 — quiet hours (caller downgrades a welcome to silent; room prompts fully off)
    const hr = new Date(now).getHours();
    const inQuiet = C.quietHoursStart > C.quietHoursEnd
      ? (hr >= C.quietHoursStart || hr < C.quietHoursEnd)
      : (hr >= C.quietHoursStart && hr < C.quietHoursEnd);
    if (inQuiet) return { allow: false, reason: "quiet-hours" };
    // 4 — sleep mode
    if (ctx.modes && ctx.modes.sleep) return { allow: false, reason: "sleep-mode" };
    // 5 — DND
    if (ctx.modes && ctx.modes.dnd) return { allow: false, reason: "dnd" };
    // 6 — focus mode (room prompts only; welcome still allowed)
    if (ctx.modes && ctx.modes.focus && isRoom) return { allow: false, reason: "focus-mode" };
    // 7 — movie mode / video playing in the room
    const roomMedia = (event.room && ctx.media) ? ctx.media[event.room] : null;
    const mediaPlaying = !!(roomMedia && roomMedia.state === "playing");
    const mediaIsVideo = !!(roomMedia && /movie|tvshow|video/i.test(roomMedia.category || ""));
    if ((ctx.modes && ctx.modes.movie) || (mediaPlaying && mediaIsVideo)) {
      return { allow: false, reason: "movie-mode" };
    }
    // 8 — media playing loud in the room
    if (mediaPlaying && typeof roomMedia.volume_level === "number"
        && roomMedia.volume_level >= C.loudVolume) {
      return { allow: false, reason: "media-playing" };
    }
    if (isRoom && !skipRateLimits) {
      const rc = (ctx.ledger.roomCooldowns && ctx.ledger.roomCooldowns[event.room]) || 0;
      if (now - rc < C.perRoomCooldownMs) return { allow: false, reason: "room-cooldown" };
    }
    // 9 — assistant spoke too recently
    if (!skipRateLimits && now - (ctx.ledger.lastAssistantSpokeTs || 0) < C.assistantQuietMs) {
      return { allow: false, reason: "assistant-recently-spoke" };
    }
    // 10 — global proactive cooldown
    if (!skipRateLimits && now - (ctx.ledger.lastProactiveSpeakTs || 0) < C.globalProactiveCooldownMs) {
      return { allow: false, reason: "global-cooldown" };
    }
    // room-only rate-limit rules (11/12/14) — skipped for test events
    if (isRoom && !skipRateLimits) {
      // 11 — per-room cooldown
      const rc = (ctx.ledger.roomCooldowns && ctx.ledger.roomCooldowns[event.room]) || 0;
      if (now - rc < C.perRoomCooldownMs) return { allow: false, reason: "room-cooldown" };
      // 12 — once per room per session (reset gate #13 is folded into greetedThisSession)
      if (ctx.greetedThisSession) return { allow: false, reason: "room-once-per-session" };
      // 14 — room prompt shortly after a welcome
      if (now - (ctx.ledger.lastWelcomeTs || 0) < C.postWelcomeQuietMs) {
        return { allow: false, reason: "post-welcome-quiet" };
      }
    }
    return { allow: true, reason: "ok" };
  }

  // ── createCoordinator — all the machinery, constructed exactly once ─────
  // Internal state lives as closure variables (equivalent to refs — the
  // factory runs once per coordinator lifetime). `env` carries only stable
  // things (the HA client ref, the React setState, addEvent) + mirror refs
  // for the values that change (media/debug/sim/frigateOnline).
  function createCoordinator(env) {
    const { haClientRef, addEvent, setProactiveState,
            mediaRef, debugModeRef, simActiveRef, frigateOnlineRef } = env;

    // ── internal state ──
    let phase = "idle";
    let reason = null;
    let curRoom = null;
    let curPerson = null;
    let away = false;
    let sightingLevel = "none";   // none | person-unidentified | weak-identity
    let sinceTs = Date.now();
    let satState = SAT.IDLE;
    let sawResponding = false;

    const timers = {};            // name -> timeoutId
    const gpsFlap = new Map();    // personKey -> { stableState, debounceId }
    const dedupe = new Map();     // logicalKey -> lastProcessedTs
    let roomBuffer = null;        // { rooms:Set, lastEvt, timerId }
    let arrival = null;           // { person, displayName, episodeId } — single-track
    let pendingFollowup = null;   // { episodeId, anchorTs, expectUntilTs }
    let episodeSeq = 0;

    const ledger = {
      lastProactiveSpeakTs: 0,
      lastWelcomeTs: _readWelcomedAt(),
      lastAssistantSpokeTs: 0,
      roomCooldowns: {},          // room -> ts of last room prompt
      greetedRooms: {},           // room -> ts greeted (this session)
      roomLastSeenTs: {},         // room -> ts last room_entered (for the absence reset gate)
    };
    const ctxOverride = { sleep: false, dnd: false, focus: false, movie: false };

    function _readWelcomedAt() {
      try {
        const v = parseFloat(window.localStorage.getItem("hg-welcomedAt") || "0");
        return isFinite(v) && v > 0 ? v * 1000 : 0;  // stored in seconds
      } catch (e) { return 0; }
    }
    function _writeWelcomedAt(nowMs) {
      try { window.localStorage.setItem("hg-welcomedAt", String(nowMs / 1000)); } catch (e) {}
    }

    // ── timer helpers ──
    function setTimer(name, ms, fn) {
      clearTimer(name);
      timers[name] = setTimeout(() => { delete timers[name]; try { fn(); } catch (e) { /* noop */ } }, ms);
    }
    function clearTimer(name) {
      if (timers[name]) { clearTimeout(timers[name]); delete timers[name]; }
    }
    function clearAllTimers() {
      for (const k of Object.keys(timers)) { clearTimeout(timers[k]); delete timers[k]; }
      for (const [, v] of gpsFlap) { if (v && v.debounceId) clearTimeout(v.debounceId); }
      gpsFlap.clear();
      if (roomBuffer && roomBuffer.timerId) clearTimeout(roomBuffer.timerId);
      roomBuffer = null;
    }

    // ── UI emit ──
    function syncState(extra) {
      setProactiveState({
        phase, reason, room: curRoom, person: curPerson,
        away, sightingLevel, sinceTs,
        ...(extra || {}),
      });
    }
    function emitFeed(text) {
      try { addEvent({ kind: "proactive", text }); } catch (e) {}
    }
    function emitDiag(text) {
      // diag events are /debug-gated in the feed — zero noise unless debugging
      try { addEvent({ kind: "diag", channel: "proactive", text: "[proactive] " + text }); } catch (e) {}
    }

    // ── the one transition primitive ──
    function transition(to, opts) {
      opts = opts || {};
      phase = to;
      if ("reason" in opts) reason = opts.reason;
      if ("room" in opts) curRoom = opts.room;
      if ("person" in opts) curPerson = opts.person;
      if ("away" in opts) away = opts.away;
      if ("sightingLevel" in opts) sightingLevel = opts.sightingLevel;
      sinceTs = Date.now();
      syncState();
    }

    function flashTransient(to, reasonText) {
      transition(to, { reason: reasonText || null });
      setTimer("transientHold", PROACTIVE_CONSTANTS.transientHoldMs, () => {
        // only drop to idle if still showing the transient state
        if (phase === to) transition("idle", { reason: null, room: null });
      });
    }

    function hardReset() {
      clearAllTimers();
      phase = "idle"; reason = null; curRoom = null; curPerson = null;
      away = false; sightingLevel = "none"; satState = SAT.IDLE; sawResponding = false;
      arrival = null; pendingFollowup = null;
      sinceTs = Date.now();
      // In Simulation Mode the SIM owns the `proactive` UI state (scenarios
      // drive it directly) — the coordinator must not clobber it. hardReset
      // still clears the coordinator's INTERNAL state above; it just doesn't
      // write React state. On sim DEACTIVATE this runs with simActive false
      // and correctly resets the UI to idle.
      if (!simActiveRef.current) syncState();
    }

    function clearStatusAction(action) {
      clearTimer("arrivalConfirmWindow");
      clearTimer("followupWindow");
      clearTimer("announceWatchdog");
      clearTimer("processingGuard");
      clearTimer("transientHold");
      arrival = null; pendingFollowup = null; sawResponding = false;
      phase = "idle"; reason = null; curRoom = null; curPerson = null;
      sightingLevel = "none";
      sinceTs = Date.now();
      syncState();
      emitDiag(`status row ${action === "acknowledge" ? "acknowledged" : "dismissed"}`);
    }

    function acknowledgePending() {
      clearStatusAction("acknowledge");
    }

    function dismissPending() {
      clearStatusAction("dismiss");
    }

    // ── context builder for evaluateProactive ──
    function buildCtx(event, now) {
      let greetedThisSession = false;
      if (event && event.type === "room_entered" && event.room) {
        const greetedTs = ledger.greetedRooms[event.room] || 0;
        if (greetedTs) {
          // reset gate (#13): cleared only after meaningfulAbsenceMs away.
          // roomLastSeenTs tracks the most recent room_entered; if the gap
          // since the greeting is long AND we haven't seen the room since,
          // treat the session-greet as expired.
          const lastSeen = ledger.roomLastSeenTs[event.room] || 0;
          const awayLongEnough = (now - Math.max(greetedTs, lastSeen)) >= PROACTIVE_CONSTANTS.meaningfulAbsenceMs;
          greetedThisSession = !awayLongEnough;
        }
      }
      return {
        modes: {
          sleep: !!ctxOverride.sleep,
          dnd: !!ctxOverride.dnd,
          focus: !!ctxOverride.focus,
          movie: !!ctxOverride.movie,
        },
        media: mediaRef.current || {},
        ledger: {
          lastAssistantSpokeTs: ledger.lastAssistantSpokeTs,
          lastProactiveSpeakTs: ledger.lastProactiveSpeakTs,
          lastWelcomeTs: ledger.lastWelcomeTs,
          roomCooldowns: ledger.roomCooldowns,
        },
        greetedThisSession,
      };
    }

    // ── the announce / speak path ──
    function fillTemplate(tpl, vars) {
      return String(tpl || "").replace(/\{(\w+)\}/g, (m, k) => (vars && vars[k] != null ? vars[k] : m));
    }
    function speak(kind, vars, opts) {
      opts = opts || {};
      const now = Date.now();
      ledger.lastProactiveSpeakTs = now;
      ledger.lastAssistantSpokeTs = now;
      const message = fillTemplate(
        kind === "welcome" ? PROACTIVE_MESSAGES.welcome
        : kind === "welcomeSoft" ? PROACTIVE_MESSAGES.welcomeSoft
        : PROACTIVE_MESSAGES.room,
        vars,
      );
      const satellite = (opts.room && ROOM_SATELLITE_MAP[opts.room]) || VOICE_PE_SATELLITE;
      sawResponding = false;
      satState = SAT.IDLE;
      transition("speaking_proactive_message", { reason: null, room: opts.room || null, person: opts.person || null });
      // announceWatchdog — the satellite never moved at all → give up
      setTimer("announceWatchdog", PROACTIVE_CONSTANTS.announceWatchdogMs, () => {
        emitDiag(sawResponding
          ? "announce played but the follow-up mic never opened"
          : "no satellite response — announce may have failed");
        transition("idle", { reason: null, room: null, person: null });
      });
      const client = haClientRef.current;
      const callP = (client && typeof client.call === "function")
        ? client.call({
            type: "call_service",
            domain: ANNOUNCE_SERVICE.domain,
            service: ANNOUNCE_SERVICE.service,
            service_data: ANNOUNCE_SERVICE.service === "start_conversation"
              ? { start_message: message, extra_system_prompt: opts.extraPrompt || undefined }
              : { message: message },
            target: { entity_id: satellite },
          })
        : Promise.reject(new Error("HA not connected"));
      Promise.resolve(callP).catch((e) => {
        clearTimer("announceWatchdog");
        emitDiag("announce call failed: " + (e && e.message || e));
        transition("idle", { reason: null, room: null, person: null });
      });
    }

    // ── return-home lights (idempotent — HA also runs this as a backstop) ──
    function fireReturnHomeScene() {
      const client = haClientRef.current;
      if (!client || typeof client.call !== "function") return;
      Promise.resolve(client.call({
        type: "call_service", domain: "script", service: "turn_on",
        target: { entity_id: "script.homeai_return_home" },
      })).catch(() => { /* HA backstop covers it */ });
    }

    // ── event handlers ──
    function handleLeftHome(evt) {
      // SAFETY action — bypasses evaluateProactive entirely. Cancels any
      // in-flight episode. (HA already turned the lights off; this is the
      // app's UI reflection + "away" state.)
      clearAllTimers();
      arrival = null; pendingFollowup = null; sawResponding = false;
      phase = "idle"; reason = null; curRoom = null; curPerson = null;
      away = true; sightingLevel = "none";
      sinceTs = Date.now();
      syncState();
      emitFeed("Away mode — lights off.");
    }

    function handleArrivedHome(evt) {
      const now = Date.now();
      // stage-1 gate: welcome cooldown (the trash-run backstop). Skipped for
      // test events so the harness can be re-run without a 30-min wait.
      if (!evt.test && now - ledger.lastWelcomeTs < PROACTIVE_CONSTANTS.welcomeHomeCooldownMs) {
        away = false; syncState();
        emitDiag(`arrival ignored — welcome cooldown (${Math.round((now - ledger.lastWelcomeTs) / 60000)}m ago)`);
        return;
      }
      // single-track: one pending arrival at a time
      if (phase !== "idle") {
        emitDiag(`arrival for ${evt.display_name || evt.person} dropped — busy (${phase})`);
        return;
      }
      away = false;
      arrival = {
        person: evt.person,
        displayName: evt.display_name || _personLabel(evt.person),
        episodeId: ++episodeSeq,
      };
      sightingLevel = "none";
      transition("arrival_pending_confirmation", {
        reason: null, person: arrival.displayName, room: null,
      });
      emitDiag(`arrival pending — awaiting Frigate confirmation for ${arrival.displayName}`);
      setTimer("arrivalConfirmWindow", PROACTIVE_CONSTANTS.arrivalConfirmWindowMs, onArrivalTimeout);
    }

    function handleIdentityConfirmed(evt) {
      // corroboration-only: meaningful ONLY while a pending arrival is open
      if (phase !== "arrival_pending_confirmation" || !arrival) return;
      // is this the arriving person?
      const name = (evt.display_name || "").toLowerCase();
      const want = (arrival.displayName || "").toLowerCase();
      if (!name || name !== want) {
        emitDiag(`identity ${evt.display_name} ignored — not the arriving person (${arrival.displayName})`);
        return;
      }
      const conf = typeof evt.confidence === "number" ? evt.confidence : 0;
      if (conf >= PROACTIVE_CONSTANTS.confirmHighConf) {
        // STRONG confirm — a high-conf face on ANY interior camera counts
        clearTimer("arrivalConfirmWindow");
        const now = Date.now();
        const ev = buildCtx(evt, now);
        const verdict = evaluateProactive({ ...evt, type: "person_arrived_home" }, ev, now);
        const person = arrival.displayName;
        const episode = arrival;
        arrival = null;
        // lights fire regardless of the speech verdict
        fireReturnHomeScene();
        emitFeed("Return-home scene applied.");
        if (verdict.allow) {
          ledger.lastWelcomeTs = now; _writeWelcomedAt(now);
          emitFeed(`Welcome home, ${person} — spoken.`);
          speak("welcome", { name: person }, {
            person, extraPrompt: `You just greeted ${person} as they arrived home; treat their reply as a follow-up to that.`,
          });
        } else if (verdict.reason === "quiet-hours") {
          // quiet-hours downgrades the welcome to a silent feed line
          ledger.lastWelcomeTs = now; _writeWelcomedAt(now);
          emitFeed(`Welcome home, ${person} (quiet hours — not spoken).`);
          flashTransient("suppressed", "quiet-hours");
        } else {
          emitDiag(`welcome suppressed: ${verdict.reason}`);
          flashTransient("suppressed", verdict.reason);
        }
      } else {
        // weak identity — note it, stay pending, wait for a better frame
        if (sightingLevel !== "weak-identity") {
          sightingLevel = "weak-identity";
          syncState();
          emitDiag(`weak identity match for ${arrival.displayName} (conf ${conf.toFixed(2)}) — staying pending`);
        }
      }
    }

    function onArrivalTimeout() {
      if (phase !== "arrival_pending_confirmation" || !arrival) return;
      const person = arrival.displayName;
      const level = sightingLevel;
      arrival = null;
      if (level === "weak-identity" || level === "person-unidentified") {
        // soft fallback — feed line, no spoken name
        emitFeed("Arrival likely (unconfirmed) — no spoken welcome.");
        flashTransient("suppressed", "unconfirmed");
      } else if (!frigateOnlineRef.current && ARRIVAL_GPS_ONLY_FALLBACK) {
        // Frigate is down — GPS is all we have; speak anyway
        const now = Date.now();
        ledger.lastWelcomeTs = now; _writeWelcomedAt(now);
        emitFeed(`Welcome home, ${person} — spoken (GPS only, Frigate offline).`);
        speak("welcome", { name: person }, { person });
      } else {
        // Frigate up but never saw you — genuinely couldn't confirm
        emitDiag("arrival window closed — no visual confirmation, no greeting");
        transition("idle", { reason: null, person: null });
      }
    }

    function handleRoomEntered(evt) {
      const room = evt.room;
      // during a pending arrival, an entry-camera room_entered is weak corroboration
      if (phase === "arrival_pending_confirmation" && arrival) {
        if (ARRIVAL_CONFIRM_CAMERAS.indexOf(room) !== -1 && sightingLevel === "none") {
          sightingLevel = "person-unidentified";
          syncState();
          emitDiag(`arrival: person seen at ${room} (unidentified)`);
        }
        return;
      }
      // any other non-idle phase → room prompts are phase-gated
      if (phase !== "idle") {
        emitDiag(`room_entered ${room} ignored — busy (${phase})`);
        return;
      }
      // idle: buffer for multi-room detection
      ledger.roomLastSeenTs[room] = Date.now();
      if (!roomBuffer) roomBuffer = { rooms: new Set(), lastEvt: null, timerId: null };
      roomBuffer.rooms.add(room);
      roomBuffer.lastEvt = evt;
      if (roomBuffer.timerId) clearTimeout(roomBuffer.timerId);
      roomBuffer.timerId = setTimeout(() => {
        const rb = roomBuffer; roomBuffer = null;
        if (!rb) return;
        if (rb.rooms.size > 1) {
          emitDiag(`suppressed: multi-room (${Array.from(rb.rooms).join(", ")})`);
          flashTransient("suppressed", "multi-room");
          return;
        }
        evaluateAndActRoom(rb.lastEvt);
      }, PROACTIVE_CONSTANTS.multiRoomWindowMs);
    }

    function evaluateAndActRoom(evt) {
      if (phase !== "idle") return;  // something grabbed the coordinator while buffering
      const now = Date.now();
      const ctx = buildCtx(evt, now);
      const verdict = evaluateProactive(evt, ctx, now);
      if (!verdict.allow) {
        emitDiag(`room prompt (${evt.room}) suppressed: ${verdict.reason}`);
        flashTransient("suppressed", verdict.reason);
        return;
      }
      ledger.roomCooldowns[evt.room] = now;
      ledger.greetedRooms[evt.room] = now;
      const roomLabel = String(evt.room || "").replace(/_/g, " ");
      emitFeed(`Room prompt — ${roomLabel}.`);
      speak("room", { room: roomLabel }, {
        room: evt.room,
        extraPrompt: (evt.room ? `[[hg-room:${evt.room}]] ` : "") + `The user just entered the ${roomLabel} and you offered to help — their reply is a follow-up to that. They are physically in the ${roomLabel} right now. Resolve every ambiguous location reference in their reply ("the lights", "them", "it", "in here", "this room") to the ${roomLabel}. Only act on a different area if they explicitly name one; never silently default to this speaker device's own area.`,
      });
    }

    // ── GPS-flap debounce (SSE path only) ──
    // Returns true to let the event through NOW, false if it was held/cancelled.
    function gpsFlapDebounce(evt) {
      const key = _personKey(evt.person) || "_";
      const wantState = evt.type === "person_arrived_home" ? "home" : "not_home";
      const rec = gpsFlap.get(key) || { stableState: null, debounceId: null };
      if (rec.stableState === wantState) {
        // already stably in this state — a repeat; drop quietly
        return false;
      }
      // (re)start the debounce; a contradicting event cancels the pending one
      if (rec.debounceId) clearTimeout(rec.debounceId);
      rec.debounceId = setTimeout(() => {
        rec.stableState = wantState;
        rec.debounceId = null;
        gpsFlap.set(key, rec);
        // re-inject the (now stable) event through the normal pipeline
        ingestEvent({ ...evt, source: "sse", __debounced: true });
      }, PROACTIVE_CONSTANTS.coordinatorDebounceMs);
      gpsFlap.set(key, rec);
      emitDiag(`${evt.type} (${key}) debouncing ${PROACTIVE_CONSTANTS.coordinatorDebounceMs / 1000}s — SSE path`);
      return false;
    }

    // ── ingestEvent — the single entry point ──
    function ingestEvent(evt) {
      if (simActiveRef.current) return;            // inert in Simulation Mode
      if (!evt || !evt.type) return;
      // GPS-flap debounce — SSE path, person events only, and not an already-
      // debounced re-injection
      if (evt.source === "sse" && !evt.__debounced
          && (evt.type === "person_arrived_home" || evt.type === "person_left_home")) {
        if (!gpsFlapDebounce(evt)) return;
      }
      // dedupe — synchronous check-and-record, at the top, before any work.
      // identity_confirmed is high-frequency corroboration → skip dedupe
      // (we WANT to see a better frame); it's cheaply phase-gated downstream.
      // test events skip dedupe too — they only arrive via one transport, so
      // there's no double-delivery to collapse, and the harness needs repeats.
      if (evt.type !== "identity_confirmed" && !evt.test) {
        const key = `${evt.type}:${evt.person || "_"}:${evt.room || "_"}`;
        const now = Date.now();
        const last = dedupe.get(key);
        if (last && last.source !== evt.source && (now - last.ts) < PROACTIVE_CONSTANTS.dedupeWindowMs) {
          emitDiag(`dedup: dropped ${evt.type} (${evt.source})`);
          return;
        }
        dedupe.set(key, { ts: now, source: evt.source });
      }
      if (evt.type === "person_left_home") return handleLeftHome(evt);
      if (evt.type === "person_arrived_home") return handleArrivedHome(evt);
      if (evt.type === "identity_confirmed") return handleIdentityConfirmed(evt);
      if (evt.type === "room_entered") return handleRoomEntered(evt);
      emitDiag(`unknown event type: ${evt.type}`);
    }

    // ── observeIdentity — normalizes an s2s:identity payload → identity_confirmed ──
    function observeIdentity(identityObj) {
      if (simActiveRef.current) return;
      if (!identityObj || !identityObj.name) return;
      // corroboration-only — cheap early-out unless a pending arrival is open
      if (phase !== "arrival_pending_confirmation") return;
      const conf = typeof identityObj.score === "number" ? identityObj.score
        : (identityObj.confidence_band === "high" ? 0.97
        : identityObj.confidence_band === "medium" ? 0.75 : 0.6);
      ingestEvent({
        type: "identity_confirmed",
        person: null,
        display_name: identityObj.name,
        room: identityObj.camera || null,
        confidence: conf,
        timestamp: identityObj.ts || (Date.now() / 1000),
        source: "sse",
        raw_source: "frigate",
      });
    }

    // ── observeSatelliteState — drives the speak → follow-up → idle lifecycle ──
    function observeSatelliteState(newState) {
      if (simActiveRef.current) return;
      const prev = satState;
      satState = newState;
      if (newState === prev) return;
      // only act in follow-up-relevant phases — normal Voice PE use is ignored
      if (phase === "speaking_proactive_message") {
        if (newState === SAT.RESPONDING) { sawResponding = true; return; }
        if (newState === SAT.LISTENING && (prev === SAT.RESPONDING || sawResponding)) {
          clearTimer("announceWatchdog");
          const episodeId = ++episodeSeq;
          pendingFollowup = {
            episodeId,
            anchorTs: Date.now(),
            expectUntilTs: Date.now() + PROACTIVE_CONSTANTS.followupWindowMs,
          };
          // opening_followup_window is the brief gap — go straight through it
          transition("opening_followup_window", {});
          transition("listening_for_followup", {});
          setTimer("followupWindow", PROACTIVE_CONSTANTS.followupWindowMs, () => {
            pendingFollowup = null;
            flashTransient("followup_timeout", null);
          });
          return;
        }
        if (newState === SAT.IDLE && sawResponding) {
          // announce played, puck went straight back to idle — no follow-up mic
          clearTimer("announceWatchdog");
          emitDiag("announce played; follow-up mic did not open");
          transition("idle", { reason: null, room: null, person: null });
          return;
        }
      }
      if (phase === "listening_for_followup") {
        if (newState === SAT.PROCESSING) {
          clearTimer("followupWindow");
          pendingFollowup = null;
          transition("processing_followup", {});
          ledger.lastAssistantSpokeTs = Date.now();
          setTimer("processingGuard", PROACTIVE_CONSTANTS.processingGuardMs, () => {
            transition("idle", { reason: null, room: null, person: null });
          });
          return;
        }
        if (newState === SAT.IDLE) {
          clearTimer("followupWindow");
          pendingFollowup = null;
          flashTransient("followup_timeout", null);
          return;
        }
      }
      if (phase === "processing_followup") {
        if (newState === SAT.IDLE) {
          clearTimer("processingGuard");
          ledger.lastAssistantSpokeTs = Date.now();
          transition("idle", { reason: null, room: null, person: null });
          return;
        }
      }
    }

    // ── observeUserTurn — enriches the follow-up feed line with the text ──
    // Secondary to observeSatelliteState; only trusted while in a follow-up phase.
    function observeUserTurn(text, meta) {
      if (simActiveRef.current) return;
      if (!text) return;
      if (phase !== "listening_for_followup" && phase !== "processing_followup") return;
      if (!pendingFollowup && phase === "listening_for_followup") return;
      emitFeed(`Follow-up: "${String(text).slice(0, 120)}"`);
    }

    // ── mode override (the /proactive slash command) ──
    function setMode(name, on) {
      if (name in ctxOverride) { ctxOverride[name] = !!on; return true; }
      return false;
    }
    function getStatus() {
      return {
        phase, away, sightingLevel,
        modes: { ...ctxOverride },
        ledger: {
          lastProactiveSpeakAgoMs: ledger.lastProactiveSpeakTs ? Date.now() - ledger.lastProactiveSpeakTs : null,
          lastWelcomeAgoMs: ledger.lastWelcomeTs ? Date.now() - ledger.lastWelcomeTs : null,
          greetedRooms: Object.keys(ledger.greetedRooms),
        },
      };
    }
    function resetLedger() {
      ledger.lastProactiveSpeakTs = 0;
      ledger.lastAssistantSpokeTs = 0;
      ledger.lastWelcomeTs = 0;
      ledger.roomCooldowns = {};
      ledger.greetedRooms = {};
      ledger.roomLastSeenTs = {};
      dedupe.clear();
      for (const [, v] of gpsFlap) { if (v && v.debounceId) clearTimeout(v.debounceId); }
      gpsFlap.clear();
      // also clear the shared welcome-cooldown key so a /proactive reset is a
      // genuine clean slate for testing (WelcomeBanner re-reads this too —
      // benign: its banner may simply re-show on the next real arrival).
      try { window.localStorage.removeItem("hg-welcomedAt"); } catch (e) { /* noop */ }
      hardReset();
    }

    // ── helpers ──
    function _personKey(personId) {
      return String(personId || "").replace(/^person\./, "").toLowerCase() || null;
    }
    function _personLabel(personId) {
      const k = _personKey(personId);
      if (!k) return "there";
      return k.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    }

    // No initial syncState() — HomeApp's `proactive` useState already
    // starts at the idle shape, and writing React state during the
    // construction render (createCoordinator runs in the hook body) would
    // be a setState-during-render. The coordinator's internal `phase`
    // starts "idle", already consistent with HomeApp's initial state.

    return {
      ingestEvent, observeIdentity, observeSatelliteState, observeUserTurn,
      hardReset, clearAllTimers, setMode, getStatus, resetLedger,
      acknowledgePending, dismissPending,
    };
  }

  const PROACTIVE_PENDING_KEY = "hg-proactive-pending";
  const PROACTIVE_PENDING_MAX_AGE_MS = 15 * 60 * 1000;
  const PROACTIVE_PERSIST_PHASES = new Set([
    "arrival_pending_confirmation",
    "speaking_proactive_message",
    "opening_followup_window",
    "listening_for_followup",
    "processing_followup",
  ]);

  function _proactivePendingSnapshot(state) {
    if (!state || !PROACTIVE_PERSIST_PHASES.has(state.phase) || state.away) return null;
    return {
      phase: state.phase,
      reason: state.reason || null,
      room: state.room || null,
      person: state.person || null,
      away: false,
      sightingLevel: state.sightingLevel || "none",
      sinceTs: Number(state.sinceTs) || Date.now(),
    };
  }

  function loadProactivePending(defaultState) {
    // Person/room/arrival state is private current context and must remain
    // session-only until it enters a governed memory transaction.
    try { window.localStorage.removeItem(PROACTIVE_PENDING_KEY); } catch (_) {}
    return defaultState || IDLE_STATE;
  }

  function saveProactivePending(state) {
    void state;
    try { window.localStorage.removeItem(PROACTIVE_PENDING_KEY); } catch (_) {}
  }

  // ── useProactiveCoordinator — the React hook (called once in HomeApp) ───
  function useProactiveCoordinator(deps) {
    const {
      connection, haClientRef, simActive, debugMode, media, frigateOnline,
      addEvent, setProactiveState,
    } = deps;

    // mirror refs for the inputs that CHANGE — kept fresh by tiny no-timer
    // effects so the coordinator never closes over a stale value and no
    // effect ever depends on an object/array.
    const mediaRef = React.useRef(media);
    const debugModeRef = React.useRef(debugMode);
    const simActiveRef = React.useRef(simActive);
    const frigateOnlineRef = React.useRef(frigateOnline);
    React.useEffect(() => { mediaRef.current = media; }, [media]);
    React.useEffect(() => { debugModeRef.current = debugMode; }, [debugMode]);
    React.useEffect(() => { frigateOnlineRef.current = frigateOnline; }, [frigateOnline]);

    // the coordinator — constructed EXACTLY ONCE. addEvent / setProactiveState
    // are React-stable; haClientRef is a ref. So a one-time construction is
    // safe and every method on `api` is a forever-stable reference.
    const apiRef = React.useRef(null);
    if (apiRef.current === null) {
      apiRef.current = createCoordinator({
        haClientRef, addEvent, setProactiveState,
        mediaRef, debugModeRef, simActiveRef, frigateOnlineRef,
      });
    }
    const api = apiRef.current;

    // sim flip → hard reset (also runs once on mount — harmless)
    React.useEffect(() => {
      simActiveRef.current = simActive;
      api.hardReset();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [simActive]);

    // THE ONE REAL EFFECT — subscribe to the custom HA event over the app's
    // existing HA WebSocket. Keyed only on primitives. Early-returns in sim
    // (matches the existing HA-subscription effects in home-app.jsx).
    React.useEffect(() => {
      if (simActive) return undefined;
      if (connection !== "online") return undefined;
      const client = haClientRef.current;
      if (!client || typeof client.subscribeEvents !== "function") return undefined;
      let unsub;
      try {
        unsub = client.subscribeEvents("homeai_proactive", (ev) => {
          api.ingestEvent(normalizeProactiveEvent(ev && ev.data, "ha_ws"));
        });
      } catch (e) {
        console.warn("[proactive] subscribe failed:", e && e.message);
        return undefined;
      }
      console.log("[proactive] subscribed to homeai_proactive");
      return () => { try { unsub && unsub(); } catch (e) {} };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [connection, simActive]);

    // unmount cleanup — clear every timer the coordinator holds
    React.useEffect(() => () => api.clearAllTimers(), []);  // eslint-disable-line react-hooks/exhaustive-deps

    return {
      ingestEvent: api.ingestEvent,
      observeIdentity: api.observeIdentity,
      observeSatelliteState: api.observeSatelliteState,
      observeUserTurn: api.observeUserTurn,
      setMode: api.setMode,
      getStatus: api.getStatus,
      resetLedger: api.resetLedger,
      acknowledgePending: api.acknowledgePending,
      dismissPending: api.dismissPending,
    };
  }

  // ── ProactiveStatusLine — the one-row status in the MetricsStrip AI tab ──
  function ProactiveStatusLine({ proactive, onAction }) {
    const p = proactive || IDLE_STATE;
    const HG_MONO = window.HG_MONO || "'Geist Mono', ui-monospace, monospace";
    const [expanded, setExpanded] = React.useState(false);
    const actionFiredRef = React.useRef(false);
    let label, tone;
    if (p.away) {
      label = "away mode"; tone = "var(--hg-fg-3)";
    } else {
      switch (p.phase) {
        case "arrival_pending_confirmation":
          label = "arrival pending — awaiting camera…"; tone = "var(--hg-ice)"; break;
        case "speaking_proactive_message":
          label = "speaking…"; tone = "var(--hg-ice-bright)"; break;
        case "opening_followup_window":
        case "listening_for_followup":
          label = "listening for follow-up…" + (p.room ? " · " + String(p.room).replace(/_/g, " ") : "");
          tone = "var(--hg-ice-bright)"; break;
        case "processing_followup":
          label = "processing follow-up…"; tone = "var(--hg-ice)"; break;
        case "followup_timeout":
          label = "no follow-up"; tone = "var(--hg-fg-3)"; break;
        case "suppressed":
          label = "suppressed" + (p.reason ? ": " + p.reason : ""); tone = "var(--hg-warn)"; break;
        default:
          label = "idle"; tone = "var(--hg-fg-4)"; break;
      }
    }
    const active = p.phase !== "idle" || p.away;
    const actionable = active && typeof onAction === "function";
    React.useEffect(() => {
      actionFiredRef.current = false;
      if (!active) setExpanded(false);
    }, [active, p.phase, p.sinceTs]);
    const runAction = (action, ev) => {
      if (ev && typeof ev.stopPropagation === "function") ev.stopPropagation();
      if (!actionable || actionFiredRef.current) return;
      actionFiredRef.current = true;
      onAction(action, p);
    };
    return (
      <div style={{ fontFamily: HG_MONO, textTransform: "lowercase" }}>
        <button
          type="button"
          onClick={() => { if (actionable) setExpanded((x) => !x); }}
          className="hg-focusable"
          aria-expanded={expanded}
          style={{
            width: "100%",
            display: "flex", alignItems: "center", gap: 8,
            padding: "5px 0",
            background: "transparent",
            border: "none",
            cursor: actionable ? "pointer" : "default",
            fontFamily: HG_MONO, fontSize: 10, letterSpacing: "0.12em",
            textAlign: "left",
          }}
        >
          <span style={{
            width: 5, height: 5, borderRadius: 999,
            background: active ? tone : "var(--hg-fg-5)",
            boxShadow: active && p.phase !== "suppressed" && p.phase !== "followup_timeout"
              ? "0 0 5px var(--hg-ice-glow)" : "none",
            flex: "0 0 auto",
          }} />
          <span style={{ color: "var(--hg-fg-4)" }}>proactive</span>
          <span style={{ color: tone, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {label}
          </span>
          {actionable && (
            <span style={{ color: "var(--hg-fg-5)", fontSize: 9, letterSpacing: "0.08em" }}>
              {expanded ? "close" : "actions"}
            </span>
          )}
        </button>
        {expanded && actionable && (
          <div style={{
            display: "flex", gap: 6,
            padding: "3px 0 7px 13px",
          }}>
            <button
              type="button"
              onClick={(ev) => runAction("acknowledge", ev)}
              className="hg-focusable"
              style={{
                background: "transparent",
                border: "1px solid var(--hg-border-soft)",
                color: "var(--hg-fg-2)",
                padding: "3px 8px",
                fontFamily: HG_MONO,
                fontSize: 9,
                letterSpacing: "0.12em",
                cursor: "pointer",
              }}
            >acknowledge</button>
            <button
              type="button"
              onClick={(ev) => runAction("dismiss", ev)}
              className="hg-focusable"
              style={{
                background: "transparent",
                border: "1px solid var(--hg-border-soft)",
                color: "var(--hg-fg-3)",
                padding: "3px 8px",
                fontFamily: HG_MONO,
                fontSize: 9,
                letterSpacing: "0.12em",
                cursor: "pointer",
              }}
            >dismiss</button>
          </div>
        )}
      </div>
    );
  }

  // ── exports ──
  Object.assign(window, {
    useProactiveCoordinator,
    evaluateProactive,
    normalizeProactiveEvent,
    loadProactivePending,
    saveProactivePending,
    PROACTIVE_CONSTANTS,
    PROACTIVE_IDLE_STATE: IDLE_STATE,
    ProactiveStatusLine,
    // config — exposed so the docs/runbook and sim fixtures can reference them
    PROACTIVE_CONFIG: {
      VOICE_PE_SATELLITE, ROOM_SATELLITE_MAP, ARRIVAL_CONFIRM_CAMERAS,
      ANNOUNCE_SERVICE, PROACTIVE_MESSAGES,
    },
  });
})();
