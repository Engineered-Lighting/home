/* Home — standalone Tauri mount.
 *
 * Replaces the design-canvas-era home-tweaks.jsx which wrapped HomeApp in a
 * Tweaks panel driven by a parent-iframe postMessage protocol. In Tauri the
 * window is top-level (no parent frame) and the runtime tweak controls are
 * design-time only, so we mount HomeApp directly.
 */

const _mountEl = document.getElementById("root");
if (_mountEl) {
  ReactDOM.createRoot(_mountEl).render(<HomeApp />);
}
