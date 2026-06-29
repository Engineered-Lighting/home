/* Home — S2S (speech-to-speech) experimental mode.
 *
 * Side-by-side experiment with full-duplex S2S models. The existing
 * HA voice pipeline (mic → HA STT → vLLM → HA TTS → speaker) stays
 * intact. When `s2sMode` is on, the mic button routes through this
 * module instead, talking to the personaplex-bridge WebSocket on the
 * AI box.
 *
 * Wire protocol — see services/personaplex-bridge/main.py for the
 * authoritative spec.
 *
 * Why a separate module: keeps the experiment off the critical path.
 * Toggle it off and home-app.jsx behaves exactly as before. Toggle
 * it on and the same mic button takes you through the bridge.
 */

(() => {
  /* PCM Int16 LE @ 16 kHz mono mic frames. Browser mic is resampled
   * by the AudioContext (we set ctx sampleRate to 16000). */
  const MIC_SAMPLE_RATE = 16000;
  const FRAME_SIZE = 4096;

  /* Convert a Float32Array of [-1,1] mic samples to Int16 LE bytes. */
  function f32ToI16(f32) {
    const i16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const v = Math.max(-1, Math.min(1, f32[i]));
      i16[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    }
    return new Uint8Array(i16.buffer);
  }

  /* Streaming PCM player. Each incoming binary frame is decoded
   * into an AudioBuffer and scheduled to start at the end of the
   * previously-scheduled buffer. Some glitching between chunks is
   * possible — fine for a feel-test; a real implementation would
   * use an AudioWorklet with a ring buffer. */
  class PcmPlayer {
    constructor() {
      this.ctx = null;
      this.outputRate = 24000;
      this.cursor = 0;
      this.gain = null;
    }
    setRate(rate) {
      this.outputRate = rate;
      // If the context exists at a different rate, recreate it.
      if (this.ctx && this.ctx.sampleRate !== rate) {
        try { this.ctx.close(); } catch (e) { /* noop */ }
        this.ctx = null;
      }
    }
    _ensureCtx() {
      if (this.ctx) return;
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      this.ctx = new AudioCtx({ sampleRate: this.outputRate });
      this.gain = this.ctx.createGain();
      this.gain.gain.value = 1.0;
      // Phase 1.5 item 6: AnalyserNode taps the gain output so the
      // VoiceBanner's "speaking" waveform reflects the assistant's
      // actual TTS audio. We chain gain → analyser → destination so
      // the audio still plays normally.
      try {
        this.analyser = this.ctx.createAnalyser();
        this.analyser.fftSize = 128;
        this.analyser.smoothingTimeConstant = 0.5;
        this.gain.connect(this.analyser);
        this.analyser.connect(this.ctx.destination);
        window.s2sAnalysers = window.s2sAnalysers || {};
        window.s2sAnalysers.player = this.analyser;
      } catch (e) {
        // Browser doesn't support AnalyserNode? Fall back to direct
        // connect — the wave just won't animate.
        this.gain.connect(this.ctx.destination);
      }
      this.cursor = this.ctx.currentTime + 0.05;
    }
    push(bytes) {
      this._ensureCtx();
      // bytes is a Uint8Array of int16 LE PCM samples at outputRate.
      const i16 = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2);
      const f32 = new Float32Array(i16.length);
      for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;
      const buf = this.ctx.createBuffer(1, f32.length, this.outputRate);
      buf.copyToChannel(f32, 0, 0);
      const src = this.ctx.createBufferSource();
      src.buffer = buf;
      src.connect(this.gain);
      const startAt = Math.max(this.cursor, this.ctx.currentTime + 0.005);
      src.start(startAt);
      this.cursor = startAt + buf.duration;
    }
    close() {
      try { this.ctx?.close(); } catch (e) { /* noop */ }
      this.ctx = null;
      this.cursor = 0;
      this.analyser = null;
      try {
        if (window.s2sAnalysers) window.s2sAnalysers.player = null;
      } catch (e) { /* noop */ }
    }
  }

  /* Build a bridge URL from a base. Accepts http(s)://host:port or
   * ws(s)://host:port; appends /s2s + optional ?token= for BRIDGE_TOKEN
   * auth (Phase 1+ — bridge rejects unauthenticated WS when token set). */
  function bridgeWsUrl(base, token) {
    if (!base) return null;
    const clean = String(base).replace(/\/+$/, "");
    if (clean.startsWith("/")) {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const q = token ? `?token=${encodeURIComponent(token)}` : "";
      return `${proto}//${window.location.host}${clean}/s2s${q}`;
    }
    let u;
    try { u = new URL(clean); } catch { return null; }
    let wsProto;
    if (u.protocol === "https:" || u.protocol === "wss:") wsProto = "wss:";
    else if (u.protocol === "http:" || u.protocol === "ws:") wsProto = "ws:";
    else return null;
    const q = token ? `?token=${encodeURIComponent(token)}` : "";
    return `${wsProto}//${u.host}/s2s${q}`;
  }

  /* Start an S2S voice run. Returns a control object with .stop().
   * Calls back with state transitions, transcripts, and errors so
   * the host React component can render them. */
  async function startS2SRun({
    s2sBase,
    s2sToken,
    voicePrompt,
    kokoroVoice,
    conversationId,
    conversationSummary,
    onState,
    onTranscript,
    onError,
    onIdentity,
    onPresence,
    onMedia,
  }) {
    // Simulation Mode: voice WS is not opened. Sim scenarios animate
    // voice.state synthetically (see simulation-data.jsx).
    if (typeof window !== "undefined" && window.__SIM_ACTIVE === true) {
      console.warn("[sim] startS2SRun blocked — Simulation Mode is active.");
      if (typeof onError === "function") {
        try { onError({ message: "Simulation Mode — voice mock only" }); } catch {}
      }
      return { stop: () => {}, setVoice: () => {}, analysers: null };
    }
    const url = bridgeWsUrl(s2sBase, s2sToken);
    if (!url) {
      onError?.("invalid s2s base url");
      return { stop: () => {}, setVoice: () => {}, setVoiceMode: () => {} };
    }

    // Acquire mic first — if it fails we don't bother with the WS.
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: MIC_SAMPLE_RATE,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
    } catch (e) {
      onError?.(`mic unavailable · ${e?.message || e}`);
      return { stop: () => {}, setVoice: () => {}, setVoiceMode: () => {} };
    }

    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const inCtx = new AudioCtx({ sampleRate: MIC_SAMPLE_RATE });
    const player = new PcmPlayer();

    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";

    let stopped = false;
    let micStarted = false;
    let processorNode = null;
    let source = null;
    let muteGain = null;

    const stop = () => {
      if (stopped) return;
      stopped = true;
      try {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "stop" }));
        }
      } catch (e) { /* noop */ }
      try { processorNode?.disconnect(); } catch (e) { /* noop */ }
      try { muteGain?.disconnect(); } catch (e) { /* noop */ }
      try { source?.disconnect(); } catch (e) { /* noop */ }
      try { stream.getTracks().forEach((t) => t.stop()); } catch (e) { /* noop */ }
      try { inCtx.close(); } catch (e) { /* noop */ }
      // Phase 1.5: clear the mic analyser reference so the LiveWaveform
      // component falls back to its idle state instead of trying to
      // sample a dead AudioContext.
      try {
        if (window.s2sAnalysers) window.s2sAnalysers.mic = null;
      } catch (e) { /* noop */ }
      // Close the WebSocket so the bridge releases its upstream lock —
      // otherwise the next mic press gets "another s2s session is
      // active" until the WS times out (~20-40s). Player keeps the
      // trailing audio chunks because they're already queued client-side.
      try {
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          ws.close();
        }
      } catch (e) { /* noop */ }
    };

    ws.addEventListener("open", () => {
      try {
        ws.send(JSON.stringify({
          type: "hello",
          sample_rate: MIC_SAMPLE_RATE,
          conversation_id: conversationId || null,
          voice_prompt: voicePrompt || undefined,
          // Stage 2 (TM-shaped): per-session Kokoro voice. Bridge falls
          // back to its env default (BRIDGE_KOKORO_VOICE) if omitted.
          kokoro_voice: kokoroVoice || undefined,
          conversation_summary: conversationSummary || undefined,
        }));
        // The s2s run is only spawned when voice mode is on, so we
        // explicitly assert that to the bridge — keeps the bridge's
        // voice_mode flag in sync from the first message.
        ws.send(JSON.stringify({ type: "set_voice_mode", on: true }));
      } catch (e) { /* noop */ }

      // Start streaming mic frames.
      source = inCtx.createMediaStreamSource(stream);
      try {
        processorNode = inCtx.createScriptProcessor(FRAME_SIZE, 1, 1);
      } catch (e) {
        onError?.("audio processor unavailable");
        stop();
        return;
      }
      processorNode.onaudioprocess = (ev) => {
        if (stopped || ws.readyState !== WebSocket.OPEN) return;
        const bytes = f32ToI16(ev.inputBuffer.getChannelData(0));
        try { ws.send(bytes); } catch (e) { /* noop */ }
      };
      // Phase 1.5 item 6: mic AnalyserNode for the live-waveform UI.
      // 128-bin FFT is fine for a 18-bar visualizer; smoothing 0.5
      // gives natural bar movement without strobing.
      try {
        const micAnalyser = inCtx.createAnalyser();
        micAnalyser.fftSize = 128;
        micAnalyser.smoothingTimeConstant = 0.5;
        source.connect(micAnalyser);
        window.s2sAnalysers = window.s2sAnalysers || {};
        window.s2sAnalysers.mic = micAnalyser;
      } catch (e) { /* noop */ }
      source.connect(processorNode);
      muteGain = inCtx.createGain();
      muteGain.gain.value = 0;
      processorNode.connect(muteGain);
      muteGain.connect(inCtx.destination);
      micStarted = true;
    });

    ws.addEventListener("message", (ev) => {
      if (typeof ev.data === "string") {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        switch (msg.type) {
          case "state":
            // Forwards listening / transcribing / thinking / speaking /
            // ready / error from the bridge to the VoiceBanner state
            // machine. New state values from May 2026: transcribing,
            // thinking, ready, error. Older clients only saw listening /
            // processing / speaking — the home app is forwards-compatible
            // (unknown states still display as `<state>…`).
            onState?.(msg.state, msg.message);
            return;
          case "transcript":
            onTranscript?.(msg.role, msg.text || "", !!msg.partial);
            return;
          case "audio_meta":
            player.setRate(msg.sample_rate || 24000);
            return;
          case "voice_mode":
            // Bridge ack of set_voice_mode — no UI action needed; just
            // log so we can see the round-trip in dev tools.
            console.log("[s2s] voice_mode ack:", msg.on);
            return;
          case "tts_error":
            // Bridge couldn't synthesize speech. Text still flowed via
            // SSE; this surfaces a toast in the home app so user knows
            // why no audio played. onError reuses the existing error
            // pipe but doesn't tear down the WS (audio degraded, not
            // session-ending).
            console.warn("[s2s] tts_error:", msg);
            onState?.("error", `voice playback degraded · ${msg.engine || "tts"}`);
            return;
          case "identity":
            // Phase 1: Frigate face-rec emitted a match. Payload:
            // {name, camera, score, confidence_band, ts, first_seen_today}
            // Home app uses this for the "Seen by" header pill,
            // vision-tile name chip, and (when confidence_band=="high")
            // the WelcomeBanner.
            onIdentity?.(msg);
            return;
          case "identity_clear":
            // Frigate's _last_recognized_face flipped back to None/Unknown
            // for a camera. Drop the seen-by pill for that camera.
            onIdentity?.({ ...msg, name: null });
            return;
          case "presence":
            // person.<X> arrival / departure event. The "arrived"
            // variant fires the WelcomeBanner regardless of face match.
            onPresence?.(msg);
            return;
          case "media":
            // Phase 2: media_player.* state change. Payload:
            //   {event: "active", room, entity_id, state, app_name,
            //    title, artist, category, ts}
            //   or {event: "cleared", room, entity_id, ts}
            // Home app uses this for the per-camera media sub-line in
            // the vision drawer.
            onMedia?.(msg);
            return;
          case "error":
            onError?.(msg.message || "s2s error");
            // Tear down the WS — bridge holds an upstream lock that
            // doesn't release until this connection closes, so leaving
            // the socket open prevents the next mic press from getting
            // a fresh session.
            try { ws.close(); } catch (e) { /* noop */ }
            return;
          default:
            return;
        }
      }
      // Binary frame = PCM output.
      const arr = ev.data instanceof ArrayBuffer
        ? new Uint8Array(ev.data)
        : new Uint8Array(ev.data.buffer || ev.data);
      try { player.push(arr); } catch (e) {
        console.error("[s2s play]", e);
      }
    });

    ws.addEventListener("error", (ev) => {
      onError?.("bridge connection error");
    });
    ws.addEventListener("close", () => {
      stop();
      // Give the buffered playback a moment before tearing down.
      setTimeout(() => player.close(), 400);
      onState?.("inactive");
    });

    // Runtime voice swap — handleCommand calls this when the user runs
    // /voice <name>. Sends a `set_voice` WS message so the change
    // applies to the next TTS synth without needing to re-mic.
    const setVoice = (voice) => {
      if (!voice || stopped) return;
      try {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "set_voice", voice }));
        }
      } catch (e) { /* noop */ }
    };

    // Voice-mode toggle without closing the WS. Bridge gates
    // enqueue_speech on its voice_mode flag — when off, mic still works
    // (so the WS stays useful) but no audio playback. Called by the
    // home app's Voice button via window.startS2SRun's returned handle.
    const setVoiceMode = (on) => {
      if (stopped) return;
      try {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "set_voice_mode", on: !!on }));
        }
      } catch (e) { /* noop */ }
    };

    return { stop, setVoice, setVoiceMode };
  }

  // Surface to the rest of the app.
  Object.assign(window, { startS2SRun, bridgeWsUrl });
})();
