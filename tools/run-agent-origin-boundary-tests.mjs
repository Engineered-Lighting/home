#!/usr/bin/env node
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { classifyAgentOrigin, parseAgentOriginBoundary } from "../web-gateway/agent-origin.mjs";

assert.equal(parseAgentOriginBoundary("").valid, false);
assert.equal(parseAgentOriginBoundary("http://agent.home.test").valid, false);
assert.equal(parseAgentOriginBoundary("https://agent.home.test/").valid, false);
assert.equal(parseAgentOriginBoundary("https://agent.home.test/path").valid, false);
assert.equal(parseAgentOriginBoundary("https://user:pass@agent.home.test").valid, false);
assert.equal(parseAgentOriginBoundary("https://agent.home.test?x=1").valid, false);
assert.equal(parseAgentOriginBoundary("https://agent.home.test#fragment").valid, false);
assert.equal(parseAgentOriginBoundary("https://agent.home.test,https://agent.home.test").valid, false);
assert.equal(parseAgentOriginBoundary("http://agent.home.test", { allowInsecure: true }).valid, true);

const boundary = parseAgentOriginBoundary(
  "https://agent.home.test,https://agent2.home.test:8443",
);
assert.equal(boundary.valid, true);
assert.deepEqual(boundary.origins, [
  "https://agent.home.test",
  "https://agent2.home.test:8443",
]);
assert.deepEqual(classifyAgentOrigin({ host: "agent.home.test" }, boundary), {
  agentHost: true,
  originAllowed: true,
  expectedOrigin: "https://agent.home.test",
});
assert.equal(classifyAgentOrigin({
  host: "agent.home.test",
  origin: "https://legacy.home.test",
}, boundary).originAllowed, false);
assert.equal(classifyAgentOrigin({
  host: "legacy.home.test",
  origin: "https://agent.home.test",
}, boundary).agentHost, false);

const gateway = fileURLToPath(new URL("../web-gateway/server.mjs", import.meta.url));
function checkExit(origin, extra = {}) {
  const env = { ...process.env, ...extra };
  if (origin === undefined) delete env.HOME_WEB_AGENT_ORIGINS;
  else env.HOME_WEB_AGENT_ORIGINS = origin;
  return spawnSync(process.execPath, [gateway, "--check"], {
    env,
    encoding: "utf8",
  }).status;
}

assert.equal(checkExit(undefined), 1);
assert.equal(checkExit("https://agent.home.test"), 0);
assert.equal(checkExit("http://agent.home.test", {
  HOME_WEB_ALLOW_INSECURE_TEST_AGENT_ORIGINS: "1",
  NODE_ENV: "production",
}), 1);
assert.equal(checkExit("http://agent.home.test", {
  HOME_WEB_ALLOW_INSECURE_TEST_AGENT_ORIGINS: "1",
  NODE_ENV: "test",
}), 0);

process.stdout.write("agent_origin_boundary_unit_test: 18 pass · 0 fail\n");
