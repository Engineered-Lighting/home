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
      this.gain.connect(this.ctx.destination);
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
    }
  }

  /* Build a bridge URL from a base. Accepts http(s)://host:port or
   * ws(s)://host:port; appends /s2s + optional ?token= for BRIDGE_TOKEN
   * auth (Phase 1+ — bridge rejects unauthenticated WS when token set). */
  function bridgeWsUrl(base, token) {
    if (!base) return null;
    let u;
    try { u = new URL(base); } catch { return null; }
    const wsProto = u.protocol === "https:" ? "wss:" : "ws:";
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
    conversationId,
    conversationSummary,
    onState,
    onTranscript,
    onError,
  }) {
    const url = bridgeWsUrl(s2sBase, s2sToken);
    if (!url) {
      onError?.("invalid s2s base url");
      return { stop: () => {} };
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
      return { stop: () => {} };
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
          conversation_summary: conversationSummary || undefined,
        }));
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
            onState?.(msg.state);
            return;
          case "transcript":
            onTranscript?.(msg.role, msg.text || "", !!msg.partial);
            return;
          case "audio_meta":
            player.setRate(msg.sample_rate || 24000);
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

    return { stop };
  }

  // Surface to the rest of the app.
  Object.assign(window, { startS2SRun, bridgeWsUrl });
})();
