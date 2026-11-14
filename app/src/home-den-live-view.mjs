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
 * The body has an independent connection, and the coordinator panel uses the current spot playback for its readout.
 * With HomeDenLive.caption (the gateway's HOME_WEB_DEN_CAPTION), each person gets the live caption
 * (home-den-inference-caption.mjs): who, Qwen's word and its life, what they hold, and what the light does for them.
 * It takes over the chip's Qwen line and the body's own labels, so nothing is said twice. With HomeDenLive.pipeline
 * (HOME_WEB_DEN_PIPELINE), the pipeline is drawn through the room (home-den-pipeline-layer.mjs). */
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
    const captionKit = config.caption === true
        ? await import(`./home-den-inference-caption.mjs${cb}`).catch(error => { console.warn('[den-live] caption unavailable', error); return null; })
        : null;
    // The data behind the body's System caption: always loaded with the body, which is the only floating label.
    const inferenceKit = typeof config.body === 'string'
        ? await import(`./home-den-inference-model.mjs${cb}`).catch(error => { console.warn('[den-live] caption data unavailable', error); return null; })
        : null;
    const pipelineKit = config.pipeline === true
        ? await import(`./home-den-pipeline-layer.mjs${cb}`).catch(error => { console.warn('[den-live] pipeline unavailable', error); return null; })
        : null;
    const enableBody = start(attachApartmentPeopleLayer, validPeopleEvent, panelKit, spotKit, captionKit, pipelineKit, inferenceKit);
    if (typeof config.body === 'string') {
        void Promise.all([import(`./home-3d/apartment-body-layer.mjs${cb}`), import(`./home-3d/apartment-body-labels.mjs${cb}`),
            import(`./home-3d/apartment-body-frame.mjs${cb}`)])
            .then(([layerModule, labelModule, frameModule]) => enableBody({ attachLayer: layerModule.attachApartmentBodyLayer,
                attachLabels: labelModule.attachApartmentBodyLabels, decode: frameModule.decodeLiveBodyEvent }))
            .catch(error => console.warn('[den-live] body layer unavailable', error));
    }
}

