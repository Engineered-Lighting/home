/* home-3d/gaussian-splat-webgpu.mjs — Home's Gaussian splat renderer for the node renderer.
 *
 * WebGPU path only (?renderer=webgpu, or ?renderer=webgpu-webgl for the same
 * renderer on its WebGL2 backend). Forked from three.js r186
 * examples/jsm/objects/GaussianSplat.js (MIT, © three.js authors; see
 * vendor/three-0.186.1/LICENSE). What changed, and why:
 *
 *   sort      three's CountingSort (4096 view-depth bins, re-sorted only after
 *             the view turns ~1.8°) is replaced by gaussian-splat-radix-sort.mjs:
 *             a stable radial-distance radix sort, 16-bit keys by default
 *             (32-bit optional), run EVERY frame on the GPU with culling and
 *             an indirect draw of the survivors. A fresh order every frame is
 *             what removes popping while orbiting (Spark's order lags about
 *             three frames); radial keys also stay right under pure rotation.
 *             The WebGL2 backend has no compute: the same radial 16-bit key is
 *             counting-sorted on the CPU, only when the camera has moved.
 *   raster    Spark 2.1's constants, so Photo looks the same on both paths:
 *             quads to sqrt(8) sigma, 0.3 px² low-pass with opacity
 *             compensation, 512 px radius cap, 1.4 clip guard band, alpha
 *             floor 0.5/255, premultiplied blending (three premultiplies the
 *             fragment's straight alpha). The fragment writes raw display
 *             values (fragmentNode, so the seam's sRGB output step is
 *             skipped): splat colours are already display-encoded, as the
 *             WebGL canvas expects.
 *   opacity   an `opacity` uniform (the Photo crossfade) times an optional
 *             keep node (splat-cutaway-tsl.mjs: the dollhouse cut and the
 *             exterior cleanup), in both the vertex stage and the cull.
 *   colour    colorHook(): a per-splat vec4 buffer the later Lit basis blend
 *             (lighting report L4) writes relit colours into, and a switch
 *             between the capture colour and that buffer.
 *   ownership update(renderer, camera) is called by modes.js's splat tick,
 *             before renderer.render(). Nothing dispatches compute from
 *             onBeforeRender: renderer.compute advances renderer.info, which
 *             the equipment depth capture keys its per-frame cache on.
 *   input     plain arrays from gaussian-splat-parse.mjs (parsed in a worker),
 *             not a BufferGeometry; raycast is a no-op (picking uses the
 *             collision proxy, as with Spark).
 */
import {
    Mesh, NodeMaterial, InstancedBufferGeometry, BufferAttribute, StorageBufferAttribute,
    Matrix4, Vector2, Vector3, NormalBlending, DoubleSide, DynamicDrawUsage,
} from 'three/webgpu';
import {
    Fn, If, Discard, cos, sin, sqrt, dot, exp, float, max, min, normalize, length, select, bitcast,
    instanceIndex, positionGeometry, cameraProjectionMatrix, screenSize, highpModelViewMatrix,
    storage, uint, uniform, unpackUnorm4x8, varyingProperty, vec2, vec3, vec4,
} from 'three/tsl';

const cb = new URL(import.meta.url).search;
const { createSplatRadixSort } = await import(`./gaussian-splat-radix-sort.mjs${cb}`);
const { SH_BAND_COMPONENTS, SH_BAND_WORDS } = await import(`./gaussian-splat-parse.mjs${cb}`);

/* Spark 2.1.0 SparkRenderer defaults. */
export const SPLAT_RASTER = Object.freeze({
    maxStdDev: Math.sqrt(8),
    blurAmount: 0.3,
    maxPixelRadius: 512,
    clipXY: 1.4,
    minAlpha: 0.5 / 255,
});
const WORKGROUP = 256;
const NEAR_CULL = 0.01;

const _modelView = new Matrix4();
const _inverse = new Matrix4();
const _worldCentre = new Vector3();
const _scale = new Vector3();
const _camera = new Vector3();
const _eye = new Vector3();

