/* The live den view in Home: people markers from den-live, live, in the den's apartment view, with a status chip.
 *
 * It does nothing unless the page shell names den-live (window.HomeDenLive, from the gateway's HOME_WEB_DEN_LIVE).
 * While the den's apartment view shows (window.__APT_ENGINE.siteId is HomeDenLive.site, its canvas is on screen at
 * room scale) and the page is visible, it:
 * - mounts the people layer (home-3d/apartment-people-layer.mjs) on that engine, and again on a new engine;
 * - holds one EventSource on HomeDenLive.stream, and feeds the layer each `people` event whose content_revision is
 *   the engine's: the stream's positions are in the scan the camera is registered to, and drawn on no other.
 * Otherwise it closes the streams and disposes the layers. The coordinator's spots ride the people stream.
 * A heartbeat is an SSE comment, which EventSource never shows; den-live sends an event at least once a second while
 * its loop runs (an idle one says why), so 3 s with no event reads as "stream stopped".
 * The body has an independent connection, and the coordinator panel uses the current spot playback for its readout. */
const config = window.HomeDenLive;
const STOPPED_MS = 3000, REOPEN_MS = 5000, TICK_MS = 500, BODY_RETRY_MS = 2000;
const BODY_CLOCK_REFRESH_MS = 60 * 60 * 1000;
let qwenLine = () => '';   // home-den-live-qwen.mjs: what den-live's Qwen ask last said, a line in the chip

if (config && typeof config.stream === 'string' && typeof config.site === 'string' && typeof window.EventSource === 'function') {
    const cb = new URL(import.meta.url).search || '';
    const { attachApartmentPeopleLayer, validPeopleEvent } = await import(`./home-3d/apartment-people-layer.mjs${cb}`);
    qwenLine = await import(`./home-den-live-qwen.mjs${cb}`).then(module => module.qwenLine)
        .catch(error => { console.warn('[den-live] Qwen line unavailable', error); return () => ''; });
    const panelKit = typeof config.coordinator === 'string'
        ? await import(`./home-den-coordinator-panel.mjs${cb}`).catch(error => { console.warn('[den-live] coordinator panel unavailable', error); return null; })
        : null;
    const spotKit = config.virtual === true
        ? await import(`./home-den-spot-playback.mjs${cb}`).catch(error => { console.warn('[den-live] virtual spots unavailable', error); return null; })
        : null;
    const enableBody = start(attachApartmentPeopleLayer, validPeopleEvent, panelKit, spotKit);
    if (typeof config.body === 'string') {
        void Promise.all([import(`./home-3d/apartment-body-layer.mjs${cb}`), import(`./home-3d/apartment-body-labels.mjs${cb}`),
            import(`./home-3d/apartment-body-frame.mjs${cb}`)])
            .then(([layerModule, labelModule, frameModule]) => enableBody({ attachLayer: layerModule.attachApartmentBodyLayer,
                attachLabels: labelModule.attachApartmentBodyLabels, decode: frameModule.decodeLiveBodyEvent }))
            .catch(error => console.warn('[den-live] body layer unavailable', error));
    }
}

