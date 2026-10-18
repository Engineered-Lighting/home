/**
 * Start one non-blocking readback of the currently visible WebGL2 framebuffer.
 *
 * The caller must invoke this while the frame it wants is still current (the
 * world surface does that from Cesium's postRender event).  `readPixels` writes
 * into a PIXEL_PACK_BUFFER, so it queues a GPU copy instead of synchronously
 * filling a JavaScript array.  A fence is polled on later animation turns and
 * getBufferSubData runs only after the GPU reports completion.
 *
 * This helper deliberately owns no canvas or context.  The context passed here
 * is the one returned by Cesium's existing canvas, so using it cannot consume a
 * fourth WebGL slot or introduce cross-context canvas capture.
 */

function positiveInteger(value, name) {
    const numeric = Number(value);
    if (!Number.isInteger(numeric) || numeric <= 0) {
        throw new TypeError(`${name} must be a positive integer`);
    }
    return numeric;
}

export function supportsAsyncPixelPackReadback(gl) {
    if (!gl) return false;
    const methods = [
        'createBuffer',
        'bindFramebuffer',
        'bindBuffer',
        'bufferData',
        'readPixels',
        'fenceSync',
        'clientWaitSync',
        'getBufferSubData',
        'deleteSync',
        'deleteBuffer',
        'flush',
        'getParameter',
        'pixelStorei',
    ];
    if (methods.some((name) => typeof gl[name] !== 'function')) return false;
    const constants = [
        'PIXEL_PACK_BUFFER',
        'PIXEL_PACK_BUFFER_BINDING',
        'READ_FRAMEBUFFER',
        'READ_FRAMEBUFFER_BINDING',
        'STREAM_READ',
        'SYNC_GPU_COMMANDS_COMPLETE',
        'TIMEOUT_EXPIRED',
        'WAIT_FAILED',
        'CONDITION_SATISFIED',
        'ALREADY_SIGNALED',
        'RGBA',
        'UNSIGNED_BYTE',
        'PACK_ALIGNMENT',
        'PACK_ROW_LENGTH',
        'PACK_SKIP_ROWS',
        'PACK_SKIP_PIXELS',
    ];
    return constants.every((name) => Number.isFinite(gl[name]));
}

function supportsFramebufferDownscale(gl) {
    if (!gl) return false;
    const methods = [
        'createFramebuffer',
        'createTexture',
        'bindTexture',
        'texParameteri',
        'texImage2D',
        'framebufferTexture2D',
        'checkFramebufferStatus',
        'blitFramebuffer',
        'deleteFramebuffer',
        'deleteTexture',
        'activeTexture',
        'getError',
    ];
    if (methods.some((name) => typeof gl[name] !== 'function')) return false;
    const constants = [
        'DRAW_FRAMEBUFFER',
        'DRAW_FRAMEBUFFER_BINDING',
        'FRAMEBUFFER_COMPLETE',
        'COLOR_ATTACHMENT0',
        'COLOR_BUFFER_BIT',
        'TEXTURE_2D',
        'TEXTURE0',
        'ACTIVE_TEXTURE',
        'TEXTURE_BINDING_2D',
        'TEXTURE_MIN_FILTER',
        'TEXTURE_MAG_FILTER',
        'TEXTURE_WRAP_S',
        'TEXTURE_WRAP_T',
        'LINEAR',
        'CLAMP_TO_EDGE',
        'NO_ERROR',
        'SAMPLES',
    ];
    return constants.every((name) => Number.isFinite(gl[name]));
}

/*
 * A multisampled default framebuffer (the Apartment's three.js canvas is
 * antialias: true) cannot be resolved and scaled in one blit, so it is
 * resolved first, at full size, into a single-sample texture of the same
 * format -- WebGL2 requires the formats to match for a resolve, and the
 * default framebuffer is RGBA8 with alpha and RGB8 without -- and that texture
 * is then scaled like any single-sample source. Measured at the outward commit
 * (round 2, RTX 4090): without it the copy crossed to the CPU at full size --
 * 11.7 MB at 1440x900 @ 1.5, 14.7 MB at 2560x1440, 33.2 MB at 2560x1440 @ 2
 * (3840x2160, the Apartment's DPR cap) -- and collecting it took 4-15 ms of
 * main thread (getBufferSubData 1-10 ms, then the CPU reduction 2-6 ms). With
 * it the copy is 0.3-0.7 MB, collected in 0.1-0.4 ms; the resolve's error
 * and completeness checks cost 0.2-0.6 ms when the capture is queued.
 */
function supportsMultisampleResolve(gl) {
    if (!gl || typeof gl.getContextAttributes !== 'function') return false;
    return ['NEAREST', 'RGB'].every((name) => Number.isFinite(gl[name]));
}