function start(attachApartmentPeopleLayer, validPeopleEvent, panelKit, spotKit, captionKit = null, pipelineKit = null, inferenceKit = null) {
    const lookOnly = window.HomeLookOnly?.active === true;
    let engine = null, scope = null, layer = null, source = null, openedAt = 0, lastAt = 0, closedAt = 0, latest = null, events = 0, chip = null;
    let panel = null, caption = null, pipeline = null;
    let bodyKit = null, body = null, bodyRetryAt = 0, bodyFrames = 0, bodyAt = 0, watermark = null, pageActive = true;
    const spots = spotKit?.createSpotPlayback({ home: config.home, site: config.site,
        onChange: () => { panel?.refresh(); caption?.setSpot(spots?.current() ?? null); pipeline?.setSpot(spots?.current() ?? null); } });
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
        const matched = sameScan(event);
        spots?.tracking(matched ? event.transform_id : null, matched ? event.content_revision : null);
        if (matched) { layer?.update(event); caption?.update(event, lastAt); pipeline?.update(event, lastAt); }
        else if (layer) { layer.dispose(); layer = attachApartmentPeopleLayer(engine); caption?.clear(); pipeline?.clear(); }
        render();
    }
    const isCurrent = () => engine && engine === denEngine() && scope === scene(engine);
    function open() {
        openedAt = performance.now(); closedAt = 0;
        spots?.connect(openedAt);
        const next = source = new EventSource(config.stream);
        next.addEventListener('people', message => { if (source === next && isCurrent()) onPeople(message); });
        if (spots) next.addEventListener('spot_clock', message => {
            if (source !== next || !isCurrent()) return;
            try { spots.anchor(JSON.parse(message.data)); }
            catch (_) { spots.sourceLost(); }
        });
        if (spots) next.addEventListener('spot_snapshot', message => {
            if (source !== next || !isCurrent()) return;
            try { spots.receive(JSON.parse(message.data)); }
            catch (_) { spots.sourceLost(); }
            caption?.setSpot(spots.current() ?? null);
            pipeline?.setSpot(spots.current() ?? null);
        });
        next.addEventListener('error', () => {
            if (source !== next) return;
            next.close(); source = null; closedAt = performance.now();
            spots?.disconnect();
            panel?.pollNow();
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
            // The body's System caption is the person's one floating label (the spine, 2026-11-14). Its words come from
            // home-den-inference-model.mjs: who, Qwen's word until the moment it expires, and the light only when the
            // spot's subject is this person. The separate caption (HOME_WEB_DEN_CAPTION) stays off and unused.
            owned.labels = caption ? { tick() {}, clear() {}, dispose() {} }
                : bodyKit.attachLabels({ engine: current, host, layer: owned.layer, animate: false, ...(inferenceKit ? captionData(owned) : {}) });
        } catch (error) { console.warn('[den-live] body layer not mounted', error); disposeBody(owned); }
    }
    /* The person the body belongs to, in the newest people event: the body's own track, else the one track within
     * 0.6 m of its centroid. */
    function bodyTrack(owned) {
        const tracks = sameScan(latest) ? latest.tracks : [], id = owned.ready?.event?.track_id;
        const own = tracks.find(track => track.track_id === id);
        if (own) return own;
        const c = owned.layer?.snapshot?.()?.centroid;
        const near = c ? tracks.filter(track => Math.hypot(track.xy_m[0] - c[0], track.xy_m[1] - c[1]) < 0.6) : [];
        return near.length === 1 ? near[0] : null;
    }
    function captionData(owned) {
        const since = () => (performance.now() - lastAt) / 1000;
        const names = () => Object.fromEntries((owned.engine.overlay?.virtualFixturesReadback?.()?.fixtures || [])
            .filter(row => typeof row.label === 'string').map(row => [row.fixture_id, row.label]));
        return {
            person: () => {
                const track = bodyTrack(owned);
                if (!track) return null;
                const id = inferenceKit.identityOf(track, latest, since());
                return id.kind === 'household' || id.kind === 'unknown'
                    ? { name: id.name, known: true, source: id.basis === 'carried' ? 'carried over' : 'face', confidence: id.score } : null;
            },
            activity: () => { const track = bodyTrack(owned); return track ? inferenceKit.answerOf(track, latest, since())?.words ?? null : null; },
            house: () => {
                const track = bodyTrack(owned), house = track ? inferenceKit.houseOf(track, spots?.current() ?? null, names()) : null;
                return house ? `${house.words}${!house.dark && house.level != null ? ` · ${Math.round(house.level * 100)}%` : ''}` : null;
            },
        };
    }
    function mountCaption(current) {
        const canvas = current.renderer.domElement;
        // The stage, so the caption sits over the room's label pills; older layouts fall back to the view itself.
        const host = canvas.closest('.apt-home-stage') || canvas.closest('[data-home-apartment-view="1"]') || canvas.parentElement;
        const names = () => Object.fromEntries((current.overlay?.virtualFixturesReadback?.()?.fixtures || [])
            .filter(row => typeof row.label === 'string').map(row => [row.fixture_id, row.label]));
        if (pipelineKit) {
            try {
                pipeline = pipelineKit.attachDenPipeline({ engine: current, host, markers: () => layer?.markers() ?? [], names, toggle: false,
                    reducedMotion: () => window.matchMedia('(prefers-reduced-motion: reduce)').matches });
            } catch (error) { console.warn('[den-live] pipeline not mounted', error); pipeline = null; }
        }
        if (!captionKit) return;
        try {
            caption = captionKit.attachDenCaption({ engine: current, host, markers: () => layer?.markers() ?? [],
                body: () => body?.engine === current && body.ready ? body.layer?.snapshot?.() ?? null : null, names,
                reducedMotion: () => window.matchMedia('(prefers-reduced-motion: reduce)').matches });
        } catch (error) { console.warn('[den-live] caption not mounted', error); caption = null; }
    }
    function close() {
        source?.close(); source = null;
        caption?.dispose(); caption = null;
        pipeline?.dispose(); pipeline = null;
        layer?.dispose(); layer = null; engine = null;
        spots?.disconnect();
        spots?.attach(null);
        disposeBody();
        latest = null; lastAt = 0;
        closedAt = 0;
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
            if (captionKit || pipelineKit) mountCaption(current);
        }
        const now = performance.now();
        if (!source && (!closedAt || now - closedAt > REOPEN_MS)) open();
        if (bodyKit && !body) void mountBody(current);
        if (body?.labels && !body.connection && now >= bodyRetryAt) openBody(body);
        spots?.check();
        place();
        render();
        panel?.tick();
    }

    /* The chip sits in the room view's top left corner, under Home's own overlays. The older layouts keep a toolbar
     * row there, and the chip joins its end instead of covering it. */
    /* With the pipeline (or the caption) on, the chip becomes one capsule in the Perception Lab's card language: the
     * den's status and Qwen's line, and three icons that hold what used to be separate buttons and drawers. The
     * pipeline icon turns the pipeline on and off, the light icon opens the coordinator's controls in a sheet under the
     * capsule, and the labels icon opens the label editor (whose own button the capsule then hides). */
    const capsule = !!(captionKit || pipelineKit);
    const ICON = {"pipeline": '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="2.8" cy="8" r="1.7"/><circle cx="8" cy="8" r="1.7"/><circle cx="13.2" cy="8" r="1.7"/><path d="M4.5 8h1.8M9.7 8h1.8M2.8 6.3C3.6 2.8 12.4 2.8 13.2 6.3"/></svg>', "light": '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5.6 1.8h4.8l-.9 3.4H6.5z"/><path d="M6.5 5.2 3.6 13.4c1.5 1 7.3 1 8.8 0L9.5 5.2"/></svg>', "labels": '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.2 2.2h5.4l6.2 6.2-5.4 5.4-6.2-6.2z"/><circle cx="5.3" cy="5.3" r="1"/></svg>'};
    function iconButton(name, label, onClick) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'home-den-live-chip-icon';
        button.dataset.icon = name;
        button.setAttribute('aria-label', label);
        button.title = label;
        button.innerHTML = ICON[name];
        button.addEventListener('click', event => { event.stopPropagation(); onClick(button); });
        return button;
    }
    function place() {
        if (!chip) {
            chip = document.createElement('div');
            chip.className = 'home-den-live-chip' + (capsule ? ' home-den-live-chip--capsule' : '');
            chip.dataset.denLiveChip = '1';
            chip.innerHTML = '<p class="home-den-live-chip-title"><span class="home-den-live-chip-dot" aria-hidden="true"></span>'
                + '<span class="home-den-live-chip-name"></span></p><p class="home-den-live-chip-facts"></p>'
                + '<p class="home-den-live-chip-qwen"></p>'
                + '<p class="home-den-live-chip-banner" role="status" aria-live="polite"></p>';
            chip.querySelector('.home-den-live-chip-name').textContent = capsule ? 'Den' : `Den · live${lookOnly ? ' · look-only' : ''}`;
            if (panelKit && !panel) {
                try {
                    panel = panelKit.createCoordinatorPanel({ url: config.coordinator, lookOnly, spots });
                    chip.append(panel.element);
                } catch (error) { console.warn('[den-live] coordinator panel not mounted', error); panel = null; }
            }
            if (capsule) {
                const actions = document.createElement('span');
                actions.className = 'home-den-live-chip-actions';
                actions.setAttribute('role', 'group');
                actions.setAttribute('aria-label', 'Den view');
                const sheet = document.createElement('div');
                sheet.className = 'home-den-live-sheet';
                sheet.hidden = true;
                if (pipelineKit) actions.append(iconButton('pipeline', 'Pipeline', () => { pipeline?.setOn(!pipeline.status().on); render(); }));
                if (panel) {
                    sheet.append(panel.element);
                    panel.element.open = true;
                    actions.append(iconButton('light', 'Lighting coordinator', button => {
                        sheet.hidden = !sheet.hidden; button.setAttribute('aria-pressed', String(!sheet.hidden));
                    }));
                }
                actions.append(iconButton('labels', 'Labels', () => document.querySelector('.home-den-labels-toggle')?.click()));
                chip.append(actions, sheet);
            }
        }
        const canvas = engine.renderer.domElement;
        const view = canvas.closest('[data-home-apartment-view="1"]') || canvas.parentElement;
        // The capsule sits with the caption and pipeline layers, over the room's label pills.
        const target = capsule ? canvas.closest('.apt-home-stage') || view : view.querySelector(':scope > [data-apt-legacy-toolbar="1"]') || view;
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
        if (capsule) {
            const pressed = String(pipeline?.status().on === true), icon = chip.querySelector('[data-icon="pipeline"]');
            if (icon && icon.getAttribute('aria-pressed') !== pressed) icon.setAttribute('aria-pressed', pressed);
            const labels = chip.querySelector('[data-icon="labels"]'), present = !!document.querySelector('.home-den-labels-toggle');
            if (labels && labels.hidden === present) labels.hidden = !present;
        }
        chip.dataset.banner = kind;
        if (chip.title !== title) chip.title = title;
        setText(chip.querySelector('.home-den-live-chip-facts'), facts);
        setText(chip.querySelector('.home-den-live-chip-qwen'), fresh && sameScan(latest) && !caption ? qwenLine(latest, (now - lastAt) / 1000) : '');
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
            coordinator: panel ? panel.status() : null, spot: spots?.current() ?? null, caption: caption ? caption.status() : null,
            pipeline: pipeline ? pipeline.status() : null }),
        markers: () => layer ? layer.markers() : [],
    });
    return kit => { bodyKit = kit; sync(); };
}
