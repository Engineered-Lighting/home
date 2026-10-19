/* Shared Apartment presentation only. Source owners supply every state and
 * action; this module does not read household data or dispatch commands. */
(function () {
  "use strict";

  const states = {
    connected: ["Connected", "dot", "ok"], current: ["Current", "dot", "ok"],
    saved: ["Saved", "dot", "ok"], applied: ["Applied", "dot", "ok"],
    reported: ["Reported", "dot", "ok"], live: ["Live", "live", "ok"],
    proposed: ["Proposed", "ring", "proposed"], draft: ["Draft", "ring", "proposed"],
    recorded: ["Recorded", "ring", "neutral"], replay: ["Replay", "ring", "neutral"],
    authored: ["Authored", "diamond", "neutral"], simulated: ["Simulated", "diamond", "neutral"],
    held: ["Held", "diamond", "attn"], stale: ["Stale", "diamond", "attn"],
    // Owner decision 2026-10-19: the approximate virtual preview may hold its
    // last aim for a producer-measured bound. Only the producer reports it.
    holding: ["Holding", "hold", "attn"],
    unsaved: ["Unsaved", "diamond", "attn"], changed: ["Changed", "diamond", "attn"],
    disconnected: ["Disconnected", "diamond", "attn"], blocked: ["Blocked", "bar", "blocked"],
    conflict: ["Conflict", "bar", "blocked"], invalid: ["Invalid", "bar", "blocked"],
    unavailable: ["Unavailable", "dot", "muted"], "no-session": ["No session", "dot", "muted"],
    modeled: ["Modeled", "dot", "muted"], loading: ["Loading", "spinner", "muted"],
  };

  function Badge({ state = "unavailable", children, title, className = "" }) {
    const key = Object.prototype.hasOwnProperty.call(states, state) ? state : "unavailable";
    const [label, shape, tone] = states[key];
    return <span className={`apt-ui-badge ${className}`.trim()} data-state={key} data-tone={tone} title={title}>
      <span className="apt-ui-badge-marker" data-shape={shape} aria-hidden="true" />
      <span>{children == null ? label : children}</span>
    </span>;
  }

  /* Formats the producer's own last-seen age for the Holding badge. It never
   * reads a clock: a missing or invalid age shows "Holding" alone, and the
   * caller re-renders only when the producer reports again. */
  function holdingLabel(lastSeenSeconds) {
    const age = Number(lastSeenSeconds);
    return Number.isFinite(age) && age >= 0 ? `Holding · last seen ${Math.round(age)}s ago` : "Holding";
  }

  /* Consume before calling user code, including throwing or asynchronous code.
   * isCurrent is the caller's existing authority check, evaluated at activation.
   * A cancelled action cannot be revived by a queued click or retained handle. */
  function createToastAction(callback, { isCurrent = () => false, onCancel } = {}) {
    if (typeof callback !== "function") throw new TypeError("Toast action must be a function");
    let active = true;
    let run = callback;
    function cancel(reason = "cancelled") {
      if (!active) return false;
      active = false;
      run = null;
      if (typeof onCancel === "function") onCancel(reason);
      return true;
    }
    return Object.freeze({
      get active() { return active; },
      cancel,
      run() {
        if (!active) return false;
        let current = false;
        try { current = isCurrent() === true; } catch (_) { /* Fail closed. */ }
        if (!current) { cancel("stale"); return false; }
        active = false;
        const action = run;
        run = null;
        action();
        return true;
      },
    });
  }

  /* The parent owns which notice is shown. It must dismiss by toast identity,
   * so an expired notice cannot clear a newer one. Timeout belongs here only;
   * the parent must not also schedule a legacy dismissal timer. */
  function Toast({ toast, onDismiss }) {
    const dismissRef = React.useRef(onDismiss);
    dismissRef.current = onDismiss;
    const lifecycleRef = React.useRef(null);
    const elementRef = React.useRef(null);

    React.useEffect(() => {
      if (!toast) return undefined;
      const requested = toast.timeoutMs;
      const duration = Number.isFinite(requested) && requested >= 0 ? requested : 2800;
      const element = elementRef.current;
      const focused = !!element?.contains(element.ownerDocument?.activeElement);
      const life = { toast, timer: null, started: 0, remaining: duration, focus: focused, pointer: false, done: false };
      function stopTimer() {
        if (life.timer == null) return;
        clearTimeout(life.timer);
        life.timer = null;
        life.remaining = Math.max(0, life.remaining - Math.max(0, Date.now() - life.started));
      }
      life.finish = (reason) => {
        if (life.done) return;
        life.done = true;
        stopTimer();
        try { toast.action?.cancel(reason); } finally { dismissRef.current?.(toast); }
      };
      function startTimer() {
        if (toast.sticky || life.done || life.focus || life.pointer || life.timer != null) return;
        life.started = Date.now();
        life.timer = setTimeout(() => life.finish("timeout"), life.remaining);
      }
      life.pause = (kind, paused) => {
        life[kind] = paused;
        if (life.focus || life.pointer) stopTimer();
        else startTimer();
      };
      lifecycleRef.current = life;
      startTimer();
      return () => {
        life.done = true;
        stopTimer();
        toast.action?.cancel("replaced");
        if (lifecycleRef.current === life) lifecycleRef.current = null;
      };
    }, [toast]);

    if (!toast) return null;
    const current = () => lifecycleRef.current?.toast === toast ? lifecycleRef.current : null;
    const pause = (kind, paused) => current()?.pause(kind, paused);
    const activate = () => {
      const life = current();
      if (!life || life.done) return;
      try { toast.action?.run(); } finally { life.finish("action"); }
    };
    return <div ref={elementRef} className="apt-ui-toast" onFocus={() => pause("focus", true)}
      onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) pause("focus", false); }}
      onPointerEnter={() => pause("pointer", true)} onPointerLeave={() => pause("pointer", false)}>
      <span className="apt-ui-toast-message" role="status" aria-live="polite" aria-atomic="true">{toast.text}</span>
      {toast.action?.active && <button type="button" className="apt-ui-button apt-ui-toast-action" onClick={activate}>
        {toast.actionLabel || "Undo"}
      </button>}
      <button type="button" className="apt-ui-button is-icon apt-ui-toast-dismiss" aria-label="Dismiss message"
        onClick={() => current()?.finish("dismiss")}><span aria-hidden="true">×</span></button>
    </div>;
  }

  window.HomeApartmentUI = Object.freeze({ Badge, Toast, createToastAction, holdingLabel });
})();
