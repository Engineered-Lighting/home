/* Home — External Reasoning Provider.
 *
 * Phase A of the External Reasoning Fallback feature. See plan at
 * C:\Users\Marcelo\.claude\plans\keen-doodling-parasol.md
 *
 * What this file owns:
 *   1. classifyIntent(text) — rules-based router (default: "local").
 *   2. openaiProvider — OpenAI Chat Completions stream() impl.
 *   3. askExternal(text, callbacks, signal) — the ONLY function the
 *      chat dispatch should call to fire an external request. Bare
 *      prompt; no home context EVER reaches this surface.
 *   4. ExternalKeyModal — popup for /external set-key (so the user
 *      doesn't have to DevTools-paste).
 *   5. window.__EXTERNAL_STATS — rolling stats buffer for the metrics
 *      drawer footer.
 *   6. window.__EXTERNAL_LAST_REQUEST — captured outgoing body for the
 *      /test external-privacy assertions.
 *   7. runExternalSuite(addEvent) — programmatic test runner so the
 *      user can verify the whole feature with one slash command.
 *   8. SIM-mode guard — window.__SIM_ACTIVE === true skips real fetch.
 *
 * Trust posture:
 *   - askExternal() takes ONLY a text string + callbacks. It has no
 *     way to access entity IDs, room context, perception, HA tokens,
 *     conversation history, or anything else. Privacy is enforced by
 *     constructor, not by sanitization.
 *   - The Authorization header lives only in the fetch options
 *     closure. Error paths log r.status, never headers or body.
 *   - OpenAI's [DONE] terminator is explicitly recognized. Other
 *     providers can implement their own terminator.
 */

const HG_FONT_MONO_EXT = "'Geist Mono', ui-monospace, monospace";

// ─────────────────────────────────────────────────────────────────────
// Default bare system prompt — explicitly frames the assistant as
// general-knowledge only, with no access to the home.
// ─────────────────────────────────────────────────────────────────────
const DEFAULT_SYSTEM_PROMPT =
  "You are a helpful general-knowledge assistant. The user is asking " +
  "from a smart-home interface but you do NOT have access to their home, " +
  "devices, cameras, or any private state. Answer in 2-4 sentences of " +
  "plain prose: no markdown, no bullets, no headings, no code fences. If asked " +
  "to perform a home action (turn on lights, play music, etc.) say you " +
  "can answer questions but cannot control the user's home — they should " +
  "ask their home assistant directly.";


// ─────────────────────────────────────────────────────────────────────
// Stats ring buffer (in-memory, last 20 calls).
// ─────────────────────────────────────────────────────────────────────
const STATS_MAX = 20;
const _statsBuf = [];

function recordStat(s) {
  _statsBuf.push({ ...s, ts: Date.now() });
  if (_statsBuf.length > STATS_MAX) {
    _statsBuf.splice(0, _statsBuf.length - STATS_MAX);
  }
  window.__EXTERNAL_STATS = _statsBuf;
}
window.__EXTERNAL_STATS = _statsBuf;

function getExternalProvider() {
  return window.__EXTERNAL_PROVIDER || openaiProvider;
}

function isExternalConfigured(provider) {
  const active = provider || getExternalProvider();
  try {
    return !!(active && typeof active.isConfigured === "function" && active.isConfigured());
  } catch {
    return false;
  }
}