function defaultFramebufferFormat(gl) {
    let attributes = null;
    try { attributes = gl.getContextAttributes(); } catch (_) { attributes = null; }
    if (!attributes) return null;
    return attributes.alpha === false ? gl.RGB : gl.RGBA;
}

function defaultSchedule(callback) {
    if (typeof globalThis.requestAnimationFrame === 'function') {
        return Object.freeze({
            kind: 'animation-frame',
            id: globalThis.requestAnimationFrame(callback),
        });
    }
    return Object.freeze({
        kind: 'timer',
        id: globalThis.setTimeout(callback, 0),
    });
}

function defaultCancel(handle) {
    if (!handle) return;
    if (handle.kind === 'animation-frame'
        && typeof globalThis.cancelAnimationFrame === 'function') {
        globalThis.cancelAnimationFrame(handle.id);
        return;
    }
    globalThis.clearTimeout(handle.id);
}

/**
 * @returns {null|{promise: Promise<object|null>, cancel: () => boolean}}
 * `null` means the caller should use its deterministic synchronous fallback.
 */
export function beginAsyncPixelPackReadback({
    gl,
    width,
    height,
    targetWidth = width,
    targetHeight = height,
    schedulePoll = defaultSchedule,
    cancelPoll = defaultCancel,
} = {}) {
    const sourceWidth = positiveInteger(width, 'width');
    const sourceHeight = positiveInteger(height, 'height');
    const requestedWidth = positiveInteger(targetWidth, 'targetWidth');
    const requestedHeight = positiveInteger(targetHeight, 'targetHeight');
    const reducedWidth = Math.min(sourceWidth, requestedWidth);
    const reducedHeight = Math.min(sourceHeight, requestedHeight);
    const wantsDownscale = reducedWidth < sourceWidth || reducedHeight < sourceHeight;
    let defaultSamples = 0;
    const downscaleSupported = wantsDownscale && supportsFramebufferDownscale(gl);
    if (downscaleSupported) {
        try { defaultSamples = Number(gl.getParameter(gl.SAMPLES)) || 0; }
        catch { defaultSamples = Number.POSITIVE_INFINITY; }
    }
    /* WebGL2 cannot both resolve a multisampled READ framebuffer and scale it
     * in one blit. The production Cesium canvas disables default-buffer MSAA
     * and uses FXAA; a multisampled canvas (the Apartment's) is resolved at full
     * size on the GPU first (supportsMultisampleResolve), and one that cannot be
     * safely retains the full-size asynchronous PBO fallback. */
    const resolveFormat = downscaleSupported && defaultSamples > 0 && Number.isFinite(defaultSamples)
        && supportsMultisampleResolve(gl)
        ? defaultFramebufferFormat(gl)
        : null;
    const resolvesMultisample = resolveFormat !== null;
    const canDownscale = downscaleSupported && (defaultSamples === 0 || resolvesMultisample);
    const frameWidth = canDownscale ? reducedWidth : sourceWidth;
    const frameHeight = canDownscale ? reducedHeight : sourceHeight;
    if (!supportsAsyncPixelPackReadback(gl)) return null;
    if (typeof schedulePoll !== 'function' || typeof cancelPoll !== 'function') {
        throw new TypeError('pixel readback schedulers must be functions');
    }

    const byteLength = frameWidth * frameHeight * 4;
    if (!Number.isSafeInteger(byteLength)) return null;

    let pixelBuffer = null;
    let fence = null;
    let downscaleFramebuffer = null;
    let downscaleTexture = null;
    let resolveFramebuffer = null;
    let resolveTexture = null;
    let pollHandle = null;
    let settled = false;
    let resolvePromise = null;

    const releaseGpuResources = () => {
        if (pollHandle !== null) {
            try { cancelPoll(pollHandle); } catch (_) { /* scheduler already gone */ }
            pollHandle = null;
        }
        if (fence) {
            try { gl.deleteSync(fence); } catch (_) { /* context may be lost */ }
            fence = null;
        }
        if (pixelBuffer) {
            try { gl.deleteBuffer(pixelBuffer); } catch (_) { /* context may be lost */ }
            pixelBuffer = null;
        }
        if (downscaleFramebuffer) {
            try { gl.deleteFramebuffer(downscaleFramebuffer); } catch (_) { /* context may be lost */ }
            downscaleFramebuffer = null;
        }
        if (downscaleTexture) {
            try { gl.deleteTexture(downscaleTexture); } catch (_) { /* context may be lost */ }
            downscaleTexture = null;
        }
        releaseResolveTarget();
    };

    /* The full-size resolve is only a stepping stone to the scaled copy; the
     * GPU keeps it alive for the queued blit, so it is released right away. */
    function releaseResolveTarget() {
        if (resolveFramebuffer) {
            try { gl.deleteFramebuffer(resolveFramebuffer); } catch (_) { /* context may be lost */ }
            resolveFramebuffer = null;
        }
        if (resolveTexture) {
            try { gl.deleteTexture(resolveTexture); } catch (_) { /* context may be lost */ }
            resolveTexture = null;
        }
    }

    const settle = (value) => {
        if (settled) return false;
        settled = true;
        releaseGpuResources();
        resolvePromise?.(value);
        return true;
    };

    /*
     * Queue the copy while the caller's postRender callback still owns the
     * visible backbuffer.  Every context-wide binding touched here is restored
     * in this same task, before Cesium or another observer can render again.
     */
    try {
        pixelBuffer = gl.createBuffer();
        if (!pixelBuffer) return null;
        const previousReadFramebuffer = gl.getParameter(gl.READ_FRAMEBUFFER_BINDING);
        const previousDrawFramebuffer = canDownscale
            ? gl.getParameter(gl.DRAW_FRAMEBUFFER_BINDING)
            : null;
        const previousPackBuffer = gl.getParameter(gl.PIXEL_PACK_BUFFER_BINDING);
        const previousPackAlignment = gl.getParameter(gl.PACK_ALIGNMENT);
        const previousPackRowLength = gl.getParameter(gl.PACK_ROW_LENGTH);
        const previousPackSkipRows = gl.getParameter(gl.PACK_SKIP_ROWS);
        const previousPackSkipPixels = gl.getParameter(gl.PACK_SKIP_PIXELS);
        const previousActiveTexture = canDownscale
            ? gl.getParameter(gl.ACTIVE_TEXTURE)
            : null;
        let previousTexture = null;
        try {
            /* Cesium can leave an implementation framebuffer bound at the end
             * of a pass. Its synchronous Context.readPixels(undefined) selects
             * the default framebuffer; mirror that explicitly so the async path
             * captures the composited frame the user sees, not an incidental
             * intermediate target. */
            gl.bindFramebuffer(gl.READ_FRAMEBUFFER, null);
            if (canDownscale) {
                /* Resolve and scale wholly on Cesium's existing GPU context.
                 * Only the bounded compositor-sized texture enters the PBO, so
                 * a 4K viewport no longer queues a 31.6 MiB GPU->CPU transfer
                 * merely to discard most pixels in JavaScript. */
                gl.activeTexture(gl.TEXTURE0);
                previousTexture = gl.getParameter(gl.TEXTURE_BINDING_2D);
                downscaleTexture = gl.createTexture();
                downscaleFramebuffer = gl.createFramebuffer();
                if (!downscaleTexture || !downscaleFramebuffer) {
                    throw new Error('WebGL downscale target allocation failed');
                }
                gl.bindTexture(gl.TEXTURE_2D, downscaleTexture);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
                gl.texImage2D(
                    gl.TEXTURE_2D,
                    0,
                    gl.RGBA,
                    frameWidth,
                    frameHeight,
                    0,
                    gl.RGBA,
                    gl.UNSIGNED_BYTE,
                    null,
                );
                gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, downscaleFramebuffer);
                gl.framebufferTexture2D(
                    gl.DRAW_FRAMEBUFFER,
                    gl.COLOR_ATTACHMENT0,
                    gl.TEXTURE_2D,
                    downscaleTexture,
                    0,
                );
                if (gl.checkFramebufferStatus(gl.DRAW_FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
                    throw new Error('WebGL downscale framebuffer is incomplete');
                }
                if (gl.getError() !== gl.NO_ERROR) {
                    throw new Error('WebGL context was not clean before downscale');
                }
                if (resolvesMultisample) {
                    /* Same rectangle, same format, NEAREST: a pure resolve. */
                    resolveTexture = gl.createTexture();
                    resolveFramebuffer = gl.createFramebuffer();
                    if (!resolveTexture || !resolveFramebuffer) {
                        throw new Error('WebGL resolve target allocation failed');
                    }
                    gl.bindTexture(gl.TEXTURE_2D, resolveTexture);
                    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
                    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
                    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
                    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
                    gl.texImage2D(
                        gl.TEXTURE_2D,
                        0,
                        resolveFormat,
                        sourceWidth,
                        sourceHeight,
                        0,
                        resolveFormat,
                        gl.UNSIGNED_BYTE,
                        null,
                    );
                    gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, resolveFramebuffer);
                    gl.framebufferTexture2D(
                        gl.DRAW_FRAMEBUFFER,
                        gl.COLOR_ATTACHMENT0,
                        gl.TEXTURE_2D,
                        resolveTexture,
                        0,
                    );
                    if (gl.checkFramebufferStatus(gl.DRAW_FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
                        throw new Error('WebGL resolve framebuffer is incomplete');
                    }
                    gl.blitFramebuffer(
                        0,
                        0,
                        sourceWidth,
                        sourceHeight,
                        0,
                        0,
                        sourceWidth,
                        sourceHeight,
                        gl.COLOR_BUFFER_BIT,
                        gl.NEAREST,
                    );
                    if (gl.getError() !== gl.NO_ERROR) {
                        throw new Error('WebGL multisample resolve failed');
                    }
                    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, resolveFramebuffer);
                    gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, downscaleFramebuffer);
                }
                gl.blitFramebuffer(
                    0,
                    0,
                    sourceWidth,
                    sourceHeight,
                    0,
                    0,
                    frameWidth,
                    frameHeight,
                    gl.COLOR_BUFFER_BIT,
                    gl.LINEAR,
                );
                if (gl.getError() !== gl.NO_ERROR) {
                    throw new Error('WebGL framebuffer downscale failed');
                }
                gl.bindFramebuffer(gl.READ_FRAMEBUFFER, downscaleFramebuffer);
            }
            gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pixelBuffer);
            gl.bufferData(gl.PIXEL_PACK_BUFFER, byteLength, gl.STREAM_READ);
            gl.pixelStorei(gl.PACK_ALIGNMENT, 4);
            gl.pixelStorei(gl.PACK_ROW_LENGTH, 0);
            gl.pixelStorei(gl.PACK_SKIP_ROWS, 0);
            gl.pixelStorei(gl.PACK_SKIP_PIXELS, 0);
            gl.readPixels(
                0,
                0,
                frameWidth,
                frameHeight,
                gl.RGBA,
                gl.UNSIGNED_BYTE,
                0,
            );
            fence = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
            if (!fence) throw new Error('WebGL fence allocation failed');
            gl.flush();
        } finally {
            gl.bindBuffer(gl.PIXEL_PACK_BUFFER, previousPackBuffer);
            gl.bindFramebuffer(gl.READ_FRAMEBUFFER, previousReadFramebuffer);
            if (canDownscale) {
                gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, previousDrawFramebuffer);
                gl.bindTexture(gl.TEXTURE_2D, previousTexture);
                gl.activeTexture(previousActiveTexture);
            }
            gl.pixelStorei(gl.PACK_ALIGNMENT, previousPackAlignment);
            gl.pixelStorei(gl.PACK_ROW_LENGTH, previousPackRowLength);
            gl.pixelStorei(gl.PACK_SKIP_ROWS, previousPackSkipRows);
            gl.pixelStorei(gl.PACK_SKIP_PIXELS, previousPackSkipPixels);
        }
        /* Nothing is bound to it any more; the queued blit keeps it alive. */
        releaseResolveTarget();
    } catch (_) {
        releaseGpuResources();
        /* A driver may reject a default-framebuffer scale blit even though it
         * advertises the complete WebGL2 API. Keep the readback asynchronous:
         * retry a full-size PBO and let the caller CPU-reduce its result before
         * falling all the way back to synchronous Context.readPixels. */
        if (canDownscale) {
            return beginAsyncPixelPackReadback({
                gl,
                width: sourceWidth,
                height: sourceHeight,
                targetWidth: sourceWidth,
                targetHeight: sourceHeight,
                schedulePoll,
                cancelPoll,
            });
        }
        return null;
    }

    const promise = new Promise((resolve) => { resolvePromise = resolve; });

    const poll = () => {
        pollHandle = null;
        if (settled) return;
        let status;
        try {
            status = gl.clientWaitSync(fence, 0, 0);
        } catch (_) {
            settle(null);
            return;
        }

        if (status === gl.TIMEOUT_EXPIRED) {
            try {
                pollHandle = schedulePoll(poll);
            } catch (_) {
                settle(null);
            }
            return;
        }
        if (status !== gl.ALREADY_SIGNALED && status !== gl.CONDITION_SATISFIED) {
            settle(null);
            return;
        }

        const pixels = new Uint8Array(byteLength);
        try {
            /* Preserve whichever pack buffer another subsystem owns now.  This
             * happens on a later task, so restoring the binding captured at
             * initiation would be stale and could corrupt unrelated work. */
            const currentPackBuffer = gl.getParameter(gl.PIXEL_PACK_BUFFER_BINDING);
            try {
                gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pixelBuffer);
                gl.getBufferSubData(gl.PIXEL_PACK_BUFFER, 0, pixels);
            } finally {
                gl.bindBuffer(gl.PIXEL_PACK_BUFFER, currentPackBuffer);
            }
        } catch (_) {
            settle(null);
            return;
        }

        settle(Object.freeze({
            width: frameWidth,
            height: frameHeight,
            bottomUp: true,
            pixels,
        }));
    };

    try {
        pollHandle = schedulePoll(poll);
    } catch (_) {
        settle(null);
    }

    return Object.freeze({
        promise,
        cancel() { return settle(null); },
    });
}