function shBasis(direction) {
    const x = direction.x, y = direction.y, z = direction.z;
    const xx = x.mul(x), yy = y.mul(y), zz = z.mul(z);
    return [
        [y.mul(-0.4886025), z.mul(0.4886025), x.mul(-0.4886025)],
        [x.mul(y).mul(1.0925484), y.mul(z).mul(-1.0925484), zz.mul(2).sub(xx).sub(yy).mul(0.3153915),
            x.mul(z).mul(-1.0925484), xx.sub(yy).mul(0.5462742)],
        [y.mul(xx.mul(3).sub(yy)).mul(-0.5900436), x.mul(y).mul(z).mul(2.8906114),
            y.mul(zz.mul(4).sub(xx).sub(yy)).mul(-0.4570458), z.mul(zz.mul(2).sub(xx.mul(3)).sub(yy.mul(3))).mul(0.3731763),
            x.mul(zz.mul(4).sub(xx).sub(yy)).mul(-0.4570458), z.mul(xx.sub(yy)).mul(1.4453057),
            x.mul(xx.sub(yy.mul(3))).mul(-0.5900436)],
    ];
}

/** View-dependent colour from packed SH bytes, (byte - 128) / 128. */
function shContribution(buffers, splatIndex, centre, cameraLocal, degree) {
    const weights = shBasis(normalize(centre.sub(cameraLocal)).toVar('shDirection'));
    let rgb = vec3(0);
    for (let band = 1; band <= degree; band++) {
        const read = buffers[`sh${band}`], words = SH_BAND_WORDS[band];
        const coefficients = [];
        for (let word = 0, left = SH_BAND_COMPONENTS[band]; left > 0; word++, left -= 4) {
            const packed = read.element(splatIndex.mul(words).add(word)).toVar(`sh${band}w${word}`);
            for (let b = 0; b < Math.min(4, left); b++) {
                coefficients.push(float(packed.shiftRight(b * 8).bitAnd(0xff)).sub(128).div(128));
            }
        }
        weights[band - 1].forEach((weight, k) => {
            rgb = rgb.add(vec3(coefficients[k * 3], coefficients[k * 3 + 1], coefficients[k * 3 + 2]).mul(weight));
        });
    }
    return rgb;
}

