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
const STOPPED_MS = 3000, REOPEN_MS = 5000, TICK_MS = 500;
let qwenLine = () => '';   // home-den-live-qwen.mjs: what den-live's Qwen ask last said, a line in the chip

if (config && typeof config.stream === 'string' && typeof config.site === 'string' && typeof window.EventSource === 'function') {
    const cb = new URL(import.meta.url).search || '';
    const { attachApartmentPeopleLayer, validPeopleEvent } = await import(`./home-3d/apartment-people-layer.mjs${cb}`);
    qwenLine = await import(`./home-den-live-qwen.mjs${cb}`).then(module => module.qwenLine)
        .catch(error => { console.warn('[den-live] Qwen line unavailable', error); return () => ''; });
    const bodyV2 = config.bodySchema === 'den-live-body-2';
    const legacyBody = !window.HomeServe?.active && config.bodySchema == null;
    const bodyKit = !window.HomeLookOnly?.active && (bodyV2 || legacyBody) && typeof config.body === 'string'
        ? await Promise.all([import(`./home-3d/apartment-body-layer.mjs${cb}`), import(`./home-3d/apartment-body-labels.mjs${cb}`)])
            .then(([layerModule, labelModule]) => ({ attachLayer: layerModule.attachApartmentBodyLayer, attachLabels: labelModule.attachApartmentBodyLabels }))
            .catch(error => { console.warn('[den-live] body layer unavailable', error); return null; })
        : null;
    const panelKit = typeof config.coordinator === 'string'
        ? await import(`./home-den-coordinator-panel.mjs${cb}`).catch(error => { console.warn('[den-live] coordinator panel unavailable', error); return null; })
        : null;
    const spotKit = config.virtual === true
        ? await import(`./home-den-spot-playback.mjs${cb}`).catch(error => { console.warn('[den-live] virtual spots unavailable', error); return null; })
        : null;
    start(attachApartmentPeopleLayer, validPeopleEvent, bodyKit, panelKit, spotKit);
}

function start(attachApartmentPeopleLayer, validPeopleEvent, bodyKit, panelKit, spotKit) {
    const lookOnly = window.HomeLookOnly?.active === true;
    let engine = null, scope = null, layer = null, source = null, openedAt = 0, lastAt = 0, closedAt = 0, latest = null, events = 0, chip = null;
    let panel = null;
    let bodyLayer = null, bodyLabels = null, bodySource = null, bodyFrames = 0, bodyAt = 0, bodyClosedAt = 0;
    const spots = spotKit?.createSpotPlayback({ home: config.home, site: config.site, onChange: () => panel?.refresh() });
    const scene = current => `${current.contentRevision ?? ''}:${current.sceneIdentity?.registrationVersion ?? ''}:${current.sceneIdentity?.frameDigest ?? ''}`;

    /* The den's room view is on screen: the den's engine, its canvas shown at room scale, the page visible. */
    function denEngine() {
        const current = window.__APT_ENGINE, canvas = current?.renderer?.domElement;
        if (document.hidden || !current?.apartmentRoot || current.siteId !== config.site || !canvas?.isConnected) return null;
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
    function onBody(message) {
        let frame;
        try { frame = JSON.parse(message.data); } catch (_) { disposeBody(); return; }
        if (!frame || (frame.home != null && frame.home !== config.home) || !sameScan(frame)
            || config.bodySchema && frame.schema !== config.bodySchema) { disposeBody(); return; }
        if (!bodyLayer) mountBody(engine);
        if (!bodyLayer) return;
        if (bodyLayer.update(frame)) { bodyFrames += 1; bodyAt = performance.now(); }
        else if (config.bodySchema && bodyLayer.status().frames === 0) disposeBody();
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
    function openBody() {
        const next = bodySource = new EventSource(config.body);
        bodyClosedAt = 0;
        next.addEventListener('body', message => { if (bodySource === next && isCurrent()) onBody(message); });
        next.addEventListener('error', () => {
            if (bodySource !== next) return;
            disposeBody();
            if (next.readyState === EventSource.CLOSED) bodyClosedAt = performance.now();
        });
    }
    function disposeBody() {
        bodyLabels?.dispose(); bodyLayer?.dispose();
        bodyLabels = bodyLayer = null;
    }
    function mountBody(current) {
        if (!bodyKit) return;
        disposeBody();
        const host = current.renderer?.domElement?.parentElement;
        if (!host) return;
        try {
            bodyLayer = bodyKit.attachLayer(current, { schema: config.bodySchema });
            if (spots) bodyLayer.setPool(false);
            bodyLabels = bodyKit.attachLabels({ engine: current, host, layer: bodyLayer });
        } catch (error) { console.warn('[den-live] body layer not mounted', error); disposeBody(); }
    }
    function close() {
        source?.close(); source = null;
        bodySource?.close(); bodySource = null;
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
            mountBody(current);
        }
        const now = performance.now();
        if (source && closedAt && now - closedAt > REOPEN_MS) { source.close(); source = null; }
        if (bodySource && bodyClosedAt && now - bodyClosedAt > REOPEN_MS) { bodySource.close(); bodySource = null; }
        if (!source) open();
        if (bodyKit && !bodySource) openBody();
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
    window.addEventListener('pagehide', close);
    sync();

    /* Read-only, for Home's browser tests. */
    window.HomeDenLiveView = Object.freeze({
        status: () => ({ stream: source ? ['connecting', 'open', 'closed'][source.readyState] : 'none', mounted: !!layer,
            siteId: engine?.siteId ?? null, events, banner: chip?.dataset.banner ?? null, chip: chip?.isConnected === true,
            layer: layer ? layer.status() : null,
            body: bodyKit ? { stream: bodySource ? ['connecting', 'open', 'closed'][bodySource.readyState] : 'none', mounted: !!bodyLayer,
                frames: bodyFrames, since_s: bodyAt ? (performance.now() - bodyAt) / 1000 : null, layer: bodyLayer ? bodyLayer.status() : null } : null,
            coordinator: panel ? panel.status() : null, spot: spots?.current() ?? null }),
        markers: () => layer ? layer.markers() : [],
    });
}
