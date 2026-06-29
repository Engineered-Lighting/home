#!/usr/bin/env node
/* Pure local tests for app/src/home-s2s.jsx. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-s2s.jsx");
const source = fs.readFileSync(SRC, "utf8");

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, cond, detail) {
  if (cond) {
    passes++;
    process.stdout.write("  PASS  " + name + "\n");
  } else {
    fails++;
    failures.push({ name, detail });
    process.stdout.write("  FAIL  " + name);
    if (detail !== undefined) {
      const dumped = typeof detail === "string" ? detail : JSON.stringify(detail, null, 2);
      process.stdout.write("\n        " + dumped.replace(/\n/g, "\n        "));
    }
    process.stdout.write("\n");
  }
}

function makeAudioContextClass(audioLog) {
  return class FakeAudioContext {
    constructor(opts) {
      this.sampleRate = opts && opts.sampleRate || 48000;
      this.currentTime = 0;
      this.destination = { kind: "destination" };
      audioLog.push({ type: "ctx", sampleRate: this.sampleRate });
    }
    createGain() {
      return { gain: { value: 1 }, connect: (node) => audioLog.push({ type: "gain.connect", node }), disconnect: () => {} };
    }
    createAnalyser() {
      return { fftSize: 0, smoothingTimeConstant: 0, connect: (node) => audioLog.push({ type: "analyser.connect", node }) };
    }
    createMediaStreamSource() {
      return { connect: (node) => audioLog.push({ type: "source.connect", node }), disconnect: () => {} };
    }
    createScriptProcessor(frameSize, inChannels, outChannels) {
      const node = {
        frameSize,
        inChannels,
        outChannels,
        onaudioprocess: null,
        connect: (target) => audioLog.push({ type: "processor.connect", target }),
        disconnect: () => {},
      };
      audioLog.push({ type: "processor", frameSize, inChannels, outChannels, node });
      return node;
    }
    createBuffer(channels, length, sampleRate) {
      return {
        channels,
        length,
        sampleRate,
        duration: length / sampleRate,
        copyToChannel: (data, channel) => audioLog.push({ type: "copy", len: data.length, channel }),
      };
    }
    createBufferSource() {
      return {
        buffer: null,
        connect: (target) => audioLog.push({ type: "buffer.connect", target }),
        start: (at) => audioLog.push({ type: "buffer.start", at }),
      };
    }
    close() {
      audioLog.push({ type: "ctx.close", sampleRate: this.sampleRate });
    }
  };
}

function makeWebSocketClass(wsLog) {
  return class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;
    constructor(url) {
      this.url = url;
      this.readyState = FakeWebSocket.CONNECTING;
      this.binaryType = "";
      this.listeners = {};
      this.sent = [];
      wsLog.instances.push(this);
    }
    addEventListener(type, cb) {
      this.listeners[type] = this.listeners[type] || [];
      this.listeners[type].push(cb);
    }
    send(data) {
      this.sent.push(data);
      wsLog.sent.push(data);
    }
    close() {
      if (this.readyState === FakeWebSocket.CLOSED) return;
      this.readyState = FakeWebSocket.CLOSED;
      wsLog.closed += 1;
      this.dispatch("close", {});
    }
    open() {
      this.readyState = FakeWebSocket.OPEN;
      this.dispatch("open", {});
    }
    dispatch(type, event) {
      for (const cb of this.listeners[type] || []) cb(event);
    }
    message(data) {
      this.dispatch("message", { data });
    }
  };
}

function makeModule(opts) {
  opts = opts || {};
  const wsLog = { instances: [], sent: [], closed: 0 };
  const audioLog = [];
  const warnings = [];
  const errors = [];
  const logs = [];
  const tracks = [{ stopped: false, stop() { this.stopped = true; } }];
  const AudioContext = makeAudioContextClass(audioLog);
  const WebSocket = makeWebSocketClass(wsLog);
  WebSocket.OPEN = WebSocket.prototype.constructor.OPEN;
  WebSocket.CONNECTING = WebSocket.prototype.constructor.CONNECTING;
  WebSocket.CLOSED = WebSocket.prototype.constructor.CLOSED;

  const navigator = {
    mediaDevices: {
      getUserMedia: async (constraints) => {
        if (opts.micReject) throw new Error(opts.micReject);
        audioLog.push({ type: "getUserMedia", constraints });
        return { getTracks: () => tracks };
      },
    },
  };
  const window = {
    __SIM_ACTIVE: !!opts.sim,
    AudioContext,
    webkitAudioContext: AudioContext,
    location: opts.location || { protocol: "http:", host: "localhost:5181" },
    setTimeout: (fn) => { fn(); return 1; },
  };
  const sandbox = {
    window,
    navigator,
    WebSocket,
    URL,
    Uint8Array,
    Int16Array,
    Float32Array,
    ArrayBuffer,
    Math,
    JSON,
    console: {
      warn: (...args) => warnings.push(args.join(" ")),
      error: (...args) => errors.push(args.join(" ")),
      log: (...args) => logs.push(args.join(" ")),
    },
    setTimeout: window.setTimeout,
  };
  vm.runInNewContext(source, sandbox, { filename: SRC });
  return { window, navigator, wsLog, audioLog, warnings, errors, logs, tracks };
}

function lastJsonSent(ws) {
  const strings = ws.sent.filter((msg) => typeof msg === "string");
  return strings.map((msg) => JSON.parse(msg));
}

async function main() {
  process.stdout.write("\ns2s_exports_and_url_test\n");
  const base = makeModule();
  assert("startS2SRun exported", typeof base.window.startS2SRun === "function");
  assert("bridgeWsUrl exported", typeof base.window.bridgeWsUrl === "function");
  assert("http base maps to ws /s2s", base.window.bridgeWsUrl("http://box.local:8092", "") === "ws://box.local:8092/s2s");
  assert("https base maps to wss /s2s", base.window.bridgeWsUrl("https://box.local", "") === "wss://box.local/s2s");
  assert("ws base stays ws", base.window.bridgeWsUrl("ws://box.local:8092", "") === "ws://box.local:8092/s2s");
  assert("wss base stays wss", base.window.bridgeWsUrl("wss://box.local/old", "") === "wss://box.local/s2s");
  assert("token is URL encoded", base.window.bridgeWsUrl("http://box.local", "a b+c") === "ws://box.local/s2s?token=a%20b%2Bc");
  const webBase = makeModule({ location: { protocol: "https:", host: "home.tailnet.ts.net" } });
  assert("relative base maps to same-origin wss /s2s", webBase.window.bridgeWsUrl("/proxy/bridge", "tok") === "wss://home.tailnet.ts.net/proxy/bridge/s2s?token=tok", webBase.window.bridgeWsUrl("/proxy/bridge", "tok"));
  assert("unsupported protocol returns null", base.window.bridgeWsUrl("ftp://box.local", "") === null);
  assert("invalid base returns null", base.window.bridgeWsUrl("not a url", "") === null);
  assert("empty base returns null", base.window.bridgeWsUrl("", "") === null);

  process.stdout.write("\ns2s_no_live_call_guard_test\n");
  const sim = makeModule({ sim: true });
  const simErrors = [];
  const simHandle = await sim.window.startS2SRun({
    s2sBase: "http://box.local:8092",
    onError: (err) => simErrors.push(err),
  });
  assert("Simulation Mode returns inert handle", simHandle && typeof simHandle.stop === "function" && typeof simHandle.setVoice === "function");
  assert("Simulation Mode does not open WebSocket", sim.wsLog.instances.length === 0, sim.wsLog.instances);
  assert("Simulation Mode does not request mic", !sim.audioLog.some((e) => e.type === "getUserMedia"), sim.audioLog);
  assert("Simulation Mode reports mock-only error", simErrors[0] && /Simulation Mode/.test(simErrors[0].message || String(simErrors[0])), simErrors);

  const invalid = makeModule();
  const invalidErrors = [];
  const invalidHandle = await invalid.window.startS2SRun({ s2sBase: "bad", onError: (err) => invalidErrors.push(err) });
  assert("invalid URL returns inert handle", invalidHandle && typeof invalidHandle.stop === "function");
  assert("invalid URL skips mic request", invalid.audioLog.length === 0, invalid.audioLog);
  assert("invalid URL reports error", invalidErrors[0] === "invalid s2s base url", invalidErrors);

  const micFail = makeModule({ micReject: "denied" });
  const micErrors = [];
  const micHandle = await micFail.window.startS2SRun({ s2sBase: "http://box.local:8092", onError: (err) => micErrors.push(err) });
  assert("mic failure returns inert handle", micHandle && typeof micHandle.stop === "function");
  assert("mic failure skips WebSocket", micFail.wsLog.instances.length === 0, micFail.wsLog.instances);
  assert("mic failure reports user-facing error", /mic unavailable/.test(micErrors[0] || ""), micErrors);

  process.stdout.write("\ns2s_session_protocol_test\n");
  const mod = makeModule();
  const states = [];
  const transcripts = [];
  const identities = [];
  const presences = [];
  const media = [];
  const errors = [];
  const handle = await mod.window.startS2SRun({
    s2sBase: "https://box.local:8092",
    s2sToken: "tok",
    voicePrompt: "calm",
    kokoroVoice: "am_eric",
    conversationId: "conv-1",
    conversationSummary: "kitchen lights",
    onState: (state, message) => states.push({ state, message }),
    onTranscript: (role, text, partial) => transcripts.push({ role, text, partial }),
    onError: (err) => errors.push(err),
    onIdentity: (msg) => identities.push(msg),
    onPresence: (msg) => presences.push(msg),
    onMedia: (msg) => media.push(msg),
  });
  assert("session opens one WebSocket", mod.wsLog.instances.length === 1, mod.wsLog.instances);
  const ws = mod.wsLog.instances[0];
  assert("session uses wss bridge URL with token", ws.url === "wss://box.local:8092/s2s?token=tok", ws.url);
  ws.open();
  const helloMsgs = lastJsonSent(ws);
  assert("hello sent first", helloMsgs[0].type === "hello" && helloMsgs[0].conversation_id === "conv-1", helloMsgs);
  assert("hello includes prompt/voice/summary", helloMsgs[0].voice_prompt === "calm" && helloMsgs[0].kokoro_voice === "am_eric" && helloMsgs[0].conversation_summary === "kitchen lights", helloMsgs[0]);
  assert("voice mode asserted after hello", helloMsgs[1].type === "set_voice_mode" && helloMsgs[1].on === true, helloMsgs);
  assert("mic analyser exposed after open", mod.window.s2sAnalysers && mod.window.s2sAnalysers.mic, mod.window.s2sAnalysers);

  const processor = mod.audioLog.find((e) => e.type === "processor").node;
  processor.onaudioprocess({
    inputBuffer: {
      getChannelData: () => new Float32Array([-2, -1, -0.5, 0, 0.5, 1, 2]),
    },
  });
  const binaryFrame = ws.sent.find((msg) => msg instanceof Uint8Array);
  assert("mic frame sends binary PCM", binaryFrame instanceof Uint8Array && binaryFrame.byteLength === 14, binaryFrame && binaryFrame.byteLength);
  const pcm = new Int16Array(binaryFrame.buffer);
  assert("PCM conversion clamps/scales samples", pcm[0] === -32768 && pcm[1] === -32768 && pcm[3] === 0 && pcm[5] === 32767 && pcm[6] === 32767, Array.from(pcm));

  ws.message(JSON.stringify({ type: "state", state: "thinking", message: "routing" }));
  ws.message(JSON.stringify({ type: "transcript", role: "user", text: "lights", partial: true }));
  ws.message(JSON.stringify({ type: "audio_meta", sample_rate: 16000 }));
  ws.message(new Uint8Array([0, 0, 255, 127]).buffer);
  ws.message(JSON.stringify({ type: "tts_error", engine: "kokoro" }));
  ws.message(JSON.stringify({ type: "identity", name: "Marcelo", camera: "kitchen" }));
  ws.message(JSON.stringify({ type: "identity_clear", camera: "kitchen" }));
  ws.message(JSON.stringify({ type: "presence", event: "arrived", room: "kitchen" }));
  ws.message(JSON.stringify({ type: "media", event: "active", room: "living_room" }));
  assert("state callback receives bridge state", states.some((s) => s.state === "thinking" && s.message === "routing"), states);
  assert("transcript callback receives partial", transcripts[0] && transcripts[0].role === "user" && transcripts[0].partial === true, transcripts);
  assert("tts error degrades voice state", states.some((s) => s.state === "error" && /kokoro/.test(s.message)), states);
  assert("identity callbacks include clear event", identities.length === 2 && identities[1].name === null, identities);
  assert("presence callback receives event", presences[0] && presences[0].event === "arrived", presences);
  assert("media callback receives event", media[0] && media[0].room === "living_room", media);
  assert("binary audio creates player analyser", mod.window.s2sAnalysers && mod.window.s2sAnalysers.player, mod.window.s2sAnalysers);

  handle.setVoice("af_heart");
  handle.setVoiceMode(false);
  const liveMsgs = lastJsonSent(ws);
  assert("setVoice sends set_voice while open", liveMsgs.some((m) => m.type === "set_voice" && m.voice === "af_heart"), liveMsgs);
  assert("setVoiceMode sends false while open", liveMsgs.some((m) => m.type === "set_voice_mode" && m.on === false), liveMsgs);

  ws.message(JSON.stringify({ type: "error", message: "upstream locked" }));
  assert("bridge error callback fires", errors.includes("upstream locked"), errors);
  assert("bridge error closes socket", ws.readyState === ws.constructor.CLOSED, ws.readyState);
  assert("close marks inactive", states.some((s) => s.state === "inactive"), states);
  assert("stop releases mic tracks", mod.tracks.every((t) => t.stopped === true), mod.tracks);
  assert("close clears analyser references", mod.window.s2sAnalysers.mic === null && mod.window.s2sAnalysers.player === null, mod.window.s2sAnalysers);

  if (fails) {
    console.log("\nFailures:");
    for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
  }
  console.log(`\n${passes} pass . ${fails} fail`);
  process.exit(fails ? 1 : 0);
}

main().catch((err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
