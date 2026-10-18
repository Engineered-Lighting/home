/* App-lifetime bytes for immutable, scoped scan packs. This is deliberately
 * separate from decoded/GPU ownership and never writes browser persistence. */
export const APARTMENT_ASSET_CACHE_MAX_BYTES = 160 * 1024 * 1024;
export const APARTMENT_ASSET_CACHE_MAX_ENTRY_BYTES = 96 * 1024 * 1024;
const abortError = () => new DOMException('apartment_asset_read_aborted', 'AbortError');
const id = value => typeof value === 'string' && /^[a-z0-9][a-z0-9-]{0,63}$/.test(value);

export function createAssetByteCache({ fetchImpl = (...args) => fetch(...args),
    baseURI = globalThis.document?.baseURI || globalThis.location?.href || 'http://localhost/',
    maxBytes = APARTMENT_ASSET_CACHE_MAX_BYTES, maxEntryBytes = APARTMENT_ASSET_CACHE_MAX_ENTRY_BYTES,
    maxEntries = 8, env = null } = {}) {
    if (typeof fetchImpl !== 'function' || !Number.isSafeInteger(maxBytes) || maxBytes < 1
        || !Number.isSafeInteger(maxEntryBytes) || maxEntryBytes < 1 || maxEntryBytes > maxBytes
        || !Number.isSafeInteger(maxEntries) || maxEntries < 1) throw Error('asset_cache_options_invalid');
    const base = new URL(baseURI), entries = new Map(), pending = new Map(), jobs = new Set(), capacity = new Set();
    let residentBytes = 0, reservedBytes = 0, disposed = false, lastClearReason = null;
    const counts = { hits: 0, misses: 0, coalesced: 0, networkRequests: 0, evictions: 0 };
    const cleanup = [];
    const live = job => !disposed && !job.controller.signal.aborted && job.waiters.size > 0;
    function wake() { for (const callback of [...capacity]) callback(); }
    function evict(key) {
        const entry = entries.get(key);
        if (!entry) return;
        entries.delete(key); residentBytes -= entry.byteLength; counts.evictions++;
    }
    function touch(key, bytes) { entries.delete(key); entries.set(key, bytes); }
    function classify({ url, siteId, contentRevision }) {
        const parsed = new URL(url, base);
        if (!parsed.pathname.startsWith('/content-packs/')) return { url: String(url), key: null };
        const segments = parsed.pathname.split('/');
        if (!id(siteId) || !id(contentRevision) || segments[2] !== siteId || segments[3] !== contentRevision
            || parsed.origin !== base.origin || !['http:', 'https:'].includes(parsed.protocol)
            || parsed.search || parsed.hash || parsed.username || parsed.password
            || segments.length < 5 || !segments.slice(4).every(part => /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(part))) {
            throw Error('asset_cache_scope_mismatch');
        }
        // JSON remains fresh metadata, as in the existing pack gateway policy.
        const cacheable = /\.(?:ply|spz|glb)$/i.test(parsed.pathname);
        return { url: parsed.href, key: cacheable ? JSON.stringify([siteId, contentRevision, parsed.href]) : null };
    }
    function releaseReservation(job) {
        reservedBytes -= job.reserved; job.reserved = 0; job.chunks.length = 0;
    }
    function cancelJob(job) {
        if (!job.controller.signal.aborted) job.controller.abort();
        if (pending.get(job.key) === job) pending.delete(job.key);
        releaseReservation(job); wake();
    }
    function reserve(job, bytes) {
        return new Promise((resolve, reject) => {
            const signal = job.controller.signal;
            const done = () => { capacity.delete(attempt); signal.removeEventListener('abort', abort); };
            const abort = () => { done(); reject(abortError()); };
            const attempt = () => {
                if (!live(job)) return abort();
                while (residentBytes + reservedBytes + bytes > maxBytes && entries.size) evict(entries.keys().next().value);
                if (residentBytes + reservedBytes + bytes > maxBytes) return;
                reservedBytes += bytes; job.reserved = bytes; done(); resolve();
            };
            signal.addEventListener('abort', abort, { once: true }); capacity.add(attempt); attempt();
        });
    }
    async function readPack(job, response) {
        const rawLength = response.headers?.get?.('Content-Length');
        const length = rawLength == null || rawLength === '' ? null : Number(rawLength);
        if (length !== null && (!Number.isSafeInteger(length) || length < 1 || length > maxEntryBytes)
            || job.expectedBytes != null && length !== null && length !== job.expectedBytes) throw Error('asset_body_length_invalid');
        const limit = job.expectedBytes ?? length ?? maxEntryBytes;
        await reserve(job, limit);
        if (!live(job)) throw abortError();
        const reader = response.body?.getReader?.();
        if (!reader) throw Error('asset_body_stream_required');
        let size = 0, chunks = 0, complete = false;
        const cancel = () => { try { Promise.resolve(reader.cancel()).catch(() => {}); } catch (_) {} };
        job.controller.signal.addEventListener('abort', cancel, { once: true });
        try {
            for (;;) {
                if (!live(job)) throw abortError();
                const result = await reader.read();
                if (!live(job)) throw abortError();
                if (result.done) break;
                const value = result.value;
                if (++chunks > 65536 || !(value instanceof Uint8Array) || !Number.isSafeInteger(size + value.byteLength)
                    || size + value.byteLength > limit) throw Error('asset_body_bound_exceeded');
                job.chunks.push(value); size += value.byteLength;
            }
            if (size === 0 || length !== null && size !== length || job.expectedBytes != null && size !== job.expectedBytes) {
                throw Error('asset_body_length_mismatch');
            }
            const bytes = new Uint8Array(size);
            let offset = 0;
            for (const chunk of job.chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
            job.chunks.length = 0; complete = true;
            return bytes.buffer;
        } finally {
            job.controller.signal.removeEventListener('abort', cancel);
            if (!complete) cancel();
            try { reader.releaseLock(); } catch (_) {}
        }
    }
    function finish(job, error, bytes) {
        if (pending.get(job.key) === job) pending.delete(job.key);
        releaseReservation(job); jobs.delete(job);
        if (!error && !live(job)) error = abortError();
        if (!error && job.key) {
            while (entries.size >= maxEntries) evict(entries.keys().next().value);
            touch(job.key, bytes); residentBytes += bytes.byteLength;
        }
        for (const waiter of [...job.waiters]) {
            job.waiters.delete(waiter); waiter.cleanup();
            if (error) waiter.reject(error);
            else if (waiter.expectedBytes !== null && waiter.expectedBytes !== bytes.byteLength) waiter.reject(Error('asset_body_length_mismatch'));
            else if (waiter.warm) waiter.resolve({ url: job.url, bytes: bytes.byteLength, cacheHit: false });
            else try { waiter.resolve(bytes.slice(0)); } catch (error) { waiter.reject(error); }
        }
        wake();
    }
    async function execute(job) {
        let response;
        try {
            if (!live(job)) throw abortError();
            counts.networkRequests++;
            response = await fetchImpl(job.url, { cache: 'force-cache', credentials: 'same-origin', signal: job.controller.signal });
            if (!live(job)) throw abortError();
            if (!response.ok) throw Error('asset_http_' + response.status);
            // Preserve direct legacy behavior; no legacy bytes are retained.
            const bytes = job.key ? await readPack(job, response) : await response.arrayBuffer();
            if (!live(job)) throw abortError();
            finish(job, null, bytes);
        } catch (error) {
            try { Promise.resolve(response?.body?.cancel?.()).catch(() => {}); } catch (_) {}
            finish(job, error);
        }
    }
    function request(args, warm) {
        if (disposed) return Promise.reject(Error('asset_cache_disposed'));
        if (args.signal?.aborted) return Promise.reject(abortError());
        const scoped = classify(args);
        const expectedBytes = args.expectedBytes ?? null;
        if (scoped.key && expectedBytes !== null && (!Number.isSafeInteger(expectedBytes)
            || expectedBytes < 1 || expectedBytes > maxEntryBytes)) return Promise.reject(Error('asset_body_length_invalid'));
        const cached = scoped.key && entries.get(scoped.key);
        if (cached) {
            if (expectedBytes !== null && cached.byteLength !== expectedBytes) return Promise.reject(Error('asset_body_length_mismatch'));
            counts.hits++; touch(scoped.key, cached);
            return Promise.resolve(warm ? { url: scoped.url, bytes: cached.byteLength, cacheHit: true } : cached.slice(0));
        }
        let job = scoped.key && pending.get(scoped.key);
        if (job && expectedBytes !== null && job.expectedBytes !== null && job.expectedBytes !== expectedBytes) {
            return Promise.reject(Error('asset_body_length_mismatch'));
        }
        const fresh = !job;
        if (!job) {
            if (scoped.key && pending.size >= maxEntries) return Promise.reject(Error('asset_cache_busy'));
            job = { ...scoped, expectedBytes, controller: new AbortController(), waiters: new Set(), reserved: 0, chunks: [] };
            counts.misses++; jobs.add(job); if (job.key) pending.set(job.key, job);
        } else counts.coalesced++;
        const result = new Promise((resolve, reject) => {
            const signal = args.signal;
            const waiter = { resolve, reject, warm, expectedBytes, cleanup: () => signal?.removeEventListener('abort', abort) };
            const abort = () => {
                if (!job.waiters.delete(waiter)) return;
                waiter.cleanup(); reject(abortError()); if (!job.waiters.size) cancelJob(job);
            };
            job.waiters.add(waiter); signal?.addEventListener('abort', abort, { once: true });
            if (signal?.aborted) abort();
        });
        if (fresh) Promise.resolve().then(() => execute(job));
        return result;
    }
    function clear(reason = 'explicit') {
        lastClearReason = String(reason).slice(0, 80);
        entries.clear(); residentBytes = 0;
        for (const job of [...jobs]) {
            cancelJob(job);
            for (const waiter of [...job.waiters]) { job.waiters.delete(waiter); waiter.cleanup(); waiter.reject(abortError()); }
        }
        wake();
    }
    function listen(target, event, callback, options) {
        if (!target?.addEventListener) return;
        target.addEventListener(event, callback, options);
        cleanup.push(() => target.removeEventListener(event, callback, options));
    }
    listen(env, 'pagehide', () => clear('pagehide'));
    listen(env, 'home:logout', () => clear('owner_logout'));
    listen(env?.document, 'click', event => { if (event.target?.closest?.('#logout')) clear('owner_logout'); }, true);
    return Object.freeze({
        async fetch(args) { return request(args, false); },
        async warm(args) { return request(args, true); },
        clear,
        diagnostics: () => ({ ...counts, entries: entries.size, cachedBytes: residentBytes, reservedBytes,
            pending: pending.size, waiters: [...jobs].reduce((sum, job) => sum + job.waiters.size, 0),
            maxBytes, maxEntryBytes, maxEntries, maxPending: maxEntries, disposed, lastClearReason }),
        dispose() { clear('disposed'); disposed = true; for (const off of cleanup.splice(0)) off(); },
    });
}

const shared = createAssetByteCache({ env: globalThis.window || null });
export const fetchApartmentAssetBytes = args => shared.fetch(args);
export const warmApartmentAssetBytes = args => shared.warm(args);
export const clearApartmentAssetByteCache = reason => shared.clear(reason);
export const apartmentAssetByteCacheDiagnostics = () => shared.diagnostics();
