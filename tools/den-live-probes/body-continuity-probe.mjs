#!/usr/bin/env node
/* Read-only Den body continuity probe: times every hidden interval of the measured body while someone is tracked,
 * in a real Home page, and attributes each to a cause.
 *
 *   node tools/den-live-probes/body-continuity-probe.mjs --out-dir <dir> [--seconds 120] [--min-occupied-s 60]
 *     [--max-extend-s 120] [--wait-occupied-s 120] [--gpu] [--headed] [--phone] [--dpr N] [--long-tab-min M]
 *     [--no-latency-trace] [--origin URL] [--site victoria] [--label name] [--playwright <playwright index.mjs>]
 *
 * Read-only: only GET requests to the Home origin leave the page; any other method, cross-origin request, WebSocket,
 * media or camera path is aborted and counted, getUserMedia refuses, and the page gets no input (the probe never
 * clicks, types or taps, and a headed window's input is swallowed). Household media rule: the receipt holds
 * timestamps, statuses, counts and 32-bit id hashes only, never points, positions, images or camera data.
 * Each run writes one new receipt, body-continuity-<utc>-<label>-<random>.json, into --out-dir and never overwrites.
 * Exit status: 0 ok, 3 the occupancy gate was not met (receipt still written), 1 the probe failed. */
import { randomBytes } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { PROBE_SCHEMA, attributeWindow, createBodyContinuityCollector, installBodyContinuityProbe, packetRows, withheld } from './body-continuity-core.mjs';

const DEFAULTS = { origin: 'https://home-app.taild52a15.ts.net', site: 'victoria', seconds: 120, minOccupiedS: 60, maxExtendS: 120,
    waitOccupiedS: 120, mountTimeoutS: 45, longTabMin: 0, gpu: false, headed: false, phone: false, dpr: 1, latencyTrace: true,
    label: 'desktop', outDir: null, playwright: null };
const NUMBERS = { '--seconds': 'seconds', '--min-occupied-s': 'minOccupiedS', '--max-extend-s': 'maxExtendS',
    '--wait-occupied-s': 'waitOccupiedS', '--mount-timeout-s': 'mountTimeoutS', '--long-tab-min': 'longTabMin', '--dpr': 'dpr' };
const STRINGS = { '--origin': 'origin', '--site': 'site', '--label': 'label', '--out-dir': 'outDir', '--playwright': 'playwright' };
const FLAGS = { '--gpu': ['gpu', true], '--headed': ['headed', true], '--phone': ['phone', true], '--no-latency-trace': ['latencyTrace', false] };
const LIMITS = { seconds: [5, 7200], minOccupiedS: [0, 7200], maxExtendS: [0, 7200], waitOccupiedS: [0, 3600], mountTimeoutS: [5, 300],
    longTabMin: [0, 240], dpr: [1, 4] };

export function parseArgs(argv) {
    const opts = { ...DEFAULTS }, labelGiven = argv.includes('--label');
    for (let i = 0; i < argv.length; i++) {
        const arg = argv[i];
        if (FLAGS[arg]) { const [key, value] = FLAGS[arg]; opts[key] = value; continue; }
        if (!NUMBERS[arg] && !STRINGS[arg]) throw new Error(`unknown argument: ${arg}`);
        const value = argv[++i];
        if (value === undefined) throw new Error(`${arg} needs a value`);
        if (STRINGS[arg]) { opts[STRINGS[arg]] = value; continue; }
        const n = Number(value), [lo, hi] = LIMITS[NUMBERS[arg]];
        if (!Number.isFinite(n) || n < lo || n > hi) throw new Error(`${arg} must be a number from ${lo} to ${hi}`);
        opts[NUMBERS[arg]] = n;
    }
    if (!opts.outDir) throw new Error('--out-dir is required');
    if (!/^[a-z0-9-]{1,32}$/.test(opts.label)) throw new Error('--label must be 1-32 lowercase letters, digits or dashes');
    if (!labelGiven && opts.phone) opts.label = 'phone';
    const origin = new URL(opts.origin);
    if (origin.protocol !== 'https:' && origin.hostname !== 'localhost' && origin.hostname !== '127.0.0.1') throw new Error('--origin must be https or local');
    opts.origin = origin.origin;
    return opts;
}

