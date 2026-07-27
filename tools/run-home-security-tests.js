"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const security = require("../app/src/home-security.js");

class MemoryStorage {
  constructor(seed = {}) { this.values = new Map(Object.entries(seed)); }
  get length() { return this.values.size; }
  key(index) { return [...this.values.keys()][index] ?? null; }
  getItem(key) { return this.values.has(key) ? this.values.get(key) : null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
}

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  process.stdout.write(`ok ${passed} - ${name}\n`);
}

test("sanitizes credential fields and credential-bearing URLs", () => {
  const safe = security.sanitizePrefs({
    endpoint: "https://user:password@example.test:8123/api?token=secret#fragment",
    token: "ha-secret",
    s2sToken: "bridge-secret",
    theme: "dark",
  });
  assert.equal(safe.endpoint, "https://example.test:8123/api");
  assert.equal(safe.theme, "dark");
  assert.equal("token" in safe, false);
  assert.equal("s2sToken" in safe, false);
  assert.deepEqual(security.sanitizePrefs({ theme: { token: "nested" }, model: { apiKey: "nested" } }), {});
});

test("sanitizes every persisted service URL store", () => {
  const storage = new MemoryStorage({
    "hg-services-v1": JSON.stringify({
      profile: "custom",
      custom: { ha: "https://user:pass@ha.test:8123/api?token=secret#fragment" },
      selected: { custom: { ha: "https://user:pass@ha.test" } },
      errors: [{ url: "https://user:pass@ha.test" }],
    }),
    "hg-intelligence-base": "https://u:p@intel.test/path?secret=yes#x",
    "videoLabeler.base": "javascript:alert(1)",
  });
  security.purgeLegacySensitiveStorage(storage);
  const services = JSON.parse(storage.getItem("hg-services-v1"));
  assert.deepEqual(services.custom, { ha: "https://ha.test:8123/api" });
  assert.deepEqual(services.selected, {});
  assert.deepEqual(services.errors, []);
  assert.equal(storage.getItem("hg-intelligence-base"), "https://intel.test/path");
  assert.equal(storage.getItem("videoLabeler.base"), null);
});

test("purges legacy secrets, conversations, and debug traces", () => {
  const storage = new MemoryStorage({
    "hg-stack-token-DEV": "stack-secret",
    "hg-external-token-DEV": "provider-secret",
    "hg-events": "private chat",
    "hg-conv-id": "conversation",
    "hg-lab-turns-v1": "trace",
    "hg-proactive-pending": "location",
    "hg-prefs": JSON.stringify({ token: "ha-secret", theme: "dark" }),
    "hg-external-enabled": "on",
  });
  const result = security.purgeLegacySensitiveStorage(storage);
  assert.equal(result.removed.length, 6);
  for (const key of security.PRIVATE_STORAGE_KEYS) assert.equal(storage.getItem(key), null);
  assert.deepEqual(JSON.parse(storage.getItem("hg-prefs")), { theme: "dark" });
  assert.equal(storage.getItem("hg-external-enabled"), "off");
  assert.equal(storage.getItem("hg-lab-persist"), "off");
  assert.equal(typeof security.purgeLegacySensitiveCaches, "function");
  assert.equal(security.CACHE_MIGRATION_KEY, "hg-security-cache-purge-v1");
  const source = fs.readFileSync(path.join(__dirname, "..", "app", "src", "home-security.js"), "utf8");
  assert.match(source, /cacheStorage\.keys\(\)/);
  assert.match(source, /cacheStorage\.delete\(name\)/);
});

test("purges legacy private spatial and media drafts", () => {
  const storage = new MemoryStorage({
    "apartment3d.modelDraft": JSON.stringify({ cameras: [{ url: "private" }] }),
    "apartment3d.remoteCache": JSON.stringify({ devices: ["private"] }),
    "videoLabeler.draft.private-video": JSON.stringify({ note: "private" }),
    "apartment3d.modePrewarm": "on",
  });
  const result = security.purgeLegacySensitiveStorage(storage);
  assert.equal(result.removed.length, 3);
  assert.equal(storage.getItem("apartment3d.modelDraft"), null);
  assert.equal(storage.getItem("apartment3d.remoteCache"), null);
  assert.equal(storage.getItem("videoLabeler.draft.private-video"), null);
  assert.equal(storage.getItem("apartment3d.modePrewarm"), "on");
});

