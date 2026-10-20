/* ============================================================================
 * home-overlay.js — window.HomeOverlay: the app's single overlay layer stack.
 *
 * One capture-phase window keydown listener owns Escape and Tab-trapping for
 * every overlay, drawer, dialog, and lightbox. Layers register on open and
 * unregister on close; Escape always goes to the TOPMOST layer only.
 *
 * Why capture phase: window-level capture runs before React's root-delegated
 * handlers by propagation phase alone, and before every component's own
 * window listener because those register on mount — after this file loads.
 * That ordering is load-bearing: home-overlay.js must stay in index.html's
 * files[] BEFORE every overlay component file and must NOT be deferred.
 *
 * Escape semantics:
 *   - Topmost-only: one press, one layer. Never walks down the stack.
 *   - Claim-or-pass: the layer's onEscape returns false to PASS (event
 *     propagates; used by the embedded apartment view so the base surface
 *     keeps its behavior). Any other return claims the press — the manager
 *     then preventDefault()s and stopImmediatePropagation()s so nothing
 *     else (e.g. home-app's pending-confirm cancel) sees it.
 *   - Scoped input guard: the press is yielded to a focused editable
 *     element ONLY when that element is inside the top layer's root or an
 *     explicitly owned portal root (getRoots) — a
 *     field inside the layer (labeler picker input) handles its own Escape;
 *     a focused chat composer BEHIND the layer does not swallow the close.
 *
 * Focus contract (per layer, opt-in via options):
 *   - on push: document.activeElement is recorded; focus moves into the
 *     layer next frame (explicit ref → first focusable → root w/ tabindex).
 *   - trap: Tab wraps within the layer root.
 *   - on pop (topmost only): focus returns to the recorded opener if still
 *     usable, else options.restoreTo, else the app fallback
 *     (setFallbackFocus, registered once by home-app).
 *
 * The core is framework-free and Node-testable (tools/run-overlay-stack-
 * tests.js); useOverlayLayer/useExitPresence are thin React sugar.
 * ========================================================================= */
"use strict";

