/* home-3d/gaussian-splat-modes.mjs — Photo on the node renderer: modes.js load adapters.
 *
 * WebGPU path only (?renderer=webgpu, or ?renderer=webgpu-webgl). The
 * default WebGL engine keeps Spark; webgpu-renderer.mjs hands these adapters
 * to createModes() instead of Spark's:
 *
 *   loadSplat           the pack's bytes through the shared asset cache, parsed
 *                       in a worker (never the main thread), into a
 *                       HomeGaussianSplat with the cutaway node compiled in;
 *   createSplatCutaway  the TSL twin of the Spark modifier, same update() API;
 *   createSparkRenderer null: there is no Spark renderer on this path.
 *
 * modes.js's splat tick then drives HomeGaussianSplat.update() each frame.
 */
const cb = new URL(import.meta.url).search;
let modules = null;
const load = () => (modules ??= Promise.all([
    import(`./gaussian-splat-webgpu.mjs${cb}`),
    import(`./gaussian-splat-loader.mjs${cb}`),
    import(`./splat-cutaway-tsl.mjs${cb}`),
    import(`./asset-byte-cache.mjs${cb}`),
]));

function abortError() {
    const error = new Error('scene_load_aborted');
    error.name = 'AbortError';
    return error;
}

/** Budget knobs (adaptive-performance.mjs) with safe defaults. */
export function splatBudget(budget) {
    const keyBits = budget?.splatSortKeyBits === 32 ? 32 : 16;
    const degree = Number.isInteger(budget?.splatMaxShDegree) ? Math.max(0, Math.min(3, budget.splatMaxShDegree)) : 3;
    return { keyBits, maxShDegree: degree };
}

export function createSplatModeAdapters({ renderer }) {
    return Object.freeze({
        async loadSplat({ url, signal, siteId = null, contentRevision = null, budget = null }) {
            if (signal?.aborted) throw abortError();
            const [{ HomeGaussianSplat }, { parseSplatInWorker }, { createSplatCutawayNode }, { fetchApartmentAssetBytes }] = await load();
            if (signal?.aborted) throw abortError();
            const bytes = await fetchApartmentAssetBytes({ url, siteId, contentRevision, signal });
            if (signal?.aborted) throw abortError();
            const data = await parseSplatInWorker(bytes, { signal });
            if (signal?.aborted) throw abortError();
            const { keyBits, maxShDegree } = splatBudget(budget);
            const splat = new HomeGaussianSplat(data, { renderer, keyBits, maxShDegree, cutaway: createSplatCutawayNode() });
            splat.userData.source = { parseMs: Math.round(data.parseMs ?? 0), format: data.source };
            return splat;
        },
        async createSplatCutaway({ splat, dollhouseCutaway = false }) {
            if (!dollhouseCutaway || !splat?.isHomeGaussianSplat || !splat._cutaway) return null;
            const node = splat._cutaway;
            return {
                update(policy, matrix, exteriorBounds = null) {
                    splat.updateWorldMatrix(true, false);
                    return node.update(policy, matrix, exteriorBounds, splat.matrixWorld);
                },
                dispose() { /* the node lives and dies with the splat */ },
            };
        },
        createSparkRenderer: null,
    });
}
