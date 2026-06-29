/* simulation-cameras.jsx — Simulation Mode camera placeholders.
 *
 * Renders mock camera frames as inline SVG data URIs. NO external assets,
 * no MJPEG, no fetches. Each (room, state) tuple yields a visually
 * distinct placeholder that the real <img> tag can consume verbatim.
 *
 * Camera states supported (see SIMULATION_MODE.md):
 *   - online-empty           — calm room, no occupants
 *   - online-person-detected — bounding box overlay, confidence label
 *   - online-low-confidence  — muted bounding box, "person? · 0.41"
 *   - offline                — slate with diagonal stripes
 *   - stale                  — dim gradient + "STALE · 3m ago"
 *   - loading                — animated shimmer
 *
 * Designed for the v6.3 vision drawer's existing <img src=...> consumers.
 */

(function () {
  // Per-room aesthetic — distinct gradient palette so each placeholder
  // reads as a different room at a glance.
  const ROOM_PALETTES = {
    living_room: ["#2b2a3e", "#3d3a4f", "#4a4259"], // warm dusk
    kitchen:     ["#1c2a26", "#2c3b34", "#3a4a40"], // cool pantry
    dining_room: ["#2a2429", "#3a3038", "#473640"], // mauve
    workshop:    ["#1f2329", "#2d3138", "#3a3f48"], // slate
    driveway:    ["#161c25", "#1f2734", "#2a3445"], // night-blue
    office:      ["#26221b", "#36302a", "#43392f"], // amber
  };
  const DEFAULT_PALETTE = ["#222", "#333", "#444"];

  // Tone for label/overlay text per state.
  const STATE_TONE = {
    "online-empty":           { dot: "#9ec5ff", label: "EMPTY",        labelTone: "fg-4" },
    "online-person-detected": { dot: "#7ce69b", label: "PERSON · 0.92", labelTone: "ok"   },
    "online-low-confidence":  { dot: "#e9c46a", label: "PERSON? · 0.41", labelTone: "warn" },
    offline:                  { dot: "#e15553", label: "OFFLINE",       labelTone: "crit" },
    stale:                    { dot: "#9aa4b1", label: "STALE · 3M",    labelTone: "fg-4" },
    loading:                  { dot: "#9ec5ff", label: "LOADING",       labelTone: "fg-3" },
  };

  function _palette(room) {
    return ROOM_PALETTES[room] || DEFAULT_PALETTE;
  }

  function _roomLabel(room) {
    return (room || "room").replace(/_/g, " ").toUpperCase();
  }

  /* Build the SVG body for a given (room, state).
   * Returns a `data:image/svg+xml;utf8,...` URI consumed by <img src>. */
  function cameraSvg(room, state) {
    const pal = _palette(room);
    const tone = STATE_TONE[state] || STATE_TONE.offline;
    const W = 320, H = 180;
    const roomText = _roomLabel(room);

    // Base gradient — three-stop diagonal so each room reads unique.
    const baseDefs = `
      <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%"  stop-color="${pal[0]}"/>
        <stop offset="55%" stop-color="${pal[1]}"/>
        <stop offset="100%" stop-color="${pal[2]}"/>
      </linearGradient>
      <pattern id="stripes" patternUnits="userSpaceOnUse" width="14" height="14" patternTransform="rotate(45)">
        <rect width="14" height="14" fill="#0d1117"/>
        <line x1="0" y1="0" x2="0" y2="14" stroke="#e15553" stroke-width="2" opacity="0.55"/>
      </pattern>
    `;

    // Bounding box (only for person scenarios)
    let bbox = "";
    if (state === "online-person-detected") {
      bbox = `
        <rect x="118" y="48" width="84" height="108" fill="none"
              stroke="#7ce69b" stroke-width="2" rx="2"/>
        <rect x="118" y="32" width="92" height="16" fill="#7ce69b"/>
        <text x="124" y="44" font-family="ui-monospace, monospace" font-size="10"
              font-weight="600" fill="#0d1117">person · 0.92</text>
      `;
    } else if (state === "online-low-confidence") {
      bbox = `
        <rect x="120" y="60" width="76" height="92" fill="none"
              stroke="#e9c46a" stroke-width="2" stroke-dasharray="4 3" rx="2" opacity="0.7"/>
        <text x="120" y="56" font-family="ui-monospace, monospace" font-size="10"
              font-weight="500" fill="#e9c46a">person? · 0.41</text>
      `;
    }

    // Loading shimmer (CSS animation in SVG)
    let shimmer = "";
    if (state === "loading") {
      shimmer = `
        <rect x="0" y="0" width="${W}" height="${H}" fill="url(#shim)"/>
        <linearGradient id="shim" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%"  stop-color="${pal[1]}" stop-opacity="0.0">
            <animate attributeName="offset" values="-0.3;1.3" dur="1.6s" repeatCount="indefinite"/>
          </stop>
          <stop offset="30%" stop-color="${pal[2]}" stop-opacity="0.6">
            <animate attributeName="offset" values="0;1.6"  dur="1.6s" repeatCount="indefinite"/>
          </stop>
          <stop offset="60%" stop-color="${pal[1]}" stop-opacity="0.0">
            <animate attributeName="offset" values="0.3;1.9" dur="1.6s" repeatCount="indefinite"/>
          </stop>
        </linearGradient>
      `;
    }

    // Background: gradient OR stripes for offline
    const bg = state === "offline"
      ? `<rect width="${W}" height="${H}" fill="url(#stripes)"/>`
      : `<rect width="${W}" height="${H}" fill="url(#g)"/>`;

    // Room name (large, faint)
    const roomLabel = `
      <text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle"
            font-family="ui-monospace, monospace"
            font-size="22" font-weight="500"
            letter-spacing="6"
            fill="#ffffff" fill-opacity="${state === "offline" ? 0.85 : 0.18}">${roomText}</text>
    `;

    // SIM badge (corner) — makes it OBVIOUS this is a placeholder
    const simBadge = `
      <rect x="${W - 56}" y="8" width="48" height="16" fill="#0d1117" opacity="0.7"/>
      <text x="${W - 32}" y="20" text-anchor="middle"
            font-family="ui-monospace, monospace" font-size="9"
            letter-spacing="2" font-weight="600" fill="#e9c46a">SIM</text>
    `;

    // State label (bottom-left)
    const stateLabel = `
      <rect x="8" y="${H - 24}" width="${tone.label.length * 6.6 + 16}" height="16"
            fill="#0d1117" opacity="0.85"/>
      <text x="16" y="${H - 12}" font-family="ui-monospace, monospace"
            font-size="9" letter-spacing="1.4" font-weight="600"
            fill="${tone.labelTone === "crit" ? "#e15553"
                  : tone.labelTone === "ok"   ? "#7ce69b"
                  : tone.labelTone === "warn" ? "#e9c46a"
                  : "#9aa4b1"}">${tone.label}</text>
    `;

    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" preserveAspectRatio="xMidYMid slice">
      <defs>${baseDefs}</defs>
      ${bg}
      ${shimmer}
      ${roomLabel}
      ${bbox}
      ${simBadge}
      ${stateLabel}
    </svg>`;

    // Compact data URI — encodeURIComponent to keep #/?/& safe.
    return `data:image/svg+xml;utf8,${encodeURIComponent(svg).replace(/'/g, "%27")}`;
  }

  /* Helpers consumed by the simulation registry and the vision drawer
   * directly when in sim mode. */
  function cameraSvgFor(room, scenarioMap) {
    // scenarioMap: { living_room: "online-person-detected", kitchen: "offline", ... }
    // Default fallback: online-empty.
    const state = (scenarioMap && scenarioMap[room]) || "online-empty";
    return cameraSvg(room, state);
  }

  /* Map room → JPEG path bundled alongside the simulation source.
   * These are real-camera screenshots (see simulation/cameras/README).
   * If the file is absent (e.g. someone forked the repo and deleted it),
   * the camera renderer falls back to the abstract SVG placeholder. */
  const JPG_PATHS = {
    living_room: "simulation/cameras/sim_living_room.jpg",
    kitchen:     "simulation/cameras/sim_kitchen.jpg",
    dining_room: "simulation/cameras/sim_dining_room.jpg",
    workshop:    "simulation/cameras/sim_workshop.jpg",
    driveway:    "simulation/cameras/sim_driveway.jpg",
    // office has no camera in the live setup — pure SVG placeholder
    office:      null,
  };

  /* Pick the right src for a (room, state) pair.
   *   - online-* states with a real JPEG → use the JPEG (looks 1:1
   *     with the real running app)
   *   - offline / stale / loading or missing JPEG → fall back to the
   *     stylized SVG so the state is still visible visually
   */
  function cameraSrcFor(room, scenarioMap) {
    const state = (scenarioMap && scenarioMap[room]) || "online-empty";
    const usesRealFrame = (
      state === "online-empty" ||
      state === "online-person-detected" ||
      state === "online-low-confidence"
    );
    if (usesRealFrame && JPG_PATHS[room]) {
      // Cache-bust by appending a session-stable query so the same
      // src renders the same image but doesn't ever hit a stale cache.
      return JPG_PATHS[room];
    }
    return cameraSvg(room, state);
  }

  // Default placeholders for the 6 rooms — used when a scenario doesn't
  // override per-room state. Convenient baseline for the `healthy` scenario.
  const DEFAULT_CAMERA_MAP = {
    living_room: "online-person-detected",
    kitchen:     "online-empty",
    dining_room: "online-empty",
    workshop:    "online-empty",
    driveway:    "online-empty",
    office:      "online-empty",
  };

  Object.assign(window, {
    SimCameraSvg:        cameraSvg,
    SimCameraSvgFor:     cameraSvgFor,
    SimCameraSrcFor:     cameraSrcFor,
    SimDefaultCameraMap: DEFAULT_CAMERA_MAP,
  });
})();