function formatExternalStatus(opts = {}) {
  const provider = opts.provider || getExternalProvider();
  const configured = opts.configured != null ? !!opts.configured : isExternalConfigured(provider);
  const enabled = !!opts.enabled;
  const stats = Array.isArray(opts.stats) ? opts.stats : (window.__EXTERNAL_STATS || []);
  const now = Number.isFinite(Number(opts.now)) ? Number(opts.now) : Date.now();
  const recent = stats.slice(-1)[0];
  const providerLabel = provider?.name || provider?.id || "(none)";
  const lines = [
    `external · provider: ${providerLabel}`,
    `  configured: ${configured ? "yes" : "no"} · auto-routing: ${enabled ? "on" : "off"}`,
    `  calls tracked: ${stats.length}`,
  ];
  if (recent) {
    const status = recent.ok ? "ok" : (recent.error || "fail");
    const ts = Number(recent.ts);
    const age = Number.isFinite(ts) ? Math.max(0, Math.round((now - ts) / 1000)) : null;
    const when = Number.isFinite(ts) ? new Date(ts).toLocaleTimeString() : "unknown time";
    const latency = Number.isFinite(Number(recent.latencyMs)) ? `${Math.round(Number(recent.latencyMs))}ms` : "?ms";
    const inTok = recent.inputTokens ?? recent.promptTokens ?? "?";
    const outTok = recent.outputTokens ?? recent.completionTokens ?? "?";
    lines.push(
      `  last call: ${status} · ${when}` +
      (age != null ? ` · age ${age}s` : "") +
      ` · latency ${latency} · tokens in ${inTok} / out ${outTok}`
    );
  }
  return lines.join("\n");
}


// ─────────────────────────────────────────────────────────────────────
// Intent classifier — rules-based, biased toward "local".
// ─────────────────────────────────────────────────────────────────────
const _HOME_VERBS = /\b(turn (on|off)|dim|brighten|warmer|cooler|set the|pause|resume|play|skip|next|previous|mute|unmute|volume|louder|quieter|lock|unlock|open|close)\b/;
const _HOME_NOUNS = /\b(light|lights|lamp|kitchen|living room|office|dining|workshop|bedroom|hallway|driveway|garage|sonos|spotify|stereo|speakers?|tv|movie mode|frigate|camera|home assistant|home-?app|haos|metrics|gpu|vram|vllm|stack|ai stack|presence|the room|in here|in the room)\b/;
const _HOME_QUERIES = /\b(what do you see|who('s| is) home|who('s| is) in the|is anyone (home|in the|here)|am i home|are we home)\b/;
const _GENERAL = /\b(explain|what (is|are|does|year|day|century)|how (do|does) .* work|difference between|compare|teach me|tell me (about|a)|describe|define|who (is|was|invented|wrote|made|created|discovered|founded|painted)|when (is|was|did|were|will)|where (is|was|did|are)|which (is|are|was|of)|list (the|some|all)|name (the|a|some)|(tldr|tl;?dr|summary|overview) (on|of|for|about)|why (is|are|does) .* (faster|slower|better|worse)|help me (write|understand|think|brainstorm)|give me ideas|code review)\b/;

/**
 * classifyIntent(text) → "local" | "external" | "ambiguous"
 *
 * "ambiguous" means no rule matched cleanly; the dispatcher will treat
 * it as "local" (safer default) but a /route command surfaces this so
 * the user can audit.
 */
function classifyIntent(text) {
  const s = (text || "").trim().toLowerCase();
  if (!s) return "ambiguous";
  if (_HOME_VERBS.test(s))   return "local";
  if (_HOME_NOUNS.test(s))   return "local";
  if (_HOME_QUERIES.test(s)) return "local";
  if (_GENERAL.test(s))      return "external";
  return "ambiguous";
}

/** Return a short string describing WHICH rule matched, for /route. */
function explainIntent(text) {
  const s = (text || "").trim().toLowerCase();
  if (!s) return "ambiguous — empty input";
  if (_HOME_VERBS.test(s))   return "local — matched HOME_VERBS";
  if (_HOME_NOUNS.test(s))   return "local — matched HOME_NOUNS";
  if (_HOME_QUERIES.test(s)) return "local — matched HOME_QUERIES";
  if (_GENERAL.test(s))      return "external — matched GENERAL";
  return "ambiguous — no rule matched; defaulting to local";
}


// ─────────────────────────────────────────────────────────────────────
// SIM-mode canned responses (no real network).
// ─────────────────────────────────────────────────────────────────────
const _SIM_CANNED_DEFAULT =
  "This is a simulated external answer. In a real call, this response " +
  "would come from the configured provider (OpenAI gpt-4o-mini by default). " +
  "It would arrive as streamed chunks. The simulation skips the network " +
  "entirely so no real API call is made and no key is required.";