export class HomeGaussianSplat extends Mesh {
    /**
     * @param {object} data       gaussian-splat-parse.mjs output
     * @param {object} options
     * @param {object} options.renderer   the initialised WebGPURenderer
     * @param {16|32} [options.keyBits]   radix key width (WebGPU)
     * @param {number} [options.maxShDegree]
     * @param {object} [options.cutaway]  splat-cutaway-tsl.mjs createSplatCutawayNode()
     */
    constructor(data, { renderer, keyBits = 16, maxShDegree = 3, cutaway = null } = {}) {
        const count = data?.count;
        if (!Number.isSafeInteger(count) || count <= 0) throw new Error('splat_data_invalid');
        if (!renderer?.backend) throw new Error('splat_renderer_required');
        const gpu = renderer.backend.isWebGPUBackend === true;
        const geometry = new InstancedBufferGeometry();
        geometry.setAttribute('position', new BufferAttribute(new Float32Array([-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0]), 3));
        geometry.setIndex([0, 1, 2, 0, 2, 3]);
        geometry.instanceCount = count;
        const material = new NodeMaterial();
        material.name = 'apartment-gaussian-splat';
        material.transparent = true;
        material.blending = NormalBlending;
        material.premultipliedAlpha = true;
        material.depthWrite = false;
        material.depthTest = true;
        material.side = DoubleSide;
        material.forceSinglePass = true;
        material.fog = false;
        material.toneMapped = false;
        super(geometry, material);

        this.isHomeGaussianSplat = true;
        this.type = 'HomeGaussianSplat';
        this.name = 'apartment-gaussian-splat';
        this.backend = gpu ? 'webgpu' : 'webgl2';
        this.numSplats = count;
        this.shDegree = data.shDegree | 0;
        this.bounds = data.bounds;
        this._renderer = renderer;
        this._cutaway = cutaway;
        this._attributes = [];
        this._opacity = uniform(1);
        this._cameraLocal = uniform(new Vector3());
        this._colorSource = uniform(0);
        this._sortModelView = uniform(new Matrix4());
        this._sortProjection = uniform(new Matrix4());
        this._sortRange = uniform(new Vector2(0, 1));
        this._stats = { ticks: 0, sorts: 0, cpuSorts: 0, shPasses: 0, lastSortMs: 0, skippedNoCamera: 0 };
        this._lastShCamera = null;
        this._lastCpuSortCamera = null;
        this._disposed = false;

        const attribute = (array, itemSize) => {
            const value = new StorageBufferAttribute(array, itemSize);
            this._attributes.push(value);
            return value;
        };
        const centres = new Float32Array(count * 4), covA = new Float32Array(count * 4), covB = new Float32Array(count * 4);
        for (let i = 0; i < count; i++) {
            centres[i * 4] = data.centers[i * 3]; centres[i * 4 + 1] = data.centers[i * 3 + 1]; centres[i * 4 + 2] = data.centers[i * 3 + 2];
            covA.set(data.covariances.subarray(i * 6, i * 6 + 4), i * 4);
            covB[i * 4] = data.covariances[i * 6 + 4]; covB[i * 4 + 1] = data.covariances[i * 6 + 5];
        }
        this._cpuCentres = data.centers;
        const colorWords = new Uint32Array(data.colors.buffer, data.colors.byteOffset, count);
        this._buffers = {
            centre: storage(attribute(centres, 4), 'vec4', count).toReadOnly(),
            covA: storage(attribute(covA, 4), 'vec4', count).toReadOnly(),
            covB: storage(attribute(covB, 4), 'vec4', count).toReadOnly(),
            color: storage(attribute(new Uint32Array(colorWords), 1), 'uint', count).toReadOnly(),
        };
        for (let band = 1; band <= this.shDegree; band++) {
            this._buffers[`sh${band}`] = storage(attribute(data[`sh${band}`], 1), 'uint', count * SH_BAND_WORDS[band]).toReadOnly();
        }
        if (!gpu) for (const node of Object.values(this._buffers)) node.setPBO(true);

        if (gpu) {
            this._sort = createSplatRadixSort({ count, keyBits });
            this._order = this._sort.orderRead;
            this._sort.setKeyPass((emit) => this._buildKeyPass(emit));
            geometry.setIndirect(this._sort.drawAttribute);
            this._shContribution = attribute(new Float32Array(count * 4), 4);
            this._shRead = storage(this._shContribution, 'vec4', count).toReadOnly();
            this._shWrite = storage(this._shContribution, 'vec4', count);
        } else {
            const order = attribute(new Uint32Array(count).map((_, i) => i), 1);
            order.setUsage(DynamicDrawUsage);
            this._cpuOrder = order;
            this._order = storage(order, 'uint', count).toReadOnly();
            this._order.setPBO(true);
            this._cpuBins = new Uint32Array(count);
            this._cpuCounts = new Uint32Array(65536);
        }
        this._relit = null;
        this.setMaxShDegree(maxShDegree);
        this.frustumCulled = false;
    }

    /** The Photo crossfade (modes.js writes it like SplatMesh.opacity). */
    get opacity() { return this._opacity.value; }
    set opacity(value) { this._opacity.value = Math.min(1, Math.max(0, Number(value) || 0)); }

    get keyBits() { return this._sort?.keyBits ?? 16; }

    /** Splat colour and SH work up to this degree (adaptive-performance knob). */
    setMaxShDegree(degree) {
        const next = Math.max(0, Math.min(this.shDegree, Math.floor(Number(degree) || 0)));
        if (next === this._evalDegree) return false;
        this._evalDegree = next;
        this._lastShCamera = null;
        this._buildNodes();
        return true;
    }

