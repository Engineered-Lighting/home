/**
 * home-apartment-setup-links.js — APT-U03 Setup › Home Assistant.
 *
 * Link rows are presentation over the Apartment model's exact light.* links.
 * Re-matching uses the existing link reconciliation (geometry and tape
 * calibration unchanged, new duplicate links refused). Existing duplicate
 * data reads Shared, with "Also controls …".
 *
 * Flash is the only output this tool sends. It is refused here unless the
 * home is Echo, Home Assistant is connected and Travel Mode is known to be
 * off; then it goes through the shared guarded dispatch
 * (HomeApartmentData.callService), which checks the site, rehearsal, the
 * caller's currentness and Travel Mode again from Home Assistant itself
 * before one light.turn_on with Home Assistant's own short flash. Nothing is
 * queued or retried.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.AptSetupLinks = api;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  const FLASH = Object.freeze({ domain: "light", service: "turn_on", flash: "short" });
  const isLight = (value) => typeof value === "string" && /^light\.[a-z0-9_]+$/.test(value);

  /** One row per modeled light, in model order. `lights` is the Home
   * Assistant light registry; options exclude lights linked elsewhere. */
  function linkRows({ devices = [], zones = [], lights = [] } = {}) {
    const lamps = devices.filter((device) => device && device.type === "light");
    const roomName = (id) => (zones.find((zone) => zone.id === id) || {}).name || null;
    return lamps.map((device) => {
      const entity = isLight(device.ha_entity_id) ? device.ha_entity_id : null;
      const others = lamps.filter((other) => other.id !== device.id);
      const sharedWith = entity ? others.filter((other) => other.ha_entity_id === entity).map((other) => other.name || other.id) : [];
      const taken = new Set(others.map((other) => other.ha_entity_id).filter(Boolean));
      const options = lights.filter((light) => isLight(light && light.entity_id) && !taken.has(light.entity_id))
        .map((light) => ({ entity_id: light.entity_id, name: light.name || light.entity_id }));
      if (entity && !options.some((option) => option.entity_id === entity)) options.unshift({ entity_id: entity, name: entity, missing: true });
      return Object.freeze({ id: device.id, name: device.name || device.id, room: roomName(device.room_id), entity,
        state: !entity ? "not_linked" : sharedWith.length ? "shared" : "linked", sharedWith, options });
    });
  }

  /** Victoria and other registration homes: every socket and fixture reads
   * Not linked until commissioning, with no picker and no Flash. */
  function registrationRows(document) {
    const equipment = (document && Array.isArray(document.equipment)) ? document.equipment : [];
    return equipment.filter((record) => record && (record.kind === "mount" || record.kind === "fixture"))
      .map((record) => Object.freeze({ id: record.id, name: record.label || record.id, room: null, entity: null,
        state: "not_linked", sharedWith: [], options: [], kind: record.kind }));
  }

  /** Reason Flash cannot run, or "" when it may be attempted. The guarded
   * dispatch still re-checks everything at the moment of output. */
  function flashRefusal({ siteId, sim = false, connection, entityId, travel } = {}) {
    if (siteId !== "echo") return "Flash is only available for homes with Home Assistant lights.";
    if (sim) return "Simulation cannot flash a real light.";
    if (connection !== "online") return "Home Assistant is offline. You can change matches; Flash needs a connection.";
    if (!isLight(entityId)) return "Link this light to a Home Assistant light first.";
    if (travel === "on") return "Travel Mode is on, so Flash is refused.";
    if (travel !== "off") return "Travel Mode's state is unknown, so Flash is refused.";
    return "";
  }

  function travelState(win) {
    const state = win && win.__HOME_TRAVEL_MODE_STATE;
    return state === "on" || state === "off" ? state : null;
  }

  /** One guarded Flash. Returns { ok } or { ok: false, reason }. */
  async function flash({ client, entityId, siteId = "echo", isCurrent, data } = {}) {
    const guarded = data || (typeof window !== "undefined" ? window.HomeApartmentData : null);
    if (siteId !== "echo") return { ok: false, reason: "Flash is only available for homes with Home Assistant lights; nothing was sent." };
    if (!client) return { ok: false, reason: "Home Assistant is offline; nothing was sent." };
    if (!isLight(entityId)) return { ok: false, reason: "This light has no Home Assistant link; nothing was sent." };
    if (!guarded || typeof guarded.callService !== "function") return { ok: false, reason: "Home Assistant controls are unavailable; nothing was sent." };
    if (typeof isCurrent !== "function") return { ok: false, reason: "Home control context is unavailable; nothing was sent." };
    try {
      await guarded.callService(client, FLASH.domain, FLASH.service, { entity_id: entityId, flash: FLASH.flash }, { siteId, isCurrent });
      return { ok: true };
    } catch (error) {
      return { ok: false, reason: (error && error.message) || "Flash failed." };
    }
  }

  return Object.freeze({ FLASH, linkRows, registrationRows, flashRefusal, travelState, flash, isLight });
});
