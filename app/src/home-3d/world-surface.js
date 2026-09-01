/**
 * Cesium outer-world surface for the existing Apartment spatial viewport.
 *
 * This is intentionally a surface adapter, not a product shell: it appends one
 * canvas-owning element to the supplied host and creates no controls, panels,
 * labels, renderer picker, site list, or navigation input.  The Apartment
 * coordinator remains the sole owner of gestures, chrome, and handoff state.
 *
 * Concept attribution: the identity-keyed render governor and monotonic camera
 * ownership patterns were adapted from Bilawal Sidhu's MIT-licensed
 * gods-eye-view at commit d8f1742783cddd6bbc86033d0db06dc6ec746304.
 * This implementation is original and intentionally omits that project's
 * provider URLs, fallbacks, credentials, UI, share state, and data feeds.
 */
import {
    WORLD_CAMERA_BANDS,
    WORLD_CAMERA_BAND_ORDER,
    createIdentityRenderHolds,
    createLatestIntentAuthority,
    normalizeConfirmedAnchor,
    requireWorldCameraBand,
} from './world-surface-core.mjs';

export { WORLD_CAMERA_BANDS, WORLD_CAMERA_BAND_ORDER } from './world-surface-core.mjs';

const REQUIRED_CESIUM_VERSION = '1.144.0';
const VENDORED_CESIUM_ROOT = '../spatial-spike/vendor/cesium/';
const VENDORED_CESIUM_ENTRY = `${VENDORED_CESIUM_ROOT}index.js`;
const BUNDLED_OFFLINE_PLANET = '../spatial-spike/vendor/fixtures/offline-planet.png';

let runtimePromise = null;

function loadVendoredCesium() {
    const runtimeRoot = new URL(VENDORED_CESIUM_ROOT, import.meta.url);
    const runtimeEntry = new URL(VENDORED_CESIUM_ENTRY, import.meta.url);
    globalThis.CESIUM_BASE_URL = runtimeRoot.href;
    runtimePromise ||= import(runtimeEntry.href).catch((error) => {
        runtimePromise = null;
        throw error;
    });
    return runtimePromise;
}

function requireHostElement(host) {
    const HTMLElementClass = host?.ownerDocument?.defaultView?.HTMLElement;
    if (!HTMLElementClass || !(host instanceof HTMLElementClass)) {
        throw new TypeError('world surface host must be an HTMLElement');
    }
    return host;
}

function motionShouldCut(surface, requestedPolicy) {
    if (requestedPolicy === 'cut') return true;
    if (requestedPolicy === 'animate') return false;
    try {
        return Boolean(surface?.ownerDocument?.defaultView?.matchMedia?.(
            '(prefers-reduced-motion: reduce)',
        ).matches);
    } catch (_) {
        return false;
    }
}

function styleSurfaceElement(surface) {
    Object.assign(surface.style, {
        position: 'absolute',
        inset: '0',
        width: '100%',
        height: '100%',
        overflow: 'hidden',
        background: '#07111a',
        contain: 'strict',
        touchAction: 'none',
    });
    surface.setAttribute('aria-hidden', 'true');
    surface.dataset.apartmentWorldSurface = 'cesium';
}

function styleCesiumCanvas(widget, surface) {
    const widgetRoot = surface.querySelector('.cesium-widget');
    if (widgetRoot) {
        Object.assign(widgetRoot.style, {
            position: 'absolute',
            inset: '0',
            width: '100%',
            height: '100%',
            overflow: 'hidden',
        });
    }
    Object.assign(widget.canvas.style, {
        display: 'block',
        width: '100%',
        height: '100%',
        outline: 'none',
    });
    widget.canvas.tabIndex = -1;
    widget.canvas.setAttribute('aria-hidden', 'true');
}

function navigationResult(record, status, reason = null) {
    return Object.freeze({
        intentId: record.token.intentId,
        band: record.token.band,
        status,
        reason,
    });
}

