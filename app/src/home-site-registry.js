/* home-site-registry.js — the confirmed-home contract for the Apartment viewport.
 *
 * `home-apartment.jsx` has always consumed `window.HomeSpatialViewport
 * .getSelectedAnchor()`. Nothing in the tree ever defined it, so every outward
 * handoff outside simulation failed closed with "world view needs a confirmed
 * home location". This is the provider.
 *
 * A classic script on purpose, not an ES module: `tools/check-jsx.js` parses
 * every top-level file under app/src with `new Function(src)`, which rejects
 * import and export. The ES modules live under home-3d/ and are reached through
 * the import map.
 *
 * Scope boundaries this file must keep:
 *
 *   No Home Assistant. A site is never a device. It carries no entity id, never
 *   enters `model.devices` or the overlay pick set, and selecting one cannot
 *   retarget an issued service call. The hazard is concrete: the Apartment's
 *   double-click handler toggles any picked object that has an `ha_entity_id`.
 *
 *   No provider credential. The registry knows where homes are, never how to
 *   reach a tile server.
 *
 *   No geocoding at runtime. Coordinates are checked-in records with recorded
 *   provenance. A geocoder returning a point is not a confirmed home.
 */

(function initialiseHomeSiteRegistry() {
  "use strict";

  var SELECTED_SITE_STORAGE_KEY = "apartment3d.site";
  var WORLD_ZOOM_STORAGE_KEY = "apartment3d.worldZoom";

  /*
   * Echo Park is the scanned site. Victoria arrives with
   * two-home switching and needs no change to anything here beyond a second
   * record.
   *
   * `site` is the canonical parcel record. `visualAnchor` is the user-confirmed
   * centroid of the enclosed home, and is what stays at the viewport centre:
   * the two differ by about 12 m here, which is the difference between centring
   * the parcel and centring the unit the user actually lives in.
   */
  var SITES = [
    Object.freeze({
      id: "echo",
      shortName: "Echo Park",
      label: "Echo Park home",
      address: "1455 Echo Park Ave, Los Angeles, CA 90026",
      site: Object.freeze({ longitude: -118.25611324123172, latitude: 34.07939046922046 }),
      /*
       * The LEFT unit of the duplex, not the building centroid.
       *
       * Corrected from a measurement against the rendered mesh: the previous
       * anchor sat 2.29 m east and 3.12 m north of here, which put the frame
       * centre on the neighbouring unit. The scan is of this unit, so this is
       * the point the world has to keep centred for the two to describe the
       * same home. Measured with measureGroundOffset against a live
       * photorealistic view, then converted through an east/north/up frame
       * rather than a flat degrees-per-metre approximation.
       */
      visualAnchor: Object.freeze({ longitude: -118.256044744871, latitude: 34.079322094089 }),
      parcelRangeM: 180,
      /*
       * The hero stills for this home, outermost capture first.
       *
       * Declared here rather than armed by hand, which is how they were reached
       * until now: the feature worked and was invisible, because nothing in the
       * product ever asked for one. A still belongs to a site in exactly the way
       * its parcel range does.
       *
       * MORE THAN ONE, because a still buys one band of range and no more. Its
       * detail ceiling is width/viewport, so a 3600 px still over a 1427 px
       * viewport is honest from its capture range down to about 40% of it and
       * is being magnified past that. Levels spaced inside that ceiling hand
       * over to each other with an overlap they both cover, so the camera can
       * approach the house far closer than one still could carry it.
       *
       * Every level is captured at the pose this home ARRIVES at -- a still is
       * registered to one bearing and one pitch, and is a picture of somewhere
       * else at any other.
       */
      heroStills: Object.freeze([]), // Supplied by the active local content pack.
      sceneAvailable: true,
      /*
       * Google's photorealistic coverage is not global. "confirmed" means it has
       * been seen rendering here; an unconfirmed site must not silently fall
       * through to the ellipsoid and look like a bug.
       */
      photorealCoverage: "unconfirmed",
      sceneRegistration: Object.freeze({
        version: 1,
        /*
         * Provisional. Apartment +X runs from the kitchen and rear toward the
         * street-facing office nook. Correcting this bumps `version`, which
         * invalidates every cached surface height derived from it, and never
         * touches the Apartment model.
         */
        localXAxisAzimuthDeg: 117.66,
        verification: Object.freeze({
          method: "street-facing-landmark",
          note: "office nook faces Echo Park Ave; kitchen is the rear",
          expectedBearingDeg: 117.66,
          toleranceDeg: 3,
        }),
        cameraAnchor: Object.freeze({
          heightMode: "surface",
          sampleWidthM: 2,
          /*
           * Deliberately unresolved rather than inherited.
           *
           * The prototype committed 96.44525501300674 m here: a float64 sampler
           * output with no recorded provenance. If it came from Google's tiles
           * then storing it in a file that ships in the installer is exactly the
           * offline use of Google content their policy prohibits, and the real
           * defect is that nobody can tell which it was.
           *
           * Until a licence-clean value is derived from a public-domain
           * elevation source (USGS 3DEP for Echo Park, CDEM for Victoria), the
           * surface samples the live provider and falls back to the ellipsoid.
           * Google-derived heights stay in session memory and are never written
           * to disk or to source.
           */
          fallbackHeightM: null,
          fallbackProvenance: "unresolved",
        }),
      }),
      provenance: Object.freeze({
        method: "user-confirmed-visual-anchor",
        confirmedAt: "2026-09-03",
        note: "parcel centroid from the county record; visual anchor corrected to the scanned unit against the rendered mesh",
      }),
    }),
    /*
     * The second home, and the one that proves the premise.
     *
     * Its Den scan can be viewed in its own metric frame while true-north
     * alignment remains unverified. The compatible active pack decides whether
     * the interior exists; absent or incompatible content stays unavailable.
     * Registration version identifies the local frame independently of a
     * qualified compass bearing. Neither viewing nor selecting it grants any
     * device authority.
     *
     * `photorealCoverage` is unconfirmed on purpose. Google's photorealistic
     * coverage is not global, and an unconfirmed site that silently fell
     * through to a blue ellipsoid would look like a bug rather than like an
     * honest absence.
     */
    Object.freeze({
      id: "victoria",
      shortName: "Victoria",
      label: "Victoria home",
      address: "3005 Rutland Rd, Victoria, BC V8R 3R5, Canada",
      site: Object.freeze({ longitude: -123.2954735, latitude: 48.4434866 }),
      // No confirmed visual anchor yet, so the parcel point serves as both.
      // They differ for Echo Park because someone stood there and corrected it.
      visualAnchor: Object.freeze({ longitude: -123.2954207, latitude: 48.4435378 }),
      parcelRangeM: 220,
      /* As for Echo Park above, at the bearing this home arrives at. */
      heroStills: Object.freeze([]), // Supplied by the active local content pack.
      /*
       * Which way the world faces on arrival before north alignment is known.
       *
       * NOT a registration. localXAxisAzimuthDeg stays null below, because
       * the scan's true-north alignment is unverified. This is a different
       * thing with a different
       * justification: the direction the camera should LOOK to see the house.
       *
       * Chosen by measurement, not taste. Captured at 220 m and pitch -35 from
       * all four cardinal headings and compared: at 0 -- the bare fallback the
       * code used before -- the sight line runs straight into closed canopy and
       * the house is entirely hidden, which is what "the house is not visible"
       * meant. 90 and 180 clip it behind trees. At 270 the roof, its driveway
       * and the lawn are clear of obstruction.
       *
       * Pitch is deliberately NOT overridden. Echo Park descends at -35 and so
       * does this; one descent grammar for both homes, with only the bearing
       * differing, because the bearing is the part that is actually about this
       * particular house.
       */
      /*
       * 225, NOT the 270 the comparison above picks out as the better view.
       *
       * A still is registered to one bearing and is a picture of somewhere else
       * at any other, so this has to be the bearing the stills were actually
       * captured at -- and they were captured at 225, because that is what cold
       * entry produced before the chosen bearing was wired through. Declaring
       * 270 while shipping 225 assets puts the image at the wrong angle over
       * the map, which is worse than the slightly more obstructed view.
       *
       * 270 remains the better framing and the captures should move to it. That
       * needs a re-run of the upscale pipeline for both Victoria levels, which
       * is blocked on fal.ai credit at the time of writing. When it is done,
       * this becomes 270 and nothing else changes.
       */
      parcelViewHeadingDeg: 225,
      sceneAvailable: false,
      photorealCoverage: "unconfirmed",
      sceneRegistration: Object.freeze({
        version: 1,
        /*
         * The measured Den frame is available for interior viewing. A null
         * bearing keeps world orientation unverified and retains the parcel
         * camera framing without claiming that scan +X points north.
         */
        localXAxisAzimuthDeg: null,
        verification: Object.freeze({
          status: "unverified",
          scope: "interior-view-only",
          note: "Den scan available for viewing; true-north alignment is unverified",
        }),
        cameraAnchor: Object.freeze({
          heightMode: "surface",
          sampleWidthM: 2,
          fallbackHeightM: null,
          fallbackProvenance: "unresolved",
        }),
      }),
      provenance: Object.freeze({
        method: "bc-address-geocoder",
        confirmedAt: "2026-09-03",
        source: "DataBC Physical Address Geocoder",
        sourcePrecision: "CIVIC_NUMBER",
        sourceScore: 97,
        note: "Exact civic address point independently rechecked against the provincial geocoder; Den scan frame available for viewing, directional registration unverified",
      }),
    }),
  ];

  var DEFAULT_SITE_ID = "echo";

  function readStorage(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function writeStorage(key, value) {
    try {
      window.localStorage.setItem(key, value);
      return true;
    } catch (error) {
      return false;
    }
  }

  function siteById(id) {
    for (var index = 0; index < SITES.length; index += 1) {
      if (SITES[index].id === id) return siteWithContent(SITES[index]);
    }
    return null;
  }

  function siteWithContent(site) {
    var packs = window.HomeContentPacks;
    if (!packs) return site;
    var pack = packs.get(site.id);
    var compatible = pack && pack.manifest.registrationVersion === site.sceneRegistration.version;
    var simulation = false;
    try { simulation = new URLSearchParams(window.location.search || '').get('simulation') === '1'; } catch (_) {}
    return Object.freeze(Object.assign({}, site, {
      heroStills: Object.freeze(compatible ? packs.heroStills(site.id) : []),
      contentRevision: compatible ? pack.manifest.revision : null,
      contentStatus: compatible ? pack.manifest.review.status : (pack ? 'registration-mismatch' : 'missing'),
      contentAttributions: Object.freeze(compatible ? (pack.manifest.attributions || []).slice() : []),
      sceneAvailable: compatible ? pack.manifest.sceneAvailable : (simulation && site.id === 'echo'),
    }));
  }

  function validCoordinate(point) {
    return !!point
      && typeof point.latitude === "number"
      && typeof point.longitude === "number"
      && isFinite(point.latitude)
      && isFinite(point.longitude)
      && point.latitude >= -90 && point.latitude <= 90
      && point.longitude >= -180 && point.longitude <= 180;
  }

  /* Restored selection is validated, never trusted: a stale or hand-edited key
   * naming a site that no longer exists must not strand the viewport. */
  var selectedSiteId = (function restoreSelection() {
    var stored = readStorage(SELECTED_SITE_STORAGE_KEY);
    return stored && siteById(stored) ? stored : DEFAULT_SITE_ID;
  })();

  var listeners = [];

  function notify() {
    var snapshot = listeners.slice();
    for (var index = 0; index < snapshot.length; index += 1) {
      try {
        snapshot[index](selectedSiteId);
      } catch (error) {
        // A broken subscriber must not take the viewport down with it.
      }
    }
  }

  /*
   * The shape home-apartment.jsx already validates: it requires
   * `confirmed === true`, finite in-range coordinates, and a finite altitude,
   * and returns null on anything else.
   */
  function anchorForSite(site) {
    if (!site || !validCoordinate(site.visualAnchor)) return null;
    var registration = site.sceneRegistration || {};
    var cameraAnchor = registration.cameraAnchor || {};
    var fallback = cameraAnchor.fallbackHeightM;
    return Object.freeze({
      siteId: site.id,
      latitude: site.visualAnchor.latitude,
      longitude: site.visualAnchor.longitude,
      // Ellipsoid zero until the surface resolves a real height. The camera is
      // posed provisionally and corrected once the provider answers.
      altitudeMeters: typeof fallback === "number" && isFinite(fallback) ? fallback : 0,
      heightMode: cameraAnchor.heightMode === "surface" ? "surface" : "fixed",
      heightProvenance: cameraAnchor.fallbackProvenance || "unresolved",
      sampleWidthM: typeof cameraAnchor.sampleWidthM === "number" ? cameraAnchor.sampleWidthM : 2,
      registrationVersion: typeof registration.version === "number" ? registration.version : 0,
      // The single yaw that maps the scan's local frame to true north. The
      // world camera needs it to convert the live Apartment basis into a
      // heading, so it travels with the anchor rather than being looked up
      // separately and drifting out of step with it.
      localXAxisAzimuthDeg: typeof registration.localXAxisAzimuthDeg === "number"
        ? registration.localXAxisAzimuthDeg
        : null,
      // And the chosen sight line, for a home with no scan to register. Carried
      // for exactly the reason the yaw above is: a field this narrowing forgets
      // is not an error anywhere, it is just a quietly different branch -- here,
      // a home that arrives facing whatever the band happened to leave it at.
      parcelViewHeadingDeg: typeof site.parcelViewHeadingDeg === "number"
        ? site.parcelViewHeadingDeg
        : null,
      // Carried for the same reason as the two bearings above: a field this
      // narrowing forgets is a silently different branch, not an error. Here it
      // is the difference between the world opening on the home and opening on
      // the raw mesh.
      heroStills: Array.isArray(site.heroStills) ? site.heroStills : [],
      contentRevision: site.contentRevision || null,
      contentAttributions: site.contentAttributions || [],
      parcelRangeM: typeof site.parcelRangeM === "number" ? site.parcelRangeM : 180,
      sceneAvailable: site.sceneAvailable === true,
      photorealCoverage: site.photorealCoverage || "unconfirmed",
      source: "confirmed-home-site",
      confirmed: true,
      synthetic: false,
    });
  }

  /*
   * Feature flag, following the repository's existing precedent: URL parameter,
   * then localStorage, then off. Default off means the Apartment tab behaves
   * exactly as it does today until someone opts in, which is what makes this
   * whole feature revertible by doing nothing.
   */
  function isWorldZoomEnabled() {
    var raw = "";
    try {
      raw = new URLSearchParams(window.location.search || "").get("worldZoom") || "";
    } catch (error) {
      raw = "";
    }
    raw = String(raw || "").trim().toLowerCase();
    if (["0", "off", "false", "no"].indexOf(raw) >= 0) return false;
    if (["1", "on", "true", "yes"].indexOf(raw) >= 0) return true;

    raw = String(readStorage(WORLD_ZOOM_STORAGE_KEY) || "").trim().toLowerCase();
    if (["0", "off", "false", "no"].indexOf(raw) >= 0) return false;
    if (["1", "on", "true", "yes"].indexOf(raw) >= 0) return true;
    return false;
  }

  function setWorldZoomEnabled(enabled) {
    var next = enabled ? "on" : "off";
    writeStorage(WORLD_ZOOM_STORAGE_KEY, next);
    return next === "on";
  }

  window.HomeSpatialViewport = {
    SELECTED_SITE_STORAGE_KEY: SELECTED_SITE_STORAGE_KEY,
    WORLD_ZOOM_STORAGE_KEY: WORLD_ZOOM_STORAGE_KEY,

    getSites: function getSites() {
      return SITES.map(siteWithContent);
    },

    getSite: function getSite(id) {
      return siteById(id);
    },

    getSelectedSiteId: function getSelectedSiteId() {
      return selectedSiteId;
    },

    getSelectedSite: function getSelectedSite() {
      return siteById(selectedSiteId);
    },

    getSelectedAnchor: function getSelectedAnchor() {
      return anchorForSite(siteById(selectedSiteId));
    },

    getAnchorForSite: function getAnchorForSite(id) {
      return anchorForSite(siteById(id));
    },

    /* Latest intent wins and the write is idempotent. Returns the id actually
     * selected so a caller can tell a refused change from an accepted one. */
    selectSite: function selectSite(id) {
      if (!siteById(id) || id === selectedSiteId) return selectedSiteId;
      selectedSiteId = id;
      writeStorage(SELECTED_SITE_STORAGE_KEY, id);
      notify();
      return selectedSiteId;
    },

    subscribe: function subscribe(listener) {
      if (typeof listener !== "function") return function noop() {};
      listeners.push(listener);
      return function unsubscribe() {
        var index = listeners.indexOf(listener);
        if (index >= 0) listeners.splice(index, 1);
      };
    },

    isWorldZoomEnabled: isWorldZoomEnabled,
    setWorldZoomEnabled: setWorldZoomEnabled,
  };
  if (window.HomeContentPacks) window.HomeContentPacks.subscribe(notify);
}());
