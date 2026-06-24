#!/usr/bin/env node
/* Pure local tests for app/src/home-ha.jsx. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-ha.jsx");

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
      const dumped = typeof detail === "string"
        ? detail
        : JSON.stringify(detail, null, 2);
      process.stdout.write("\n        " + dumped.replace(/\n/g, "\n        "));
    }
    process.stdout.write("\n");
  }
}

function loadModule(opts = {}) {
  const sandbox = {
    window: opts.window || {},
    console,
    performance: { now: () => 1234 },
    setInterval: opts.setInterval || setInterval,
    clearInterval: opts.clearInterval || clearInterval,
    setTimeout: opts.setTimeout || setTimeout,
    clearTimeout: opts.clearTimeout || clearTimeout,
  };
  if (opts.WebSocket) sandbox.WebSocket = opts.WebSocket;
  vm.runInNewContext(fs.readFileSync(SRC, "utf8"), sandbox, { filename: SRC });
  return sandbox.window;
}

async function expectReject(name, promise, pattern) {
  try {
    await promise;
    assert(name, false, "expected rejection");
  } catch (err) {
    assert(name, pattern.test(err && err.message || String(err)), err && err.message);
  }
}

function makeFakeTimers() {
  const timeouts = [];
  const intervals = [];
  return {
    timeouts,
    intervals,
    setTimeout(fn, delay) {
      const timer = { fn, delay, cleared: false };
      timeouts.push(timer);
      return timer;
    },
    clearTimeout(timer) {
      if (timer) timer.cleared = true;
    },
    setInterval(fn, delay) {
      const timer = { fn, delay, cleared: false };
      intervals.push(timer);
      return timer;
    },
    clearInterval(timer) {
      if (timer) timer.cleared = true;
    },
  };
}

function makeFakeWebSocket() {
  const sockets = [];
  class FakeWebSocket {
    constructor(url) {
      this.url = url;
      this.readyState = FakeWebSocket.CONNECTING;
      this.sent = [];
      sockets.push(this);
    }
    send(raw) {
      this.sent.push(raw);
    }
    close() {
      this.readyState = FakeWebSocket.CLOSED;
      if (this.onclose) this.onclose({ code: 1000, reason: "closed" });
    }
    receive(msg) {
      if (this.onmessage) this.onmessage({ data: JSON.stringify(msg) });
    }
  }
  FakeWebSocket.CONNECTING = 0;
  FakeWebSocket.OPEN = 1;
  FakeWebSocket.CLOSING = 2;
  FakeWebSocket.CLOSED = 3;
  return { FakeWebSocket, sockets };
}

function driveAuthOk(socket, version = "2026.6.0") {
  socket.receive({ type: "auth_required", ha_version: version });
  socket.readyState = 1;
  socket.receive({ type: "auth_ok", ha_version: version });
}

(async function main() {
  process.stdout.write("\nha_client_exports_test\n");
  const api = loadModule();
  assert("HAClient exported", typeof api.HAClient === "function");
  assert("haUrlToWs exported", typeof api.haUrlToWs === "function");
  assert("buildCallServicePayload exported", typeof api.buildCallServicePayload === "function");

  process.stdout.write("\nha_url_test\n");
  assert(
    "haUrlToWs maps http to ws /api/websocket",
    api.haUrlToWs("http://ha.local:8123") === "ws://ha.local:8123/api/websocket",
  );
  assert(
    "haUrlToWs maps https to wss and avoids duplicate suffix",
    api.haUrlToWs("https://ha.example/api/websocket") === "wss://ha.example/api/websocket",
  );

  process.stdout.write("\ncall_service_payload_test\n");
  const payload = api.buildCallServicePayload("input_boolean", "turn_on", {
    entity_id: "input_boolean.homeai_sleep",
    area_id: null,
    value: true,
    reason: "drawer",
  });
  assert("payload uses call_service type", payload.type === "call_service", payload);
  assert("payload preserves domain/service", payload.domain === "input_boolean" && payload.service === "turn_on", payload);
  assert("payload moves entity_id into target", payload.target.entity_id === "input_boolean.homeai_sleep", payload);
  assert("payload omits null target keys", !("area_id" in payload.target), payload);
  assert("payload keeps non-target data in service_data", payload.service_data.reason === "drawer", payload);
  assert("payload keeps boolean service data", payload.service_data.value === true, payload);
  const noTarget = api.buildCallServicePayload("input_text", "set_value", { value: "all" });
  assert("payload omits target when no target keys exist", !("target" in noTarget), noTarget);

  process.stdout.write("\nclient_call_service_test\n");
  const client = new api.HAClient();
  const sent = [];
  client.ws = {
    readyState: 1,
    send(raw) { sent.push(JSON.parse(raw)); },
  };
  const done = client.callService("input_number", "set_value", {
    entity_id: ["input_number.a", "input_number.b"],
    value: 30,
  });
  assert("callService sends one websocket message", sent.length === 1, sent);
  assert("callService assigns id", sent[0].id === 1, sent[0]);
  assert("callService sends call_service", sent[0].type === "call_service", sent[0]);
  assert("callService target supports entity arrays", Array.isArray(sent[0].target.entity_id), sent[0]);
  assert("callService keeps value in service_data", sent[0].service_data.value === 30, sent[0]);
  assert("callService marks call terminal on result", client._runs.get(1)._terminalOnResult === true);
  client._runs.get(1).onResult({ success: true, result: { ok: true } });
  const result = await done;
  assert("callService resolves result payload", result.ok === true, result);

  await expectReject(
    "callService rejects when websocket disconnected",
    new api.HAClient().callService("input_boolean", "turn_off", { entity_id: "input_boolean.homeai_sleep" }),
    /not connected/,
  );

  process.stdout.write("\nclient_reconnect_lifecycle_test\n");
  {
    const timers = makeFakeTimers();
    const fake = makeFakeWebSocket();
    const api2 = loadModule({
      WebSocket: fake.FakeWebSocket,
      setTimeout: timers.setTimeout,
      clearTimeout: timers.clearTimeout,
      setInterval: timers.setInterval,
      clearInterval: timers.clearInterval,
    });
    const reconnecting = new api2.HAClient();
    const states = [];
    reconnecting.onConnection((event) => states.push(event));

    const connected = reconnecting.connect("http://ha.local:8123", "tok");
    const first = fake.sockets[0];
    assert("connect opens HA websocket URL", first.url === "ws://ha.local:8123/api/websocket", first.url);
    first.receive({ type: "auth_required", ha_version: "2026.6.0" });
    assert("connect sends auth token", JSON.parse(first.sent[0]).access_token === "tok", first.sent);
    first.readyState = fake.FakeWebSocket.OPEN;
    first.receive({ type: "auth_ok", ha_version: "2026.6.0" });
    await connected;
    assert("auth_ok emits online", states.some((event) => event.state === "online"), states);

    states.length = 0;
    first.close();
    assert("network close emits offline", states.some((event) => event.state === "offline"), states);
    assert("network close emits reconnecting state", states.some((event) => event.state === "connecting" && event.reconnect === true), states);
    assert("network close schedules reconnect with 1s initial backoff",
      timers.timeouts.length === 1 && timers.timeouts[0].delay === 1000, timers.timeouts);
    timers.timeouts[0].fn();
    const second = fake.sockets[1];
    assert("reconnect opens a fresh websocket", second && second !== first, fake.sockets.length);
    driveAuthOk(second, "2026.6.1");
    assert("reconnect auth_ok emits online again",
      states[states.length - 1]?.state === "online", states);
    assert("successful reconnect resets attempt counter", reconnecting._reconnectAttempt === 0, reconnecting._reconnectAttempt);
    states.length = 0;
    first.onclose({ code: 1006, reason: "late stale close" });
    assert("late stale socket close is ignored after reconnect",
      states.every((event) => event.state !== "offline"), states);
  }

  process.stdout.write("\nclient_auth_invalid_close_test\n");
  {
    const timers = makeFakeTimers();
    const fake = makeFakeWebSocket();
    const api3 = loadModule({
      WebSocket: fake.FakeWebSocket,
      setTimeout: timers.setTimeout,
      clearTimeout: timers.clearTimeout,
      setInterval: timers.setInterval,
      clearInterval: timers.clearInterval,
    });
    const authClient = new api3.HAClient();
    const states = [];
    authClient.onConnection((event) => states.push(event));
    const rejected = authClient.connect("http://ha.local:8123", "bad");
    const socket = fake.sockets[0];
    socket.receive({ type: "auth_required", ha_version: "2026.6.0" });
    socket.readyState = fake.FakeWebSocket.OPEN;
    socket.receive({ type: "auth_invalid", message: "bad token" });
    await expectReject("auth_invalid rejects connect promise", rejected, /bad token/);
    assert("auth_invalid is not overwritten by close/offline",
      states[states.length - 1]?.state === "auth_invalid" &&
      states.every((event) => event.state !== "offline"),
      states);
    assert("auth_invalid does not schedule reconnect", timers.timeouts.length === 0, timers.timeouts);
  }

  process.stdout.write("\nclient_manual_disconnect_test\n");
  {
    const timers = makeFakeTimers();
    const fake = makeFakeWebSocket();
    const api4 = loadModule({
      WebSocket: fake.FakeWebSocket,
      setTimeout: timers.setTimeout,
      clearTimeout: timers.clearTimeout,
      setInterval: timers.setInterval,
      clearInterval: timers.clearInterval,
    });
    const manual = new api4.HAClient();
    const states = [];
    manual.onConnection((event) => states.push(event));
    const connected = manual.connect("http://ha.local:8123", "tok");
    driveAuthOk(fake.sockets[0]);
    await connected;
    states.length = 0;
    manual.disconnect();
    assert("manual disconnect clears active socket", manual.ws === null);
    assert("manual disconnect stays quiet instead of emitting offline",
      states.every((event) => event.state !== "offline"), states);
    assert("manual disconnect does not schedule reconnect", timers.timeouts.length === 0, timers.timeouts);
  }

  if (fails) {
    console.log("\nFailures:");
    for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
  }
  console.log(`\n${passes} pass . ${fails} fail`);
  process.exit(fails ? 1 : 0);
})().catch((err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