    _keep(centre, covA, covB) {
        return this._cutaway ? this._cutaway.keep(centre, covA, covB) : float(1);
    }

    _buildKeyPass(emit) {
        const b = this._buffers;
        const index = instanceIndex;
        const centre = b.centre.element(index).xyz.toVar('centre');
        const alpha = unpackUnorm4x8(b.color.element(index)).a
            .mul(this._keep(centre, b.covA.element(index), b.covB.element(index))).mul(this._opacity).toVar('alpha');
        const view = this._sortModelView.mul(vec4(centre, 1)).toVar('viewCentre');
        const clip = this._sortProjection.mul(view).toVar('clipCentre');
        const guard = clip.w.mul(SPLAT_RASTER.clipXY);
        const visible = alpha.greaterThanEqual(SPLAT_RASTER.minAlpha)
            .and(view.z.lessThan(-NEAR_CULL))
            .and(clip.x.abs().lessThanEqual(guard)).and(clip.y.abs().lessThanEqual(guard));
        If(visible, () => {
            const distance = length(view.xyz).toVar('distance');
            if (this._sort.keyBits === 32) {
                emit(bitcast(max(distance, 0), 'uint').bitNot());
            } else {
                const range = this._sortRange;
                const t = distance.sub(range.x).div(max(range.y.sub(range.x), 1e-4)).clamp(0, 1);
                emit(uint(65535).sub(uint(t.mul(65535))));
            }
        });
    }

