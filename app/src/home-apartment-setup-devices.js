/**
 * home-apartment-setup-devices.js — PROPOSED fields, pending the owner's
 * approval (docs/design/apartment-unified/setup-stored-fields-proposal.md).
 *
 * Pure helpers for Setup › Names labels (APT-U02), hand-placed camera poses
 * (APT-U06) and ordered mmWave sensors (APT-U07). Nothing here runs unless
 * the Setup proposal flag is on. A hand-placed pose never touches calibrated
 * intrinsics or extrinsics, and a sensor's status comes only from Home
 * Assistant entities. Nothing here sends a command.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.AptSetupDevices = api;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  const FLAG_PARAM = "setupProposal", FLAG_KEY = "home.apartment.setupProposal";
  const LABEL_MAX = 10;
  const DEG = Math.PI / 180;
  const r3 = (v) => Math.round(v * 1000) / 1000;
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  function proposalEnabled(win) {
    try {
      const value = new URLSearchParams(win?.location?.search || "").get(FLAG_PARAM);
      if (value === "1") return true;
      if (value === "0") return false;
      return win?.localStorage?.getItem(FLAG_KEY) === "1";
    } catch (_) { return false; }
  }

  /* ---------------- Names (U02) ---------------- */

  function labelError(label) {
    const text = String(label ?? "");
    if (!text.trim()) return "";
    return text.trim().length > LABEL_MAX ? `A room label is at most ${LABEL_MAX} characters.` : "";
  }
  function duplicateNames(devices = []) {
    const counts = new Map();
    for (const device of devices) {
      const key = String(device?.name || "").trim().toLowerCase();
      if (key) counts.set(key, (counts.get(key) || 0) + 1);
    }
    return new Set(devices.filter((device) => counts.get(String(device?.name || "").trim().toLowerCase()) > 1).map((device) => device.id));
  }

  /* ---------------- placement (U06, U07) ---------------- */

  /** Camera lens: vertical field from horizontal for a 16:9 sensor. */
  function cameraVfov(hfovDeg) {
    return Math.round(2 * Math.atan(Math.tan(hfovDeg * DEG / 2) * 9 / 16) / DEG);
  }

  /** Where a device sits after one mount click, facing into the room.
   * Wall: the clicked wall point; ceiling: above the clicked floor point;
   * shelf: on the clicked top. `roomCenter` is [x, y]. */
  function poseFromPick({ mount, point, normal = [0, 0, 1], roomHeight = 2.4, roomCenter = [2, 2] }) {
    if (!Array.isArray(point)) return null;
    let pos;
    if (mount === "ceiling") pos = [point[0], point[1], Math.max(0.2, roomHeight - 0.05)];
    else if (mount === "wall") pos = [point[0] + normal[0] * 0.04, point[1] + normal[1] * 0.04, point[2]];
    else pos = [point[0], point[1], point[2] + 0.04];
    const facing = mount === "wall" && Math.hypot(normal[0], normal[1]) > 0.5
      ? Math.atan2(normal[1], normal[0])
      : Math.atan2(roomCenter[1] - pos[1], roomCenter[0] - pos[0]);
    return { pos: pos.map(r3), yaw_rad: r3(facing), pitch_deg: mount === "ceiling" ? -60 : -15 };
  }

  /** Pan and tilt that point the device at a clicked spot. */
  function aimAt(pos, spot) {
    const dx = spot[0] - pos[0], dy = spot[1] - pos[1], dz = spot[2] - pos[2];
    const horizontal = Math.hypot(dx, dy);
    if (horizontal < 1e-6 && Math.abs(dz) < 1e-6) return null;
    return { yaw_rad: r3(horizontal < 1e-6 ? 0 : Math.atan2(dy, dx)), pitch_deg: Math.round(clamp(Math.atan2(dz, horizontal) / DEG, -90, 30)) };
  }

  function axes(yawRad, pitchDeg) {
    const p = pitchDeg * DEG, c = Math.cos(p);
    const forward = [Math.cos(yawRad) * c, Math.sin(yawRad) * c, Math.sin(p)];
    const right = [Math.sin(yawRad), -Math.cos(yawRad), 0];
    const up = [right[1] * forward[2] - right[2] * forward[1], right[2] * forward[0] - right[0] * forward[2], right[0] * forward[1] - right[1] * forward[0]];
    return { forward, right, up };
  }

  /** Apex and far-plane corners of the coverage frustum (cameras: solid to
   * `range_m`, default 6 m; sensors: dashed to their range). */
  function frustum({ pos, yaw_rad = 0, placement = {} }) {
    const hfov = placement.hfov_deg || 90, vfov = placement.vfov_deg || cameraVfov(hfov), range = placement.range_m || 6;
    const { forward, right, up } = axes(yaw_rad, placement.pitch_deg || 0);
    const w = Math.tan(hfov * DEG / 2) * range, h = Math.tan(vfov * DEG / 2) * range;
    const at = (sw, sh) => [0, 1, 2].map((i) => r3(pos[i] + forward[i] * range + right[i] * sw * w + up[i] * sh * h));
    return { apex: pos.slice(), corners: [at(-1, 1), at(1, 1), at(1, -1), at(-1, -1)] };
  }

  /** Inside the field and range (line of sight is not checked). */
  function inView(device, point) {
    const placement = device.placement || {};
    const hfov = placement.hfov_deg || 90, vfov = placement.vfov_deg || cameraVfov(hfov), range = placement.range_m || 6;
    const { forward, right, up } = axes(device.yaw_rad || 0, placement.pitch_deg || 0);
    const d = [0, 1, 2].map((i) => point[i] - device.pos[i]);
    const ahead = d[0] * forward[0] + d[1] * forward[1] + d[2] * forward[2];
    if (ahead <= 0.05 || Math.hypot(...d) > range) return false;
    const side = d[0] * right[0] + d[1] * right[1] + d[2] * right[2], lift = d[0] * up[0] + d[1] * up[1] + d[2] * up[2];
    return Math.abs(Math.atan2(side, ahead)) <= hfov * DEG / 2 && Math.abs(Math.atan2(lift, ahead)) <= vfov * DEG / 2;
  }

  /** Interim Covers list: named aims and lights inside field and range. */
  function covers(device, { targets = [], devices = [] } = {}) {
    if (!device?.pos) return [];
    return [
      ...targets.filter((target) => Array.isArray(target.pos) && inView(device, target.pos)).map((target) => target.name),
      ...devices.filter((other) => other.id !== device.id && other.type === "light" && Array.isArray(other.pos) && inView(device, other.pos)).map((other) => other.name),
    ];
  }

  /** A stable digest of calibrated extrinsics, to tell a move after
   * calibration from a calibrated camera that was not moved. */
  function calibrationRef(extrinsics) {
    if (!extrinsics) return null;
    const text = JSON.stringify(extrinsics, Object.keys(extrinsics).sort());
    let h = 0x811c9dc5;
    for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    return "fnv1a:" + h.toString(16).padStart(8, "0");
  }

  /** Derived camera status; nothing is stored about it. */
  function cameraStatus(device) {
    const extrinsics = device?.camera?.extrinsics || null;
    if (!extrinsics) return { state: "placed", label: "Placed by hand · not calibrated" };
    // A hand placement records the calibration it was made against. If that
    // calibration is still the current one, the camera moved after it; a
    // newer calibration (or none recorded at placement) supersedes the move.
    if (device.placement?.method === "hand" && device.placement.calibration_ref === calibrationRef(extrinsics)) {
      return { state: "moved", label: "Moved since calibration · recalibrate" };
    }
    return { state: "calibrated", label: "Calibrated" };
  }

  /** Derived sensor status: paired only when every listed entity exists in
   * Home Assistant's states. Never "connected" from Apartment data alone. */
  function sensorStatus(device, states = {}) {
    const ids = Array.isArray(device?.ha_entity_ids) ? device.ha_entity_ids.filter((id) => typeof id === "string" && id) : [];
    if (!ids.length || !ids.every((id) => states && Object.prototype.hasOwnProperty.call(states, id))) return { state: "ordered", label: "Ordered · not connected" };
    return { state: "paired", label: "Paired with Home Assistant" };
  }

  /** The proposed placement object for a save. */
  function placementRecord({ mount, pitch_deg, hfov_deg, vfov_deg, range_m, now, extrinsics = null, sensor = false }) {
    const record = { method: "hand", mount, pitch_deg: Math.round(clamp(pitch_deg, -90, 30)), hfov_deg: Math.round(clamp(hfov_deg, 30, 150)),
      vfov_deg: Math.round(clamp(vfov_deg, 20, 120)), placed_at: now };
    if (sensor) record.range_m = Math.round(clamp(range_m, 1, 12) * 2) / 2;
    else record.calibration_ref = calibrationRef(extrinsics);
    return record;
  }

  return Object.freeze({ FLAG_PARAM, FLAG_KEY, LABEL_MAX, proposalEnabled, labelError, duplicateNames, cameraVfov, poseFromPick,
    aimAt, frustum, inView, covers, calibrationRef, cameraStatus, sensorStatus, placementRecord });
});
