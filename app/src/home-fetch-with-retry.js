/* ============================================================================
 * home-fetch-with-retry.js — resilient HTTP helper for the home web app.
 *
 * Exposes a single global, window.fetchWithRetry, that wraps a normal fetch
 * (or the Tauri HTTP bridge) with per-attempt timeouts, bounded retries with
 * exponential backoff + jitter, and Retry-After awareness. It exists so panels
 * that load state over HTTP can ride out the AI-box reboot window (the same
 * failure the boot loader's reconnecting overlay guards against) instead of
 * showing a hard "Failed to fetch" the instant the box blips.
 *
 * Transport: uses window.tauriFetch when present (which is browserTauriFetch —
 * a thin fetch wrapper — in web mode, and the native bridge in the desktop
 * shell) and falls back to the global fetch. Both honor an AbortSignal, so the
 * per-attempt timeout works in either environment. In Node test harnesses there
 * is no window, so the global fetch is used directly.
 *
 * Retry policy:
 *   - Retries on network errors, per-attempt timeout, and HTTP 408/429/502/503/504.
 *   - Never retries HTTP 400/401/403/404/409 (client/permanent) — throws at once.
 *   - Backoff: backoffMs * backoffFactor**(attempt-1), plus uniform jitter in
 *     [-backoffJitter, +backoffJitter] of that delay.
 *   - Retry-After (seconds or HTTP date) is honored, capped at 60s.
 *   - An external AbortSignal passed via options.signal cancels immediately and
 *     is NOT retried.
 *
 * Resolution: returns the successful Response. On exhaustion it throws an Error
 * carrying .attempts, .lastStatus, and .reason ("network" | "http" | "abort").
 * ========================================================================== */
