/* home-3d/gaussian-splat-worker.mjs — parses one splat file off the main thread.
 *
 * Module worker for gaussian-splat-loader.mjs. Receives { id, bytes } with
 * the file's ArrayBuffer transferred in, answers { id, data } with every
 * array transferred back, or { id, error }. SPZ is gunzipped here with the
 * platform DecompressionStream.
 */
// The handler is installed before the parser module resolves, so a message
// posted while the import is in flight is never dropped.
const parser = import(`./gaussian-splat-parse.mjs${new URL(import.meta.url).search}`);

async function gunzip(bytes) {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
}

self.onmessage = async ({ data: message }) => {
    const { id, bytes } = message || {};
    try {
        const { parseSplatFile, splatTransferables } = await parser;
        const started = performance.now();
        const data = await parseSplatFile(bytes, { gunzip });
        data.parseMs = performance.now() - started;
        self.postMessage({ id, data }, splatTransferables(data));
    } catch (error) {
        self.postMessage({ id, error: String(error?.message || error) });
    }
};