    _buildNodes() {
        const b = this._buffers, degree = this._evalDegree, gpu = this.backend === 'webgpu';
        if (gpu && degree > 0) {
            this._shNode = Fn(() => {
                const centre = b.centre.element(instanceIndex).xyz;
                this._shWrite.element(instanceIndex).assign(vec4(shContribution(b, instanceIndex, centre, this._cameraLocal, degree), 0));
            })().compute(this.numSplats, [WORKGROUP]).setName('HomeSplatSphericalHarmonics');
        } else this._shNode = null;

        const splatUv = varyingProperty('vec2', 'vSplatUv');
        const splatColor = varyingProperty('vec4', 'vSplatColor');
        const relit = this._relit;
        this.material.vertexNode = Fn(() => {
            const index = this._order.element(instanceIndex).toVar('splatIndex');
            const centre = b.centre.element(index).xyz.toVar('centre');
            const covA = b.covA.element(index).toVar('covA');
            const covB = b.covB.element(index).toVar('covB');
            const color = unpackUnorm4x8(b.color.element(index)).toVar('captureColor');
            const rgb = color.rgb.toVar('splatRgb');
            const shWeight = float(1).toVar('shWeight');
            if (relit) {
                const value = relit.read.element(index);
                If(this._colorSource.greaterThan(0.5), () => { rgb.assign(value.rgb); shWeight.assign(value.w); });
            }
            if (degree > 0) {
                const sh = gpu ? this._shRead.element(index).rgb : shContribution(b, index, centre, this._cameraLocal, degree);
                rgb.addAssign(sh.mul(shWeight));
            }
            const alpha = color.a.mul(this._keep(centre, covA, covB)).mul(this._opacity).toVar('splatAlpha');
            splatUv.assign(positionGeometry.xy.mul(SPLAT_RASTER.maxStdDev));

            const viewCentre4 = highpModelViewMatrix.mul(vec4(centre, 1)).toVar('viewCentre4');
            const viewCentre = viewCentre4.xyz;
            const clipCentre = cameraProjectionMatrix.mul(viewCentre4).toVar('clipCentre');
            const m = highpModelViewMatrix;
            const r0 = vec3(m[0].x, m[1].x, m[2].x), r1 = vec3(m[0].y, m[1].y, m[2].y), r2 = vec3(m[0].z, m[1].z, m[2].z);
            const c0 = vec3(covA.x, covA.y, covA.z), c1 = vec3(covA.y, covA.w, covB.x), c2 = vec3(covA.z, covB.x, covB.y);
            const v0 = vec3(dot(r0, c0), dot(r0, c1), dot(r0, c2)).toVar('vc0');
            const v1 = vec3(dot(r1, c0), dot(r1, c1), dot(r1, c2)).toVar('vc1');
            const v2 = vec3(dot(r2, c0), dot(r2, c1), dot(r2, c2)).toVar('vc2');
            const s00 = dot(v0, r0), s01 = dot(v0, r1), s02 = dot(v0, r2), s11 = dot(v1, r1), s12 = dot(v1, r2), s22 = dot(v2, r2);
            const z = min(viewCentre.z, -NEAR_CULL).toVar('z');
            const invZ = float(1).div(z).toVar('invZ');
            const focal = screenSize.mul(0.5).mul(vec2(cameraProjectionMatrix[0].x, cameraProjectionMatrix[1].y)).toVar('focal');
            const j00 = focal.x.negate().mul(invZ), j11 = focal.y.negate().mul(invZ);
            const j02 = focal.x.mul(viewCentre.x).mul(invZ).mul(invZ), j12 = focal.y.mul(viewCentre.y).mul(invZ).mul(invZ);
            const aBase = j00.mul(j00).mul(s00).add(j00.mul(j02).mul(s02).mul(2)).add(j02.mul(j02).mul(s22)).toVar('aBase');
            const bCov = j00.mul(j11).mul(s01).add(j00.mul(j12).mul(s02)).add(j02.mul(j11).mul(s12)).add(j02.mul(j12).mul(s22)).toVar('bCov');
            const cBase = j11.mul(j11).mul(s11).add(j11.mul(j12).mul(s12).mul(2)).add(j12.mul(j12).mul(s22)).toVar('cBase');
            const a = aBase.add(SPLAT_RASTER.blurAmount).toVar('a2d');
            const c = cBase.add(SPLAT_RASTER.blurAmount).toVar('c2d');
            const det = a.mul(c).sub(bCov.mul(bCov)).toVar('det');
            const blurAdjust = sqrt(max(aBase.mul(cBase).sub(bCov.mul(bCov)).div(max(det, 1e-6)), 0));
            const finalAlpha = alpha.mul(blurAdjust).toVar('finalAlpha');
            splatColor.assign(vec4(rgb.clamp(0, 1), finalAlpha));

            const mid = a.add(c).mul(0.5).toVar('mid');
            const delta = sqrt(max(mid.mul(mid).sub(det), 0)).toVar('delta');
            const lambda1 = max(mid.add(delta), 1e-7), lambda2 = max(mid.sub(delta), 1e-7);
            const axis1 = normalize(vec2(select(bCov.abs().lessThan(0.001), float(1), bCov), lambda1.sub(a))).toVar('axis1');
            const axis2 = vec2(axis1.y.negate(), axis1.x);
            const scale1 = min(sqrt(lambda1).mul(SPLAT_RASTER.maxStdDev), SPLAT_RASTER.maxPixelRadius);
            const scale2 = min(sqrt(lambda2).mul(SPLAT_RASTER.maxStdDev), SPLAT_RASTER.maxPixelRadius);
            const offset = axis1.mul(positionGeometry.x).mul(scale1).add(axis2.mul(positionGeometry.y).mul(scale2));
            const clip = clipCentre.add(vec4(offset.mul(2).div(screenSize).mul(clipCentre.w), 0, 0)).toVar('clip');
            const guard = clipCentre.w.mul(SPLAT_RASTER.clipXY);
            If(viewCentre.z.greaterThanEqual(-NEAR_CULL)
                .or(finalAlpha.lessThan(SPLAT_RASTER.minAlpha))
                .or(clipCentre.z.greaterThan(clipCentre.w))
                .or(clipCentre.x.abs().greaterThan(guard)).or(clipCentre.y.abs().greaterThan(guard)), () => {
                clip.assign(vec4(2, 2, 2, 1));
            });
            return clip;
        })();
        this.material.fragmentNode = Fn(() => {
            const r2 = dot(splatUv, splatUv);
            If(r2.greaterThan(SPLAT_RASTER.maxStdDev * SPLAT_RASTER.maxStdDev), () => { Discard(); });
            const alpha = exp(r2.mul(-0.5)).mul(splatColor.a).toVar('fragmentAlpha');
            If(alpha.lessThan(SPLAT_RASTER.minAlpha), () => { Discard(); });
            return vec4(splatColor.rgb, alpha);
        })();
        this.material.needsUpdate = true;
    }

