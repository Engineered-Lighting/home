/* home-3d/assets.js — asset URL resolution + manifest.
 *
 * Heavy runtime assets live OUTSIDE frontendDist (Tauri would embed them into
 * the MSI): C:\Claude\home\app\data\apartment, served via the Tauri asset
 * protocol. Small sim fixtures are bundled at assets/apartment/. Resolution
 * order per asset:
 *   1. localStorage override base (apartment3d.assetsBase — e.g. a dev http server)
 *   2. Tauri asset protocol path to app/data/apartment (real app, full assets)
 *   3. bundled sim fixture (browser preview / sim mode / fallback)
 */

const DATA_DIR = 'C:/Claude/home/app/data/apartment';

function tauriConvert(name) {
    try {
        const conv = window.__TAURI__?.core?.convertFileSrc;
        if (conv) return conv(`${DATA_DIR}/${name}`);
    } catch (e) { /* */ }
    // This app ships WITHOUT withGlobalTauri (window.__TAURI__ is undefined in
    // production), but the asset protocol is still registered via
    // tauri.conf.json — its URL shape is deterministic, so build it by hand.
    // In a plain browser this candidate just 404s and the chain falls through.
    return `http://asset.localhost/${encodeURIComponent(`${DATA_DIR}/${name}`)}`;
}

function overrideBase() {
    try { return localStorage.getItem('apartment3d.assetsBase') || null; } catch (e) { return null; }
}

/* Ordered candidate URLs for an asset; `simName` is the bundled fallback. */
export function candidates(name, simName, { sim = false } = {}) {
    const list = [];
    if (!sim) {
        const base = overrideBase();
        if (base) list.push(`${base.replace(/\/+$/, '')}/${name}`);
        const t = tauriConvert(name);
        if (t) list.push(t);
    }
    if (simName) list.push(`assets/apartment/${simName}`);
    return list;
}

export async function fetchManifest({ sim = false } = {}) {
    for (const url of candidates('manifest.json', 'manifest.json', { sim })) {
        try {
            const r = await fetch(`${url}?cb=${Date.now()}`);
            if (r.ok) return { manifest: await r.json(), base: url.replace(/manifest\.json.*$/, '') };
        } catch (e) { /* next */ }
    }
    return { manifest: null, base: null };
}

/* Resolve the points PLY: full cloud when reachable, sim fixture otherwise. */
export function pointsCandidates({ sim = false } = {}) {
    return candidates('points.ply', 'points.sim.ply', { sim });
}

/* frame.json — the scan->apartment registration (T_splat poses the SplatMesh). */
export async function fetchFrame({ sim = false } = {}) {
    for (const url of candidates('frame.json', 'frame.json', { sim })) {
        try {
            const r = await fetch(`${url}?cb=${Date.now()}`);
            if (r.ok) return await r.json();
        } catch (e) { /* next */ }
    }
    return null;
}