(function () {
  var HAS_DOM = typeof window !== "undefined" && typeof document !== "undefined";

  var _stack = [];          // [{ id, key, onEscape, getRoot, trap, passive, ... }]
  var _nextId = 1;
  var _subscribers = [];
  var _fallbackFocus = null; // fn -> element|selector, or selector string

  function _emit() {
    // CSS hook: <html data-hg-overlay-open> while any blocking layer is up.
    // Lets chrome behind an overlay dim affordances (e.g. the action card's
    // "esc" hint, which Escape no longer reaches) without React plumbing.
    if (HAS_DOM) {
      try {
        var root = document.documentElement;
        if (hasBlockingLayer()) root.setAttribute("data-hg-overlay-open", "1");
        else root.removeAttribute("data-hg-overlay-open");
      } catch (e) { /* stubbed DOM in tests may lack documentElement */ }
    }
    for (var i = 0; i < _subscribers.length; i++) {
      try { _subscribers[i](_stack.length, top() ? top().key : null); } catch (e) { /* subscriber error is not ours */ }
    }
  }

  function top() {
    return _stack.length ? _stack[_stack.length - 1] : null;
  }

  function _isEditable(el) {
    if (!el) return false;
    if (el.isContentEditable) return true;
    var tag = el.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
  }

  function _rootOf(layer) {
    if (!layer) return null;
    try {
      if (typeof layer.getRoot === "function") return layer.getRoot() || null;
      if (layer.rootRef) return layer.rootRef.current || null;
    } catch (e) { /* ref gone */ }
    return null;
  }

  // Extra roots affect editable ownership only. They never widen a modal's
  // focus trap or make an unrelated editor behind the layer own Escape.
  function _containsTarget(layer, target) {
    var root = _rootOf(layer);
    if (root && root.contains && root.contains(target)) return true;
    var roots;
    try { roots = typeof layer.getRoots === "function" ? layer.getRoots() : null; }
    catch (e) { return false; }
    if (!Array.isArray(roots)) return false;
    for (var i = 0; i < roots.length; i++) {
      var extra = roots[i];
      if (extra && extra.contains && extra.contains(target)) return true;
    }
    return false;
  }

  function _canFocus(el) {
    if (!HAS_DOM || !el || typeof el.focus !== "function" || !el.isConnected || el.disabled) return false;
    // A canvas click can leave body active. Its no-op focus must not count
    // as restoration and suppress the deliberate viewport/list fallback.
    var doc = el.ownerDocument || document;
    if ((el === doc.body || el === doc.documentElement) && !el.hasAttribute("tabindex")) return false;
    try {
      if (el.matches && el.matches(":disabled")) return false;
      if (el.hidden || el.inert || (el.closest && el.closest("[hidden], [inert]"))) return false;
      if (el.getClientRects && el.getClientRects().length === 0) return false;
      var view = el.ownerDocument && el.ownerDocument.defaultView || window;
      var style = view.getComputedStyle ? view.getComputedStyle(el) : null;
      if (style && (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse")) return false;
    } catch (e) { return false; }
    return true;
  }

  function _tryFocus(el) {
    if (!_canFocus(el)) return false;
    try { el.focus({ preventScroll: true }); }
    catch (e) { try { el.focus(); } catch (e2) { return false; } }
    // focus() can silently refuse a target; only a successful focus ends the
    // fallback chain. This also covers a target invalidated during focus.
    return (el.ownerDocument || document).activeElement === el;
  }

  function _focusTarget(value) {
    try {
      if (typeof value === "function") value = value();
      if (value && typeof value === "object" && "current" in value) value = value.current;
      if (typeof value === "string") value = document.querySelector(value);
      return value || null;
    } catch (e) { return null; }
  }

  function restoreFocus(opener, fallback) {
    if (!HAS_DOM) return false;
    return _tryFocus(_focusTarget(opener)) || _tryFocus(_focusTarget(fallback))
      || _tryFocus(_focusTarget(_fallbackFocus));
  }

  var FOCUSABLE_SEL = 'a[href], button:not([disabled]), input:not([disabled]), ' +
    'select:not([disabled]), textarea:not([disabled]), summary, ' +
    '[tabindex]:not([tabindex="-1"])';

  function _focusables(root) {
    if (!root || !root.querySelectorAll) return [];
    var nodes = root.querySelectorAll(FOCUSABLE_SEL);
    var out = [];
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (_canFocus(n)) out.push(n);
    }
    return out;
  }

  function _moveFocusIn(layer) {
    if (!HAS_DOM || layer.initialFocus === "none") return;
    var root = _rootOf(layer);
    // Focus may already have moved into the panel before its deferred mount
    // callback runs. Preserve that choice instead of retargeting the next key.
    // A wrapper can also contain its opener; that original focus still needs
    // to enter the panel rather than suppressing the initial-focus target.
    var active = document.activeElement;
    if (root && active !== root && active !== layer.opener && root.contains && root.contains(active) && _canFocus(active)) return;
    var target = null;
    try {
      if (layer.initialFocus && typeof layer.initialFocus === "object" && "current" in layer.initialFocus) {
        target = layer.initialFocus.current;
      }
    } catch (e) { /* ignore */ }
    if (_tryFocus(target)) return;
    if ((target || layer.initialFocus === "first") && root && _tryFocus(_focusables(root)[0])) return;
    if (root) {
      if (!root.hasAttribute("tabindex")) root.setAttribute("tabindex", "-1");
      _tryFocus(root);
    }
  }

  function _restoreFocus(layer) {
    return restoreFocus(layer.opener, layer.restoreTo);
  }

  function push(opts) {
    var layer = {
      id: _nextId++,
      key: (opts && opts.key) || "layer-" + _nextId,
      onEscape: (opts && opts.onEscape) || function () { return undefined; },
      rootRef: opts ? opts.rootRef : null,
      getRoot: opts ? opts.getRoot : null,
      getRoots: opts ? opts.getRoots : null,
      trap: !!(opts && opts.trap),
      passive: !!(opts && opts.passive),
      initialFocus: (opts && opts.initialFocus) || "root",
      restoreTo: opts ? opts.restoreTo : null,
      opener: HAS_DOM ? document.activeElement : null,
    };
    _stack.push(layer);
    if (HAS_DOM && !layer.passive) {
      // Next frame: the layer's DOM usually mounts in the same commit that
      // triggered the push effect; rAF lets refs attach first.
      var raf = window.requestAnimationFrame || function (f) { setTimeout(f, 16); };
      raf(function () {
        // Only if still the topmost active layer — a fast open/close or a
        // stacked push must not steal focus backward. Lazily-loaded surfaces
        // can attach their root a commit later; retry once on a second frame.
        if (top() !== layer) return;
        if (_rootOf(layer) || (layer.initialFocus && typeof layer.initialFocus === "object")) {
          _moveFocusIn(layer);
        } else {
          raf(function () { if (top() === layer) _moveFocusIn(layer); });
        }
      });
    }
    _emit();
    return {
      pop: function () {
        var idx = _stack.indexOf(layer);
        if (idx < 0) return;
        var wasTop = idx === _stack.length - 1;
        _stack.splice(idx, 1);
        if (wasTop && !layer.passive) _restoreFocus(layer);
        _emit();
      },
      update: function (patch) {
        if (patch) {
          for (var k in patch) {
            if (Object.prototype.hasOwnProperty.call(patch, k)) layer[k] = patch[k];
          }
        }
      },
    };
  }

  function hasBlockingLayer() {
    for (var i = 0; i < _stack.length; i++) {
      if (!_stack[i].passive) return true;
    }
    return false;
  }

  function _wrapTab(e, layer) {
    var root = _rootOf(layer);
    if (!root) return;
    var items = _focusables(root);
    if (!items.length) {
      e.preventDefault();
      _moveFocusIn({ getRoot: function () { return root; } });
      return;
    }
    var first = items[0];
    var last = items[items.length - 1];
    var active = document.activeElement;
    var inside = root.contains(active);
    if (!inside) {
      e.preventDefault();
      if (!_tryFocus(e.shiftKey ? last : first)) _moveFocusIn({ getRoot: function () { return root; } });
      return;
    }
    if (!e.shiftKey && active === last) {
      e.preventDefault();
      if (!_tryFocus(first)) _moveFocusIn({ getRoot: function () { return root; } });
    } else if (e.shiftKey && active === first) {
      e.preventDefault();
      if (!_tryFocus(last)) _moveFocusIn({ getRoot: function () { return root; } });
    }
  }

  // The dispatcher — exposed for the Node tests as _handleKeydown.
  function _handleKeydown(e) {
    var t = top();
    if (!t) return;
    if (e.key === "Escape") {
      // Scoped input guard: yield only to an editable INSIDE the top layer.
      var target = e.target;
      if (_isEditable(target)) {
        if (_containsTarget(t, target)) return;
        // Editable OUTSIDE the layer (e.g. the chat composer behind a
        // drawer): the layer still owns Escape — fall through.
      }
      // A held Escape is one cancellation, even after React exposes the next
      // operation. Owned editables still receive their native repeat behavior.
      if (e.repeat) {
        e.preventDefault();
        if (typeof e.stopImmediatePropagation === "function") e.stopImmediatePropagation();
        else if (typeof e.stopPropagation === "function") e.stopPropagation();
        return;
      }
      var claimed;
      try { claimed = t.onEscape(e) !== false; } catch (err) { claimed = true; }
      if (claimed) {
        e.preventDefault();
        if (typeof e.stopImmediatePropagation === "function") e.stopImmediatePropagation();
        else if (typeof e.stopPropagation === "function") e.stopPropagation();
      }
      return;
    }
    if (e.key === "Tab" && t.trap && !t.passive && HAS_DOM) {
      _wrapTab(e, t);
    }
  }

  if (HAS_DOM) {
    window.addEventListener("keydown", _handleKeydown, { capture: true });
  }

  /* React sugar. React is a window global (UMD, loaded in <head> before the
   * boot chain) — but guard anyway so the Node tests can import the core. */
  function useOverlayLayer(opts) {
    var React = HAS_DOM ? window.React : null;
    if (!React) throw new Error("useOverlayLayer requires React on window");
    var cbRef = React.useRef(null);
    cbRef.current = opts.onEscape;
    var rootsRef = React.useRef(null);
    rootsRef.current = opts.getRoots;
    var active = !!opts.active;
    React.useEffect(function () {
      if (!active) return undefined;
      var handle = push({
        key: opts.key,
        onEscape: function (e) { return cbRef.current ? cbRef.current(e) : undefined; },
        rootRef: opts.rootRef,
        getRoot: opts.getRoot,
        getRoots: function () { return rootsRef.current ? rootsRef.current() : null; },
        trap: opts.trap,
        passive: opts.passive,
        initialFocus: opts.initialFocus,
        restoreTo: opts.restoreTo,
      });
      return function () { handle.pop(); };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [active, opts.key]);
  }

  /* Exit-presence helper for mirrored exit animations (adoption step 9):
   * keeps `mounted` true for `ms` after `open` flips false so an exit class
   * can play; collapses to 0 under prefers-reduced-motion. */
  function useExitPresence(open, ms) {
    var React = HAS_DOM ? window.React : null;
    if (!React) throw new Error("useExitPresence requires React on window");
    var state = React.useState(!!open);
    var mounted = state[0], setMounted = state[1];
    React.useEffect(function () {
      if (open) { setMounted(true); return undefined; }
      if (!mounted) return undefined;
      var delay = ms || 200;
      try {
        if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) delay = 0;
      } catch (e) { /* ignore */ }
      var timer = setTimeout(function () { setMounted(false); }, delay);
      return function () { clearTimeout(timer); };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open]);
    return { mounted: mounted, closing: mounted && !open };
  }

  var api = {
    push: push,
    useOverlayLayer: useOverlayLayer,
    useExitPresence: useExitPresence,
    hasBlockingLayer: hasBlockingLayer,
    topKey: function () { return top() ? top().key : null; },
    isTopmost: function (key) { return !!top() && top().key === key; },
    subscribe: function (fn) {
      _subscribers.push(fn);
      return function () {
        var i = _subscribers.indexOf(fn);
        if (i >= 0) _subscribers.splice(i, 1);
      };
    },
    setFallbackFocus: function (fnOrSelector) { _fallbackFocus = fnOrSelector; },
    restoreFocus: restoreFocus,
    _handleKeydown: _handleKeydown,   // exposed for the Node harness
    _stackSize: function () { return _stack.length; },
  };

  if (typeof window !== "undefined") {
    window.HomeOverlay = api;
  } else if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})();
