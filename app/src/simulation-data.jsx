/* simulation-data.jsx — Scenario definitions + mock fixtures.
 *
 * Each scenario is a pure data object describing the state that the
 * UI should render. The simulation entry (simulation.jsx) reads these
 * and writes to the real React setters in HomeApp.
 *
 * Scenario shape:
 *   {
 *     id:           "healthy",
 *     label:        "everything healthy",
 *     description:  "baseline — model warm, all integrations online",
 *     baseline:     "healthy",          // optional: inherit from another scenario
 *     preservesChatHistory: true,       // patch only metrics/health; keep events
 *     snapshot:     {                   // partial setter writes (merged via apply)
 *       metrics: {...},
 *       bridgeHealth: {...},
 *       ...
 *     },
 *     timeline:     [...]               // optional scripted deltas (see simulation-timeline.jsx)
 *   }
 *
 * Privacy: NO real names, NO real entity_ids beyond generic placeholders,
 * NO real IPs or hostnames. Persona = "alex" only. Rooms are the same
 * generic names that already appear in the HA prompt's public example
 * block (living_room, kitchen, dining_room, workshop, driveway, office).
 */

(function () {
  // ── Helpers ────────────────────────────────────────────────────────

  function sinSeries(len, base, amp, freq) {
    return Array.from({ length: len }, (_, i) => {
      return Math.max(0, base + Math.sin(i * (freq || 0.2)) * amp);
    });
  }

  function noisySeries(len, base, jitter) {
    return Array.from({ length: len }, () => {
      return Math.max(0, base + (Math.random() - 0.5) * jitter);
    });
  }

  function nowSecs() { return Math.floor(Date.now() / 1000); }
  function uid() { return Math.random().toString(36).slice(2, 10); }

  // ── Fixture helpers ────────────────────────────────────────────────

  function makeHealthyChatHistory() {
    // 5-8 prior turns showing a typical day. Generic content.
    const t = nowSecs();
    return [
      { id: uid(), kind: "user",   time: "09:14:02", text: "good morning" },
      { id: uid(), kind: "home",   time: "09:14:03", text: "Morning. Office lights up to 80." },
      {
        id: uid(), kind: "action", time: "09:14:03",
        text: "light.turn_on", target: "area.office",
        status: "ok", brand: "lights",
        attrs: { brightness_pct: 80 },
      },
      { id: uid(), kind: "perception", time: "10:32:14", text: "office: alex at desk, focused on screen" },
      { id: uid(), kind: "user",   time: "12:48:22", text: "kitchen lights cool" },
      { id: uid(), kind: "home",   time: "12:48:23", text: "Kitchen at 5500K." },
      {
        id: uid(), kind: "action", time: "12:48:23",
        text: "light.turn_on", target: "area.kitchen",
        status: "ok", brand: "lights",
        attrs: { color_temp_kelvin: 5500 },
      },
      { id: uid(), kind: "perception", time: "13:05:41", text: "kitchen: alex preparing food" },
    ];
  }

  // Build a control-card action event (kind:"action" with `controllable` +
  // `actionCtx`) for the inline-control-card scenarios — mirrors what the SSE
  // handler in home-app.jsx does. `ageMs` backdates the context so a card can
  // be made expired/superseded for the lifecycle scenario.
  function controlActionEvent(opts) {
    const id = uid();
    const service = opts.service;
    const entityTargets = opts.entityTargets || [];
    const areaTargets = opts.areaTargets || [];
    const attrs = opts.attrs || {};
    const status = opts.status || "success";
    const ctx = window.buildActionContext
      ? window.buildActionContext({ id, service, entityTargets, areaTargets, deviceTargets: [], attrs, status })
      : null;
    if (ctx) {
      if (status === "failed") ctx.error = opts.error || "action failed";
      if (opts.ageMs) { ctx.startedAt -= opts.ageMs; ctx.completedAt -= opts.ageMs; }
    }
    const tgt = entityTargets[0] || areaTargets[0] || null;
    return {
      id, kind: "action", time: opts.time || "now",
      text: service,
      title: `${service}${tgt ? " · " + tgt : ""}`,
      service,
      target: entityTargets.length > 1 ? `${entityTargets.length} targets` : tgt,
      attrs: entityTargets.length > 1 ? { ...attrs, targets: entityTargets } : attrs,
      status: status === "failed" ? "error" : "success",
      brand: service.indexOf("light.") === 0 ? "lights" : "media",
      controllable: ctx ? ctx.controllable : false,
      actionCtx: ctx,
    };
  }

  function makeEmptyChatHistory() {
    return [
      { id: uid(), kind: "system", time: "—", text: "[sim] empty scenario — minimal history", tone: "info" },
    ];
  }

  // Realistic trace shape for the LAST VOICE TURN card.
  function makeTrace({ stt = 287, llm = 412, synth = 246, audio = 1430, cold = false, user = "make the office lights warmer", tts = "chatterbox" } = {}) {
    const t_parakeet = stt;
    const t_pipeline_intent_end = t_parakeet + llm;
    const t_synth_done = t_pipeline_intent_end + synth;
    const t_first_audio_sent = t_pipeline_intent_end + 40; // sentence chunk before synth done
    const t_done = t_first_audio_sent + audio;
    return {
      turn_id: uid(),
      user_text: user,
      path: "ha_pipeline",
      tool_name: "light.turn_on",
      target: "area.office",
      sentences: 1,
      resolver_miss: false,
      is_voice: true,
      cold: !!cold,
      tts_engine: tts,
      t_bridge_recv: 12,
      t_prompt_assembled: 38,
      t_pipeline_start: 42,
      t_pipeline_intent_end: Math.round(t_pipeline_intent_end),
      t_parakeet_done: Math.round(t_parakeet),
      t_synth_start: Math.round(t_pipeline_intent_end + 4),
      t_synth_done: Math.round(t_synth_done),
      t_first_audio_sent: Math.round(t_first_audio_sent),
      t_done: Math.round(t_done),
      audio_duration_ms: Math.round(audio),
      prompt_tokens: 1820, completion_tokens: 38, cached_tokens: 1640,
    };
  }

  /* ──────────────────────────────────────────────────────────────────
   * Lab tab fixture generator (Addendum 10)
   *
   * Produces { turns: LabTurn[], samples: LabSample[] } for the Lab
   * tab's HmLabChart. Each turn carries its own per-window samples
   * so 21-turn history works regardless of how stale calls are. Uses
   * a seeded PRNG so renders are deterministic.
   * ────────────────────────────────────────────────────────────────── */
  function _rng(seed) {
    let s = (seed >>> 0) || 1;
    return () => { s = (s * 9301 + 49297) % 233280; return s / 233280; };
  }

  const _LAB_TRANSCRIPTS = [
    "living room lights warm", "play something calm", "who is at the door",
    "kitchen brighter please", "is anyone in the office", "set living room reading scene",
    "turn it down a bit", "pause the music", "resume music", "lights off in the dining room",
    "show driveway camera", "is the garage locked", "good morning home",
    "what is the temperature outside", "set the workshop to focus", "movie scene please",
    "lower the kitchen to fifty", "is alex home", "lock everything", "good night",
    "turn the kitchen down to fifty percent",
  ];

  function makeLabFixture(scenarioId = "healthy") {
    const r = _rng(scenarioId.length * 17 + 23);
    const N = 21;
    const now = Date.now();
    const turns = [];
    let cursor = now - 21 * 60 * 1000; // last 21 minutes
    const baseVram = scenarioId.includes("high-vram") ? 0.91 : 0.62;
    const slowLast5 = scenarioId.includes("slow-llm") || scenarioId.includes("history");
    const sttCpuSpike = scenarioId.includes("stt-cpu-spike");
    const ttsSlowLast = scenarioId.includes("tts-slow");
    const trending = scenarioId.includes("history");

    for (let i = 0; i < N; i++) {
      const isSlow = slowLast5 && i >= N - 5;
      const isTrendDegrade = trending && i >= N - 7;
      // Stage durations (ms)
      let stt   = 270 + Math.round((r() - 0.5) * 60);
      let llm   = isSlow ? 1100 + Math.round(r() * 300)
                  : (isTrendDegrade ? 600 + i * 35
                  : 380 + Math.round((r() - 0.5) * 120));
      let synth = ttsSlowLast && i >= N - 4 ? 800 + Math.round(r() * 200)
                  : 240 + Math.round((r() - 0.5) * 80);
      let audio = ttsSlowLast && i >= N - 4 ? 2400 + Math.round(r() * 300)
                  : 1400 + Math.round((r() - 0.5) * 200);
      if (sttCpuSpike) stt = 380 + Math.round(r() * 70);

      const totalMs = stt + llm + synth + audio;
      const startedAt = cursor;
      const endedAt = startedAt + totalMs;
      cursor = endedAt + (30_000 + Math.round(r() * 90_000)); // 30-120s gaps

      const stages = [
        { label: "stt",   ms: stt,   startedAt: startedAt,                          endedAt: startedAt + stt },
        { label: "llm",   ms: llm,   startedAt: startedAt + stt,                    endedAt: startedAt + stt + llm },
        { label: "synth", ms: synth, startedAt: startedAt + stt + llm,              endedAt: startedAt + stt + llm + synth },
        { label: "audio", ms: audio, startedAt: startedAt + stt + llm + synth,      endedAt },
      ];

      // Per-turn samples — at ~250ms cadence within the turn
      const samples = [];
      const sampleStep = 250;
      const total = totalMs;
      for (let t = 0; t <= total; t += sampleStep) {
        const absT = startedAt + t;
        // Detect which stage we're in
        let stage = "stt";
        if (t < stt) stage = "stt";
        else if (t < stt + llm) stage = "llm";
        else if (t < stt + llm + synth) stage = "synth";
        else stage = "audio";
        const vramJitter = (r() - 0.5) * 0.02;
        const vramPct = Math.max(0, Math.min(1, baseVram + vramJitter));
        let gpuPct;
        if (stage === "llm" || stage === "synth") {
          gpuPct = (isSlow ? 0.95 : 0.86) - r() * 0.22;
          if (r() > 0.85) gpuPct = 0.3 + r() * 0.15;
        } else gpuPct = 0.04 + r() * 0.08;
        let cpuPct;
        if (sttCpuSpike && stage === "stt") cpuPct = 0.62 + r() * 0.15;
        else if (stage === "stt" || stage === "audio") cpuPct = 0.30 + r() * 0.26;
        else cpuPct = 0.08 + r() * 0.10;
        const ramPct = 0.24 + (r() - 0.5) * 0.02 + (scenarioId.includes("high-vram") ? 0.06 : 0);
        samples.push({
          t: absT,
          vramPct, gpuPct: Math.max(0, Math.min(1, gpuPct)),
          cpuPct: Math.max(0, Math.min(1, cpuPct)),
          ramPct: Math.max(0, Math.min(1, ramPct)),
          vramUsedGb: vramPct * 96, vramTotalGb: 96,
          ramUsedGb: ramPct * 64, ramTotalGb: 64,
        });
      }

      turns.push({
        id: `sim-turn-${scenarioId}-${i}`,
        startedAt, endedAt, totalMs,
        stages, samples,
        transcript: _LAB_TRANSCRIPTS[i % _LAB_TRANSCRIPTS.length],
        cold: i === 0,
        slow: !!isSlow || (ttsSlowLast && i >= N - 4),
        crit: false,
      });
    }

    // Shared rolling buffer — only the samples falling between recent turns
    // make sense in the shared array; for sim we just use the last turn's
    // samples as the "current" trend.
    const shared = turns.length > 0 ? [...turns[turns.length - 1].samples] : [];

    return { turns, samples: shared };
  }

  // ── Baseline fixture sets ──────────────────────────────────────────

  const HEALTHY_METRICS = {
    model: "qwen3-vl-30b",
    ttft: 78, tps: 0,
    gpu: 4, vram: 86, vramMax: 96,
    cpu: 14, ram: 24, ramMax: 64,
    // F-19 (Addendum 27 P0): GPU temperature in °C. Matches sidecar
    // /metrics shape (m.gpu_temp_c → metrics.tempC). 52°C ≈ light idle
    // load — healthy band, well below warn (78°C).
    tempC: 52,
  };

  const HEALTHY_METRICS_HISTORY = {
    ttft: sinSeries(200, 78, 8, 0.15),
    tps:  Array.from({ length: 200 }, () => 0),
    gpu:  sinSeries(200, 6, 4, 0.08).map((v, i) =>
      // simulate brief GPU spikes during the 5-8 chat turns
      [40, 95, 22, 130, 60].includes(i) ? v + 70 : v
    ),
    vram: sinSeries(200, 86, 0.3, 0.05),
    cpu:  sinSeries(200, 14, 6, 0.1),
    ram:  sinSeries(200, 24, 0.8, 0.04),
  };

  const HEALTHY_BRIDGE_HEALTH = {
    ok: true,
    backend: "moshi",
    ha_connected: true,
    upstream_url: "sim://bridge",
    uptime_s: 9320,
    warmup_complete: true,
    rooms_loaded: 6,
    media_players_registered: 4,
    stale_media_integrations: [],
    last_trace_age_s: 124,
    tts_engine: "chatterbox",
    tts_fallback: "kokoro",
    voice_rewriter: false,
    direct_dispatch: false,
    dry_run: false,
  };

  const HEALTHY_HOST = {
    cpu: 18, ram: 41, ramGb: null, disk: 62, diskGb: null,
    uptime: "12d 4h",
  };

  const HEALTHY_FRIGATE = {
    totalCpu: 23.4,
    coralMs: 7.2,
    fps: {
      living_room: 10.0, kitchen: 10.1, dining_room: 10.0,
      workshop: 9.9, driveway: 10.1,
    },
    uptime: "5d 8h",
  };

  const HEALTHY_NETWORK = {
    udm: { cpu: 12, mem: 64, state: "connected", uptime: "12d" },
    switches: [
      { name: "USW Flex 2.5G 8 PoE", cpu: 7,  mem: 71, state: "connected", uptime: "12d" },
      { name: "USW Flex 2.5G 5",     cpu: 4,  mem: 58, state: "connected", uptime: "12d" },
    ],
    clientsOnline: 14,
    clientsKnown:  18,
  };

  const HEALTHY_VISION = {
    phash_enabled: true,
    phash_hit_rate: 0.62,
    phash_cameras_cached: 5,
  };

  const HEALTHY_TRACE_SUMMARY = {
    window: "1h",
    count: 18,
    ttfa_ms: { n: 18, p50: 685, p90: 1240, max: 1820 },
  };

  // Supervisor status for the AI tab's AiStackCard. Without this the healthy
  // sim leaves aiStackOnline/aiStackState at their live defaults (null), and
  // the card falls through to real-infra empty states ("polling" /
  // "AI STACK NOT CONFIGURED"). Same shape as the metrics-timeline-healthy
  // lab scenario. (The STACK_TOKEN half of that warning is mocked at the
  // render site — renderAi in home-app.jsx treats the token as configured
  // while sim is active, since window.__STACK_TOKEN is not snapshot-settable.)
  const HEALTHY_AI_STACK = {
    overall: "ready",
    services: [
      { name: "vllm", container: "running", health: "healthy", probe: "ok", probe_kind: "http", detail: "qwen3-vl-30b" },
      { name: "wyoming-parakeet", container: "running", health: "healthy", probe: "ok", probe_kind: "http" },
      { name: "kokoro", container: "running", health: "healthy", probe: "ok", probe_kind: "http" },
      { name: "vision-sidecar", container: "running", health: "healthy", probe: "ok", probe_kind: "http" },
    ],
    uptime_h: 44,
    vllm_model_loaded: "qwen3-vl-30b",
    expected_vllm_model: "qwen3-vl-30b",
  };

  const HEALTHY_LAST_TRACE = makeTrace({});

  const HEALTHY_IDENTITY = {
    name: "Alex",
    camera: "office",
    score: 0.94,
    confidence_band: "high",
    ts: nowSecs() - 90,
    first_seen_today: false,
  };

  const HEALTHY_MEDIA = {
    living_room: { state: "idle", app_name: null, title: null },
  };

  const HEALTHY_CAMERA_LABELS = {
    living_room: ["chair", "couch", "tv"],
    kitchen:     [],
    dining_room: [],
    workshop:    [],
    driveway:    [],
  };

  const HEALTHY_ROOM_CONTEXT = {
    rooms: {
      kitchen: {
        occupied: true,
        persons: [{ identity: { name: "Alex" }, age_seconds: 14 }],
        perception_age_seconds: 28,
      },
      living_room: {
        occupied: false,
        persons: [],
        perception_age_seconds: 180,
      },
      office: {
        occupied: true,
        occupant: "Alex",
        age_s: 90,
        media: { app_name: "music", title: "focus mix" },
      },
    },
  };

  // ── Scenarios ──────────────────────────────────────────────────────

  /* Snapshot helper — merge into a base. */
  function patch(base, over) {
    const out = { ...base };
    for (const k of Object.keys(over || {})) {
      const v = over[k];
      if (k === "cameraLabels") {
        out[k] = v;
      } else if (v && typeof v === "object" && !Array.isArray(v)) {
        out[k] = { ...(base[k] || {}), ...v };
      } else {
        out[k] = v;
      }
    }
    return out;
  }

  // Append/update events helper for timelines
  function appendEvent(ev) {
    return (apply, current) => {
      const events = (current && current.events) || [];
      apply({ events: [...events, { id: uid(), ...ev }] });
    };
  }

  // Healthy snapshot — the canonical baseline.
  const HEALTHY_SNAPSHOT = {
    metrics:         HEALTHY_METRICS,
    metricsHistory:  HEALTHY_METRICS_HISTORY,
    bridgeHealth:    HEALTHY_BRIDGE_HEALTH,
    hostMetrics:     HEALTHY_HOST,
    frigateMetrics:  HEALTHY_FRIGATE,
    networkMetrics:  HEALTHY_NETWORK,
    roomContext:     HEALTHY_ROOM_CONTEXT,
    visionSidecarOnline: true,
    visionHealth:    HEALTHY_VISION,
    traceSummary:    HEALTHY_TRACE_SUMMARY,
    lastTrace:       HEALTHY_LAST_TRACE,
    identity:        HEALTHY_IDENTITY,
    media:           HEALTHY_MEDIA,
    cameraLabels:    HEALTHY_CAMERA_LABELS,
    connection:      "online",
    sidecarOnline:   true,
    bridgeOnline:    true,
    aiStackOnline:   true,
    aiStackState:    HEALTHY_AI_STACK,
    voice:           { state: "inactive" },
    proactive:       { phase: "idle", away: false, reason: null, room: null, person: null, sightingLevel: "none", sinceTs: 0 },
    events:          makeHealthyChatHistory(),
    cameraStates:    window.SimDefaultCameraMap,
  };

  const scenarios = {
    // ── Baseline scenarios ────────────────────────────────────────

    "healthy": {
      id: "healthy",
      label: "everything healthy",
      description: "baseline — model warm, all integrations online",
      preservesChatHistory: false,
      snapshot: HEALTHY_SNAPSHOT,
    },

    "empty": {
      id: "empty",
      label: "calm baseline",
      description: "no recent activity, all idle",
      preservesChatHistory: false,
      snapshot: patch(HEALTHY_SNAPSHOT, {
        events: makeEmptyChatHistory(),
        lastTrace: null,
        traceSummary: { window: "1h", count: 0, ttfa_ms: null },
        identity: null,
        cameraLabels: {
          living_room: [], kitchen: [], dining_room: [], workshop: [], driveway: [],
        },
      }),
    },

    // ── Voice state scenarios ─────────────────────────────────────

    "listening": {
      id: "listening",
      label: "voice listening",
      description: "mic open, capturing user speech",
      preservesChatHistory: true,
      snapshot: { voice: { state: "listening" } },
    },

    "thinking": {
      id: "thinking",
      label: "voice thinking",
      description: "transcript dispatched, model generating",
      preservesChatHistory: true,
      snapshot: { voice: { state: "thinking" } },
    },

    "speaking": {
      id: "speaking",
      label: "voice speaking",
      description: "TTS streaming back to the user",
      preservesChatHistory: true,
      snapshot: { voice: { state: "speaking" } },
    },

    // ── Action scenarios (story timelines) ────────────────────────

    "action-success": {
      id: "action-success",
      label: "successful action",
      description: "user → thinking → action card → ok → assistant reply",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        {
          at: 100,
          delta: (apply) => apply({
            __append: { kind: "user", time: "now", text: "make the living room warmer" }
          }),
        },
        { at: 800,  delta: { voice: { state: "thinking" } } },
        {
          at: 1500,
          delta: (apply) => apply({
            __append: {
              kind: "action", time: "now",
              text: "light.turn_on", target: "area.living_room",
              status: "pending", brand: "lights",
              attrs: { brightness_pct: 35, color_temp_kelvin: 2400 },
            }
          }),
        },
        {
          at: 2400,
          delta: (apply) => apply({ __updateLastAction: { status: "ok" } }),
        },
        {
          at: 2900,
          delta: (apply) => apply({
            __append: { kind: "home", time: "now", text: "Warmed the living room — 35% at 2400K." },
            voice: { state: "inactive" },
          }),
        },
      ],
    },

    "action-failed": {
      id: "action-failed",
      label: "failed action",
      description: "HA call returns an error; assistant explains",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        {
          at: 100,
          delta: (apply) => apply({
            __append: { kind: "user", time: "now", text: "turn on the bonus room lights" }
          }),
        },
        { at: 700, delta: { voice: { state: "thinking" } } },
        {
          at: 1400,
          delta: (apply) => apply({
            __append: {
              kind: "action", time: "now",
              text: "light.turn_on", target: "area.bonus_room",
              status: "pending", brand: "lights",
              attrs: {},
            }
          }),
        },
        {
          at: 2200,
          delta: (apply) => apply({
            __updateLastAction: { status: "error", reason: "entity not found" }
          }),
        },
        {
          at: 2700,
          delta: (apply) => apply({
            __append: { kind: "home", time: "now", text: "I don't see a bonus room — which room did you mean?" },
            voice: { state: "inactive" },
          }),
        },
      ],
    },

    "movie-mode": {
      id: "movie-mode",
      label: "movie mode",
      description: "user starts a movie → Apple TV plays → lights dim",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        {
          at: 100,
          delta: (apply) => apply({
            __append: { kind: "user", time: "now", text: "start a movie in the living room" },
          }),
        },
        { at: 800, delta: { voice: { state: "thinking" } } },
        {
          at: 1400,
          delta: (apply) => apply({
            __append: {
              kind: "action", time: "now",
              text: "media_player.media_play",
              target: "media_player.living_room_2",
              status: "pending", brand: "media",
            }
          }),
        },
        {
          at: 2100,
          delta: (apply) => apply({
            __updateLastAction: { status: "ok" },
            media: {
              living_room: {
                state: "playing", app_name: "Apple TV",
                title: "A Documentary",
              },
            },
          }),
        },
        {
          at: 2400,
          delta: (apply) => apply({
            __append: {
              kind: "action", time: "now",
              text: "light.turn_on", target: "area.living_room",
              status: "pending", brand: "lights",
              attrs: { brightness_pct: 20, color_temp_kelvin: 2400 },
            }
          }),
        },
        {
          at: 3100,
          delta: (apply) => apply({ __updateLastAction: { status: "ok" } }),
        },
        {
          at: 3500,
          delta: (apply) => apply({
            __append: { kind: "home", time: "now", text: "Movie's on. Living room dimmed for you." },
            voice: { state: "inactive" },
          }),
        },
      ],
    },

    // ── Latency / performance scenarios ───────────────────────────

    "slow-voice": {
      id: "slow-voice",
      label: "slow voice pipeline",
      description: "synth stage is degraded; total turn ~3.5s",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: {
        events: makeHealthyChatHistory(),
        lastTrace: makeTrace({ stt: 287, llm: 412, synth: 2400, audio: 1430 }),
        traceSummary: { window: "1h", count: 14, ttfa_ms: { n: 14, p50: 2150, p90: 3420, max: 4180 } },
      },
    },

    // ── Resource pressure scenarios ───────────────────────────────

    "high-vram": {
      id: "high-vram",
      label: "high VRAM usage",
      description: "model under memory pressure (93/96 GB)",
      preservesChatHistory: true,
      snapshot: {
        metrics: { ...HEALTHY_METRICS, vram: 93 },
        metricsHistory: { ...HEALTHY_METRICS_HISTORY, vram: sinSeries(200, 92, 0.7, 0.04) },
      },
    },

    "model-warming": {
      id: "model-warming",
      label: "model warming up",
      description: "container started, vLLM not ready yet",
      preservesChatHistory: true,
      snapshot: {
        bridgeHealth: { ...HEALTHY_BRIDGE_HEALTH, warmup_complete: false, uptime_s: 28 },
        metrics: { ...HEALTHY_METRICS, ttft: null, tps: null },
      },
    },

    "model-offline": {
      id: "model-offline",
      label: "model offline",
      description: "vLLM/model runtime unavailable",
      preservesChatHistory: true,
      snapshot: {
        metrics: { ...HEALTHY_METRICS, model: null, ttft: null, tps: null, gpu: 0, vram: 0 },
        bridgeOnline: false,
        bridgeHealth: { ...HEALTHY_BRIDGE_HEALTH, warmup_complete: false, tts_engine: null },
        aiStackOnline: true,
        aiStackState: {
          overall: "down",
          services: [
            { name: "vllm", container: "missing", health: "unknown", probe: "fail", detail: "model runtime unavailable" },
            { name: "wyoming-parakeet", container: "running", health: "healthy", probe: "ok" },
            { name: "kokoro", container: "running", health: "healthy", probe: "ok" },
            { name: "vision-sidecar", container: "running", health: "healthy", probe: "ok" },
            { name: "metrics-sidecar", container: "running", health: "healthy", probe: "ok" },
          ],
          expected_vllm_model: "qwen3-vl-30b",
        },
      },
    },

    "tts-offline": {
      id: "tts-offline",
      label: "TTS offline",
      description: "Chatterbox unavailable; text still works",
      preservesChatHistory: true,
      snapshot: {
        bridgeHealth: { ...HEALTHY_BRIDGE_HEALTH, tts_engine: null },
      },
    },

    // ── Integration scenarios ─────────────────────────────────────

    "haos-offline": {
      id: "haos-offline",
      label: "Home Assistant offline",
      description: "HAOS unreachable; commands blocked",
      preservesChatHistory: true,
      snapshot: {
        connection: "offline",
        bridgeHealth: { ...HEALTHY_BRIDGE_HEALTH, ha_connected: false },
        hostMetrics: null,
      },
    },

    "frigate-offline": {
      id: "frigate-offline",
      label: "Frigate offline",
      description: "no Frigate telemetry; cameras may degrade",
      preservesChatHistory: true,
      snapshot: {
        frigateMetrics: null,
        roomContext: null,
        cameraLabels: {},
        cameraStates: {
          living_room: "stale", kitchen: "stale", dining_room: "stale",
          workshop: "stale", driveway: "stale", office: "stale",
        },
      },
    },

    "camera-offline": {
      id: "camera-offline",
      label: "one camera offline",
      description: "living room stream is offline, rest healthy",
      preservesChatHistory: true,
      snapshot: {
        cameraStates: {
          ...window.SimDefaultCameraMap,
          living_room: "offline",
        },
      },
    },

    "media-unavailable": {
      id: "media-unavailable",
      label: "media integrations stale",
      description: "Apple TV / Sonos reporting unavailable",
      preservesChatHistory: true,
      snapshot: {
        bridgeHealth: {
          ...HEALTHY_BRIDGE_HEALTH,
          stale_media_integrations: [
            { entity_id: "media_player.living_room_2", age_s: 86400 },
            { entity_id: "media_player.kitchen_speaker", age_s: 43200 },
          ],
        },
      },
    },

    // ── Network ────────────────────────────────────────────────────

    "network-degraded": {
      id: "network-degraded",
      label: "network degraded",
      description: "switch RAM high, latency creeping up",
      preservesChatHistory: true,
      snapshot: {
        networkMetrics: {
          ...HEALTHY_NETWORK,
          switches: [
            { name: "USW Flex 2.5G 8 PoE", cpu: 22, mem: 92, state: "connected", uptime: "12d" },
            { name: "USW Flex 2.5G 5",     cpu: 18, mem: 85, state: "connected", uptime: "12d" },
          ],
          clientsOnline: 11,
        },
      },
    },

    "network-healthy": {
      id: "network-healthy",
      label: "network healthy",
      description: "compact summary, no warnings",
      preservesChatHistory: true,
      snapshot: { networkMetrics: HEALTHY_NETWORK },
    },

    // ── Full outage ────────────────────────────────────────────────

    "bridge-offline": {
      id: "bridge-offline",
      label: "bridge offline",
      description: "core backend unavailable",
      preservesChatHistory: true,
      snapshot: {
        bridgeOnline: false,
        sidecarOnline: false,
        bridgeHealth: null,
        roomContext: null,
        metrics: { ...HEALTHY_METRICS, model: null, ttft: null, tps: null },
      },
    },

    "everything-offline": {
      id: "everything-offline",
      label: "full outage",
      description: "every dependency down — UI still renders gracefully",
      preservesChatHistory: true,
      snapshot: {
        connection: "offline",
        bridgeOnline: false,
        sidecarOnline: false,
        bridgeHealth: null,
        hostMetrics: null,
        frigateMetrics: null,
        cameraLabels: {},
        networkMetrics: { udm: null, switches: [], clientsOnline: 0, clientsKnown: 0 },
        roomContext: null,
        visionSidecarOnline: false,
        visionHealth: null,
        metrics: { ...HEALTHY_METRICS, model: null, ttft: null, tps: null, gpu: 0, vram: 0 },
        cameraStates: {
          living_room: "offline", kitchen: "offline", dining_room: "offline",
          workshop: "offline", driveway: "offline", office: "offline",
        },
      },
    },

    // ── Inline control-card scenarios ──────────────────────────────
    // These exercise home-control.jsx — the cards read/write the mock
    // window.SimControlStore (simulation-controls.jsx) instead of real HA.

    "light-control": {
      id: "light-control",
      label: "light control card",
      description: "single light — brightness + color-temp sliders",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 60, delta: () => { if (window.SimControlStore) window.SimControlStore.reset(); } },
        { at: 200, delta: (apply) => apply({ __append: { kind: "user", time: "now", text: "warm up the kitchen light" } }) },
        { at: 900, delta: { voice: { state: "thinking" } } },
        { at: 1700, delta: (apply) => apply({ __append: controlActionEvent({
          service: "light.turn_on",
          entityTargets: ["light.kitchen"],
          attrs: { brightness_pct: 60, color_temp_kelvin: 3000 },
        }) }) },
        { at: 2400, delta: (apply) => apply({
          __append: { kind: "home", time: "now", text: "Kitchen light warmed — 60% at 3000K." },
          voice: { state: "inactive" },
        }) },
      ],
    },

    "light-group-control": {
      id: "light-group-control",
      label: "light group control",
      description: "multi-light group — averaged + mixed values; one light has no color temp",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 60, delta: () => { if (window.SimControlStore) window.SimControlStore.reset(); } },
        { at: 200, delta: (apply) => apply({ __append: { kind: "user", time: "now", text: "dim the living room lights" } }) },
        { at: 900, delta: { voice: { state: "thinking" } } },
        { at: 1700, delta: (apply) => apply({ __append: controlActionEvent({
          service: "light.turn_on",
          entityTargets: ["light.living_room_lamp", "light.living_room_ceiling", "light.kitchen"],
          attrs: { brightness_pct: 40 },
        }) }) },
        { at: 2400, delta: (apply) => apply({
          __append: { kind: "home", time: "now", text: "Living room lights dimmed to 40%." },
          voice: { state: "inactive" },
        }) },
      ],
    },

    "media-control": {
      id: "media-control",
      label: "media control card",
      description: "single Sonos — transport, volume, now-playing",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 60, delta: () => { if (window.SimControlStore) window.SimControlStore.reset(); } },
        { at: 200, delta: (apply) => apply({ __append: { kind: "user", time: "now", text: "play music in the kitchen" } }) },
        { at: 900, delta: { voice: { state: "thinking" } } },
        { at: 1700, delta: (apply) => apply({ __append: controlActionEvent({
          service: "media_player.play_media",
          entityTargets: ["media_player.kitchen_sonos"],
          attrs: {},
        }) }) },
        { at: 2400, delta: (apply) => apply({
          __append: { kind: "home", time: "now", text: "Playing in the kitchen." },
          voice: { state: "inactive" },
        }) },
      ],
    },

    "media-group-control": {
      id: "media-group-control",
      label: "media group control",
      description: "grouped Sonos — per-zone volume (living room routed to Onkyo) + speaker chips",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 60, delta: () => {
          if (!window.SimControlStore) return;
          window.SimControlStore.reset();
          // pre-group kitchen + living room so the card shows a real group
          window.SimControlStore.callService({
            domain: "media_player", service: "join",
            service_data: { group_members: ["media_player.living_room_sonos"] },
            targets: ["media_player.kitchen_sonos"],
          });
        } },
        { at: 200, delta: (apply) => apply({ __append: { kind: "user", time: "now", text: "play music in the kitchen and living room" } }) },
        { at: 900, delta: { voice: { state: "thinking" } } },
        { at: 1700, delta: (apply) => apply({ __append: controlActionEvent({
          service: "media_player.play_media",
          entityTargets: ["media_player.kitchen_sonos", "media_player.living_room_sonos"],
          attrs: {},
        }) }) },
        { at: 2400, delta: (apply) => apply({
          __append: { kind: "home", time: "now", text: "Playing in the kitchen and living room." },
          voice: { state: "inactive" },
        }) },
      ],
    },

    "action-card-expired": {
      id: "action-card-expired",
      label: "expired / superseded cards",
      description: "older inline control cards — read-only (one superseded, one expired)",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: {
        voice: { state: "inactive" },
        events: [].concat(makeHealthyChatHistory(), [
          { id: uid(), kind: "user", time: "08:02:00", text: "play something in the kitchen" },
          controlActionEvent({ service: "media_player.play_media", entityTargets: ["media_player.kitchen_sonos"], attrs: {}, time: "08:02:01", ageMs: 40 * 60 * 1000 }),
          { id: uid(), kind: "home", time: "08:02:02", text: "Playing in the kitchen." },
          { id: uid(), kind: "user", time: "08:20:00", text: "actually move it to the office" },
          controlActionEvent({ service: "media_player.play_media", entityTargets: ["media_player.office_sonos"], attrs: {}, time: "08:20:01", ageMs: 22 * 60 * 1000 }),
          { id: uid(), kind: "home", time: "08:20:02", text: "Moved playback to the office." },
          { id: uid(), kind: "user", time: "09:05:00", text: "what's the weather" },
          { id: uid(), kind: "home", time: "09:05:01", text: "Clear and cool — 14°." },
        ]),
      },
    },

    "action-card-error": {
      id: "action-card-error",
      label: "failed action card",
      description: "control card for a failed action — disabled controls + reason",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 60, delta: () => { if (window.SimControlStore) window.SimControlStore.reset(); } },
        { at: 200, delta: (apply) => apply({ __append: { kind: "user", time: "now", text: "play music in the sunroom" } }) },
        { at: 900, delta: { voice: { state: "thinking" } } },
        { at: 1700, delta: (apply) => apply({ __append: controlActionEvent({
          service: "media_player.play_media",
          entityTargets: ["media_player.sunroom_sonos"],   // intentionally absent from the mock store
          attrs: {},
          status: "failed",
          error: "entity media_player.sunroom_sonos not found",
        }) }) },
        { at: 2300, delta: (apply) => apply({
          __append: { kind: "home", time: "now", text: "I couldn't find a sunroom speaker." },
          voice: { state: "inactive" },
        }) },
      ],
    },

    // ── Proactive smart-home scenarios (home-proactive.jsx) ────────────
    // These drive the `proactive` coordinator UI state directly — the
    // coordinator hook is INERT in Simulation Mode — and append the feed
    // lines (kind:"proactive") + diag lines (kind:"diag") it would emit.
    // See SIMULATION_MODE.md.

    "arrival-pending": {
      id: "arrival-pending",
      label: "arrival pending confirmation",
      description: "GPS says home — awaiting Frigate face-rec, no speech yet",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: {
        voice: { state: "inactive" },
        proactive: { phase: "arrival_pending_confirmation", away: false, reason: null, room: null, person: "Alex", sightingLevel: "none", sinceTs: 0 },
        events: [].concat(makeHealthyChatHistory(), [
          { id: uid(), kind: "diag", channel: "proactive", time: "now", text: "[proactive] arrival pending — awaiting Frigate confirmation for Alex" },
        ]),
      },
    },

    "arrived-home": {
      id: "arrived-home",
      label: "arrived home (two-stage)",
      description: "GPS → pending → Frigate confirms → return-home scene + welcome spoken",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 300, delta: (apply) => apply({
          proactive: { phase: "arrival_pending_confirmation", person: "Alex", reason: null, room: null, sightingLevel: "none" },
          __append: { kind: "diag", channel: "proactive", time: "now", text: "[proactive] arrival pending — awaiting Frigate confirmation for Alex" },
        }) },
        { at: 1800, delta: (apply) => apply({
          __append: { kind: "diag", channel: "proactive", time: "now", text: "[proactive] Frigate confirmed Alex (conf 0.97) — speaking welcome" },
        }) },
        { at: 2000, delta: (apply) => apply({
          __append: { kind: "proactive", time: "now", text: "Return-home scene applied." },
        }) },
        { at: 2200, delta: (apply) => apply({
          proactive: { phase: "speaking_proactive_message", person: "Alex" },
          voice: { state: "speaking" },
          __append: { kind: "proactive", time: "now", text: "Welcome home, Alex — spoken." },
        }) },
        { at: 4400, delta: (apply) => apply({
          proactive: { phase: "idle", person: null, room: null, reason: null },
          voice: { state: "inactive" },
        }) },
      ],
    },

    "arrival-unconfirmed": {
      id: "arrival-unconfirmed",
      label: "arrival never confirmed",
      description: "GPS home but Frigate never confirms — window times out, soft fallback, no spoken name",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 300, delta: (apply) => apply({
          proactive: { phase: "arrival_pending_confirmation", person: "Alex", reason: null, room: null, sightingLevel: "none" },
          __append: { kind: "diag", channel: "proactive", time: "now", text: "[proactive] arrival pending — awaiting Frigate confirmation for Alex" },
        }) },
        { at: 1600, delta: (apply) => apply({
          proactive: { sightingLevel: "person-unidentified" },
          __append: { kind: "diag", channel: "proactive", time: "now", text: "[proactive] arrival: person seen at living_room (unidentified)" },
        }) },
        { at: 3000, delta: (apply) => apply({
          proactive: { phase: "suppressed", reason: "unconfirmed" },
          __append: { kind: "proactive", time: "now", text: "Arrival likely (unconfirmed) — no spoken welcome." },
        }) },
        { at: 5500, delta: (apply) => apply({
          proactive: { phase: "idle", reason: null, person: null, sightingLevel: "none" },
        }) },
      ],
    },

    "left-home": {
      id: "left-home",
      label: "left home — away mode",
      description: "departure → lights off, away mode, status line flips to away",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: {
        voice: { state: "inactive" },
        proactive: { phase: "idle", away: true, reason: null, room: null, person: null, sightingLevel: "none", sinceTs: 0 },
        events: [].concat(makeHealthyChatHistory(), [
          { id: uid(), kind: "proactive", time: "now", text: "Away mode — lights off." },
        ]),
      },
    },

    "welcome-home-followup": {
      id: "welcome-home-followup",
      label: "welcome-home + follow-up",
      description: "full timeline — confirm → welcome → mic opens → user reply → processed",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 300, delta: (apply) => apply({
          proactive: { phase: "arrival_pending_confirmation", person: "Alex", reason: null, room: null, sightingLevel: "none" },
          __append: { kind: "diag", channel: "proactive", time: "now", text: "[proactive] arrival pending — awaiting Frigate confirmation for Alex" },
        }) },
        { at: 1600, delta: (apply) => apply({
          __append: { kind: "proactive", time: "now", text: "Return-home scene applied." },
        }) },
        { at: 1900, delta: (apply) => apply({
          proactive: { phase: "speaking_proactive_message", person: "Alex" },
          voice: { state: "speaking" },
          __append: { kind: "proactive", time: "now", text: "Welcome home, Alex — spoken." },
        }) },
        // satellite: responding → listening (TTS done, mic open)
        { at: 3600, delta: (apply) => apply({
          proactive: { phase: "listening_for_followup" },
          voice: { state: "listening" },
        }) },
        // satellite: listening → processing (the user replied)
        { at: 5400, delta: (apply) => apply({
          proactive: { phase: "processing_followup" },
          voice: { state: "thinking" },
          __append: { kind: "voice", time: "now", text: "make it cozy" },
        }) },
        { at: 6000, delta: (apply) => apply({
          __append: { kind: "proactive", time: "now", text: 'Follow-up: "make it cozy"' },
        }) },
        { at: 6800, delta: (apply) => apply({
          proactive: { phase: "idle", person: null, room: null },
          voice: { state: "inactive" },
          __append: { kind: "home", time: "now", text: "On it — living room warm and low." },
        }) },
      ],
    },

    "proactive-timeout": {
      id: "proactive-timeout",
      label: "follow-up window timeout",
      description: "welcome spoken → follow-up window opens → no reply → returns to idle",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 300, delta: (apply) => apply({
          proactive: { phase: "speaking_proactive_message", person: "Alex" },
          voice: { state: "speaking" },
          __append: { kind: "proactive", time: "now", text: "Welcome home, Alex — spoken." },
        }) },
        { at: 2000, delta: (apply) => apply({
          proactive: { phase: "listening_for_followup" },
          voice: { state: "listening" },
        }) },
        // no reply — the followupWindow timer fires
        { at: 6000, delta: (apply) => apply({
          proactive: { phase: "followup_timeout" },
          voice: { state: "inactive" },
        }) },
        { at: 9500, delta: (apply) => apply({
          proactive: { phase: "idle", person: null, room: null, reason: null },
        }) },
      ],
    },

    "room-entry-kitchen": {
      id: "room-entry-kitchen",
      label: "room-entry prompt",
      description: "enter the kitchen → short room-aware prompt + follow-up window",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 400, delta: (apply) => apply({
          proactive: { phase: "speaking_proactive_message", room: "kitchen", reason: null, person: null },
          voice: { state: "speaking" },
          __append: { kind: "proactive", time: "now", text: "Room prompt — kitchen." },
        }) },
        { at: 2200, delta: (apply) => apply({
          proactive: { phase: "listening_for_followup", room: "kitchen" },
          voice: { state: "listening" },
        }) },
        { at: 4200, delta: (apply) => apply({
          proactive: { phase: "processing_followup" },
          voice: { state: "thinking" },
          __append: { kind: "voice", time: "now", text: "no thanks" },
        }) },
        { at: 5200, delta: (apply) => apply({
          proactive: { phase: "idle", room: null },
          voice: { state: "inactive" },
        }) },
      ],
    },

    "room-entry-suppressed": {
      id: "room-entry-suppressed",
      label: "room prompt suppressed (context)",
      description: "enter a room while movie mode is on → prompt suppressed, diag explains why",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 400, delta: (apply) => apply({
          proactive: { phase: "suppressed", reason: "movie-mode", room: "kitchen", person: null },
          __append: { kind: "diag", channel: "proactive", time: "now", text: "[proactive] room prompt (kitchen) suppressed: movie-mode" },
        }) },
        { at: 4600, delta: (apply) => apply({
          proactive: { phase: "idle", reason: null, room: null },
        }) },
      ],
    },

    "room-entry-cooldown": {
      id: "room-entry-cooldown",
      label: "room prompt suppressed (cooldown)",
      description: "re-enter the kitchen within the per-room cooldown → suppressed",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        // first entry — greeted
        { at: 300, delta: (apply) => apply({
          proactive: { phase: "speaking_proactive_message", room: "kitchen", reason: null, person: null },
          voice: { state: "speaking" },
          __append: { kind: "proactive", time: "now", text: "Room prompt — kitchen." },
        }) },
        { at: 2000, delta: (apply) => apply({
          proactive: { phase: "idle", room: null },
          voice: { state: "inactive" },
        }) },
        // re-entry, still inside the per-room cooldown
        { at: 3200, delta: (apply) => apply({
          proactive: { phase: "suppressed", reason: "room-cooldown", room: "kitchen" },
          __append: { kind: "diag", channel: "proactive", time: "now", text: "[proactive] room prompt (kitchen) suppressed: room-cooldown" },
        }) },
        { at: 7400, delta: (apply) => apply({
          proactive: { phase: "idle", reason: null, room: null },
        }) },
      ],
    },

    // ── External Reasoning Fallback scenarios ─────────────────────
    // Six scenarios validating the router, key-entry, log streaming,
    // unavailable + failure states without ever calling a real API.
    // (askExternal()'s window.__SIM_ACTIVE guard handles the runtime
    // side; these timelines drive the UI.)

    "external-answer": {
      id: "external-answer",
      label: "external answer (no stream)",
      description: "user asks a general-knowledge question; external answer renders with subtle 'external' tag",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 100,  delta: (apply) => apply({ __append: { kind: "user", time: "now", text: "explain quantum physics" } }) },
        { at: 600,  delta: (apply) => apply({ __append: { kind: "thinking", time: "now", text: "asking external model…" } }) },
        { at: 1800, delta: (apply) => apply({
          __append: {
            kind: "external", time: "now",
            text: "Quantum physics is the branch of physics describing matter and energy at the smallest scales. Where classical physics predicts definite outcomes, quantum theory uses probabilities. The big results: wave-particle duality, superposition, and entanglement.",
            streaming: false,
          },
        }) },
      ],
    },

    "external-streaming": {
      id: "external-streaming",
      label: "external streaming",
      description: "external answer arrives as chunks over ~4s with a streaming caret",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 100,  delta: (apply) => apply({ __append: { kind: "user", time: "now", text: "what is the difference between OLED and Mini LED?" } }) },
        { at: 500,  delta: (apply) => apply({ __append: { kind: "thinking", time: "now", text: "asking external model…" } }) },
        { at: 1000, delta: (apply) => apply({ __append: { kind: "external", time: "now", text: "OLED panels emit ", streaming: true } }) },
        { at: 1400, delta: (apply) => apply({ __updateLastAction: { text: "OLED panels emit light per pixel, so blacks are absolute. " } }) },
        { at: 2000, delta: (apply) => apply({ __updateLastAction: { text: "OLED panels emit light per pixel, so blacks are absolute. Mini LED uses a dense LCD backlight " } }) },
        { at: 2700, delta: (apply) => apply({ __updateLastAction: { text: "OLED panels emit light per pixel, so blacks are absolute. Mini LED uses a dense LCD backlight with thousands of dimming zones — brighter peak, but limited per-pixel control. " } }) },
        { at: 3500, delta: (apply) => apply({ __updateLastAction: { text: "OLED panels emit light per pixel, so blacks are absolute. Mini LED uses a dense LCD backlight with thousands of dimming zones — brighter peak, but limited per-pixel control. OLED wins on contrast; Mini LED wins on sustained brightness.", streaming: false } }) },
      ],
    },

    "external-unavailable": {
      id: "external-unavailable",
      label: "external unavailable",
      description: "user asks a general question; provider is not configured — friendly error",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 100, delta: (apply) => apply({ __append: { kind: "user", time: "now", text: "explain how transformer attention works" } }) },
        { at: 400, delta: (apply) => apply({ __append: {
          kind: "system", time: "now", tone: "warn",
          text: "external reasoning is not configured — use /external set-key",
        } }) },
      ],
    },

    "external-confirmation": {
      id: "external-confirmation",
      label: "mixed-query confirmation (Phase C mockup)",
      description: "placeholder UI: mixed-query confirmation modal would appear here once sanitized-context opt-in is wired",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 100, delta: (apply) => apply({ __append: { kind: "user", time: "now", text: "given my office, suggest lighting design principles" } }) },
        { at: 400, delta: (apply) => apply({ __append: {
          kind: "system", time: "now", tone: "info",
          text: "[Phase C mockup] would prompt: 'I can answer locally, or send a sanitized room summary to the external model for a richer answer — which?'",
        } }) },
      ],
    },

    "mixed-query": {
      id: "mixed-query",
      label: "mixed query → local",
      description: "user asks a mixed home+general question; classifier routes to local because of HOME_NOUNS match (acceptable per plan)",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 100,  delta: (apply) => apply({ __append: { kind: "user", time: "now", text: "given i'm in the office, why does warm lighting feel cozier" } }) },
        { at: 500,  delta: (apply) => apply({ voice: { state: "thinking" } }) },
        { at: 1800, delta: (apply) => apply({
          __append: { kind: "home", time: "now", text: "Warmer color temperatures (2200-2700K) bias toward red/orange wavelengths, which the body associates with sunset light — promoting evening relaxation. Cooler light suppresses melatonin and feels more alert. In your office, dropping to ~2700K is a good cozy-mode setting." },
          voice: { state: "inactive" },
        }) },
      ],
    },

    "external-failure": {
      id: "external-failure",
      label: "external timeout/failure",
      description: "request times out at 30s — friendly error replaces in-flight state",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: { events: makeHealthyChatHistory(), voice: { state: "inactive" } },
      timeline: [
        { at: 100,  delta: (apply) => apply({ __append: { kind: "user", time: "now", text: "explain how transformer attention works" } }) },
        { at: 500,  delta: (apply) => apply({ __append: { kind: "thinking", time: "now", text: "asking external model…" } }) },
        { at: 3500, delta: (apply) => apply({ __append: {
          kind: "system", time: "now", tone: "error",
          text: "external request timed out (30s)",
        } }) },
      ],
    },

    // ─── Identity / world-state scenarios (Addendum 4, Phase 4A.4) ─────
    // Mock the UI surfaces (identity pill, camera labels, perception
    // events) that the world-state aggregator drives in production. Lets
    // us verify the UX without standing in front of a real camera.

    "alex-in-kitchen": {
      id: "alex-in-kitchen",
      label: "Alex in the kitchen",
      description: "face-rec match on kitchen camera, high confidence, fresh",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: {
        events: makeHealthyChatHistory(),
        voice: { state: "inactive" },
        identity: {
          name: "Alex", camera: "kitchen", score: 0.92,
          confidence_band: "high", ts: Date.now() / 1000, first_seen_today: false,
        },
        cameraLabels: { kitchen: ["person"] },
      },
    },

    "alex-in-office": {
      id: "alex-in-office",
      label: "Alex in the workshop (office)",
      description: "face-rec match on workshop camera, high confidence",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: {
        events: makeHealthyChatHistory(),
        voice: { state: "inactive" },
        identity: {
          name: "Alex", camera: "workshop", score: 0.88,
          confidence_band: "high", ts: Date.now() / 1000, first_seen_today: false,
        },
        cameraLabels: { workshop: ["person"] },
      },
    },

    "person-unknown-living-room": {
      id: "person-unknown-living-room",
      label: "unknown person in living room",
      description: "person occupancy on, NO face-rec match — should render 'someone'",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: {
        events: makeHealthyChatHistory().concat([
          { id: uid(), kind: "perception", time: "now", text: "living_room: a person in dark clothes facing away from camera" },
        ]),
        voice: { state: "inactive" },
        identity: null,
        cameraLabels: { living_room: ["person"] },
      },
    },

    "multiple-people": {
      id: "multiple-people",
      label: "Alex + unknown person",
      description: "Alex identified in kitchen, unknown person in living_room",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: {
        events: makeHealthyChatHistory().concat([
          { id: uid(), kind: "perception", time: "now", text: "kitchen: Alex at the island, reading something" },
          { id: uid(), kind: "perception", time: "now", text: "living_room: someone standing near the window, face obscured" },
        ]),
        voice: { state: "inactive" },
        identity: {
          name: "Alex", camera: "kitchen", score: 0.91,
          confidence_band: "high", ts: Date.now() / 1000, first_seen_today: false,
        },
        cameraLabels: { kitchen: ["person"], living_room: ["person"] },
      },
    },

    "stale-identity": {
      id: "stale-identity",
      label: "Alex last seen 5 min ago",
      description: "identity pill is dim, age suffix should be visible",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: {
        events: makeHealthyChatHistory(),
        voice: { state: "inactive" },
        identity: {
          name: "Alex", camera: "workshop", score: 0.87,
          confidence_band: "high", ts: (Date.now() / 1000) - 300, first_seen_today: false,
        },
        cameraLabels: {},
      },
    },

    "low-confidence-face": {
      id: "low-confidence-face",
      label: "low-confidence face match",
      description: "confidence 0.45 — pill renders dim with band='medium'",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: {
        events: makeHealthyChatHistory(),
        voice: { state: "inactive" },
        identity: {
          name: "Alex", camera: "living_room", score: 0.45,
          confidence_band: "medium", ts: Date.now() / 1000, first_seen_today: false,
        },
        cameraLabels: { living_room: ["person"] },
      },
    },

    "frigate-face-offline": {
      id: "frigate-face-offline",
      label: "Frigate face-rec offline",
      description: "no identity available; only generic person detection",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: {
        events: makeHealthyChatHistory().concat([
          { id: uid(), kind: "diag", channel: "camera", time: "now", text: "[camera] face-rec sensors unavailable" },
          { id: uid(), kind: "perception", time: "now", text: "kitchen: a person visible at the counter" },
        ]),
        voice: { state: "inactive" },
        identity: null,
        cameraLabels: { kitchen: ["person"], living_room: ["person"] },
      },
    },

    // ── M1 perception scheduler (Addendum 27) ─────────────────────
    // Three scenarios validate the auto-trigger → snapshot-thumbnail
    // → chat-bubble path end-to-end without requiring the live HA +
    // vision-sidecar deploy. Each carries a snapshotUrl on the
    // perception event so PerceptionContent renders the thumbnail.
    // Source images use Frigate's actual snapshot endpoint pattern
    // (or a placeholder picsum.photos URL when running without
    // Frigate); UI behaves identically either way.

    "perception-auto-trigger-on-occupancy": {
      id: "perception-auto-trigger-on-occupancy",
      label: "M1: occupancy → perception thumbnail",
      description: "kitchen occupancy fires the scheduler, perception chip shows a thumbnail + caption",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: {
        events: makeHealthyChatHistory().concat([
          {
            id: uid(),
            kind: "diag",
            channel: "routing",
            time: "now",
            text: "[route] auto • perception_dispatch • room=kitchen • verdict=completed • 1342ms",
          },
          {
            id: uid(),
            kind: "perception",
            time: "now",
            text: "kitchen: Alex at the island reading something on a tablet",
            snapshotUrl: "https://picsum.photos/seed/m1-kitchen/320/200",
            source: "occupancy",
            latencyMs: 1342,
          },
        ]),
        voice: { state: "inactive" },
        identity: {
          name: "Alex", camera: "kitchen", score: 0.92,
          confidence_band: "high", ts: Date.now() / 1000, first_seen_today: false,
        },
        cameraLabels: { kitchen: ["person"] },
      },
    },

    "perception-storm-debounced": {
      id: "perception-storm-debounced",
      label: "M1: event storm → debounced",
      description: "person walking past camera fires 5 events; scheduler dispatches once then debounces 4",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: {
        events: makeHealthyChatHistory().concat([
          {
            id: uid(),
            kind: "perception",
            time: "now",
            text: "driveway: Alex approaching the front door, carrying a package",
            snapshotUrl: "https://picsum.photos/seed/m1-driveway/320/200",
            source: "frigate_event",
            latencyMs: 1820,
          },
          { id: uid(), kind: "diag", channel: "routing", time: "now", text: "[route] auto • perception_dispatch • room=driveway • verdict=debounced • age=3s" },
          { id: uid(), kind: "diag", channel: "routing", time: "now", text: "[route] auto • perception_dispatch • room=driveway • verdict=debounced • age=8s" },
          { id: uid(), kind: "diag", channel: "routing", time: "now", text: "[route] auto • perception_dispatch • room=driveway • verdict=debounced • age=14s" },
          { id: uid(), kind: "diag", channel: "routing", time: "now", text: "[route] auto • perception_dispatch • room=driveway • verdict=debounced • age=22s" },
        ]),
        voice: { state: "inactive" },
        identity: {
          name: "Alex", camera: "driveway", score: 0.87,
          confidence_band: "high", ts: Date.now() / 1000, first_seen_today: false,
        },
        cameraLabels: { driveway: ["person"] },
      },
    },

    "perception-multi-room-thumbnails": {
      id: "perception-multi-room-thumbnails",
      label: "M1: multiple cached perceptions",
      description: "every room with recent activity has a cached caption + thumbnail; tap to expand",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: {
        events: makeHealthyChatHistory().concat([
          {
            id: uid(),
            kind: "perception",
            time: "now",
            text: "kitchen: Alex at the island, reading on a tablet",
            snapshotUrl: "https://picsum.photos/seed/m1-kitchen-2/320/200",
            source: "frigate_event",
            latencyMs: 1200,
          },
          {
            id: uid(),
            kind: "perception",
            time: "now",
            text: "living_room: empty couch with the TV on standby",
            snapshotUrl: "https://picsum.photos/seed/m1-living/320/200",
            source: "occupancy",
            latencyMs: 1450,
          },
          {
            id: uid(),
            kind: "perception",
            time: "now",
            text: "workshop: tools laid out on the bench, no one present",
            snapshotUrl: "https://picsum.photos/seed/m1-workshop/320/200",
            source: "occupancy",
            latencyMs: 1380,
          },
        ]),
        voice: { state: "inactive" },
        identity: {
          name: "Alex", camera: "kitchen", score: 0.91,
          confidence_band: "high", ts: Date.now() / 1000, first_seen_today: false,
        },
        cameraLabels: { kitchen: ["person"] },
      },
    },

    // ── Lab tab scenarios (Addendum 10) ───────────────────────────

    "metrics-timeline-healthy": {
      id: "metrics-timeline-healthy",
      label: "lab · healthy",
      description: "21 nominal turns, VRAM 60-65%, GPU spikes only during LLM/synth",
      preservesChatHistory: false,
      baseline: "healthy",
      snapshot: {
        lab: makeLabFixture("metrics-timeline-healthy"),
        aiStackState: {
          overall: "ready",
          services: [
            { name: "vllm", container: "running", health: "healthy", probe: "ok", probe_kind: "http", detail: "qwen3-vl-30b" },
            { name: "wyoming-parakeet", container: "running", health: "healthy", probe: "ok", probe_kind: "http" },
            { name: "kokoro", container: "running", health: "healthy", probe: "ok", probe_kind: "http" },
            { name: "vision-sidecar", container: "running", health: "healthy", probe: "ok", probe_kind: "http" },
          ],
          uptime_h: 44,
          vllm_model_loaded: "qwen3-vl-30b",
          expected_vllm_model: "qwen3-vl-30b",
        },
        aiStackOnline: true,
      },
    },

    "metrics-timeline-high-vram": {
      id: "metrics-timeline-high-vram",
      label: "lab · high vram",
      description: "VRAM at 91% — pressured state, free-gpu action visible",
      preservesChatHistory: false,
      baseline: "metrics-timeline-healthy",
      snapshot: {
        metrics: { ...HEALTHY_METRICS, vram: 87.5, vramMax: 96, gpu: 82 },
        lab: makeLabFixture("metrics-timeline-high-vram"),
      },
    },

    "metrics-timeline-slow-llm": {
      id: "metrics-timeline-slow-llm",
      label: "lab · slow llm",
      description: "Last 5 turns have ~1.1s LLM stage (vs ~400ms baseline)",
      preservesChatHistory: false,
      baseline: "metrics-timeline-healthy",
      snapshot: {
        lab: makeLabFixture("metrics-timeline-slow-llm"),
      },
    },

    "metrics-timeline-stt-cpu-spike": {
      id: "metrics-timeline-stt-cpu-spike",
      label: "lab · stt cpu spike",
      description: "CPU spikes to 60-75% during every STT segment",
      preservesChatHistory: false,
      baseline: "metrics-timeline-healthy",
      snapshot: {
        lab: makeLabFixture("metrics-timeline-stt-cpu-spike"),
      },
    },

    "metrics-timeline-tts-slow": {
      id: "metrics-timeline-tts-slow",
      label: "lab · tts slow",
      description: "Synth + audio stages widen on last 4 turns",
      preservesChatHistory: false,
      baseline: "metrics-timeline-healthy",
      snapshot: {
        lab: makeLabFixture("metrics-timeline-tts-slow"),
      },
    },

    "metrics-timeline-history": {
      id: "metrics-timeline-history",
      label: "lab · degradation history",
      description: "21 turns trending healthy → high VRAM → slow LLM — the demo scenario",
      preservesChatHistory: false,
      baseline: "metrics-timeline-healthy",
      snapshot: {
        metrics: { ...HEALTHY_METRICS, vram: 80, vramMax: 96, gpu: 55 },
        lab: makeLabFixture("metrics-timeline-history"),
      },
    },

    "metrics-timeline-no-data": {
      id: "metrics-timeline-no-data",
      label: "lab · awaiting turns",
      description: "Empty buffer — 'awaiting first 5 turns' copy",
      preservesChatHistory: false,
      baseline: "metrics-timeline-healthy",
      snapshot: {
        lab: { turns: [], samples: [] },
      },
    },

    "ai-stack-starting": {
      id: "ai-stack-starting",
      label: "lab · stack starting",
      description: "AI stack warming — progress bar, loading model",
      preservesChatHistory: false,
      baseline: "metrics-timeline-healthy",
      snapshot: {
        aiStackState: {
          overall: "starting",
          services: [
            { name: "vllm", container: "running", health: "starting", probe: "loading" },
            { name: "wyoming-parakeet", container: "running", health: "healthy", probe: "ok" },
            { name: "kokoro", container: "running", health: "healthy", probe: "ok" },
            { name: "vision-sidecar", container: "running", health: "healthy", probe: "ok" },
          ],
          in_flight: { verb: "start", step: "loading qwen3-vl-30b weights", progress_pct: 58, eta_s: 18, started_at: nowSecs() - 25 },
        },
        aiStackOnline: true,
      },
    },

    "ai-stack-error": {
      id: "ai-stack-error",
      label: "lab · stack error",
      description: "vllm crashed (OOM) — error banner, restart action visible",
      preservesChatHistory: false,
      baseline: "metrics-timeline-healthy",
      snapshot: {
        aiStackState: {
          overall: "error",
          services: [
            { name: "vllm", container: "exited", health: "error", probe: "error", detail: "oom on layer 32/64 · retrying with reduced ctx" },
            { name: "wyoming-parakeet", container: "running", health: "healthy", probe: "ok" },
            { name: "kokoro", container: "running", health: "healthy", probe: "ok" },
            { name: "vision-sidecar", container: "running", health: "healthy", probe: "ok" },
          ],
        },
        aiStackOnline: true,
      },
    },

    "spatial-calibration": {
      id: "spatial-calibration",
      label: "spatial · light footprint map",
      description: "Addendum 38 Phase 1 — open /spatial to review the Map tab against a mock calibration (12 lights, 1 vlm-disagreement to exercise the human gate)",
      preservesChatHistory: true,
      snapshot: { voice: { state: "inactive" } },
    },
  };

  /* Return a scenario by id, OR healthy if unknown. */
  function get(id) {
    return scenarios[id] || null;
  }

  /* Resolve a scenario into a fully-merged snapshot by walking
   * `baseline` chains. Returns the snapshot ready for application. */
  function resolveSnapshot(id) {
    const sc = scenarios[id];
    if (!sc) return null;
    let merged = {};
    if (sc.baseline) {
      const base = resolveSnapshot(sc.baseline);
      if (base) merged = { ...base };
    } else if (id !== "healthy") {
      // Default-implicit baseline is healthy unless explicitly set
      merged = { ...HEALTHY_SNAPSHOT };
    }
    return patch(merged, sc.snapshot || {});
  }

  /* List for autocomplete / help. */
  function list() {
    return Object.keys(scenarios).map((k) => ({
      id: k, label: scenarios[k].label, description: scenarios[k].description,
    }));
  }

  /* M5 (Addendum 27) — sim-mode fixture for the explainability drawer.
   * When the drawer opens with a convId AND sim is active, it calls
   * window.__SIM_EXPLAIN_FIXTURE(convId) for canned entries instead
   * of hitting the real /routing_log endpoint. Returns null for
   * "passthrough to live fetch" — but in sim mode there IS no live
   * fetch, so we return a canned multi-entry turn for any convId. */
  function makeExplainFixture(convId) {
    const now = Math.floor(Date.now() / 1000);
    return [
      {
        ts: new Date((now - 8) * 1000).toISOString(),
        kind: "routing_decision",
        conv_id: convId || "sim-conv-0001",
        text: "what do you see in the kitchen",
        text_len: 30,
        src: "voice",
        intent: "local",
        matched: "HOME_QUERIES",
        key_present: true,
        dispatched: "local",
        local_reply_chars: 64,
        local_reply_snippet: "I see Alex standing at the island, holding a coffee mug.",
        ext_status: null, ext_latency_ms: null,
        ext_reply_chars: null, ext_reply_snippet: null,
      },
      {
        ts: now - 7.5,
        kind: "tool_call",
        conv_id: convId || "sim-conv-0001",
        tool: "get_room_state",
        args: { room: "kitchen" },
        result: { ok: true, latency_ms: 48, freshness: "fresh" },
        latency_ms: 48,
      },
      {
        ts: now - 7.2,
        kind: "tool_call",
        conv_id: convId || "sim-conv-0001",
        tool: "find_person",
        args: { name: "alex" },
        result: { ok: true, latency_ms: 31, suggested_phrasing: "I see Alex in the kitchen.", confidence_band: "high" },
        latency_ms: 31,
      },
      {
        // M5+ (Addendum 27): demonstrates the tool timeline bar's
        // value — describe_clip dominates the turn's latency budget.
        // Pre-this-fixture-update the explain fixture had only 2 fast
        // tools → timeline showed two near-equal bars with no signal.
        // Adding describe_clip at 4800ms makes the visualization
        // immediately readable: one wide ice bar (vision call) + two
        // tiny ones (state lookups).
        ts: now - 6.5,
        kind: "tool_call",
        conv_id: convId || "sim-conv-0001",
        tool: "describe_clip",
        args: { camera: "kitchen", frames: 4, interval_s: 1.0 },
        result: {
          ok: true, latency_ms: 4820,
          frames_captured: 4, span_s: 3.0,
          description: "Alex crosses the kitchen from left to right.",
        },
        latency_ms: 4820,
      },
      {
        ts: now - 6,
        kind: "perception_dispatch",
        conv_id: convId || "sim-conv-0001",
        room: "kitchen",
        verdict: "ok",
        source: "occupancy",
        latency_ms: 1850,
        camera_entity_id: "camera.kitchen",
        caption_chars: 56,
      },
    ];
  }

  /* F-32 (Addendum 27) — sim-mode fixture for the world-state drawer.
   * Returns the same shape WorldStateView produces (per
   * world_state.py:get_world_state). Lets the drawer be visually
   * exercised in /sim without HA. */
  function makeWorldStateFixture() {
    const nowIso = new Date().toISOString();
    const tsAgo = (sec) => new Date(Date.now() - sec * 1000).toISOString();
    return {
      enabled: true,
      updated_at: nowIso,
      rooms: {
        kitchen: {
          name: "kitchen",
          occupied: true,
          persons: [{
            identity: { name: "Alex", confidence: 0.92, confidence_band: "high" },
            age_seconds: 12,
            camera_entity_id: "camera.kitchen",
          }],
          perception_summary: "Alex standing at the island, mug in hand.",
          perception_age_seconds: 30,
          frigate_labels: ["person"],
        },
        living_room: {
          name: "living_room",
          occupied: true,
          persons: [{
            identity: { name: "Amelia", confidence: 0.78, confidence_band: "medium" },
            age_seconds: 85,
            camera_entity_id: "camera.living_room",
          }, {
            identity: null,
            age_seconds: 200,
            camera_entity_id: "camera.living_room",
          }],
          perception_summary: "Two people on the couch.",
          perception_age_seconds: 110,
          frigate_labels: ["person"],
        },
        office: {
          name: "office",
          occupied: false,
          persons: [],
          perception_summary: null,
          perception_age_seconds: null,
          frigate_labels: [],
        },
        driveway: {
          name: "driveway",
          occupied: false,
          persons: [],
          perception_summary: "Empty driveway, no vehicles.",
          perception_age_seconds: 720,
          frigate_labels: [],
        },
      },
      people: {
        Alex: {
          ha_location: "home",
          last_visual_room: "kitchen",
          last_visual_at: tsAgo(12),
          last_visual_confidence: 0.92,
          last_visual_camera: "camera.kitchen",
          currently_seen: true,
        },
        Amelia: {
          ha_location: "home",
          last_visual_room: "living_room",
          last_visual_at: tsAgo(85),
          last_visual_confidence: 0.78,
          last_visual_camera: "camera.living_room",
          currently_seen: true,
        },
        David: {
          ha_location: "away",
          last_visual_room: "driveway",
          last_visual_at: tsAgo(3600 * 4),
          last_visual_confidence: 0.85,
          last_visual_camera: "camera.driveway",
          currently_seen: false,
        },
      },
      system: {
        frigate_online: true,
        tracked_entity_count: 22,
      },
    };
  }

  Object.assign(window, {
    SimScenarios: {
      get, resolveSnapshot, list, all: scenarios,
      healthy: HEALTHY_SNAPSHOT,
    },
    __SIM_EXPLAIN_FIXTURE: makeExplainFixture,
    __SIM_WORLD_STATE_FIXTURE: makeWorldStateFixture,
    // Addendum 38 Phase 1 — mock spatial_model.json for the /spatial
    // drawer Map tab. Lets the Map tab + the human correctness gate be
    // visually validated before the one-time photometric calibration
    // (tools/calibrate-light-footprints.py) has ever run. 12 lights:
    // 9 vlm-validated, 1 vlm-disagreement (light.island_right — drives
    // the Confirm/Re-calibrate/Mark-empty gate), 2 footprint-empty
    // ambient switches (out of every camera's view).
    __SIM_SPATIAL_MODEL_FIXTURE: function () {
      const cluster = (cx, cy, w0) => {
        const out = {};
        for (let dc = -2; dc <= 2; dc++) {
          for (let dr = -1; dr <= 1; dr++) {
            const c = cx + dc;
            const r = cy + dr;
            if (c < 0 || c > 31 || r < 0 || r > 17) continue;
            const w = Math.max(0.15,
              w0 - 0.17 * (Math.abs(dc) + Math.abs(dr)));
            out[c + "," + r] = Math.round(w * 1000) / 1000;
          }
        }
        return out;
      };
      const cam = (zones) => ({
        detect_w: 1280, detect_h: 720, grid_cols: 32, grid_rows: 18,
        cell_w: 40, cell_h: 40, zones: zones || {},
      });
      // Real Frigate zone geometry (normalised [0,1] vertices) so the
      // /spatial Map tab's zone overlay is exercised in sim mode.
      const zone = (polygon, color) => ({
        polygon: polygon, objects: ["person"], inertia: 3,
        loitering_time: 0, color: color,
      });
      const mk = (camera, cx, cy, w, extra) => {
        const base = {
          footprint: {},
          footprint_empty: !camera,
          vlm_validated: !!camera,
          vlm_disagreement: false,
          vlm_region: "",
          vlm_jaccard: camera ? 0.8 : null,
          clipped_cell_count: {},
          human_verdict: null,
        };
        if (camera) base.footprint[camera] = cluster(cx, cy, w);
        return Object.assign(base, extra || {});
      };
      return {
        schema: 1,
        calibrated: true,
        calibrated_at: new Date(Date.now() - 6 * 3600 * 1000).toISOString(),
        frigate_url: "http://sim-frigate.local:5000",
        grid: { target_cols: 32 },
        cameras: {
          living_room: cam({
            sofa: zone([[0.43, 0.708], [0.509, 0.364], [0.847, 0.344],
                        [0.844, 0.577], [0.756, 0.722], [0.759, 0.892],
                        [0.709, 0.954]], [31, 119, 180]),
            Front_Door: zone([[0.172, 0.382], [0.071, 0.455],
                              [0.167, 0.84], [0.257, 0.766]], [255, 127, 14]),
          }),
          kitchen: cam({
            island_left: zone([[0, 0.275], [0.446, 0.272], [0.467, 1],
                               [0.003, 0.997]], [44, 160, 44]),
            sink: zone([[0.296, 0], [0.308, 1], [0.994, 0.995],
                        [0.996, 0.003]], [214, 39, 40]),
          }),
          dining_room: cam({
            Dining_Left: zone([[0.002, 0.054], [0.002, 0.993], [0.455, 1],
                               [0.468, 0], [0, 0.007]], [148, 103, 189]),
            Dining_Right: zone([[0.469, 0.003], [0.456, 1], [0.998, 0.996],
                                [0.999, 0]], [140, 86, 75]),
          }),
          workshop: cam({
            workshop_zone: zone([[0.123, 1], [0.07, 0.042], [0.445, 0.136],
                                 [0.407, 0.995]], [227, 119, 194]),
          }),
        },
        lights: {
          "light.front_left": mk("living_room", 7, 12, 0.95,
            { vlm_region: "lower-left, around the sofa", vlm_jaccard: 0.83 }),
          "light.front_right": mk("living_room", 24, 12, 0.92,
            { vlm_region: "lower-right, near the front door", vlm_jaccard: 0.79 }),
          "light.rear_left": mk("living_room", 7, 5, 0.88,
            { vlm_region: "upper-left corner", vlm_jaccard: 0.71 }),
          "light.rear_right": mk("living_room", 24, 5, 0.90,
            { vlm_region: "upper-right corner", vlm_jaccard: 0.74 }),
          "light.office": mk("living_room", 28, 8, 0.85,
            { vlm_region: "far right — the office nook", vlm_jaccard: 0.68 }),
          "light.dining_table_left": mk("dining_room", 11, 9, 0.97,
            { vlm_region: "over the dining table, left", vlm_jaccard: 0.90 }),
          "light.dining_table_right": mk("dining_room", 21, 9, 0.96,
            { vlm_region: "over the dining table, right", vlm_jaccard: 0.88 }),
          "light.sink": mk("kitchen", 16, 10, 0.94,
            { vlm_region: "the sink counter", vlm_jaccard: 0.85 }),
          "light.island_left": mk("kitchen", 9, 11, 0.91,
            { vlm_region: "left half of the island", vlm_jaccard: 0.80 }),
          "light.island_right": mk("kitchen", 23, 11, 0.62, {
            vlm_validated: false, vlm_disagreement: true, vlm_jaccard: 0.18,
            vlm_region: "unclear — the VLM saw the back wall, photometric saw the island counter",
          }),
          "switch.ambient_light_left_mss110_main_channel": mk(null, 0, 0, 0),
          "switch.ambient_light_right_mss110_main_channel": mk(null, 0, 0, 0),
        },
        calibration_meta: {
          run_duration_s: 287.4,
          footprint_threshold: 0.08,
          onvif_exposure_locked: false,
          agc_robust_metric: true,
          vlm_model: "qwen3-vl-30b",
          notes: ["sim fixture — not a real calibration"],
        },
      };
    },
    // /cameras slash fixture — returns a per-camera-id → snapshotUrl
    // map. Uses tiny inline SVG data URLs (placeholder thumbnails
    // colored per room) so the perception render path is exercised
    // without hitting a real vision-sidecar.
    __SIM_CAMERAS_FIXTURE: function () {
      const placeholder = (room, hue) => {
        const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='160' height='90' viewBox='0 0 160 90'>` +
          `<rect width='160' height='90' fill='hsl(${hue},35%,18%)'/>` +
          `<text x='80' y='52' fill='hsl(${hue},40%,72%)' font-family='monospace' font-size='14' text-anchor='middle'>${room}</text>` +
          `<text x='80' y='72' fill='hsl(${hue},25%,55%)' font-family='monospace' font-size='9' text-anchor='middle'>sim · 160×90</text>` +
          `</svg>`;
        return "data:image/svg+xml;utf8," + encodeURIComponent(svg);
      };
      return {
        kitchen:     placeholder("kitchen",     180),
        living_room: placeholder("living room", 30),
        dining_room: placeholder("dining room", 280),
        workshop:    placeholder("workshop",    220),
        driveway:    placeholder("driveway",    120),
      };
    },
    // F-3 + /describe-clip sim fixture. Returns a canned response
    // matching the vision-sidecar /describe_clip shape so the slash
    // command renders end-to-end without hitting the real Ubuntu box.
    // Description varies by camera for visual variety.
    __SIM_DESCRIBE_CLIP_FIXTURE: function (camera, frames, interval_s) {
      const cam = (camera || "kitchen").toLowerCase();
      const sceneByCam = {
        kitchen:     "Alex crosses the kitchen from left to right and reaches for something on the counter.",
        living_room: "The room is empty across all frames; no motion detected.",
        driveway:    "A delivery van slows at the curb in the first frames, then a person steps out and approaches the door.",
        workshop:    "Someone moves at a workbench, picking up and setting down a tool.",
        dining_room: "Two people sit at the table — no significant motion across the clip.",
      };
      const desc = sceneByCam[cam] || `Frames captured at ${cam}; no notable motion across the clip.`;
      const span = (frames - 1) * interval_s;
      // Pseudo-realistic timing — sampling roughly matches the wall
      // clock; inference small for "sim everything is fast"
      const samplingMs = Math.round(span * 1000) + 40;
      const inferenceMs = 380 + Math.round(Math.random() * 200);
      return {
        camera, entity_id: `camera.${cam.replace(/\s/g, "_")}`,
        description: desc,
        frames_captured: frames, span_s: Math.round(span * 100) / 100,
        sampling_ms: samplingMs, inference_ms: inferenceMs,
        latency_ms: samplingMs + inferenceMs + 60,
        model: "qwen3-vl-30b",
      };
    },
  });
})();