    /**
     * L4 hook: one vec4 per splat (file order). rgb replaces the capture
     * colour in the same display-encoded space, before SH; w scales the
     * capture's view-dependent SH residual (1 keeps it, 0 drops it). A
     * compute pass writes it through `write`; setActive(true) draws from it.
     * WebGPU only: the WebGL2 fallback declares relit splats unavailable.
     */
    colorHook() {
        if (this.backend !== 'webgpu') return { available: false, reason: 'splat_relit_unavailable_on_webgl2' };
        if (!this._relit) {
            const values = new Float32Array(this.numSplats * 4);
            const colors = this._buffers.color.value.array;
            for (let i = 0; i < this.numSplats; i++) {
                const word = colors[i];
                values[i * 4] = (word & 255) / 255; values[i * 4 + 1] = ((word >>> 8) & 255) / 255;
                values[i * 4 + 2] = ((word >>> 16) & 255) / 255; values[i * 4 + 3] = 1;
            }
            const attribute = new StorageBufferAttribute(values, 4);
            this._attributes.push(attribute);
            this._relit = { attribute, read: storage(attribute, 'vec4', this.numSplats).toReadOnly(),
                write: storage(attribute, 'vec4', this.numSplats) };
            this._buildNodes();
        }
        const splat = this;
        return {
            available: true,
            count: this.numSplats,
            layout: 'vec4<f32> per splat, file order: rgb display-encoded colour, w = SH residual weight',
            attribute: this._relit.attribute,
            write: this._relit.write,
            captureColor: this._buffers.color,
            get active() { return splat._colorSource.value > 0.5; },
            setActive(value) { splat._colorSource.value = value ? 1 : 0; },
        };
    }

    /** Per-frame work, before renderer.render(). Returns what ran. */
    update(renderer, camera) {
        if (this._disposed || !camera) return { status: 'skipped' };
        this._stats.ticks += 1;
        camera.updateMatrixWorld();
        this.updateWorldMatrix(true, false);
        _inverse.copy(this.matrixWorld).invert();
        _camera.setFromMatrixPosition(camera.matrixWorld).applyMatrix4(_inverse);
        this._cameraLocal.value.copy(_camera);
        const cameraKey = camera.matrixWorld.elements.join(',') + '|' + this.matrixWorld.elements.join(',');

        if (this.backend === 'webgpu') {
            if (this._shNode && cameraKey !== this._lastShCamera) {
                renderer.compute(this._shNode);
                this._lastShCamera = cameraKey;
                this._stats.shPasses += 1;
            }
            _modelView.multiplyMatrices(camera.matrixWorldInverse, this.matrixWorld);
            this._sortModelView.value.copy(_modelView);
            this._sortProjection.value.copy(camera.projectionMatrix);
            const range = this._depthRange(camera);
            this._sortRange.value.set(range[0], range[1]);
            const started = performance.now();
            this._sort.compute(renderer);
            this._stats.lastSortMs = performance.now() - started;
            this._stats.sorts += 1;
            return { status: 'sorted', backend: 'webgpu' };
        }
        if (cameraKey !== this._lastCpuSortCamera) {
            const started = performance.now();
            this._sortCpu(_camera);
            this._stats.lastSortMs = performance.now() - started;
            this._stats.cpuSorts += 1;
            this._lastCpuSortCamera = cameraKey;
            return { status: 'sorted', backend: 'webgl2' };
        }
        return { status: 'unchanged', backend: 'webgl2' };
    }