function start(attachApartmentPeopleLayer, validPeopleEvent, panelKit, spotKit) {
    const lookOnly = window.HomeLookOnly?.active === true;
    let engine = null, scope = null, layer = null, source = null, openedAt = 0, lastAt = 0, closedAt = 0, latest = null, events = 0, chip = null;
    let panel = null;
    let bodyKit = null, body = null, bodyRetryAt = 0, bodyFrames = 0, bodyAt = 0, watermark = null, pageActive = true;
    const spots = spotKit?.createSpotPlayback({ home: config.home, site: config.site, onChange: () => panel?.refresh() });
    const scene = current => `${current.contentRevision ?? ''}:${current.sceneIdentity?.registrationVersion ?? ''}:${current.sceneIdentity?.frameDigest ?? ''}`;

    /* The den's room view is on screen: the den's engine, its canvas shown at room scale, the page visible. */
    function denEngine() {
        const current = window.__APT_ENGINE, canvas = current?.renderer?.domElement;
        if (!pageActive || document.hidden || !current?.apartmentRoot || current.siteId !== config.site || !canvas?.isConnected) return null;
        const host = canvas.parentElement;
        if (host?.dataset?.aptSpatialOwner && host.dataset.aptSpatialOwner !== 'apartment') return null;
        if (!canvas.getClientRects().length || window.getComputedStyle(canvas).visibility === 'hidden') return null;
        const rect = canvas.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 ? current : null;
    }
    const sameScan = event => !!event && !!engine && typeof event.content_revision === 'string'
        && event.content_revision === engine.contentRevision;

    function onPeople(message) {
        let event;
        try { event = JSON.parse(message.data); } catch (_) { return; }
        if (!validPeopleEvent(event) || (event.home != null && event.home !== config.home)) return;
        latest = event; lastAt = performance.now(); events += 1;
        if (sameScan(event)) layer?.update(event);
        else if (layer) { layer.dispose(); layer = attachApartmentPeopleLayer(engine); }
        render();
    }
    const isCurrent = () => engine && engine === denEngine() && scope === scene(engine);
    function open() {
        const next = source = new EventSource(config.stream);
        openedAt = performance.now(); closedAt = 0;
        next.addEventListener('people', message => { if (source === next && isCurrent()) onPeople(message); });
        if (spots) next.addEventListener('spot_snapshot', message => {
            if (source !== next || !isCurrent()) return;
            try { spots.receive(JSON.parse(message.data)); }
            catch (_) { spots.sourceLost(); }
        });
        next.addEventListener('error', () => {
            if (source !== next) return;
            spots?.disconnect();
            panel?.pollNow();
            if (next.readyState === EventSource.CLOSED) closedAt = performance.now();
        });
    }
    function clearBody(owned, reason) {
        owned.ready = null;
        owned.reason = reason;
        owned.lastPaintAge = null;
        owned.layer?.clear();
        owned.labels?.clear();
    }
    function closeBodySource(owned) {
        const connection = owned.connection;
        owned.connection = null;
        connection?.source.close();
    }
    function retryBody(owned, reason) {
        closeBodySource(owned);
        clearBody(owned, reason);
        bodyRetryAt = performance.now() + BODY_RETRY_MS;
    }
    function disposeBody(owned = body) {
        if (!owned || body !== owned) return;
        body = null;
        clearBody(owned, 'unavailable');
        closeBodySource(owned);
        owned.controller.abort();
        owned.labels?.dispose();
        owned.layer?.dispose();
    }
    function producerNow(connection, nowMs) {
        // The clock sample is taken after this request starts. Charging that elapsed time makes this an upper bound.
        const elapsed = (nowMs - connection.started) / 1000;
        return connection.serverTs + elapsed + 0.005 + elapsed * 0.0001;
    }
    function openBody(owned) {
        const started = performance.now(), source = new EventSource(config.body);
        const connection = { source, started, serverTs: null, runId: null };
        owned.connection = connection;
        const current = () => body === owned && owned.connection === connection && isCurrent() && engine === owned.engine;
        source.addEventListener('body_clock', message => {
            if (!current() || connection.serverTs !== null) return;
            let sample;
            try { sample = JSON.parse(message.data); } catch (_) { return; }
            if (sample && Object.keys(sample).length === 1 && Number.isFinite(sample.server_ts)) connection.serverTs = sample.server_ts;
        });
        source.addEventListener('body', message => {
            if (!current() || connection.serverTs === null) return;
            let result;
            try { result = bodyKit.decode(JSON.parse(message.data)); } catch (_) { return; }
            if (!result || result.event.home !== config.home) return;
            const { event, decoded } = result;
            if (connection.runId !== null && connection.runId !== event.run_id) return;
            connection.runId = event.run_id;
            if (watermark?.runId === event.run_id && event.seq <= watermark.seq) return;
            watermark = { runId: event.run_id, seq: event.seq };
            if (event.content_revision !== owned.engine.contentRevision) { clearBody(owned, 'scope_changed'); return; }
            if (event.status === 'clear') { clearBody(owned, event.reason); return; }
            const now = performance.now();
            const producerTime = producerNow(connection, now);
            if (producerTime < event.capture_ts || producerTime >= event.expires_ts) {
                // Refresh an old, over-conservative anchor on demand, without renewing this frame's lease.
                if (now - started >= BODY_CLOCK_REFRESH_MS) retryBody(owned, 'stale');
                else clearBody(owned, 'stale');
                return;
            }
            const old = owned.ready;
            if (old && ['run_id', 'track_epoch', 'content_revision', 'transform_id', 'mode', 'track_id'].some(key => event[key] !== old.event[key]))
                clearBody(owned, 'scope_changed');
            owned.ready = { event, decoded };
            owned.reason = null;
            bodyFrames += 1; bodyAt = now;
        });
        source.addEventListener('error', () => {
            if (body !== owned || owned.connection !== connection) return;
            retryBody(owned, 'unavailable');
        });
    }
    async function mountBody(current) {
        const host = current.picking?.getHost();
        if (!host || typeof current.createUiOverlayLayer !== 'function') return;
        const controller = new AbortController(), owned = { engine: current, controller, reason: 'unavailable' };
        body = owned;
        const admitted = () => body === owned && isCurrent() && engine === current;
        try {
            const presentation = await current.createUiOverlayLayer({ host, signal: controller.signal, isCurrent: admitted,
                onPresentationFrame: ({ deltaSeconds }) => {
                    if (!owned.ready) return;
                    const now = producerNow(owned.connection, performance.now());
                    if (now >= owned.ready.event.expires_ts) { clearBody(owned, 'stale'); return; }
                    if (owned.ready.decoded && (current.rendererBackend === 'webgl' || owned.layer.status().webgpu)) {
                        owned.layer.updateMeasured(owned.ready.decoded);
                        owned.ready.decoded = null;
                        owned.lastPaintAge = now - owned.ready.event.capture_ts;
                    }
                    owned.layer.tick(deltaSeconds);
                    owned.labels.tick(deltaSeconds);
                }, onPresentationDispose: () => disposeBody(owned) });
            if (!presentation || !admitted()) { presentation?.dispose(); disposeBody(owned); return; }
            owned.layer = bodyKit.attachLayer(current, { measuredOnly: true, animate: false,
                reducedMotion: () => window.matchMedia('(prefers-reduced-motion: reduce)').matches });
            owned.labels = bodyKit.attachLabels({ engine: current, host, layer: owned.layer, animate: false });
        } catch (error) { console.warn('[den-live] body layer not mounted', error); disposeBody(owned); }
    }
    function close() {
        source?.close(); source = null;
        layer?.dispose(); layer = null; engine = null;
        spots?.disconnect();
        spots?.attach(null);
        disposeBody();
        latest = null; lastAt = 0;
        panel?.pause();
        chip?.remove();
    }

    function sync() {
        const current = denEngine();
        if (!current) { if (source || layer) close(); return; }
        if (current !== engine || scope !== scene(current)) {
            close();
            engine = current;
            scope = scene(current);
            spots?.attach(current.overlay);
            layer = attachApartmentPeopleLayer(current);
        }
        const now = performance.now();
        if (source && closedAt && now - closedAt > REOPEN_MS) { source.close(); source = null; }
        if (!source) open();
        if (bodyKit && !body) void mountBody(current);
        if (body?.labels && !body.connection && now >= bodyRetryAt) openBody(body);
        spots?.check();
        place();
        render();
        panel?.tick();
    }

    /* The chip sits in the room view's top left corner, under Home's own overlays. The older layouts keep a toolbar
     * row there, and the chip joins its end instead of covering it. */
    function place() {
        if (!chip) {
            chip = document.createElement('div');
            chip.className = 'home-den-live-chip';
            chip.dataset.denLiveChip = '1';
            chip.innerHTML = '<p class="home-den-live-chip-title"><span class="home-den-live-chip-dot" aria-hidden="true"></span>'
                + '<span class="home-den-live-chip-name"></span></p><p class="home-den-live-chip-facts"></p>'
                + '<p class="home-den-live-chip-qwen"></p>'
                + '<p class="home-den-live-chip-banner" role="status" aria-live="polite"></p>';
            chip.querySelector('.home-den-live-chip-name').textContent = `Den · live${lookOnly ? ' · look-only' : ''}`;
            if (panelKit && !panel) {
                try {
                    panel = panelKit.createCoordinatorPanel({ url: config.coordinator, lookOnly, spots });
                    chip.append(panel.element);
                } catch (error) { console.warn('[den-live] coordinator panel not mounted', error); panel = null; }
            }
        }
        const canvas = engine.renderer.domElement;
        const view = canvas.closest('[data-home-apartment-view="1"]') || canvas.parentElement;
        const target = view.querySelector(':scope > [data-apt-legacy-toolbar="1"]') || view;
        if (chip.parentElement !== target) target.appendChild(chip);
    }
    const seconds = value => `${value < 10 ? value.toFixed(2) : Math.round(value)} s`;
    function banner(stopped) {
        if (stopped) return ['stream-stopped', 'Stream stopped'];
        if (!sameScan(latest)) return ['other-scan', 'Another scan revision than the camera is registered to: no markers'];
        const camera = latest.camera || {}, why = typeof camera.why === 'string' && camera.why ? camera.why.slice(0, 120) : '';
        if (camera.status && camera.status !== 'ok') return [`camera-${camera.status}`, `Camera ${camera.status}${why ? `: ${why}` : ''}`];
        if (why) return [why === 'too dark' ? 'too-dark' : why === 'detector errors' ? 'detector-errors'
            : why === 'depth not corrected at the camera' ? 'depth-not-corrected' : 'camera-why', why[0].toUpperCase() + why.slice(1)];
        if (latest.mode === 'none') return ['too-dark', 'Too dark'];
        return ['', ''];
    }
    function setText(node, text) { if (node.textContent !== text) node.textContent = text; }
    function render() {
        if (!chip) return;
        const now = performance.now(), fresh = !!latest && now - lastAt <= STOPPED_MS;
        const waiting = !fresh && now - openedAt <= STOPPED_MS;   // just opened: nothing to say yet
        const [kind, words] = waiting ? ['', ''] : banner(!fresh);
        let facts = waiting ? 'Connecting…' : '', title = '';
        if (fresh) {
            const count = latest.tracks.length, health = latest.health || {};
            facts = count === 0 ? 'No one' : count === 1 ? '1 person' : `${count} people`;
            if (latest.mode === 'infrared') facts += ' · infrared';
            const lag = Number.isFinite(health.lag_s_median) ? health.lag_s_median : null;
            const transit = Number.isFinite(health.transit_s_median) ? health.transit_s_median : null;
            if (lag != null) {
                facts += ` · ${seconds(lag + (transit ?? 0))} behind`;
                title = `Capture to stream ${seconds(lag)}${transit != null ? `, plus ${seconds(transit)} in transit` : ''}`;
            }
        }
        chip.dataset.state = waiting ? 'connecting' : kind ? 'attention' : 'live';
        chip.dataset.banner = kind;
        if (chip.title !== title) chip.title = title;
        setText(chip.querySelector('.home-den-live-chip-facts'), facts);
        setText(chip.querySelector('.home-den-live-chip-qwen'), fresh && sameScan(latest) ? qwenLine(latest, (now - lastAt) / 1000) : '');
        setText(chip.querySelector('.home-den-live-chip-banner'), words);
    }

    setInterval(sync, TICK_MS);
    document.addEventListener('visibilitychange', sync);
    window.addEventListener('pagehide', () => { pageActive = false; close(); });
    window.addEventListener('pageshow', () => { pageActive = true; sync(); });
    sync();

    /* Read-only, for Home's browser tests. */
    window.HomeDenLiveView = Object.freeze({
        status: () => ({ stream: source ? ['connecting', 'open', 'closed'][source.readyState] : 'none', mounted: !!layer,
            siteId: engine?.siteId ?? null, events, banner: chip?.dataset.banner ?? null, chip: chip?.isConnected === true,
            layer: layer ? layer.status() : null,
            body: bodyKit ? { stream: body?.connection ? ['connecting', 'open', 'closed'][body.connection.source.readyState] : 'none',
                mounted: !!body?.layer, frames: bodyFrames, since_s: bodyAt ? (performance.now() - bodyAt) / 1000 : null,
                reason: body?.reason ?? null, last_paint_age_s: body?.lastPaintAge ?? null, layer: body?.layer?.status() ?? null } : null ,
            coordinator: panel ? panel.status() : null, spot: spots?.current() ?? null }),
        markers: () => layer ? layer.markers() : [],
    });
    return kit => { bodyKit = kit; sync(); };
}
