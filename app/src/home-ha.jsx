/* Home — Home Assistant WebSocket client + pipeline event mapping.
 *
 * Implements the auth handshake + assist_pipeline/run subscription required
 * to drive a conversation through HA. Surfaces inbound pipeline events as
 * UI-shaped event objects (the same shape home-events.jsx renders).
 *
 * HA protocol reference:
 *   https://developers.home-assistant.io/docs/api/websocket
 *   https://developers.home-assistant.io/docs/voice/pipelines/
 */

const HG_HA_KEEPALIVE_MS = 30_000;
const HG_HA_RUN_TIMEOUT_MS = 120_000;

/* Turn an http(s) URL into the matching ws(s) /api/websocket URL.
 *   http://192.168.0.125:8123       → ws://192.168.0.125:8123/api/websocket
 *   https://ha.example.com          → wss://ha.example.com/api/websocket
 */
function haUrlToWs(baseUrl) {
  if (!baseUrl) return "";
  let u = baseUrl.trim().replace(/\/+$/, "");
  if (u.startsWith("/")) {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}${u}/api/websocket`;
  }
  if (u.startsWith("https://"))      u = "wss://"  + u.slice("https://".length);
  else if (u.startsWith("http://"))  u = "ws://"   + u.slice("http://".length);
  else if (!u.startsWith("ws"))      u = "ws://"   + u;
  if (!/\/api\/websocket$/.test(u))  u += "/api/websocket";
  return u;
}

/* Tiny event emitter; HAClient is the single sink for HA events. */
function makeEmitter() {
  const subs = new Set();
  return {
    on(fn)  { subs.add(fn);    return () => subs.delete(fn); },
    emit(x) { for (const fn of subs) try { fn(x); } catch (e) { console.error(e); } },
    clear() { subs.clear(); },
  };
}

function buildCallServicePayload(domain, service, data = {}) {
  const targetKeys = new Set(["entity_id", "area_id", "device_id"]);
  const target = {};
  const serviceData = {};
  for (const [key, value] of Object.entries(data || {})) {
    if (value == null) continue;
    if (targetKeys.has(key)) target[key] = value;
    else serviceData[key] = value;
  }
  const payload = {
    type: "call_service",
    domain,
    service,
    service_data: serviceData,
  };
  if (Object.keys(target).length > 0) payload.target = target;
  return payload;
}

class HAClient {
  constructor() {
    this.ws = null;
    this.url = null;
    this.token = null;
    this.haVersion = null;
    this._msgId = 1;
    this._authPromise = null;
    this._runs = new Map();     // wsMsgId → { onEvent, onResult, t0 }
    this._connectionEmitter = makeEmitter();
    this._keepaliveTimer = null;
    this._reconnectTimer = null;
    this._reconnectAttempt = 0;
    this._manualDisconnect = false;
    this._suppressReconnect = false;
  }

  /* Public: subscribe to "connection state" changes. */
  onConnection(fn) { return this._connectionEmitter.on(fn); }

  /* Open + auth. Resolves on `auth_ok`, rejects on `auth_invalid` or
   * a network error. Multiple concurrent connect() calls share the
   * same promise. */
  connect(url, token) {
    // Simulation Mode: refuse to open the HA WS. Sim scenarios set the
    // connection state synthetically. Return an already-rejected promise
    // so callers see "offline" cleanly without throwing.
    if (typeof window !== "undefined" && window.__SIM_ACTIVE === true) {
      console.warn("[sim] HAClient.connect blocked — Simulation Mode is active.");
      return Promise.reject(new Error("blocked-by-simulation-mode"));
    }
    this._manualDisconnect = false;
    this._suppressReconnect = false;
    this._clearReconnect();
    // If a previous socket is dead or dying (closed/closing after an
    // auth_invalid or a network drop), tear it down before reconnecting.
    // Otherwise the stale rejected _authPromise gets replayed forever and
    // a retry with a fresh token never reaches HA.
    if (this.ws && this.ws.readyState !== WebSocket.OPEN && this.ws.readyState !== WebSocket.CONNECTING) {
      try { this.ws.close(); } catch {}
      this.ws = null;
      this._authPromise = null;
    }
    if (this.ws && this._authPromise) return this._authPromise;
    this.url = url;
    this.token = token;
    this._authPromise = new Promise((resolve, reject) => {
      let resolved = false;
      const wsUrl = haUrlToWs(url);
      this._connectionEmitter.emit({ state: "connecting", url: wsUrl });
      let ws;
      try {
        ws = new WebSocket(wsUrl);
      } catch (e) {
        this._connectionEmitter.emit({ state: "offline", error: String(e) });
        reject(e);
        return;
      }
      this.ws = ws;
      ws.onopen = () => { /* auth flow runs on first message */ };
      ws.onclose = (e) => {
        const closedCurrentSocket = this.ws === ws;
        if (!closedCurrentSocket) return;
        if (!this._manualDisconnect && !this._suppressReconnect) {
          this._connectionEmitter.emit({ state: "offline", code: e.code, reason: e.reason || "" });
        }
        if (!resolved) reject(new Error(`ws closed (${e.code})`));
        this._stopKeepalive();
        for (const [, run] of this._runs) {
          try { run.onResult({ success: false, error: { code: "ws_closed", message: e.reason || "WS closed" } }); }
          catch {}
        }
        this._runs.clear();
        // Drop references to the dead socket so the next connect() builds a
        // fresh one instead of replaying this socket's settled _authPromise.
        this.ws = null;
        this._authPromise = null;
        if (!this._manualDisconnect && !this._suppressReconnect && this.url && this.token) {
          this._scheduleReconnect();
        }
      };
      ws.onerror = (e) => {
        if (!resolved) {
          this._connectionEmitter.emit({ state: "offline", error: "network error" });
          reject(new Error("ws error"));
        }
      };
      ws.onmessage = (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch { return; }
        if (msg.type === "auth_required") {
          this.haVersion = msg.ha_version;
          ws.send(JSON.stringify({ type: "auth", access_token: token }));
          return;
        }
        if (msg.type === "auth_ok") {
          this.haVersion = msg.ha_version || this.haVersion;
          this._reconnectAttempt = 0;
          this._connectionEmitter.emit({ state: "online", haVersion: this.haVersion });
          this._startKeepalive();
          resolved = true;
          resolve({ haVersion: this.haVersion });
          return;
        }
        if (msg.type === "auth_invalid") {
          this._suppressReconnect = true;
          this._connectionEmitter.emit({ state: "auth_invalid", message: msg.message });
          resolved = true;
          reject(new Error(msg.message || "auth invalid"));
          try { ws.close(); } catch {}
          return;
        }
        if (msg.type === "event" && this._runs.has(msg.id)) {
          const run = this._runs.get(msg.id);
          run.onEvent(msg.event);
          // Pipeline runs complete on `run-end` (the actual event), not on
          // the first `result` message — that's only an ack the subscription
          // was accepted. Tear down the run here.
          if (msg.event?.type === "run-end") {
            this._runs.delete(msg.id);
            run.onResult({ success: true });
          } else if (msg.event?.type === "error") {
            this._runs.delete(msg.id);
            run.onResult({
              success: false,
              error: { message: msg.event.data?.message || "pipeline error" },
            });
          }
          return;
        }
        if (msg.type === "result" && this._runs.has(msg.id)) {
          // For assist_pipeline/run the result is just the subscription ACK
          // and the pipeline events stream after — we only tear down on
          // success=false. For everything else (subscribe_events, plain
          // calls) the result IS terminal — flagged via _terminalOnResult.
          const run = this._runs.get(msg.id);
          if (!msg.success || run._terminalOnResult) {
            this._runs.delete(msg.id);
            run.onResult(msg);
          }
          return;
        }
        // pong / unknown — ignore
      };
    });
    return this._authPromise;
  }

  disconnect() {
    this._manualDisconnect = true;
    this._clearReconnect();
    this._stopKeepalive();
    try { this.ws?.close(); } catch {}
    this.ws = null;
    this._authPromise = null;
    this._runs.clear();
  }

  _clearReconnect() {
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
  }

  _scheduleReconnect() {
    if (this._reconnectTimer) return;
    const delay = Math.min(30_000, 1_000 * (2 ** Math.min(this._reconnectAttempt, 5)));
    this._reconnectAttempt += 1;
    this._connectionEmitter.emit({ state: "connecting", url: haUrlToWs(this.url), reconnect: true });
    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null;
      if (this._manualDisconnect || this._suppressReconnect || !this.url || !this.token) return;
      this.connect(this.url, this.token).catch(() => {
        if (!this._manualDisconnect && !this._suppressReconnect) this._scheduleReconnect();
      });
    }, delay);
  }

  /* HA's WS connection drops silently if idle on some routers. Send a
   * cheap ping every 30s. */
  _startKeepalive() {
    this._stopKeepalive();
    this._keepaliveTimer = setInterval(() => {
      if (this.ws?.readyState !== 1) return;
      const id = this._msgId++;
      try { this.ws.send(JSON.stringify({ id, type: "ping" })); } catch {}
    }, HG_HA_KEEPALIVE_MS);
  }
  _stopKeepalive() {
    if (this._keepaliveTimer) {
      clearInterval(this._keepaliveTimer);
      this._keepaliveTimer = null;
    }
  }

  /* Start a text pipeline run. Returns { id, cancel, done } where
   * `done` resolves when the run completes (success or error). Events
   * arrive via the `onEvent` callback in real time.
   *
   * For voice mode, pass startStage: "stt" + audioStream — see Phase 2.
   */
  runPipeline({
    text,
    conversationId = null,
    pipelineId = null,
    onEvent = () => {},
  } = {}) {
    if (!this.ws || this.ws.readyState !== 1) {
      return Promise.reject(new Error("not connected"));
    }
    const id = this._msgId++;
    const payload = {
      id,
      type: "assist_pipeline/run",
      start_stage: "intent",
      end_stage: "intent",
      input: { text },
    };
    if (conversationId) payload.conversation_id = conversationId;
    if (pipelineId)     payload.pipeline = pipelineId;

    const t0 = performance.now();
    let timeoutHandle = null;

    const done = new Promise((resolve, reject) => {
      this._runs.set(id, {
        t0,
        onEvent: (ev) => { try { onEvent(ev); } catch (e) { console.error(e); } },
        onResult: (res) => {
          if (timeoutHandle) clearTimeout(timeoutHandle);
          console.log("[ha result]", res);
          if (res.success) resolve({ t0, elapsedMs: performance.now() - t0 });
          else             reject(new Error(
            res.error?.message ||
            JSON.stringify(res.error) ||
            "pipeline failed"
          ));
        },
      });
      try {
        console.log("[ha send]", payload);
        this.ws.send(JSON.stringify(payload));
      } catch (e) {
        console.error("[ha send fail]", e);
        this._runs.delete(id);
        reject(e);
        return;
      }
      timeoutHandle = setTimeout(() => {
        if (!this._runs.has(id)) return;
        this._runs.delete(id);
        reject(new Error("run timeout"));
      }, HG_HA_RUN_TIMEOUT_MS);
    });

    const cancel = () => {
      if (this._runs.has(id)) this._runs.delete(id);
      if (timeoutHandle) clearTimeout(timeoutHandle);
    };

    return { id, done, cancel };
  }

  /* Voice pipeline run: start_stage=stt, end_stage=tts.
   *
   * Returns { id, audioHandlerReady, sendAudioChunk, done, cancel }.
   * Caller awaits audioHandlerReady to learn the binary handler id from
   * HA's run-start event, then calls sendAudioChunk(Uint8Array) per
   * captured frame. End the stream by sending an empty Uint8Array. */
  runVoicePipeline({
    onEvent = () => {},
    conversationId = null,
    pipelineId = null,
  } = {}) {
    if (!this.ws || this.ws.readyState !== 1) {
      return Promise.reject(new Error("not connected"));
    }
    const id = this._msgId++;
    const payload = {
      id,
      type: "assist_pipeline/run",
      start_stage: "stt",
      end_stage: "tts",
      input: { sample_rate: 16000 },
      // HA 2026.5 rejects audio_settings as an invalid extra key — noise
      // suppression / AGC live in the satellite or pipeline config now.
    };
    if (conversationId) payload.conversation_id = conversationId;
    if (pipelineId)     payload.pipeline = pipelineId;

    const t0 = performance.now();
    let stt_handler_id = null;
    let audioReadyResolve, audioReadyReject;
    const audioHandlerReady = new Promise((res, rej) => {
      audioReadyResolve = res;
      audioReadyReject = rej;
    });
    let timeoutHandle = null;

    const wrappedOnEvent = (ev) => {
      if (ev?.type === "run-start") {
        // run-start.data.runner_data.stt_binary_handler_id is the binary
        // handler — every audio frame must be prefixed with this byte.
        stt_handler_id = ev?.data?.runner_data?.stt_binary_handler_id;
        if (stt_handler_id != null) audioReadyResolve(stt_handler_id);
      }
      try { onEvent(ev); } catch (e) { console.error(e); }
    };

    const done = new Promise((resolve, reject) => {
      this._runs.set(id, {
        t0,
        onEvent: wrappedOnEvent,
        onResult: (res) => {
          if (timeoutHandle) clearTimeout(timeoutHandle);
          if (res.success) resolve({ t0, elapsedMs: performance.now() - t0 });
          else             reject(new Error(res.error?.message || "voice pipeline failed"));
        },
      });
      try {
        console.log("[ha voice send]", payload);
        this.ws.send(JSON.stringify(payload));
      } catch (e) {
        this._runs.delete(id);
        audioReadyReject(e);
        reject(e);
        return;
      }
      timeoutHandle = setTimeout(() => {
        if (!this._runs.has(id)) return;
        this._runs.delete(id);
        audioReadyReject(new Error("voice run timeout"));
        reject(new Error("voice run timeout"));
      }, HG_HA_RUN_TIMEOUT_MS);
    });

    const sendAudioChunk = (pcmBytes) => {
      if (stt_handler_id == null || this.ws?.readyState !== 1) return;
      // Prepend the handler-id byte. HA expects raw PCM s16le 16kHz mono
      // following one byte of handler id, all as one binary frame.
      const frame = new Uint8Array(1 + pcmBytes.byteLength);
      frame[0] = stt_handler_id;
      frame.set(pcmBytes, 1);
      try { this.ws.send(frame); } catch (e) { console.error("[audio send]", e); }
    };

    const cancel = () => {
      if (this._runs.has(id)) this._runs.delete(id);
      if (timeoutHandle) clearTimeout(timeoutHandle);
    };

    return { id, audioHandlerReady, sendAudioChunk, done, cancel };
  }

  /* Subscribe to HA event_type stream. Returns an unsubscribe function. */
  subscribeEvents(eventType, onEvent) {
    if (!this.ws || this.ws.readyState !== 1) {
      throw new Error("not connected");
    }
    const id = this._msgId++;
    this._runs.set(id, {
      t0: performance.now(),
      onEvent: (ev) => { try { onEvent(ev); } catch (e) { console.error(e); } },
      onResult: () => {},
    });
    const payload = { id, type: "subscribe_events" };
    if (eventType) payload.event_type = eventType;
    try { this.ws.send(JSON.stringify(payload)); } catch {}
    return () => {
      if (!this._runs.has(id)) return;
      this._runs.delete(id);
      try {
        const unsubId = this._msgId++;
        this.ws?.send(JSON.stringify({ id: unsubId, type: "unsubscribe_events", subscription: id }));
      } catch {}
    };
  }

  /* Generic WS call → result promise (no event subscription). */
  call(payload) {
    if (!this.ws || this.ws.readyState !== 1) {
      return Promise.reject(new Error("not connected"));
    }
    const id = this._msgId++;
    payload.id = id;
    return new Promise((resolve, reject) => {
      // Use the _runs map but only listen for result.
      this._runs.set(id, {
        t0: performance.now(),
        onEvent: () => {},
        onResult: (res) => {
          if (res.success) resolve(res.result);
          else             reject(new Error(res.error?.message || "ws call failed"));
        },
      });
      // Re-tweak: WS messages of type=result for non-run calls also need
      // tear-down, so flag this run as terminal-on-result.
      this._runs.get(id)._terminalOnResult = true;
      try { this.ws.send(JSON.stringify(payload)); }
      catch (e) { this._runs.delete(id); reject(e); }
    });
  }

  callService(domain, service, data = {}) {
    return this.call(buildCallServicePayload(domain, service, data));
  }
}

/* ── Pipeline event → UI event mapping ──────────────────────────────────
 *
 * Returns: { uiEvents: [...], speechText: "...", convId: "...", toolCalls: [...] }
 *
 * Called when an `intent-end` event arrives. The UI events list is
 * appended to the feed; the speechText becomes the streaming home reply
 * (if it hadn't streamed already via intent-progress); convId is stored
 * for the next turn's continuity.
 */
function extractIntentEnd(haEvent) {
  const data = haEvent?.data || {};
  const intent = data.intent_output || {};
  const response = intent.response || {};
  const speech = response?.speech?.plain?.speech
              || response?.speech?.ssml?.speech
              || "";
  const convId = intent.conversation_id || null;

  // Tool calls — HA's chat_log has assistant turns with tool_calls. Different
  // HA versions and agent backends format this differently; try a few shapes
  // and emit cards for whatever we find. Anything else falls through to just
  // the speech text.
  const toolCalls = [];
  const chatLog = data.chat_log || intent.chat_log || [];
  for (const turn of chatLog) {
    if (Array.isArray(turn?.tool_calls)) {
      for (const tc of turn.tool_calls) {
        toolCalls.push({
          name: tc.tool_name || tc.name || "tool",
          args: tc.tool_args || tc.arguments || {},
          status: "success",
        });
      }
    }
    if (turn?.role === "tool" && turn?.tool_call_id) {
      // Tool result — try to enrich the matching call with success/failure.
      const last = toolCalls[toolCalls.length - 1];
      if (last && turn.tool_call_id === (last._id || last.id)) {
        last.result = turn.tool_result || turn.content;
      }
    }
  }

  // Newer HA versions stash device control results in response.data.success / .failed
  // arrays — surface those as action cards too.
  const successes = response?.data?.success || [];
  const failures  = response?.data?.failed  || [];
  const actions = [];
  for (const s of successes) {
    actions.push({
      kind: "action",
      title: s.name || "Action",
      service: s.domain ? `${s.domain}.${s.service || ""}` : null,
      target: s.target || s.entity_id || null,
      attrs: s.service_data || s.data || {},
      status: "success",
    });
  }
  for (const f of failures) {
    actions.push({
      kind: "action",
      title: f.name || "Action",
      service: f.domain ? `${f.domain}.${f.service || ""}` : null,
      target: f.target || f.entity_id || null,
      attrs: f.service_data || f.data || {},
      status: "error",
      reason: f.message || f.error || "failed",
    });
  }

  return { speech, convId, toolCalls, actions };
}

/* Extract a streaming chunk from an intent-progress event, if any.
 *
 * Empirical shape (HA 2026.5.1 + extended_openai_conversation, probe-pipeline-verbose.ps1
 * transcript 2026-05-22): role and content arrive in SEPARATE events. The role event is a
 * marker like { chat_log_delta: { role: "assistant" } } with no content; subsequent deltas
 * are { chat_log_delta: { content: "D" } } / { content: "ishes" } / ... with no role. Tool
 * calls arrive as their own event: { chat_log_delta: { tool_calls: [{ tool_name, tool_args,
 * id, external }] } }. So we cannot gate text-deltas on role; just take any string content.
 */
function extractIntentProgress(haEvent) {
  const d = haEvent?.data || {};
  const delta = d.chat_log_delta;
  if (delta) {
    if (typeof delta.content === "string" && delta.content.length > 0) {
      return { textDelta: delta.content, toolCalls: null };
    }
    if (Array.isArray(delta.tool_calls) && delta.tool_calls.length > 0) {
      return {
        textDelta: null,
        toolCalls: delta.tool_calls.map((tc) => ({
          name: tc.tool_name || tc.name || "tool",
          args: tc.tool_args || tc.arguments || {},
          id: tc.id || null,
        })),
      };
    }
  }
  // Some integrations emit a raw token delta on `data.delta`.
  if (typeof d.delta === "string") return { textDelta: d.delta, toolCalls: null };
  return null;
}

Object.assign(window, {
  HAClient, haUrlToWs, buildCallServicePayload,
  extractIntentEnd, extractIntentProgress,
});