async function _simulateExternal(text, callbacks) {
  // Capture would-be body for /test external-privacy assertions.
  window.__EXTERNAL_LAST_REQUEST = {
    model: "gpt-4o-mini",
    messages: [
      { role: "system", content: DEFAULT_SYSTEM_PROMPT },
      { role: "user", content: text },
    ],
    stream: true,
    max_tokens: 800,
    temperature: 0.5,
    _sim: true,
  };

  const canned = (window.__SIM_EXTERNAL_RESPONSE || _SIM_CANNED_DEFAULT);
  const t0 = Date.now();

  // Stream by chunks of ~6 words every 80ms — feels like a real stream.
  const words = canned.split(/\s+/);
  let acc = "";
  for (let i = 0; i < words.length; i += 6) {
    const chunk = words.slice(i, i + 6).join(" ") + (i + 6 < words.length ? " " : "");
    acc += chunk;
    if (callbacks && callbacks.onChunk) callbacks.onChunk({ delta: chunk });
    await new Promise((r) => setTimeout(r, 80));
  }
  const result = { text: acc, latencyMs: Date.now() - t0, sim: true };
  recordStat({ provider: "sim", ok: true, latencyMs: result.latencyMs });
  if (callbacks && callbacks.onDone) callbacks.onDone(result);
  return result;
}


