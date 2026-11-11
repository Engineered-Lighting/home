/* home-3d/point-features-worker.mjs — runs point-features.mjs off the main thread.
 * In: { positions: Float32Array, intensity: Uint8Array, count }. Out: the features, transferred. */
const features = import(`./point-features.mjs${self.location.search}`);

self.onmessage = async (event) => {
    try {
        const { computePointFeatures } = await features;
        const { positions, intensity, count } = event.data || {};
        const t0 = performance.now();
        const out = computePointFeatures(positions, intensity, count);
        out.ms = Math.round(performance.now() - t0);
        self.postMessage({ ok: true, ...out }, [out.normal.buffer, out.edge.buffer, out.spacing.buffer]);
    } catch (error) {
        self.postMessage({ ok: false, error: String(error?.message || error) });
    }
};