// Camera and media paths are never fetched, even by GET: Frigate and HA camera proxies, frames, stills, clips, the
// '<base>/latest.jpg' snapshot form, HLS playlists and segments, and go2rtc-style frame and stream paths.
const CAMERA = /(?:\/proxy\/frigate(?:\/|$)|\/camera_proxy(?:_stream)?(?:\/|$)|\/frame\.(?:jpe?g|png|npz)$|\/mjpeg(?:\/|$)|\/webrtc(?:\/|$)|\/snapshot(?:\.jpe?g)?(?:\/|$)|\/clips\/|\/recordings\/|\/stills?(?:\/|$)|\/camera(?:\/|$)|\/api\/tts_proxy\/|\/latest\.(?:jpe?g|png|webp)$|\.(?:m3u8|m4s|ts)$|\/hls\/|\/api\/(?:frame|stream)(?:[./]|$))/i;
export function guardRequest({ method, url, resourceType }, origin) {
    let target;
    try { target = new URL(url); } catch (_) { return 'invalid'; }
    if (target.protocol === 'data:' || target.protocol === 'blob:') return 'allow';
    if (target.origin !== origin) return 'cross_origin';
    if (method !== 'GET') return 'non_get';
    if (resourceType === 'media' || resourceType === 'websocket') return 'media';
    if (CAMERA.test(target.pathname)) return 'camera';
    return 'allow';
}

/* The occupancy gate. The window opens on one fresh single confirmed track (not one held through a perception stall)
 * with the body clock set. It is decisive only when it gathered minOccupiedMs of fresh single-confirmed time and its
 * timeline was exported. */
export const readyToOpen = state => state?.tracking?.eligible === true && state.tracking.stall !== true && state.body_clock === true;
export function gateStatus({ opened, singleConfirmedMs, minOccupiedMs, exported }) {
    const status = !opened ? 'unoccupied' : singleConfirmedMs >= minOccupiedMs ? 'ok' : 'insufficient_occupancy';
    const decisive = status === 'ok' && exported === true;
    return { status, decisive, exit_code: decisive ? 0 : 3 };
}

export function receiptName(date, label, random = randomBytes(4).toString('hex'), prefix = 'body-continuity') {
    const stamp = date.toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
    return `${prefix}-${stamp}-${label}-${random}.json`;
}
/* A new file every time: 'wx' fails on an existing name, and a fresh random suffix is tried instead. */
export function writeReceipt(outDir, label, receipt, date = new Date(), prefix = 'body-continuity') {
    mkdirSync(outDir, { recursive: true });
    for (let attempt = 0; attempt < 8; attempt++) {
        const file = join(outDir, receiptName(date, label, undefined, prefix));
        try { writeFileSync(file, JSON.stringify(receipt, null, 2) + '\n', { flag: 'wx' }); return file; }
        catch (error) { if (error.code !== 'EEXIST') throw error; }
    }
    throw new Error('could not find an unused receipt name');
}

export function launchArgs(opts) {
    const common = ['--disable-background-networking', '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
        '--disable-backgrounding-occluded-windows'];
    return opts.gpu ? [...common, '--use-angle=d3d11', '--ignore-gpu-blocklist', '--enable-gpu-rasterization']
        : [...common, '--use-angle=swiftshader', '--enable-unsafe-swiftshader'];
}

async function loadPlaywright(explicit) {
    const spec = explicit || process.env.HOME_PLAYWRIGHT_MODULE;
    if (spec) return import(spec.startsWith('file:') ? spec : pathToFileURL(resolve(spec)).href);
    return import('playwright');
}

const sleep = ms => new Promise(done => setTimeout(done, ms));

async function servedBuild(origin) {
    try {
        const reply = await fetch(origin + '/healthz', { signal: AbortSignal.timeout(8000), headers: { Accept: 'application/json' } });
        const body = await reply.json().catch(() => ({}));
        return { status: reply.status, commit: typeof body.commit === 'string' ? body.commit : null,
            asset_version: typeof body.assetVersion === 'string' ? body.assetVersion : null };
    } catch (error) { return { status: null, error: error.name }; }
}

