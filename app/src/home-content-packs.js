/* Local pack resolver shared by classic UI and the ESM renderers.
 * Native import opens a directory picker in Rust; callers cannot supply paths.
 * In development the read-only runtime asset service exposes verified packs.
 */
(function initialiseHomeContentPacks() {
  'use strict';
  var descriptors = [];
  var listeners = [];
  var refreshGeneration = 0;
  var mutation = Promise.resolve();
  var native = !!(window.__TAURI_INTERNALS__ || window.IS_TAURI || window.__TAURI__);
  var idPattern = /^[a-z0-9][a-z0-9-]{0,63}$/;
  var namePattern = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/;
  function validPath(value) {
    return typeof value === 'string' && value.length <= 240 && /\.(json|jpe?g|png|webp|glb|ply|spz)$/i.test(value)
      && value.split('/').every(function (part) {
        return namePattern.test(part) && !part.endsWith('.') && !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(part);
      });
  }
  function deepFreeze(value) {
    if (!value || typeof value !== 'object' || Object.isFrozen(value)) return value;
    Object.keys(value).forEach(function (key) { deepFreeze(value[key]); });
    return Object.freeze(value);
  }
  function invoke(command, args) {
    var fn = window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke;
    if (!fn && window.__TAURI_INTERNALS__) fn = window.__TAURI_INTERNALS__.invoke;
    return fn ? fn(command, args || {}) : Promise.reject(new Error('content_pack_native_required'));
  }
  function normalize(input, serviceBase) {
    var item = JSON.parse(JSON.stringify(input));
    var manifest = item.manifest;
    if (!manifest || manifest.schemaVersion !== 1 || !idPattern.test(manifest.siteId) || !idPattern.test(manifest.revision)
      || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(manifest.siteId)
      || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(manifest.revision)
      || !Number.isInteger(manifest.registrationVersion) || manifest.registrationVersion < 0 || manifest.registrationVersion > 4294967295
      || typeof manifest.sceneAvailable !== 'boolean' || !manifest.assets || !Array.isArray(manifest.heroStills)) throw new Error('content_pack_manifest_invalid');
    var entries = Object.entries(manifest.assets);
    if (!entries.length || entries.length > 128) throw new Error('content_pack_manifest_invalid');
    var paths = new Set();
    var totalBytes = 0;
    entries.forEach(function (entry) {
      var asset = entry[1];
      if (!namePattern.test(entry[0]) || !asset || !validPath(asset.path) || paths.has(asset.path.toLowerCase())
        || !Number.isSafeInteger(asset.bytes) || asset.bytes <= 0 || asset.bytes > 2 * 1024 ** 3
        || !/^[a-f0-9]{64}$/.test(asset.sha256)) throw new Error('content_pack_manifest_invalid');
      paths.add(asset.path.toLowerCase()); totalBytes += asset.bytes;
    });
    if (totalBytes > 8 * 1024 ** 3 || manifest.heroStills.length > 16) throw new Error('content_pack_manifest_invalid');
    manifest.heroStills.forEach(function (hero) {
      var projection = hero && hero.projection;
      if (!hero || !Object.prototype.hasOwnProperty.call(manifest.assets, hero.asset)
        || !/\.(jpe?g|png|webp)$/i.test(manifest.assets[hero.asset].path) || !Number.isFinite(hero.rangeM) || hero.rangeM <= 0
        || !Number.isInteger(hero.widthPx) || hero.widthPx < 1 || hero.widthPx > 32768
        || !Number.isInteger(hero.heightPx) || hero.heightPx < 1 || hero.heightPx > 32768
        || !Number.isFinite(hero.headingDeg) || hero.headingDeg < 0 || hero.headingDeg >= 360
        || !Number.isFinite(hero.pitchDeg) || hero.pitchDeg < -90 || hero.pitchDeg > 0
        || !projection || projection.type !== 'perspective' || !Number.isFinite(projection.aspect)
        || Math.abs(projection.aspect - hero.widthPx / hero.heightPx) > 0.001
        || typeof projection.verified !== 'boolean'
        || (projection.fovDeg !== null && (!Number.isFinite(projection.fovDeg) || projection.fovDeg <= 0 || projection.fovDeg >= 180))
        || [null, 'horizontal', 'vertical'].indexOf(projection.fovAxis) < 0
        || (projection.verified && (projection.fovDeg === null || projection.fovAxis === null))) throw new Error('content_pack_manifest_invalid');
    });
    if (manifest.sceneAvailable && (!manifest.assets['frame.json'] || !manifest.assets['points.ply']
      || !(manifest.assets['apartment.ply'] || manifest.assets['apartment.spz']))) throw new Error('content_pack_manifest_invalid');
    if (!manifest.review || ['research-only', 'approved'].indexOf(manifest.review.status) < 0) throw new Error('content_pack_manifest_invalid');
    if (manifest.review.attributionReviewed !== undefined && typeof manifest.review.attributionReviewed !== 'boolean') throw new Error('content_pack_manifest_invalid');
    if (manifest.attributions !== undefined && (!Array.isArray(manifest.attributions) || manifest.attributions.length > 8)) throw new Error('content_pack_manifest_invalid');
    (manifest.attributions || []).forEach(function (credit) {
      if (!credit || typeof credit.text !== 'string' || !credit.text.trim() || credit.text.length > 512
        || /[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/.test(credit.text)
        || Object.keys(credit).some(function (key) { return ['text', 'url'].indexOf(key) < 0; })) throw new Error('content_pack_manifest_invalid');
      if (credit.url !== undefined && credit.url !== null) {
        if (typeof credit.url !== 'string' || new TextEncoder().encode(credit.url).length > 2048
          || /[\u0000-\u0020\u007f-\u009f]/.test(credit.url)) throw new Error('content_pack_manifest_invalid');
        var creditUrl = new URL(credit.url);
        if (creditUrl.protocol !== 'https:' || !creditUrl.hostname || creditUrl.username || creditUrl.password) throw new Error('content_pack_manifest_invalid');
      }
    });
    if (native) {
      var convert = window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.convertFileSrc;
      if (!convert || typeof item.directory !== 'string') throw new Error('content_pack_native_required');
      item.baseUrl = convert(item.directory.replace(/[\\/]+$/, '') + '/');
    } else {
      var base = new URL(item.baseUrl, serviceBase);
      if (base.origin !== new URL(serviceBase).origin
        || base.pathname !== '/content-packs/' + manifest.siteId + '/' + manifest.revision + '/'
        || base.search || base.hash || base.username || base.password) throw new Error('content_pack_url_invalid');
      item.baseUrl = base.href;
    }
    delete item.directory;
    item.active = item.active === true;
    return deepFreeze(item);
  }
  function notify() { listeners.slice().forEach(function (fn) { try { fn(descriptors.slice()); } catch (_) {} }); }
  function install(items, serviceBase) {
    if (!Array.isArray(items) || items.length > 512) throw new Error('content_pack_index_invalid');
    var next = items.map(function (item) { return normalize(item, serviceBase); });
    var active = new Set();
    var revisions = new Set();
    next.forEach(function (item) {
      var identity = item.manifest.siteId + '/' + item.manifest.revision;
      if (revisions.has(identity)) throw new Error('content_pack_index_invalid');
      revisions.add(identity);
      if (item.active && active.has(item.manifest.siteId)) throw new Error('content_pack_index_invalid');
      if (item.active) active.add(item.manifest.siteId);
    });
    descriptors = Object.freeze(next); notify(); return descriptors.slice();
  }
  function serviceBases() {
    var bases = [window.location.origin];
    if (['localhost', '127.0.0.1', '::1'].indexOf(window.location.hostname) >= 0) bases.push('http://127.0.0.1:5190');
    return bases;
  }
  async function refresh() {
    var generation = ++refreshGeneration;
    if (native) {
      var items = await invoke('content_pack_list');
      return generation === refreshGeneration ? install(items) : descriptors.slice();
    }
    for (var base of serviceBases()) {
      var controller = new AbortController();
      var timeout = setTimeout(function () { controller.abort(); }, 2000);
      try {
        var response = await fetch(base + '/content-packs/index.json', { cache: 'no-store', credentials: 'same-origin', signal: controller.signal });
        if (!response.ok) continue;
        var index = await response.json();
        if (index.schemaVersion !== 1) continue;
        if (generation === refreshGeneration) return install(index.packs, base);
        return descriptors.slice();
      } catch (_) { /* Try the local companion without dropping a valid pack. */ }
      finally { clearTimeout(timeout); }
    }
    return descriptors.slice();
  }
  function get(siteId, revision) {
    if (revision === null) return null; // An engine may pin an absent generation.
    return descriptors.find(function (item) {
      return item.manifest.siteId === siteId && (revision === undefined ? item.active : item.manifest.revision === revision);
    }) || null;
  }
  function resolve(siteId, name, revision) {
    var item = get(siteId, revision);
    var asset = item && Object.prototype.hasOwnProperty.call(item.manifest.assets, name) && item.manifest.assets[name];
    if (!asset) return null;
    // convertFileSrc percent-encodes the entire Windows path; append before
    // conversion would require exposing a path. URL-decoding the asset prefix
    // is unnecessary: encode separators using the same protocol convention.
    var nativeSuffix = native ? encodeURIComponent(asset.path) : asset.path.split('/').map(encodeURIComponent).join('/');
    return item.baseUrl + nativeSuffix;
  }
  function heroStills(siteId, revision) {
    var item = get(siteId, revision); if (!item) return [];
    return item.manifest.heroStills.map(function (hero) {
      return Object.freeze(Object.assign({}, hero, {
        url: resolve(siteId, hero.asset, item.manifest.revision), siteId: siteId,
        registrationVersion: item.manifest.registrationVersion, contentRevision: item.manifest.revision,
        aspect: hero.projection.aspect, fovDeg: hero.projection.fovDeg, fovAxis: hero.projection.fovAxis,
      }));
    });
  }
  function mutate(command, args) {
    var next = mutation.then(async function () {
      ++refreshGeneration;
      var items = await invoke(command, args);
      ++refreshGeneration; // A list read started during an import is obsolete.
      return install(items);
    });
    mutation = next.catch(function () {});
    return next;
  }
  window.HomeContentPacks = {
    isNative: native, refresh: refresh, get: get, resolve: resolve, heroStills: heroStills,
    list: function () { return descriptors.slice(); },
    pickAndImport: function () { return mutate('content_pack_import'); },
    activate: async function (siteId, revision) {
      if (!idPattern.test(siteId) || !idPattern.test(revision)) throw new Error('content_pack_identifier_invalid');
      return mutate('content_pack_activate', { siteId: siteId, revision: revision });
    },
    subscribe: function (fn) { listeners.push(fn); return function () { listeners = listeners.filter(function (item) { return item !== fn; }); }; },
  };
  window.HomeContentPacks.ready = refresh().catch(function () { return []; });
}());