/**
 * Create the renderer instance.  mount(host) never replaces host children;
 * the caller controls stacking and opacity alongside the resident Apartment
 * canvas.  A disposed instance is terminal and must be recreated.
 */
export function createWorldSurface() {
    let host = null;
    let surface = null;
    let Cesium = null;
    let widget = null;
    let anchorPoints = null;
    let anchor = null;
    let mounted = false;
    let mounting = false;
    let disposed = false;
    let running = false;
    let currentBand = null;
    let activeNavigation = null;
    let lifecycleRevision = 0;

    const intentAuthority = createLatestIntentAuthority();

    function requestRender() {
        if (!widget || widget.isDestroyed()) return false;
        widget.scene.requestRender();
        return true;
    }

    function syncRenderMode() {
        if (!widget || widget.isDestroyed()) return;
        // A stopped surface never spins, even if a caller forgot a hold.  When
        // active, exact hold identities temporarily opt into continuous frames.
        widget.scene.requestRenderMode = !running || renderHolds.size() === 0;
        widget.scene.maximumRenderTimeChange = Infinity;
        widget.useDefaultRenderLoop = running;
        requestRender();
    }

    const renderHolds = createIdentityRenderHolds(syncRenderMode);

    function requireMounted() {
        if (disposed) throw new Error('world surface has been disposed');
        if (!mounted || !widget || widget.isDestroyed()) {
            throw new Error('world surface must be mounted first');
        }
    }

    function anchorPosition() {
        return Cesium.Cartesian3.fromDegrees(
            anchor.longitude,
            anchor.latitude,
            anchor.altitudeMeters,
        );
    }

    function bandOffset(band) {
        const definition = WORLD_CAMERA_BANDS[band];
        return new Cesium.HeadingPitchRange(
            0,
            Cesium.Math.toRadians(definition.pitchDegrees),
            definition.rangeMeters,
        );
    }

    function setBandImmediately(band) {
        requireWorldCameraBand(band);
        const center = anchorPosition();
        widget.camera.lookAt(center, bandOffset(band));
        widget.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
        currentBand = band;
        requestRender();
    }

    function settleNavigation(record, status, reason = null) {
        if (!record || record.settled) return false;
        record.settled = true;
        record.releaseRenderHold?.();
        if (activeNavigation === record) activeNavigation = null;
        intentAuthority.complete(record.token);
        record.resolve(navigationResult(record, status, reason));
        syncRenderMode();
        return true;
    }

    function cancelActiveNavigation(status, reason) {
        const record = activeNavigation;
        if (!record) {
            intentAuthority.invalidate();
            return false;
        }
        activeNavigation = null;
        record.settled = true;
        record.releaseRenderHold?.();
        intentAuthority.invalidate();
        record.resolve(navigationResult(record, status, reason));
        if (widget && !widget.isDestroyed()) widget.camera.cancelFlight();
        syncRenderMode();
        return true;
    }

    const api = Object.freeze({
        async mount(nextHost) {
            if (disposed) throw new Error('world surface has been disposed');
            if (mounted || mounting) throw new Error('world surface is already mounted');
            host = requireHostElement(nextHost);
            mounting = true;
            const mountRevision = ++lifecycleRevision;
            const documentRef = host.ownerDocument;
            const nextSurface = documentRef.createElement('div');
            styleSurfaceElement(nextSurface);
            host.append(nextSurface);
            surface = nextSurface;

            let nextWidget = null;
            try {
                const nextCesium = await loadVendoredCesium();
                if (nextCesium.VERSION !== REQUIRED_CESIUM_VERSION) {
                    throw new Error(`unexpected Cesium runtime version: ${nextCesium.VERSION}`);
                }
                if (disposed || lifecycleRevision !== mountRevision) {
                    throw new Error('world surface mount was cancelled');
                }

                // No ion token, default base layer, provider URL, or remote
                // fallback is reachable from this adapter.
                nextCesium.Ion.defaultAccessToken = undefined;
                const detachedCredits = documentRef.createElement('div');
                nextWidget = new nextCesium.CesiumWidget(nextSurface, {
                    animation: false,
                    baseLayer: false,
                    creditContainer: detachedCredits,
                    creditViewport: detachedCredits,
                    contextOptions: {
                        webgl: {
                            alpha: false,
                            antialias: true,
                            preserveDrawingBuffer: false,
                        },
                    },
                    globe: new nextCesium.Globe(nextCesium.Ellipsoid.WGS84),
                    maximumRenderTimeChange: Infinity,
                    moon: false,
                    requestRenderMode: true,
                    scene3DOnly: true,
                    skyAtmosphere: false,
                    skyBox: false,
                    sun: false,
                    terrainProvider: new nextCesium.EllipsoidTerrainProvider(),
                    useBrowserRecommendedResolution: true,
                    useDefaultRenderLoop: false,
                });
                styleCesiumCanvas(nextWidget, nextSurface);

                const offlinePlanet = await nextCesium.SingleTileImageryProvider.fromUrl(
                    new URL(BUNDLED_OFFLINE_PLANET, import.meta.url).href,
                    { rectangle: nextCesium.Rectangle.fromDegrees(-180, -90, 180, 90) },
                );
                if (disposed || lifecycleRevision !== mountRevision) {
                    throw new Error('world surface mount was cancelled');
                }

                Cesium = nextCesium;
                widget = nextWidget;
                widget.imageryLayers.addImageryProvider(offlinePlanet);
                widget.scene.backgroundColor = Cesium.Color.fromCssColorString('#07111a');
                widget.scene.fog.enabled = false;
                widget.scene.globe.baseColor = Cesium.Color.fromCssColorString('#0a2030');
                widget.scene.globe.depthTestAgainstTerrain = false;
                widget.scene.globe.enableLighting = false;
                widget.scene.globe.showGroundAtmosphere = false;
                widget.scene.screenSpaceCameraController.enableInputs = false;
                anchorPoints = widget.scene.primitives.add(new Cesium.PointPrimitiveCollection());
                mounted = true;
                syncRenderMode();
                widget.resize();
                requestRender();
                return api;
            } catch (error) {
                if (nextWidget && !nextWidget.isDestroyed()) nextWidget.destroy();
                nextSurface.remove();
                if (surface === nextSurface) surface = null;
                host = null;
                Cesium = null;
                widget = null;
                anchorPoints = null;
                mounted = false;
                throw error;
            } finally {
                mounting = false;
            }
        },

        setAnchor(nextAnchor) {
            requireMounted();
            const confirmedAnchor = normalizeConfirmedAnchor(nextAnchor);
            cancelActiveNavigation('cancelled', 'anchor-changed');
            anchor = confirmedAnchor;
            anchorPoints.removeAll();
            anchorPoints.add({
                position: anchorPosition(),
                color: Cesium.Color.fromCssColorString('#78d8df'),
                outlineColor: Cesium.Color.fromCssColorString('#e4fbfc'),
                outlineWidth: 2,
                pixelSize: 9,
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
            });
            // Parcel is an anchor-scale camera only.  No parcel outline,
            // building footprint, terrain, or accuracy claim is synthesized.
            setBandImmediately('parcel');
            return api.snapshot();
        },

        navigate({ intentId, band, motion = 'auto', durationMs } = {}) {
            requireMounted();
            if (!anchor) throw new Error('world surface requires a confirmed anchor before navigation');
            requireWorldCameraBand(band);

            const { token } = intentAuthority.begin({ intentId, band });
            const previous = activeNavigation;
            if (previous) {
                activeNavigation = null;
                previous.settled = true;
                previous.releaseRenderHold?.();
                previous.resolve(navigationResult(previous, 'superseded', 'latest-intent'));
                widget.camera.cancelFlight();
            }

            const definition = WORLD_CAMERA_BANDS[band];
            const requestedDuration = durationMs == null
                ? definition.durationMs
                : Math.max(0, Math.min(5_000, Number(durationMs)));
            if (!Number.isFinite(requestedDuration)) {
                intentAuthority.complete(token);
                throw new TypeError('navigation durationMs must be finite');
            }
            const cut = !running || motionShouldCut(surface, motion) || requestedDuration === 0;

            return new Promise((resolve, reject) => {
                const record = {
                    token,
                    resolve,
                    reject,
                    releaseRenderHold: null,
                    settled: false,
                };
                activeNavigation = record;

                if (cut) {
                    try {
                        setBandImmediately(band);
                        settleNavigation(record, 'settled');
                    } catch (error) {
                        activeNavigation = null;
                        intentAuthority.complete(token);
                        record.settled = true;
                        reject(error);
                    }
                    return;
                }

                record.releaseRenderHold = renderHolds.acquire(record);
                syncRenderMode();
                try {
                    widget.camera.flyToBoundingSphere(
                        new Cesium.BoundingSphere(anchorPosition(), 1),
                        {
                            offset: bandOffset(band),
                            duration: requestedDuration / 1_000,
                            complete: () => {
                                if (!intentAuthority.isCurrent(token) || record.settled) return;
                                currentBand = band;
                                settleNavigation(record, 'settled');
                            },
                            cancel: () => {
                                if (record.settled) return;
                                settleNavigation(record, 'cancelled', 'camera-flight-cancelled');
                            },
                        },
                    );
                } catch (error) {
                    activeNavigation = null;
                    intentAuthority.complete(token);
                    record.settled = true;
                    record.releaseRenderHold?.();
                    syncRenderMode();
                    reject(error);
                }
            });
        },

        acquireRenderHold(identity) {
            if (disposed) throw new Error('world surface has been disposed');
            return renderHolds.acquire(identity);
        },

        releaseRenderHold(identity) {
            return renderHolds.release(identity);
        },

        setRunning(nextRunning) {
            if (disposed) throw new Error('world surface has been disposed');
            const shouldRun = nextRunning === true;
            if (running === shouldRun) return api.snapshot();
            running = shouldRun;
            if (!running) cancelActiveNavigation('cancelled', 'surface-suspended');
            syncRenderMode();
            return api.snapshot();
        },

        resize() {
            if (!widget || widget.isDestroyed()) return false;
            widget.resize();
            requestRender();
            return true;
        },

        requestRender,

        snapshot() {
            const authority = intentAuthority.snapshot();
            return Object.freeze({
                renderer: 'cesium',
                rendererVersion: Cesium?.VERSION ?? null,
                mounted,
                running,
                disposed,
                anchorConfirmed: Boolean(anchor),
                currentBand,
                targetBand: authority.activeBand,
                activeIntentId: authority.activeIntentId,
                navigating: Boolean(activeNavigation),
                requestRenderMode: widget && !widget.isDestroyed()
                    ? widget.scene.requestRenderMode
                    : true,
                renderHoldCount: renderHolds.size(),
                imagery: 'bundled-offline-planet',
                terrain: 'ellipsoid',
                parcelGeometry: 'not-provided',
            });
        },

        dispose() {
            if (disposed) return;
            lifecycleRevision += 1;
            cancelActiveNavigation('cancelled', 'surface-disposed');
            running = false;
            renderHolds.clear();
            if (widget && !widget.isDestroyed()) {
                widget.useDefaultRenderLoop = false;
                widget.destroy();
            }
            surface?.remove();
            host = null;
            surface = null;
            Cesium = null;
            widget = null;
            anchorPoints = null;
            anchor = null;
            mounted = false;
            mounting = false;
            currentBand = null;
            disposed = true;
        },
    });

    return api;
}