export async function main(argv = process.argv.slice(2)) {
    let opts;
    try { opts = parseArgs(argv); }
    catch (error) { console.error(error.message); return 2; }
    const { chromium } = await loadPlaywright(opts.playwright);
    const started = new Date();
    const blocked = { non_get: 0, cross_origin: 0, media: 0, camera: 0, websocket: 0, invalid: 0 };
    const receipt = { schema: PROBE_SCHEMA, status: 'probe_failed', started_utc: started.toISOString(),
        options: { seconds: opts.seconds, min_occupied_s: opts.minOccupiedS, max_extend_s: opts.maxExtendS, wait_occupied_s: opts.waitOccupiedS,
            long_tab_min: opts.longTabMin, gpu: opts.gpu, headed: opts.headed, phone: opts.phone, dpr: opts.dpr, latency_trace: opts.latencyTrace,
            site: opts.site, label: opts.label },
        environment: { launch_args: launchArgs(opts), served: await servedBuild(opts.origin) }, read_only: { blocked } };
    let exitCode = 1, checkpoint = 'launch', browser = null;
    try {
        browser = await chromium.launch({ channel: 'chromium', headless: !opts.headed, args: launchArgs(opts) });
        const viewport = opts.phone ? { width: 390, height: 844 } : { width: 1366, height: 768 };
        const context = await browser.newContext({ serviceWorkers: 'block', viewport, deviceScaleFactor: opts.dpr,
            isMobile: opts.phone, hasTouch: opts.phone, permissions: [] });
        await context.route('**/*', route => {
            const request = route.request();
            const verdict = guardRequest({ method: request.method(), url: request.url(), resourceType: request.resourceType() }, opts.origin);
            if (verdict === 'allow') return route.continue();
            blocked[verdict]++;
            return route.abort('blockedbyclient');
        });
        await context.routeWebSocket('**/*', ws => { blocked.websocket++; ws.close(); });
        const pageOptions = { collector: {}, latencyPollMs: 1000,
            storage: { 'home.perf.backgroundWarmup': 'off', 'apartment3d.prewarm': 'off', 'apartment3d.site': opts.site } };
        await context.addInitScript({ content: `(${installBodyContinuityProbe.toString()})(window, ${createBodyContinuityCollector.toString()}, ${JSON.stringify(pageOptions)});` });
        const page = await context.newPage();
        let pageErrors = 0, dialogs = 0;
        page.on('pageerror', () => { pageErrors++; });
        page.on('dialog', dialog => { dialogs++; void dialog.dismiss().catch(() => {}); });
        checkpoint = 'navigation';
        const url = `${opts.origin}/?apartmentRedesign=1&worldZoom=off${opts.latencyTrace ? '&den-latency=1' : ''}`;
        receipt.navigation_status = (await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 }))?.status() ?? null;
        checkpoint = 'den_body_mount';
        await page.waitForFunction(site => window.__APT_ENGINE?.siteId === site && window.HomeDenLiveView?.status?.().body?.stream === 'open',
            opts.site, { timeout: opts.mountTimeoutS * 1000 });
        const state = () => page.evaluate(() => {
            const probe = window.__denBodyProbe, t = performance.now();
            return probe ? { ...probe.collector.state(t), occupancy: probe.collector.occupancy(t) } : null;
        });
        if (opts.longTabMin > 0) {
            checkpoint = 'long_tab_wait';
            const until = Date.now() + opts.longTabMin * 60000;
            while (Date.now() < until) await sleep(Math.min(30000, until - Date.now()));
        }
        checkpoint = 'occupancy_wait';
        const waitUntil = Date.now() + opts.waitOccupiedS * 1000;
        let occupied = false;
        do {
            occupied = readyToOpen(await state());
            if (!occupied) await sleep(500);
        } while (!occupied && Date.now() < waitUntil);
        receipt.occupied_at_start = occupied;
        checkpoint = 'window';
        receipt.state_at_gate = await state();
        const need = opts.minOccupiedS * 1000;
        let singleConfirmedMs = 0;
        if (occupied) {
            await page.evaluate(() => window.__denBodyProbe.collector.beginWindow(performance.now()));
            await sleep(opts.seconds * 1000);
            const extendUntil = Date.now() + opts.maxExtendS * 1000;
            let current = await state();
            while (current.occupancy.single_confirmed_ms < need && Date.now() < extendUntil) { await sleep(5000); current = await state(); }
            singleConfirmedMs = current.occupancy.single_confirmed_ms;
        }
        checkpoint = 'export';
        const exported = await page.evaluate(() => {
            const probe = window.__denBodyProbe, t = performance.now(), engine = window.__APT_ENGINE, body = window.HomeDenLiveView?.status?.().body;
            const timeline = probe.collector.state(t).in_window ? probe.collector.export(t) : null;
            const meta = { time_origin_ms: performance.timeOrigin, build: window.__HOME_BUILD_COMMIT ?? null, backend: engine?.rendererBackend ?? null,
                gpu: typeof engine?.gpu === 'string' ? engine.gpu.slice(0, 160) : null, device_pixel_ratio: window.devicePixelRatio,
                visibility: document.visibilityState, latency_trace: !!window.HomeDenLatency, blocked_in_page: { ...probe.blocked },
                body_status_end: body ? { stream: body.stream, frames: body.frames, reason: body.reason ?? null,
                    accepted: body.accepted ?? null, reopens: body.reopens ?? null, accepted_not_drawn: body.accepted_not_drawn ?? body.unapplied ?? null,
                    rejected_by_reason: body.rejected_by_reason ?? body.rejected ?? null } : null };
            probe.stop();
            return { timeline, meta };
        });
        receipt.environment = { ...receipt.environment, ...exported.meta, page_errors: pageErrors, dialogs_dismissed: dialogs, viewport };
        if (exported.timeline) {
            const { timeline, meta } = exported;
            const origin = meta.time_origin_ms;
            receipt.window = { start_utc: new Date(origin + timeline.window.start).toISOString(), end_utc: new Date(origin + timeline.window.end).toISOString(),
                start_wall_ms: Math.round(origin + timeline.window.start), end_wall_ms: Math.round(origin + timeline.window.end) };
            receipt.connections = timeline.connections.map(conn => ({ route: conn.route, state_at_end: conn.state,
                opened_ms_before_window: Math.round(timeline.window.start - conn.started),
                age_at_window_end_s: Math.round((timeline.window.end - conn.started) / 100) / 10, clock_setup_ms: conn.clock_setup_ms == null ? null : Math.round(conn.clock_setup_ms),
                opens: conn.opens.map(t => ({ at_ms: Math.round(t - timeline.window.start), utc: new Date(origin + t).toISOString() })),
                errors: conn.errors.map(t => ({ at_ms: Math.round(t - timeline.window.start), utc: new Date(origin + t).toISOString() })),
                closes: conn.closes.map(t => ({ at_ms: Math.round(t - timeline.window.start), utc: new Date(origin + t).toISOString() })) }));
            receipt.reopens = { body: Math.max(0, timeline.connections.filter(c => c.route === 'body').length - 1),
                people: Math.max(0, timeline.connections.filter(c => c.route === 'people').length - 1),
                app_counter: meta.body_status_end?.reopens ?? null };
            receipt.attribution = attributeWindow(timeline, { timeOrigin: origin });
            receipt.timeline = { packets: packetRows(timeline, origin) };
            if (meta.backend !== 'webgl') receipt.notes = ['the draw witness hooks the WebGL body-recording points; this backend may hide draws from it'];
        }
        const gate = gateStatus({ opened: occupied, singleConfirmedMs, minOccupiedMs: need, exported: !!exported.timeline });
        receipt.status = gate.status;
        // Only a window that met the occupancy gate may pick a fix; the attribution is kept either way.
        receipt.decisive = gate.decisive;
        if (receipt.attribution && !gate.decisive) receipt.attribution.decision = withheld(receipt.attribution.decision, `not decisive (status ${gate.status})`);
        exitCode = gate.exit_code;
    } catch (error) {
        receipt.status = error.name === 'TimeoutError' ? 'mount_timeout' : 'probe_failed';
        receipt.failure = { checkpoint, error: error.name, message: String(error.message).split('\n')[0].slice(0, 200) };
    } finally {
        await browser?.close().catch(() => {});
    }
    receipt.completed_utc = new Date().toISOString();
    const file = writeReceipt(resolve(opts.outDir), opts.label, receipt, started);
    const a = receipt.attribution;
    console.log(JSON.stringify({ status: receipt.status, decisive: receipt.decisive ?? false, receipt: file, occupancy_ms: a?.occupancy ?? null,
        blank_ms_by_family: a?.blank_ms_by_family ?? null, decision: a?.decision?.verdict ?? null, family: a?.decision?.family ?? null,
        attributed_fraction: a?.attribution_coverage?.attributed_fraction ?? null,
        drawn_fraction: a?.drawn_fraction ?? null, raf: a?.frames?.raf_interval_ms ?? null, gpu: receipt.environment.gpu ?? null,
        gpu_completed: a?.gpu_completion_witness?.gpu_completed ?? null, blocked }));
    return exitCode;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) process.exitCode = await main();
