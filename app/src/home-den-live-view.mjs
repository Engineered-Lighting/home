/* The live den view in Home: people markers from den-live, live, in the den's apartment view, with a status chip.
 *
 * It does nothing unless the page shell names den-live (window.HomeDenLive, from the gateway's HOME_WEB_DEN_LIVE).
 * While the den's apartment view shows (window.__APT_ENGINE.siteId is HomeDenLive.site, its canvas is on screen at
 * room scale) and the page is visible, it:
 * - mounts the people layer (home-3d/apartment-people-layer.mjs) on that engine, and again on a new engine;
 * - holds one EventSource on HomeDenLive.stream, and feeds the layer each `people` event whose content_revision is
 *   the engine's: the stream's positions are in the scan the camera is registered to, and drawn on no other.
 * Otherwise it closes the stream and disposes the layer, so a device elsewhere in Home holds no stream (den-live
 * serves at most 16). It reaches nothing but HomeDenLive.stream, on this page's own origin, and writes nothing.
 * A heartbeat is an SSE comment, which EventSource never shows; den-live sends an event at least once a second while
 * its loop runs (an idle one says why), so 3 s with no event reads as "stream stopped". */
const config = window.HomeDenLive;
const STOPPED_MS = 3000, REOPEN_MS = 5000, TICK_MS = 500;

if (config && typeof config.stream === 'string' && typeof config.site === 'string' && typeof window.EventSource === 'function') {
    const cb = new URL(import.meta.url).search || '';
    const { attachApartmentPeopleLayer, validPeopleEvent } = await import(`./home-3d/apartment-people-layer.mjs${cb}`);
    start(attachApartmentPeopleLayer, validPeopleEvent);
}

function start(attachApartmentPeopleLayer, validPeopleEvent) {
    const lookOnly = window.HomeLookOnly?.active === true;
    let engine = null, layer = null, source = null, openedAt = 0, lastAt = 0, closedAt = 0, latest = null, events = 0, chip = null;

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
        if (layer && sameScan(event)) layer.update(event);
        render();
    }
    function open() {
        source = new EventSource(config.stream);
        openedAt = performance.now(); closedAt = 0;
        source.addEventListener('people', onPeople);
        // EventSource retries a dropped stream itself; a refused one (an HTTP error) it closes for good.
        source.addEventListener('error', () => { if (source?.readyState === EventSource.CLOSED && !closedAt) closedAt = performance.now(); });
    }
    function close() {
        source?.close(); source = null;
        layer?.dispose(); layer = null; engine = null;
        latest = null; lastAt = 0;
        chip?.remove();
    }

    function sync() {
        const current = denEngine();
        if (!current) { if (source || layer) close(); return; }
        if (current !== engine) {
            layer?.dispose();
            engine = current;
            layer = attachApartmentPeopleLayer(current);
            if (latest && sameScan(latest)) layer.update(latest);
        }
        if (source && closedAt && performance.now() - closedAt > REOPEN_MS) { source.close(); source = null; }
        if (!source) open();
        place();
        render();
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
                + '<p class="home-den-live-chip-banner" role="status" aria-live="polite"></p>';
            chip.querySelector('.home-den-live-chip-name').textContent = `Den · live${lookOnly ? ' · look-only' : ''}`;
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
        setText(chip.querySelector('.home-den-live-chip-banner'), words);
    }

    setInterval(sync, TICK_MS);
    document.addEventListener('visibilitychange', sync);
    sync();

    /* Read-only, for Home's browser tests. */
    window.HomeDenLiveView = Object.freeze({
        status: () => ({ stream: source ? ['connecting', 'open', 'closed'][source.readyState] : 'none', mounted: !!layer,
            siteId: engine?.siteId ?? null, events, banner: chip?.dataset.banner ?? null, chip: chip?.isConnected === true,
            layer: layer ? layer.status() : null }),
        markers: () => layer ? layer.markers() : [],
    });
}