// ─────────────────────────────────────────────────────────────────────
// OpenAI provider.
// ─────────────────────────────────────────────────────────────────────
const openaiProvider = {
  id: "openai",
  name: "OpenAI gpt-4o-mini",
  model: "gpt-4o-mini",

  isConfigured() {
    return false;
  },

  _getKey() {
    return "";
  },

  /**
   * Stream a chat completion. Resolves with { text, latencyMs, usage? }.
   * Calls callbacks.onChunk({delta}) per content chunk.
   * Rejects on auth fail / network / timeout / abort.
   *
   * IMPLEMENTATION NOTE: writes its own SSE parser inline (rather than
   * using streamSse from home-sse-fetch.js) because streamSse is
   * GET-only. OpenAI requires POST + JSON body. ~40 lines.
   */
  async stream(req, signal, callbacks) {
    const key = this._getKey();
    if (!key) {
      const e = new Error("not_configured");
      e.code = "not_configured";
      throw e;
    }

    const bodyObj = {
      model: req.model || this.model,
      messages: [
        { role: "system", content: req.systemPrompt || DEFAULT_SYSTEM_PROMPT },
        { role: "user", content: req.prompt },
      ],
      stream: true,
      max_tokens: req.maxTokens || 800,
      temperature: req.temperature ?? 0.5,
    };
    const body = JSON.stringify(bodyObj);

    // Capture for /test external-privacy. Deep-clone to avoid leaking
    // the reference (caller shouldn't mutate the captured payload).
    window.__EXTERNAL_LAST_REQUEST = JSON.parse(body);

    const t0 = Date.now();
    let response;
    try {
      response = await fetch("https://api.openai.com/v1/chat/completions", {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + key,  // never logged
          "Content-Type": "application/json",
        },
        body,
        signal,
        cache: "no-store",
      });
    } catch (e) {
      if (signal && signal.aborted) {
        const ae = new Error("aborted"); ae.code = "aborted"; throw ae;
      }
      console.warn("[external] fetch threw:", e && e.name);
      const ne = new Error("unreachable"); ne.code = "unreachable"; throw ne;
    }

    if (!response.ok) {
      // Don't log headers or body — auth-hygiene rule.
      console.warn("[external] http " + response.status);
      const e = new Error("http_" + response.status);
      e.code = response.status === 401 ? "auth_failed"
             : response.status === 429 ? "rate_limited"
             : response.status >= 500  ? "transient"
             :                            "http_error";
      e.status = response.status;
      // Best-effort retry-after for 429.
      const retryAfter = response.headers.get("Retry-After");
      if (retryAfter) e.retryAfterS = parseInt(retryAfter, 10);
      throw e;
    }
    if (!response.body) {
      const e = new Error("no_body"); e.code = "no_body"; throw e;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let text = "";
    let usage = null;
    let done = false;

    while (!done) {
      let chunk;
      try {
        chunk = await reader.read();
      } catch (e) {
        if (signal && signal.aborted) {
          const ae = new Error("aborted"); ae.code = "aborted"; throw ae;
        }
        throw e;
      }
      if (chunk.done) break;
      buf += decoder.decode(chunk.value, { stream: true });
      // SSE frames are separated by \n\n.
      let sep;
      while ((sep = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        if (!frame || frame.startsWith(":")) continue;  // heartbeat
        // Each frame has "data: <payload>" lines.
        for (const ln of frame.split("\n")) {
          if (!ln.startsWith("data:")) continue;
          const v = ln.slice(5).replace(/^ /, "");
          // OpenAI's terminator.
          if (v === "[DONE]") { done = true; break; }
          let parsed;
          try { parsed = JSON.parse(v); } catch { continue; }
          // Delta content chunk.
          const delta = parsed?.choices?.[0]?.delta?.content;
          if (typeof delta === "string" && delta.length > 0) {
            text += delta;
            if (callbacks && callbacks.onChunk) {
              callbacks.onChunk({ delta });
            }
          }
          // Usage (some streams report it in the last frame).
          if (parsed?.usage) usage = parsed.usage;
        }
        if (done) break;
      }
    }

    const result = {
      text,
      usage,
      latencyMs: Date.now() - t0,
      provider: "openai",
      model: bodyObj.model,
    };
    recordStat({
      provider: "openai",
      ok: true,
      latencyMs: result.latencyMs,
      outputTokens: usage?.completion_tokens,
      inputTokens: usage?.prompt_tokens,
    });
    if (callbacks && callbacks.onDone) callbacks.onDone(result);
    return result;
  },
};


// ─────────────────────────────────────────────────────────────────────
// askExternal — the only export the chat dispatch should call.
// ─────────────────────────────────────────────────────────────────────
async function askExternal(text, callbacks, signal) {
  // SIM-mode guard. Identical posture to tauriFetch — never let a real
  // API call escape during simulation playback.
  if (typeof window !== "undefined" && window.__SIM_ACTIVE === true) {
    return _simulateExternal(text, callbacks || {});
  }

  const provider = getExternalProvider();
  if (!isExternalConfigured(provider)) {
    const e = new Error("not_configured");
    e.code = "not_configured";
    recordStat({ provider: provider?.id || "unknown", ok: false, error: "not_configured" });
    if (callbacks && callbacks.onError) callbacks.onError(e);
    throw e;
  }

  try {
    const statsBefore = _statsBuf.length;
    const result = await provider.stream({ prompt: text }, signal, callbacks);
    if (_statsBuf.length === statsBefore) {
      recordStat({
        provider: provider?.id || "unknown",
        ok: true,
        latencyMs: result?.latencyMs,
        inputTokens: result?.usage?.prompt_tokens ?? result?.inputTokens,
        outputTokens: result?.usage?.completion_tokens ?? result?.outputTokens,
      });
    }
    return result;
  } catch (err) {
    recordStat({
      provider: provider?.id || "unknown",
      ok: false,
      error: err && err.code ? err.code : "unknown",
    });
    if (callbacks && callbacks.onError) callbacks.onError(err);
    throw err;
  }
}


// ─────────────────────────────────────────────────────────────────────
// ExternalKeyModal — modal that lets the user paste the API key into
// a normal <input> field (no DevTools paste-warning).
// ─────────────────────────────────────────────────────────────────────
function ExternalKeyModal({ onClose }) {
  const [value, setValue] = React.useState("");
  const [showing, setShowing] = React.useState(false);
  const existing = "";
  const masked = existing
    ? `${existing.slice(0, 3)}...${existing.slice(-4)} (${existing.length} chars)`
    : "(not set)";

  const doSave = React.useCallback(() => {
    void value;
    onClose && onClose({ blocked: "secure-bff-required" });
  }, [value, onClose]);

  const doClear = React.useCallback(() => {
    try { localStorage.removeItem("hg-external-token-DEV"); }
    catch (e) { console.warn("[external] clear failed:", e && e.name); }
    onClose && onClose({ cleared: true });
  }, [onClose]);

  React.useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose && onClose({});
      else if (e.key === "Enter" && value.trim()) doSave();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [value, onClose, doSave]);

  return (
    <div
      onClick={() => onClose && onClose({})}
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0,0,0,0.55)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 9999,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          minWidth: 380, maxWidth: 440,
          background: "var(--hg-bg-0, #0c0c0c)",
          border: "1px solid var(--hg-ice-bright)",
          padding: 18,
          fontFamily: HG_FONT_MONO_EXT,
          color: "var(--hg-fg-1)",
        }}
      >
        <div style={{
          fontSize: 11.5, letterSpacing: "0.18em",
          textTransform: "uppercase", color: "var(--hg-ice-bright)",
          marginBottom: 10,
        }}>set external provider key</div>

        <div style={{
          fontSize: 10, color: "var(--hg-fg-3)",
          lineHeight: 1.55, marginBottom: 10,
        }}>
          OpenAI API key. Stored in this workstation's localStorage only.
          Never sent to the local stack, Home Assistant, or git.
        </div>

        <div style={{
          fontSize: 10.5, color: "var(--hg-fg-3)",
          marginBottom: 8, letterSpacing: "0.08em",
        }}>
          current: <span style={{ color: existing ? "var(--hg-fg-2)" : "var(--hg-warn)" }}>{masked}</span>
        </div>

        <input
          type={showing ? "text" : "password"}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="sk-..."
          autoFocus
          style={{
            width: "100%",
            background: "var(--hg-bg-1, rgba(255,255,255,0.04))",
            border: "1px solid var(--hg-border, #333)",
            color: "var(--hg-fg-1)",
            padding: "6px 8px",
            fontFamily: HG_FONT_MONO_EXT,
            fontSize: 11,
            marginBottom: 10,
            boxSizing: "border-box",
          }}
        />

        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span
            onClick={() => setShowing((s) => !s)}
            style={{
              fontSize: 10.5, color: "var(--hg-fg-3)",
              cursor: "pointer", letterSpacing: "0.08em",
              textTransform: "uppercase",
            }}
          >[{showing ? "hide" : "show"}]</span>
          <span style={{ flex: 1 }} />
          {existing && (
            <button
              onClick={doClear}
              style={{
                fontFamily: HG_FONT_MONO_EXT, fontSize: 10.5,
                letterSpacing: "0.12em", textTransform: "uppercase",
                color: "var(--hg-crit)", background: "transparent",
                border: "1px solid var(--hg-crit)",
                padding: "4px 10px", cursor: "pointer",
              }}
            >clear</button>
          )}
          <button
            onClick={() => onClose && onClose({})}
            style={{
              fontFamily: HG_FONT_MONO_EXT, fontSize: 10.5,
              letterSpacing: "0.12em", textTransform: "uppercase",
              color: "var(--hg-fg-3)", background: "transparent",
              border: "1px solid var(--hg-fg-5)",
              padding: "4px 10px", cursor: "pointer",
            }}
          >cancel</button>
          <button
            onClick={doSave}
            disabled={!value.trim()}
            style={{
              fontFamily: HG_FONT_MONO_EXT, fontSize: 10.5,
              letterSpacing: "0.12em", textTransform: "uppercase",
              color: value.trim() ? "var(--hg-ice-bright)" : "var(--hg-fg-5)",
              background: "transparent",
              border: `1px solid ${value.trim() ? "var(--hg-ice-bright)" : "var(--hg-fg-5)"}`,
              padding: "4px 10px",
              cursor: value.trim() ? "pointer" : "default",
              opacity: value.trim() ? 1 : 0.5,
            }}
          >save</button>
        </div>
      </div>
    </div>
  );
}