(function () {
  "use strict";

  var glob = typeof window !== "undefined" ? window : (typeof globalThis !== "undefined" ? globalThis : this);

  // HTTP statuses that indicate a transient condition worth retrying.
  var RETRYABLE_STATUS = { 408: true, 429: true, 502: true, 503: true, 504: true };
  // Explicit non-retry statuses (client/permanent). Listed for clarity; any
  // status that is neither retryable nor a success falls through to a throw too.
  var NON_RETRYABLE_STATUS = { 400: true, 401: true, 403: true, 404: true, 409: true };
  var RETRY_AFTER_CAP_MS = 60000;

  function resolveTransport() {
    if (glob && typeof glob.tauriFetch === "function") return glob.tauriFetch;
    if (typeof fetch === "function") return fetch;
    if (glob && typeof glob.fetch === "function") return glob.fetch;
    return null;
  }

  function sleep(ms, externalSignal) {
    return new Promise(function (resolve, reject) {
      if (externalSignal && externalSignal.aborted) {
        reject(makeAbortError());
        return;
      }
      var timer = setTimeout(function () {
        cleanup();
        resolve();
      }, Math.max(0, ms));
      function onAbort() {
        cleanup();
        reject(makeAbortError());
      }
      function cleanup() {
        clearTimeout(timer);
        if (externalSignal && externalSignal.removeEventListener) {
          externalSignal.removeEventListener("abort", onAbort);
        }
      }
      if (externalSignal && externalSignal.addEventListener) {
        externalSignal.addEventListener("abort", onAbort);
      }
    });
  }

  function makeAbortError() {
    var err = new Error("aborted");
    err.name = "AbortError";
    return err;
  }

  // Parse a Retry-After header value: an integer number of seconds, or an
  // HTTP date. Returns milliseconds to wait, capped, or null when absent/bad.
  function parseRetryAfter(response) {
    if (!response || !response.headers || typeof response.headers.get !== "function") return null;
    var raw = response.headers.get("Retry-After");
    if (!raw) return null;
    raw = String(raw).trim();
    if (/^\d+$/.test(raw)) {
      return Math.min(RETRY_AFTER_CAP_MS, parseInt(raw, 10) * 1000);
    }
    var when = Date.parse(raw);
    if (!isNaN(when)) {
      var delta = when - Date.now();
      if (delta < 0) delta = 0;
      return Math.min(RETRY_AFTER_CAP_MS, delta);
    }
    return null;
  }

  function computeBackoff(attempt, backoffMs, backoffFactor, backoffJitter) {
    var base = backoffMs * Math.pow(backoffFactor, attempt - 1);
    // Uniform jitter in [-backoffJitter, +backoffJitter] of the current delay.
    var jitter = base * backoffJitter * (Math.random() * 2 - 1);
    var delay = base + jitter;
    return delay < 0 ? 0 : delay;
  }

  function fetchWithRetry(config) {
    config = config || {};
    var url = config.url;
    var options = config.options || {};
    var timeoutMs = config.timeoutMs != null ? config.timeoutMs : 8000;
    var maxAttempts = config.maxAttempts != null ? config.maxAttempts : 4;
    var backoffMs = config.backoffMs != null ? config.backoffMs : 500;
    var backoffFactor = config.backoffFactor != null ? config.backoffFactor : 2;
    var backoffJitter = config.backoffJitter != null ? config.backoffJitter : 0.3;
    var onAttempt = typeof config.onAttempt === "function" ? config.onAttempt : null;
    var externalSignal = options.signal || null;

    var transport = resolveTransport();
    if (!transport) {
      var noTransport = new Error("no fetch transport available");
      noTransport.reason = "network";
      noTransport.attempts = 0;
      noTransport.lastStatus = null;
      return Promise.reject(noTransport);
    }

    var lastStatus = null;

    function attemptOnce(attempt) {
      // Each attempt gets its own controller so the timeout aborts THIS request
      // only; an external abort (unmount) forwards through to it as well.
      var controller = typeof AbortController === "function" ? new AbortController() : null;
      var timedOut = false;
      var timer = null;
      var onExternalAbort = null;

      if (controller && timeoutMs > 0) {
        timer = setTimeout(function () {
          timedOut = true;
          try { controller.abort(); } catch (_) {}
        }, timeoutMs);
      }
      if (externalSignal && controller) {
        if (externalSignal.aborted) {
          try { controller.abort(); } catch (_) {}
        } else if (externalSignal.addEventListener) {
          onExternalAbort = function () { try { controller.abort(); } catch (_) {} };
          externalSignal.addEventListener("abort", onExternalAbort);
        }
      }

      function cleanup() {
        if (timer) clearTimeout(timer);
        if (externalSignal && onExternalAbort && externalSignal.removeEventListener) {
          externalSignal.removeEventListener("abort", onExternalAbort);
        }
      }

      var init = {};
      for (var k in options) {
        if (Object.prototype.hasOwnProperty.call(options, k)) init[k] = options[k];
      }
      if (controller) init.signal = controller.signal;

      return Promise.resolve()
        .then(function () { return transport(url, init); })
        .then(function (response) {
          cleanup();
          lastStatus = response ? response.status : null;
          if (response && response.ok) {
            return { done: true, response: response };
          }
          // Non-OK HTTP response.
          var status = response ? response.status : 0;
          var retryable = RETRYABLE_STATUS[status] === true;
          return decide(attempt, {
            reason: "http",
            response: response,
            status: status,
            error: null,
            retryable: retryable,
          });
        }, function (err) {
          cleanup();
          // Distinguish an external cancel (do not retry) from our own timeout
          // (retryable) from a plain network failure (retryable).
          if (externalSignal && externalSignal.aborted && !timedOut) {
            var cancelErr = err || makeAbortError();
            cancelErr.reason = "abort";
            return { done: false, fatal: true, error: cancelErr, reason: "abort" };
          }
          var reason = timedOut ? "abort" : "network";
          return decide(attempt, {
            reason: reason,
            response: null,
            status: null,
            error: err || new Error(reason),
            retryable: true,
          });
        });
    }

    // Decide whether to retry or give up after one attempt's outcome.
    function decide(attempt, outcome) {
      var isLast = attempt >= maxAttempts;
      if (!outcome.retryable || isLast) {
        // Report the final attempt too, so a caller tracking progress sees it.
        if (onAttempt) {
          safeOnAttempt({ attempt: attempt, error: outcome.error, response: outcome.response, nextDelay: null });
        }
        return { done: false, fatal: true, error: outcome.error, reason: outcome.reason, response: outcome.response };
      }
      // Retry: prefer Retry-After when the server supplied one.
      var retryAfter = parseRetryAfter(outcome.response);
      var nextDelay = retryAfter != null
        ? retryAfter
        : computeBackoff(attempt, backoffMs, backoffFactor, backoffJitter);
      if (onAttempt) {
        safeOnAttempt({ attempt: attempt, error: outcome.error, response: outcome.response, nextDelay: nextDelay });
      }
      return { done: false, retry: true, nextDelay: nextDelay };
    }

    function safeOnAttempt(info) {
      try { onAttempt(info); } catch (_) { /* UI callback must never break retry */ }
    }

    function run(attempt) {
      if (externalSignal && externalSignal.aborted) {
        return Promise.reject(finalError(makeAbortError(), "abort", attempt - 1));
      }
      return attemptOnce(attempt).then(function (result) {
        if (result.done) return result.response;
        if (result.fatal) {
          throw finalError(result.error, result.reason, attempt);
        }
        // Retry path: wait, then go again.
        return sleep(result.nextDelay, externalSignal).then(function () {
          return run(attempt + 1);
        }, function (abortErr) {
          throw finalError(abortErr, "abort", attempt);
        });
      });
    }

    function finalError(err, reason, attempts) {
      var wrapped = err instanceof Error ? err : new Error(String(err || reason));
      wrapped.reason = reason;
      wrapped.attempts = attempts;
      wrapped.lastStatus = lastStatus;
      return wrapped;
    }

    return run(1);
  }

  glob.fetchWithRetry = fetchWithRetry;

  // Allow CommonJS test harnesses to require the module directly.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = fetchWithRetry;
  }
})();
