/* home-3d/assets.js — asset URL resolution + manifest.
 *
 * Imported versioned packs live in app-managed local storage, outside the
 * installer. Development retains the legacy Echo asset service; simulation
 * uses small bundled Echo fixtures. Neither may stand in for another home.
 */

function packAsset(name, siteId, contentRevision) {
    return window.HomeContentPacks?.resolve?.(siteId, name, contentRevision) || null;
}

function assertFetchLive(signal) {
    if (signal?.aborted) throw new DOMException('scene_load_aborted', 'AbortError');
}

function overrideBase() {
    try {
        const resolved = window.HomeServices?.get?.('apartmentAssets');
        if (resolved) return String(resolved).replace(/\/+$/, '');
    } catch (e) { /* */ }
    try {
        if (!window.HomeServices) return localStorage.getItem('apartment3d.assetsBase') || null;
    } catch (e) { /* */ }
    return null;
}

function browserDevBase() {
    try {
        const loc = window.location;
        const loopback = loc && (loc.hostname === '127.0.0.1' || loc.hostname === 'localhost' || loc.hostname === '::1');
        // The static dev server cannot expose app/data/apartment. Prefer the
        // companion runtime asset service even when home-web-runtime has set
        // HG_WEB_MODE; otherwise /assets/apartment resolves to the tiny
        // bundled simulation fixtures and exact target dragging has no mesh.
        if (loopback && (loc.port === '5180' || loc.port === '5173')) return 'http://127.0.0.1:5190';
        if (window.HG_WEB_MODE && window.HG_DEFAULT_APARTMENT_ASSET_BASE) {
            return String(window.HG_DEFAULT_APARTMENT_ASSET_BASE).replace(/\/+$/, '');
        }
        if (!window.IS_TAURI && !window.__TAURI__) return 'assets/apartment';
    } catch (e) { /* */ }
    return null;
}

/* Ordered candidate URLs for an asset; `simName` is the bundled fallback.
 * In simulation, prefer bundled fixtures when they exist. Modes without a
 * bundled fixture (mesh/photo) still use the runtime asset service so preview
 * and audit paths can exercise the real scan/mesh instead of only the error UI.
 */
export function candidates(name, simName, { sim = false, siteId = null, contentRevision = undefined } = {}) {
    siteId = siteId || window.HomeSpatialViewport?.getSelectedSiteId?.() || 'echo';
    const list = [];
    if (!sim || !simName || siteId !== 'echo') {
        const pack = packAsset(name, siteId, contentRevision);
        if (pack) return [pack];
        // An imported pack is a complete generation. Never combine a missing
        // member with another home's legacy scan or bundled preview fixture.
        if (contentRevision != null || window.HomeContentPacks?.get?.(siteId, contentRevision)
            || (contentRevision === null && window.HomeContentPacks?.get?.(siteId))) return [];
        if (siteId !== 'echo' || window.__TAURI_INTERNALS__ || window.__TAURI__) return [];
        const base = overrideBase();
        if (base) list.push(`${base.replace(/\/+$/, '')}/${name}`);
        const devBase = browserDevBase();
        if (devBase) list.push(`${devBase}/${name}`);
    }
    if (simName) list.push(`assets/apartment/${simName}`);
    return list;
}

export async function fetchManifest({ sim = false, siteId = null, contentRevision = undefined, signal = undefined } = {}) {
    assertFetchLive(signal);
    await window.HomeContentPacks?.ready;
    assertFetchLive(signal);
    for (const url of candidates('manifest.json', 'manifest.json', { sim, siteId, contentRevision })) {
        try {
            const r = await fetch(`${url}?cb=${Date.now()}`, { signal });
            if (r.ok) return { manifest: await r.json(), base: url.replace(/manifest\.json.*$/, '') };
        } catch (e) { if (signal?.aborted || e?.name === 'AbortError') throw e; /* next */ }
    }
    return { manifest: null, base: null };
}

/* Resolve the points PLY: full cloud when reachable, sim fixture otherwise. */
export function pointsCandidates({ sim = false, siteId = null, contentRevision = undefined } = {}) {
    return candidates('points.ply', 'points.sim.ply', { sim, siteId, contentRevision });
}

/* frame.json — the scan->apartment registration (T_splat poses the SplatMesh). */
export async function fetchFrame({ sim = false, siteId = null, contentRevision = undefined, signal = undefined } = {}) {
    assertFetchLive(signal);
    await window.HomeContentPacks?.ready;
    assertFetchLive(signal);
    for (const url of candidates('frame.json', 'frame.json', { sim, siteId, contentRevision })) {
        try {
            const r = await fetch(`${url}?cb=${Date.now()}`, { signal });
            if (r.ok) return await r.json();
        } catch (e) { if (signal?.aborted || e?.name === 'AbortError') throw e; /* next */ }
    }
    return null;
}
