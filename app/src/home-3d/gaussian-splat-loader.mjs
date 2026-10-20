/* home-3d/gaussian-splat-loader.mjs — splat bytes to arrays, never on the main thread.
 *
 * One module worker per parse (gaussian-splat-worker.mjs). The caller's bytes
 * are transferred, so pass a buffer you own (asset-byte-cache.mjs hands out
 * copies). Aborting terminates the worker, which also frees whatever it had
 * allocated. A browser without module workers refuses rather than parsing on
 * the main thread: a 307k-splat PLY blocks it for about a second.
 */
const cb = new URL(import.meta.url).search;

function abortError() {
    const error = new Error('scene_load_aborted');
    error.name = 'AbortError';
    return error;
}

let nextId = 1;

export function parseSplatInWorker(bytes, { signal = null, WorkerClass = globalThis.Worker } = {}) {
    if (signal?.aborted) return Promise.reject(abortError());
    if (typeof WorkerClass !== 'function') return Promise.reject(new Error('splat_worker_unavailable'));
    let worker;
    try {
        worker = new WorkerClass(new URL(`./gaussian-splat-worker.mjs${cb}`, import.meta.url), { type: 'module', name: 'home-splat-parse' });
    } catch (_) {
        return Promise.reject(new Error('splat_worker_unavailable'));
    }
    const id = nextId++;
    return new Promise((resolve, reject) => {
        let settled = false;
        const finish = (error, data) => {
            if (settled) return;
            settled = true;
            signal?.removeEventListener?.('abort', onAbort);
            worker.terminate();
            if (error) reject(error); else resolve(data);
        };
        const onAbort = () => finish(abortError());
        signal?.addEventListener?.('abort', onAbort, { once: true });
        worker.onmessage = ({ data: message }) => {
            if (message?.id !== id) return;
            if (message.error) finish(new Error(message.error));
            else finish(null, message.data);
        };
        worker.onerror = (event) => {
            event?.preventDefault?.();
            finish(new Error(`splat_worker_failed: ${event?.message || 'error'}`));
        };
        const buffer = bytes instanceof ArrayBuffer ? bytes : bytes?.buffer;
        if (!(buffer instanceof ArrayBuffer)) { finish(new Error('splat_bytes_invalid')); return; }
        const payload = bytes instanceof ArrayBuffer ? bytes
            : (bytes.byteOffset === 0 && bytes.byteLength === buffer.byteLength ? buffer : buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
        worker.postMessage({ id, bytes: payload }, [payload]);
    });
}