// ─────────────────────────────────────────────────────────────────────
// /test classifier — runs the rules against a fixture set.
// ─────────────────────────────────────────────────────────────────────
const _CLASSIFIER_FIXTURES = [
  // [input, expected]
  ["turn off the kitchen lights",         "local"],
  ["make the living room warmer",         "local"],
  ["pause sonos",                          "local"],
  ["what do you see in the office?",      "local"],
  ["who's home?",                          "local"],
  ["is anyone in the kitchen",            "local"],
  ["dim the lights",                       "local"],
  ["the lamp is too bright",              "local"],
  ["movie mode",                           "local"],
  ["explain quantum physics",             "external"],
  ["what is the difference between OLED and Mini LED", "external"],
  ["explain how transformer attention works",          "external"],
  ["help me understand this python error",             "external"],
  ["compare two design approaches",       "external"],
  ["teach me about chess openings",       "external"],
  ["give me ideas for dinner",            "external"],
  ["why is rust faster than python",      "external"],
  // mixed — current rules route to local on home noun match; both
  // routes are acceptable per the plan.
  ["explain why warm lighting in the office feels cozier", "local"],
  ["compare warm and cool light",         "local"],
  // ambiguous / fallback
  ["hello",                                "ambiguous"],
  ["",                                     "ambiguous"],
];