    _depthRange(camera) {
        const bounds = this.bounds;
        if (!bounds) return [0, 100];
        _worldCentre.fromArray(bounds.center).applyMatrix4(this.matrixWorld);
        _scale.setFromMatrixScale(this.matrixWorld);
        const radius = bounds.radius * Math.max(_scale.x, _scale.y, _scale.z);
        const distance = _worldCentre.distanceTo(_eye.setFromMatrixPosition(camera.matrixWorld));
        const near = Math.max(0, distance - radius);
        return [near, Math.max(near + 1e-3, distance + radius)];
    }

    /* WebGL2 fallback: the same radial 16-bit key, one counting pass. */
    _sortCpu(cameraLocal) {
        const centres = this._cpuCentres, n = this.numSplats, bins = this._cpuBins, counts = this._cpuCounts;
        const order = this._cpuOrder.array;
        const bounds = this.bounds;
        const cx = cameraLocal.x, cy = cameraLocal.y, cz = cameraLocal.z;
        const d = bounds ? Math.hypot(bounds.center[0] - cx, bounds.center[1] - cy, bounds.center[2] - cz) : 0;
        const near = bounds ? Math.max(0, d - bounds.radius) : 0, far = bounds ? d + bounds.radius : 100;
        const scale = 65535 / Math.max(far - near, 1e-4);
        counts.fill(0);
        for (let i = 0; i < n; i++) {
            const dx = centres[i * 3] - cx, dy = centres[i * 3 + 1] - cy, dz = centres[i * 3 + 2] - cz;
            const t = (Math.sqrt(dx * dx + dy * dy + dz * dz) - near) * scale;
            const key = 65535 - (t <= 0 ? 0 : t >= 65535 ? 65535 : Math.floor(t));
            bins[i] = key; counts[key]++;
        }
        for (let k = 0, sum = 0; k < 65536; k++) { const c = counts[k]; counts[k] = sum; sum += c; }
        for (let i = 0; i < n; i++) order[counts[bins[i]]++] = i;
        this._cpuOrder.needsUpdate = true;
        if (this._order.value?.pbo) this._order.value.pbo.needsUpdate = true;
    }

    /** Test/diagnostic readback: visible count and order violations of the last GPU sort. */
    async verifySortAsync(renderer = this._renderer) {
        if (this.backend !== 'webgpu') return null;
        const [counter, keys] = await Promise.all([renderer.getArrayBufferAsync(this._sort.counterAttribute),
            renderer.getArrayBufferAsync(this._sort.keysAttribute)]);
        const visible = new Uint32Array(counter)[0], sorted = new Uint32Array(keys);
        let violations = 0;
        for (let i = 1; i < visible; i++) if (sorted[i] < sorted[i - 1]) violations++;
        return { visible, violations, keyBits: this._sort.keyBits };
    }

    pipelineReadback() {
        return {
            renderer: 'home-gaussian-splat',
            backend: this.backend,
            count: this.numSplats,
            shDegree: this.shDegree,
            evaluatedShDegree: this._evalDegree,
            sort: this.backend === 'webgpu'
                ? { kind: 'gpu-radix-radial', keyBits: this._sort.keyBits, everyFrame: true }
                : { kind: 'cpu-counting-radial', keyBits: 16, everyFrame: false, onCameraMove: true },
            cutaway: !!this._cutaway,
            colorHook: this._relit ? (this._colorSource.value > 0.5 ? 'relit' : 'capture') : 'unallocated',
            counts: { ...this._stats },
        };
    }

    raycast() { /* picking uses the collision proxy */ }

    dispose() {
        if (this._disposed) return;
        this._disposed = true;
        this.geometry.dispose();
        this.material.dispose();
        this._sort?.dispose(this._renderer);
        for (const attribute of this._attributes) {
            try { this._renderer?.backend?.destroyAttribute?.(attribute); } catch (_) { /* best effort */ }
        }
        this._cutaway?.dispose?.();
        this._attributes.length = 0;
    }
}