test("malformed legacy preferences cannot bypass fail-closed flags", () => {
  const storage = new MemoryStorage({
    "hg-prefs": "{not-json",
    "hg-external-enabled": "on",
    "hg-lab-persist": "on",
  });
  security.purgeLegacySensitiveStorage(storage);
  assert.equal(storage.getItem("hg-prefs"), null);
  assert.equal(storage.getItem("hg-external-enabled"), "off");
  assert.equal(storage.getItem("hg-lab-persist"), "off");
});

test("application sources no longer write browser token keys", () => {
  const root = path.join(__dirname, "..", "app", "src");
  for (const file of ["home-app.jsx", "home-external.jsx", "home-tauri.jsx"]) {
    const source = fs.readFileSync(path.join(root, file), "utf8");
    assert.doesNotMatch(source, /localStorage\.setItem\(["']hg-(?:stack-token|external-token)/);
  }
  const app = fs.readFileSync(path.join(root, "home-app.jsx"), "utf8");
  assert.doesNotMatch(app, /savePrefs\(\{[^}]*\btoken\b/);
  assert.doesNotMatch(app, /savePrefs\(\{[^}]*\bs2sToken\b/);
  const proactive = fs.readFileSync(path.join(root, "home-proactive.jsx"), "utf8");
  assert.doesNotMatch(proactive, /localStorage\.setItem\(PROACTIVE_PENDING_KEY/);

  const tauriRoot = path.join(__dirname, "..", "app", "src-tauri");
  const cargo = fs.readFileSync(path.join(tauriRoot, "Cargo.toml"), "utf8");
  const capability = fs.readFileSync(path.join(tauriRoot, "capabilities", "default.json"), "utf8");
  assert.doesNotMatch(cargo, /devtools/);
  assert.doesNotMatch(capability, /toggle-devtools/);
});

test("legacy Intelligence deployment is quarantined by default", () => {
  const compose = fs.readFileSync(path.join(__dirname, "..", "stack", "docker-compose.yml"), "utf8");
  assert.match(compose, /127\.0\.0\.1:\$\{INTELLIGENCE_PORT:-8095\}:8094/);
  const intelligence = compose.split("  intelligence:", 2)[1].split("\n  # =========================================================", 1)[0];
  const locked = new Map([
    ["INTELLIGENCE_MEMORY_ENABLED", "0"],
    ["INTELLIGENCE_SCHEDULER_ENABLED", "0"],
    ["INTELLIGENCE_READ_ONLY", "1"],
    ["INTELLIGENCE_MULTIMODAL_ENABLED", "0"],
    ["INTELLIGENCE_MULTIMODAL_RING_ENABLED", "0"],
    ["INTELLIGENCE_MULTIMODAL_PILOT_ENABLED", "0"],
    ["INTELLIGENCE_MULTIMODAL_PILOT_AUTO_LABEL", "0"],
  ]);
  for (const [name, value] of locked) {
    assert.match(intelligence, new RegExp(`${name}: "${value}"`));
    assert.doesNotMatch(intelligence, new RegExp(`${name}: \\$\\{`));
  }
  assert.match(intelligence, /read_only: true/);
  assert.match(intelligence, /cap_drop: \[ALL\]/);
  assert.match(intelligence, /no-new-privileges:true/);
  assert.match(intelligence, /intelligence-data\}:\/data:ro/);
  assert.match(intelligence, /networks: \[intelligence-quarantine\]/);
  assert.doesNotMatch(intelligence, /^\s+HA_TOKEN:/m);
  assert.doesNotMatch(intelligence, /INTELLIGENCE_HA_TOKEN/);
  assert.doesNotMatch(intelligence, /\.\.\/ha-config:\/config/);
  assert.match(compose, /VISION_SIDECAR_BIND_ADDR:-127\.0\.0\.1/);
  assert.match(compose, /MODEL_EDGE_BIND_ADDR:-127\.0\.0\.1/);
  assert.match(compose, /METRICS_SIDECAR_BIND_ADDR:-127\.0\.0\.1/);
  assert.match(compose, /S2S_BRIDGE_BIND_ADDR:-127\.0\.0\.1/);

  const deploy = fs.readFileSync(path.join(__dirname, "deploy-intelligence.ps1"), "utf8");
  assert.match(deploy, /127\.0\.0\.1:8095:8094/);
  for (const [name, value] of locked) {
    assert.match(deploy, new RegExp(`${name}: "${value}"`));
    assert.doesNotMatch(deploy, new RegExp(name + ': `\\$\\{'));
  }
  assert.match(deploy, /networks: \[intelligence-quarantine\]/);
  assert.doesNotMatch(deploy, /INTELLIGENCE_HA_TOKEN:/);
  assert.doesNotMatch(deploy, /^\s+HA_TOKEN:/m);
});

test("model-adjacent legacy voice bridge has no HA action credential", () => {
  const compose = fs.readFileSync(path.join(__dirname, "..", "stack", "docker-compose.yml"), "utf8");
  const bridge = compose.split("  personaplex-bridge:", 2)[1]
    .split("\n  # =========================================================", 1)[0];
  assert.match(bridge, /S2S_HA_DISPATCH_ENABLED: "false"/);
  assert.match(bridge, /CHATTEE_URL: ""/);
  assert.doesNotMatch(bridge, /CHATTEE_URL: \$\{/);
  assert.doesNotMatch(bridge, /^\s+HA_TOKEN:/m);
  assert.doesNotMatch(bridge, /^\s+HA_URL:/m);
  assert.doesNotMatch(bridge, /^\s+DIRECT_DISPATCH:/m);
});

test("untrusted legacy vision adapter has no HA bearer", () => {
  const root = path.join(__dirname, "..");
  const compose = fs.readFileSync(path.join(root, "stack", "docker-compose.yml"), "utf8");
  const vision = compose.split("  vision-sidecar:", 2)[1]
    .split("\n  # =========================================================", 1)[0];
  const source = fs.readFileSync(path.join(root, "stack", "services", "vision", "app.py"), "utf8");
  assert.match(vision, /^\s+HA_TOKEN: ""/m);
  assert.doesNotMatch(vision, /\$\{HA_TOKEN/);
  assert.match(source, /HA_TOKEN = os\.environ\.get\("HA_TOKEN", ""\)/);
});

test("spatial research adapter is loopback-only and receives no shared HA token", () => {
  const root = path.join(__dirname, "..");
  const snippet = fs.readFileSync(path.join(root, "stack", "services", "spatial-tracker", "compose-snippet.yml"), "utf8");
  const deploy = fs.readFileSync(path.join(root, "tools", "deploy-spatial-tracker.ps1"), "utf8");
  assert.match(snippet, /127\.0\.0\.1:8098:8098/);
  assert.match(snippet, /- HA_TOKEN=/);
  assert.doesNotMatch(snippet, /HA_TOKEN=\$\{/);
  assert.match(deploy, /-p 127\.0\.0\.1:8098:8098/);
  assert.match(deploy, /--read-only --cap-drop ALL --security-opt no-new-privileges/);
  assert.doesNotMatch(deploy, /--env-file \/opt\/home-ai-voice\/\.env/);
});

test("legacy Intelligence mutation paths are disabled across both boundaries", () => {
  const root = path.join(__dirname, "..");
  const scheduler = fs.readFileSync(path.join(root, "stack", "services", "intelligence", "app", "scheduler.py"), "utf8");
  const service = fs.readFileSync(path.join(root, "stack", "services", "intelligence", "app", "main.py"), "utf8");
  const gateway = fs.readFileSync(path.join(root, "web-gateway", "server.mjs"), "utf8");
  assert.match(scheduler, /INTELLIGENCE_MEMORY_ENABLED", False/);
  assert.match(service, /INTELLIGENCE_READ_ONLY[\s\S]*"1"/);
  assert.match(service, /request\.method in \{"POST", "PUT", "PATCH", "DELETE"\}/);
  assert.match(gateway, /HOME_WEB_ENABLE_LEGACY_INTELLIGENCE_PROXY/);
  assert.match(gateway, /route\.enabled !== false/);
});

test("legacy web gateway fails closed unless local development opts out", () => {
  const root = path.join(__dirname, "..");
  const gateway = fs.readFileSync(path.join(root, "web-gateway", "server.mjs"), "utf8");
  const linuxInstaller = fs.readFileSync(path.join(root, "tools", "install-home-web-gateway-linux.sh"), "utf8");
  const windowsLauncher = fs.readFileSync(path.join(root, "tools", "start-home-web-gateway.ps1"), "utf8");
  assert.match(gateway, /HOME_WEB_AUTH_REQUIRED \|\| "1"/);
  assert.match(gateway, /ok: !AUTH_REQUIRED/);
  assert.match(gateway, /authentication_unconfigured/);
  assert.match(gateway, /HttpOnly; Secure; SameSite=Lax/);
  assert.match(linuxInstaller, /HOME_WEB_AUTH_REQUIRED:-1/);
  assert.doesNotMatch(linuxInstaller, /HOME_WEB_AUTH_REQUIRED:-0/);
  assert.match(linuxInstaller, /HOME_WEB_ENABLE_LEGACY_HA_PROXY:-0/);
  assert.match(linuxInstaller, /HOME_WEB_ENABLE_LEGACY_VISION_PROXY:-0/);
  assert.match(windowsLauncher, /HOME_WEB_AUTH_REQUIRED = "1"/);
  assert.doesNotMatch(windowsLauncher, /HOME_WEB_AUTH_REQUIRED = "0"/);
  assert.match(windowsLauncher, /HOME_WEB_ENABLE_LEGACY_VISION_PROXY = "0"/);
});

test("generic legacy sensor, model, media, and admin proxies are opt-in", () => {
  const gateway = fs.readFileSync(path.join(__dirname, "..", "web-gateway", "server.mjs"), "utf8");
  for (const name of [
    "HA",
    "VLLM",
    "VISION",
    "SUPERVISOR",
    "BRIDGE",
    "TRACKER",
    "VIDEO_LABELER",
    "FRIGATE",
  ]) {
    assert.match(gateway, new RegExp(`HOME_WEB_ENABLE_LEGACY_${name}_PROXY`));
  }
});

test("Video Labeler is loopback-only and bearer-protected", () => {
  const root = path.join(__dirname, "..");
  const main = fs.readFileSync(path.join(root, "stack", "services", "video-labeler", "main.py"), "utf8");
  const config = fs.readFileSync(path.join(root, "stack", "services", "video-labeler", "videolabeler", "config.py"), "utf8");
  const dockerfile = fs.readFileSync(path.join(root, "stack", "services", "video-labeler", "Dockerfile"), "utf8");
  const deploy = fs.readFileSync(path.join(root, "tools", "deploy-video-labeler.ps1"), "utf8");
  assert.match(main, /require_api_bearer/);
  assert.match(main, /bearer_is_valid/);
  assert.match(config, /BIND_HOST", "127\.0\.0\.1"/);
  assert.match(config, /BIND_HOST must be loopback/);
  assert.match(dockerfile, /--host \\"\$host\\"/);
  assert.doesNotMatch(dockerfile, /--host 0\.0\.0\.0/);
  assert.match(deploy, /VIDEO_LABELER_API_TOKEN_FILE=\/run\/secrets\/video_labeler_token/);
  assert.match(deploy, /--cap-drop ALL/);
  assert.match(deploy, /--read-only/);
  assert.match(deploy, /--env-file \/etc\/home-ai-voice\/video-labeler\.env/);
  assert.doesNotMatch(deploy, /-v \/var\/run\/docker\.sock/);
});

test("contentful metrics conversation tracing is absent and cannot be env-enabled", () => {
  const compose = fs.readFileSync(path.join(__dirname, "..", "stack", "docker-compose.yml"), "utf8");
  const source = fs.readFileSync(
    path.join(__dirname, "..", "stack", "services", "metrics-sidecar", "main.py"),
    "utf8",
  );
  const metrics = compose.split("  metrics-sidecar:", 2)[1]
    .split("\n  # =========================================================", 1)[0];
  assert.match(metrics, /METRICS_CONVERSATION_CONTENT_ENABLED: "0"/);
  assert.doesNotMatch(metrics, /\$\{METRICS_CONVERSATION_CONTENT_ENABLED/);
  assert.doesNotMatch(metrics, /TRACES_DIR/);
  assert.doesNotMatch(metrics, /metrics-sidecar\/data:\/app\/data/);
  assert.match(source, /CONVERSATION_CONTENT_ENABLED = False/);
  assert.doesNotMatch(source, /os\.environ\.get\("METRICS_CONVERSATION_CONTENT_ENABLED"/);
  assert.doesNotMatch(source, /_recent_completions|_broadcast_completion|full_content/);
  assert.match(source, /async for chunk in r\.aiter_raw\(\):\s+yield chunk/);
  assert.match(source, /@app\.get\("\/conversations\/recent"\)[\s\S]*?"capability_disabled"[\s\S]*?status_code=404/);
  assert.match(source, /@app\.get\("\/conversations\/stream"\)[\s\S]*?"capability_disabled"[\s\S]*?status_code=404/);
});

test("legacy model tools and automatic private context are locked off", () => {
  const root = path.join(__dirname, "..");
  const constants = fs.readFileSync(
    path.join(root, "ha-config", "extended_openai_conversation", "const.py"),
    "utf8",
  );
  const conversation = fs.readFileSync(
    path.join(root, "ha-config", "extended_openai_conversation", "conversation.py"),
    "utf8",
  );
  assert.match(constants, /MODEL_TOOL_CATALOG_ENABLED = False/);
  assert.match(constants, /MODEL_PRIVATE_CONTEXT_ENABLED = False/);
  assert.match(constants, /EXTERNAL_REASONING_ROUTING_ENABLED = False/);
  assert.match(constants, /if MODEL_TOOL_CATALOG_ENABLED[\s\S]*else \[\]/);
  assert.match(conversation, /if not MODEL_TOOL_CATALOG_ENABLED:[\s\S]*return \[\]/);
  assert.match(conversation, /if not MODEL_PRIVATE_CONTEXT_ENABLED:[\s\S]*return \[\]/);
  assert.match(conversation, /if not EXTERNAL_REASONING_ROUTING_ENABLED:/);
  assert.doesNotMatch(conversation, /"user_input_text":/);
  assert.doesNotMatch(conversation, /"assistant_text":/);
  assert.doesNotMatch(conversation, /_raw_text\[:/);

  const integration = fs.readFileSync(
    path.join(root, "ha-config", "extended_openai_conversation", "__init__.py"),
    "utf8",
  );
  const externalRouting = fs.readFileSync(
    path.join(root, "ha-config", "extended_openai_conversation", "external_routing.py"),
    "utf8",
  );
  assert.match(externalRouting, /def _log_mode\(\)[\s\S]*return "off"/);
  assert.match(integration, /_LEGACY_SENSITIVE_LOG_NAMES = \("asr_debug\.log", "external_routing\.log"\)/);
  assert.match(integration, /user = request\.get\("hass_user"\)[\s\S]*user, "is_admin", False/);
  assert.match(integration, /for _view_class in _LEGACY_PRIVATE_VIEW_CLASSES:/);
  assert.match(integration, /Legacy sensitive log purge failed closed/);

  const identityStore = fs.readFileSync(
    path.join(root, "ha-config", "extended_openai_conversation", "identity_store.py"),
    "utf8",
  );
  const legacyFence = fs.readFileSync(
    path.join(root, "ha-config", "extended_openai_conversation", "legacy_identity_fence.py"),
    "utf8",
  );
  assert.match(identityStore, /scrub_legacy_audit_content\(self\._conn\)/);
  assert.match(
    legacyFence,
    /UPDATE change_log SET ts = \?, kind = \?, actor = \?,[\s\S]*target_uuid = NULL, before_json = NULL, after_json = \?[\s\S]*link_conv_id = NULL[\s\S]*pinned = 0/,
  );
  assert.match(legacyFence, /SCRUBBED_AUDIT_ACTOR = "legacy_scrubbed"/);
  assert.match(identityStore, /json\.dumps\(\{"operation_code": operation_code\}\)/);
  assert.doesNotMatch(identityStore, /json\.dumps\(before\) if before is not None/);
});

process.stdout.write(`# ${passed} security containment tests passed\n`);