function runClassifierTest() {
  const results = [];
  let passed = 0, failed = 0;
  for (const [input, expected] of _CLASSIFIER_FIXTURES) {
    const got = classifyIntent(input);
    const ok = got === expected;
    if (ok) passed++; else failed++;
    results.push({ input, expected, got, ok });
  }
  return { suite: "classifier", passed, failed, results };
}


// ─────────────────────────────────────────────────────────────────────
// /test external-privacy — synthesize an external call in sim mode,
// inspect window.__EXTERNAL_LAST_REQUEST for forbidden strings.
// ─────────────────────────────────────────────────────────────────────
const _FORBIDDEN_PATTERNS = [
  { re: /\blight\.[a-z_]+/,                  name: "HA entity-id (light.*)" },
  { re: /\bbinary_sensor\.[a-z_]+/,          name: "HA entity-id (binary_sensor.*)" },
  { re: /\bmedia_player\.[a-z_]+/,           name: "HA entity-id (media_player.*)" },
  { re: /\bsensor\.[a-z_]+/,                 name: "HA entity-id (sensor.*)" },
  { re: /\bhav-[a-z-]+/,                      name: "container name (hav-*)" },
  { re: /\b192\.168\./,                       name: "LAN IP (192.168.*)" },
  { re: /\beyJ[A-Za-z0-9_\-]{8,}/,           name: "JWT (HA_TOKEN)" },
  { re: /\bqwen3-vl-30b/i,                   name: "local model name" },
  { re: /\bmarcelo-lima\b/,                  name: "system username" },
  { re: /\bROOM_CONTEXT|\bbound_area\b/,     name: "room binding internals" },
  { re: /\brtsp:\/\//,                       name: "RTSP URL" },
];

async function runPrivacyTest() {
  // Save current SIM_ACTIVE state, force it on for the test.
  const prevSim = window.__SIM_ACTIVE;
  window.__SIM_ACTIVE = true;
  try {
    // Fixture inputs that the classifier WOULD route external.
    // (We bypass the classifier here — we're testing what
    // askExternal sends.)
    const inputs = [
      "explain quantum physics",
      "what is the difference between OLED and Mini LED",
    ];
    const results = [];
    for (const input of inputs) {
      window.__EXTERNAL_LAST_REQUEST = null;
      await askExternal(input, { onChunk: () => {}, onDone: () => {} });
      const body = window.__EXTERNAL_LAST_REQUEST;
      const serialized = JSON.stringify(body);
      const violations = [];
      for (const p of _FORBIDDEN_PATTERNS) {
        if (p.re.test(serialized)) {
          violations.push(p.name);
        }
      }
      // Also assert messages array shape: exactly system+user, no extras.
      const msgs = body?.messages || [];
      const shapeOk = msgs.length === 2
        && msgs[0]?.role === "system"
        && msgs[1]?.role === "user"
        && msgs[1]?.content === input;
      results.push({ input, violations, shapeOk });
    }
    const failed = results.filter(r => r.violations.length > 0 || !r.shapeOk).length;
    return { suite: "privacy", passed: results.length - failed, failed, results };
  } finally {
    window.__SIM_ACTIVE = prevSim;
  }
}


// ─────────────────────────────────────────────────────────────────────
// /test external-suite — runs everything and prints a pass/fail summary.
// ─────────────────────────────────────────────────────────────────────
async function runExternalSuite(addEvent) {
  if (typeof addEvent !== "function") {
    console.warn("[external] runExternalSuite requires addEvent callback");
    return;
  }

  const print = (kind, text, tone) => addEvent({ kind, text, tone });
  print("system", "── external reasoning suite ──", "info");

  // ── Smoke (S1–S5) ──
  print("system", "[smoke] classifier returns 'local' for home commands…", "info");
  const smokeA = classifyIntent("turn off the kitchen lights") === "local";
  const smokeB = classifyIntent("test ping") === "ambiguous";  // S1
  const smokeC = typeof window.streamSse === "function";       // S5 dependency check
  const smokeD = typeof window.AskExternal === "function" || typeof window.askExternal === "function";
  const smokeOk = smokeA && smokeB && smokeC && smokeD;
  print("system",
    `[smoke] home-cmd→local=${smokeA} · ambiguous-fallthrough=${smokeB} · streamSse=${smokeC} · askExternal=${smokeD}`,
    smokeOk ? "ok" : "error",
  );
  if (!smokeOk) {
    print("system", "regression — implementation must not modify existing chat behavior", "error");
    return;
  }

  // ── Classifier ──
  const cls = runClassifierTest();
  print("system",
    `[classifier] ✓ ${cls.passed} · ✗ ${cls.failed}`,
    cls.failed === 0 ? "ok" : "error",
  );
  cls.results.filter(r => !r.ok).slice(0, 4).forEach(r => {
    print("system",
      `  fail: "${r.input}" → got ${r.got}, expected ${r.expected}`,
      "error");
  });

  // ── Privacy ──
  const priv = await runPrivacyTest();
  print("system",
    `[privacy] ✓ ${priv.passed} · ✗ ${priv.failed}`,
    priv.failed === 0 ? "ok" : "error",
  );
  priv.results.forEach(r => {
    if (r.violations.length > 0) {
      print("system",
        `  leak: "${r.input}" → ${r.violations.join(", ")}`,
        "error");
    }
    if (!r.shapeOk) {
      print("system",
        `  shape: "${r.input}" → messages array malformed`,
        "error");
    }
  });

  // ── Live API roundtrip (only if real key configured AND we're not in sim mode) ──
  let liveOk = null;
  if (window.__SIM_ACTIVE === true) {
    print("system", "[live] skipped — sim mode active", "warn");
  } else if (isExternalConfigured()) {
    print("system", "[live] roundtrip to api.openai.com (1 short call)…", "info");
    try {
      const out = await askExternal(
        "Respond with exactly the word 'pong' and nothing else.",
        { onChunk: () => {} },
      );
      liveOk = typeof out?.text === "string" && out.text.length > 0;
      print("system",
        `  ${liveOk ? "✓" : "✗"} latency=${out?.latencyMs}ms tokens=${out?.usage?.completion_tokens ?? "?"}` +
        ` reply="${(out?.text || "").slice(0, 40)}"`,
        liveOk ? "ok" : "error",
      );
    } catch (e) {
      liveOk = false;
      print("system", `  ✗ live call failed: ${e?.code || e?.message}`, "error");
    }
  } else {
    print("system", "[live] skipped — no key configured (run /external set-key)", "warn");
  }

  // ── Summary ──
  const totals = {
    passed: cls.passed + priv.passed + (liveOk === true ? 1 : 0),
    failed: cls.failed + priv.failed + (liveOk === false ? 1 : 0),
  };
  print("system",
    `── ✓ ${totals.passed} passed · ✗ ${totals.failed} failed ──`,
    totals.failed === 0 ? "ok" : "error",
  );
}


// ─────────────────────────────────────────────────────────────────────
// Export everything to window so home-app.jsx + others can pick it up.
// ─────────────────────────────────────────────────────────────────────
Object.assign(window, {
  askExternal,
  classifyIntent,
  explainIntent,
  getExternalProvider,
  isExternalConfigured,
  formatExternalStatus,
  openaiProvider,
  ExternalKeyModal,
  runExternalSuite,
  runClassifierTest,
  runPrivacyTest,
});
