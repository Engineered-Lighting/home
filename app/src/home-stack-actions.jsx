/* home-stack-actions.jsx — shared supervisor REST module.
 *
 * Extracted from home-ai-stack.jsx's dispatch() so both the existing
 * AiStackCard (in the AI tab) and the new HmLabTab (in the Lab tab)
 * can drive supervisor actions without parallel auth/retry logic.
 *
 * The supervisor lives at <metricsBase host>:8093 and accepts a Bearer
 * STACK_TOKEN supplied through the native/server-controlled
 * window.__STACK_TOKEN boundary. Legacy localStorage token keys are purged and
 * never read. The supervisor returns:
 *   • 200/202 + { task_id } on accepted action
 *   • 409 + { detail: { task_id, verb } } when another task is already running
 *   • 401 if the token is wrong
 *   • 429 if rate-limited (esp. stop has its own 10s cooldown)
 *   • 400 with body.detail.error if the request body is malformed
 *
 * All functions return a Promise<{ok, status, body, error?}> where:
 *   ok    = true iff the request was accepted (200/202/409)
 *   body  = parsed JSON or null
 *   error = human-readable string on auth/rate/network failures
 *
 * Per Addendum 10 §AA — keep this thin (no React state); callers wrap
 * with their own UI feedback.
 */

const HomeStackActions = (() => {
  function _base(supervisorUrl) {
    return String(supervisorUrl || "").replace(/\/+$/, "");
  }

  function _headers(stackToken, extra) {
    return {
      Authorization: `Bearer ${stackToken}`,
      ...(extra || {}),
    };
  }

  async function _post(url, opts = {}) {
    try {
      const r = await window.tauriFetch(url, {
        method: "POST",
        headers: opts.headers,
        cache: "no-store",
      });
      let body = null;
      try { body = await r.json(); } catch { /* empty body is fine */ }
      if (r.ok || r.status === 409) {
        return { ok: true, status: r.status, body };
      }
      if (r.status === 401) {
        return { ok: false, status: 401, body, error: "auth failed — check STACK_TOKEN" };
      }
      if (r.status === 429) {
        const after = body?.detail?.retry_after_s ?? null;
        return {
          ok: false, status: 429, body,
          error: after ? `rate-limited — retry in ${after}s` : "rate-limited — try again shortly",
        };
      }
      if (r.status === 400) {
        const why = body?.detail?.error || "bad request";
        return { ok: false, status: 400, body, error: why };
      }
      return { ok: false, status: r.status, body, error: `http ${r.status}` };
    } catch (e) {
      return {
        ok: false, status: 0, body: null,
        error: (e && e.message) ? e.message : String(e),
      };
    }
  }

  async function _get(url, opts = {}) {
    try {
      const r = await window.tauriFetch(url, {
        method: "GET",
        headers: opts.headers,
        cache: "no-store",
      });
      let body = null;
      try { body = await r.json(); } catch { /* might be SSE stream */ }
      return { ok: r.ok, status: r.status, body };
    } catch (e) {
      return { ok: false, status: 0, body: null, error: (e && e.message) ? e.message : String(e) };
    }
  }

  return {
    /* Whole-stack actions */
    async start({ supervisorUrl, stackToken }) {
      return _post(`${_base(supervisorUrl)}/api/stack/start`, { headers: _headers(stackToken) });
    },
    async restart({ supervisorUrl, stackToken }) {
      return _post(`${_base(supervisorUrl)}/api/stack/restart`, { headers: _headers(stackToken) });
    },
    async stop({ supervisorUrl, stackToken }) {
      // Backend requires X-Confirm header for stop.
      return _post(`${_base(supervisorUrl)}/api/stack/stop`, {
        headers: _headers(stackToken, { "X-Confirm": "stop-ai-stack" }),
      });
    },
    async freeGpu({ supervisorUrl, stackToken }) {
      return _post(`${_base(supervisorUrl)}/api/stack/free-gpu`, {
        headers: _headers(stackToken, { "X-Confirm": "free-gpu" }),
      });
    },
    async pausePerception({ supervisorUrl, stackToken }) {
      return _post(`${_base(supervisorUrl)}/api/services/vision-sidecar/stop`, {
        headers: _headers(stackToken, { "X-Confirm": "stop-vision-sidecar" }),
      });
    },

    /* Per-service actions */
    async serviceLogs({ supervisorUrl, stackToken, service, n = 50 }) {
      return _get(`${_base(supervisorUrl)}/api/services/${encodeURIComponent(service)}/logs?n=${n}`, {
        headers: _headers(stackToken),
      });
    },
    async serviceStop({ supervisorUrl, stackToken, service }) {
      return _post(`${_base(supervisorUrl)}/api/services/${encodeURIComponent(service)}/stop`, {
        headers: _headers(stackToken, { "X-Confirm": `stop-${service}` }),
      });
    },
    async serviceRestart({ supervisorUrl, stackToken, service }) {
      return _post(`${_base(supervisorUrl)}/api/services/${encodeURIComponent(service)}/restart`, {
        headers: _headers(stackToken),
      });
    },

    /* Status fetch — same shape as the existing 15s poll uses. */
    async status({ supervisorUrl, stackToken }) {
      return _get(`${_base(supervisorUrl)}/api/stack/status`, { headers: _headers(stackToken) });
    },

    /* SSE log-stream URL builder. Caller wires their own EventSource
     * (or tauriFetch streaming) since SSE handling is hairier than
     * one-shot POST/GET. */
    logsStreamUrl({ supervisorUrl, taskId }) {
      const url = `${_base(supervisorUrl)}/api/stack/logs/stream`;
      return taskId ? `${url}?task_id=${encodeURIComponent(taskId)}` : url;
    },

    /* Convenience: from a dispatch result, pick the active task_id.
     * Handles both 200 ({task_id}) and 409 ({detail:{task_id}}) paths. */
    extractTaskId(result) {
      if (!result || !result.body) return null;
      const b = result.body;
      return b.task_id || b?.detail?.task_id || null;
    },
    extractVerb(result, fallback) {
      const b = result?.body || {};
      return b?.detail?.verb || b?.verb || fallback;
    },

    /* Error classifiers — lets callers branch cleanly. */
    isAuth401(result) { return result && result.status === 401; },
    isRateLimit429(result) { return result && result.status === 429; },
    isConflict409(result) { return result && result.status === 409; },
  };
})();

window.HomeStackActions = HomeStackActions;
