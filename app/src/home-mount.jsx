/* Home — standalone Tauri mount.
 *
 * Replaces the design-canvas-era home-tweaks.jsx which wrapped HomeApp in a
 * Tweaks panel driven by a parent-iframe postMessage protocol. In Tauri the
 * window is top-level (no parent frame) and the runtime tweak controls are
 * design-time only, so we mount HomeApp directly.
 *
 * The mount is exposed as window.__homeMount so the boot watchdog in
 * index.html can retry it if #root is still empty after the boot chain
 * finishes (e.g. a render crash unmounted the tree). Re-entry is safe: the
 * root is created once and reused, and an already-populated #root is a no-op.
 */

window.__homeMount = function () {
  const el = document.getElementById("root");
  if (!el) return false;
  if (el.children.length > 0) return true; // already mounted
  if (!window.__homeRoot) window.__homeRoot = ReactDOM.createRoot(el);
  window.__homeRoot.render(<HomeApp />);
  if (typeof window.__homeRemoveBootShell === "function") {
    requestAnimationFrame(() => window.__homeRemoveBootShell());
  }
  return true;
};

window.__homeMount();
