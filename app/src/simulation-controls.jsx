/* simulation-controls.jsx — mock light + media state store for control cards.
 *
 * Simulation Mode has no real Home Assistant. When __SIM_ACTIVE, the inline
 * control cards (home-control.jsx) read state from and write commands to THIS
 * store instead of HA — modeled on simulation-cameras.jsx's DEFAULT_CAMERA_MAP.
 *
 * Entities are shaped exactly like HA state objects ({state, attributes}) so
 * the cards' readEntityStates / readMediaState / buildLightCaps helpers work
 * against this store byte-for-byte the same as a real get_states response.
 *
 * Loaded via index.html after home-control.jsx + simulation-cameras.jsx and
 * before simulation-data.jsx (scenarios reference window.SimControlStore).
 *
 * Privacy: generic entity_ids only — living_room, kitchen, office, bedroom.
 */

(function () {
  // media_player supported_features bits.
  const SF_FULL = 4 | 8 | 16 | 32 | 524288;   // volume, mute, prev, next, grouping
  const SF_AMP  = 4 | 8;                       // volume + mute only (an AV receiver)

  function makeDefaultStore() {
    return {
      // ── lights ──
      "light.kitchen": { state: "on", attributes: {
        friendly_name: "Kitchen", brightness: 200, color_temp_kelvin: 3000,
        min_color_temp_kelvin: 2200, max_color_temp_kelvin: 6500,
        supported_color_modes: ["color_temp"],
      } },
      "light.living_room_lamp": { state: "on", attributes: {
        friendly_name: "Living Room Lamp", brightness: 140, color_temp_kelvin: 2700,
        min_color_temp_kelvin: 2000, max_color_temp_kelvin: 6500,
        supported_color_modes: ["color_temp"],
      } },
      "light.living_room_ceiling": { state: "on", attributes: {
        friendly_name: "Living Room Ceiling", brightness: 255,
        supported_color_modes: ["brightness"],   // NO color temp — mixed-group edge case
      } },
      "light.office": { state: "off", attributes: {
        friendly_name: "Office", supported_color_modes: ["color_temp"],
        min_color_temp_kelvin: 2200, max_color_temp_kelvin: 6500,
      } },
      // ── media players ──
      "media_player.kitchen_sonos": { state: "playing", attributes: {
        friendly_name: "Kitchen Sonos", volume_level: 0.34, is_volume_muted: false,
        media_title: "Topographic Oceans", media_artist: "Close Harmony",
        media_album_name: "Field Notes", app_name: "Sonos", entity_picture: null,
        group_members: ["media_player.kitchen_sonos"], supported_features: SF_FULL,
      } },
      "media_player.living_room_sonos": { state: "paused", attributes: {
        friendly_name: "Living Room Sonos", volume_level: 0.5, is_volume_muted: false,
        media_title: null, app_name: "Sonos",
        group_members: ["media_player.living_room_sonos"], supported_features: SF_FULL,
      } },
      "media_player.office_sonos": { state: "idle", attributes: {
        friendly_name: "Office Sonos", volume_level: 0.2, is_volume_muted: false,
        group_members: ["media_player.office_sonos"], supported_features: SF_FULL,
      } },
      "media_player.bedroom_sonos": { state: "idle", attributes: {
        friendly_name: "Bedroom Sonos", volume_level: 0.2, is_volume_muted: false,
        group_members: ["media_player.bedroom_sonos"], supported_features: SF_FULL,
      } },
      // The living-room Sonos feeds this Onkyo AV receiver — the routed volume
      // target for the living room (see VOLUME_ROUTING in home-control.jsx).
      "media_player.living_room_onkyo": { state: "on", attributes: {
        friendly_name: "Living Room Onkyo", volume_level: 0.45, is_volume_muted: false,
        supported_features: SF_AMP,
      } },
    };
  }

  let _store = makeDefaultStore();
  const _subs = new Set();
  function _emit() { for (const fn of _subs) { try { fn(); } catch (e) { /* noop */ } } }

  const PLAYLIST = [
    "Topographic Oceans", "Field of Reeds", "Hounds of Love",
    "A Love Supreme", "Selected Ambient Works",
  ];
  function _cycleTitle(cur, dir) {
    let i = PLAYLIST.indexOf(cur);
    if (i < 0) i = 0;
    i = (i + (dir > 0 ? 1 : -1) + PLAYLIST.length) % PLAYLIST.length;
    return PLAYLIST[i];
  }

  // Apply a service call to the mock store — mirrors the {domain, service,
  // service_data, targets} shape the cards' fireServiceCall passes.
  function callService(opts) {
    const { domain, service } = opts || {};
    const service_data = (opts && opts.service_data) || {};
    const targets = (opts && opts.targets) || [];
    for (const id of targets) {
      const ent = _store[id];
      if (!ent) continue;
      const a = ent.attributes;
      if (domain === "light") {
        if (service === "turn_off") {
          ent.state = "off";
        } else if (service === "turn_on" || service === "toggle") {
          ent.state = (service === "toggle" && ent.state === "on") ? "off" : "on";
          if (ent.state === "on") {
            if (service_data.brightness_pct != null) a.brightness = Math.round((service_data.brightness_pct / 100) * 255);
            if (service_data.brightness != null) a.brightness = service_data.brightness;
            if (service_data.color_temp_kelvin != null) a.color_temp_kelvin = service_data.color_temp_kelvin;
          }
        }
      } else if (domain === "media_player") {
        if (service === "media_play") ent.state = "playing";
        else if (service === "media_pause") ent.state = "paused";
        else if (service === "media_play_pause") ent.state = ent.state === "playing" ? "paused" : "playing";
        else if (service === "media_next_track") a.media_title = _cycleTitle(a.media_title, 1);
        else if (service === "media_previous_track") a.media_title = _cycleTitle(a.media_title, -1);
        else if (service === "volume_set" && service_data.volume_level != null) {
          a.volume_level = Math.max(0, Math.min(1, service_data.volume_level));
        }
        else if (service === "volume_mute") a.is_volume_muted = !!service_data.is_volume_muted;
        else if (service === "join") {
          const add = service_data.group_members || [];
          const merged = Array.from(new Set([].concat(a.group_members || [id], add)));
          a.group_members = merged;
          for (const m of merged) {
            const me = _store[m];
            if (me) {
              me.attributes.group_members = merged.slice();
              if (m !== id && ent.state === "playing") me.state = "playing";
            }
          }
        }
        else if (service === "unjoin") {
          for (const other of Object.values(_store)) {
            if (other.attributes && Array.isArray(other.attributes.group_members)) {
              other.attributes.group_members = other.attributes.group_members.filter((m) => m !== id);
            }
          }
          a.group_members = [id];
        }
      }
    }
    _emit();
    return Promise.resolve();   // sim always succeeds — the error scenario is data-driven
  }

  const SimControlStore = {
    reset() { _store = makeDefaultStore(); _emit(); },
    getState(id) { return _store[id] || null; },
    getAll() { return Object.assign({}, _store); },
    subscribe(fn) { _subs.add(fn); return () => { _subs.delete(fn); }; },
    callService,
  };

  Object.assign(window, { SimControlStore });
})();
