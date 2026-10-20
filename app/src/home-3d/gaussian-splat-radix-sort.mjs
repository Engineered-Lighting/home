/* home-3d/gaussian-splat-radix-sort.mjs — per-frame GPU radix sort for splats.
 *
 * WebGPU path only (?renderer=webgpu). Replaces three r186's CountingSort
 * (4096 view-depth bins, unordered atomic scatter, re-sorted only after the
 * view turns ~1.8°) with the research spike's sort: a stable LSD radix sort
 * on 16-bit (default) or 32-bit keys, run every frame.
 *
 * Built from TSL compute nodes, which three compiles to WGSL. Workgroup
 * atomics and barriers only: no subgroup operations, so it runs on Safari /
 * iOS 26 (no `subgroups`) as on Chrome. Per frame:
 *
 *   reset     the visible counter;
 *   keys      the caller's pass: culls, then emit(key) appends (key, index)
 *             to the A buffers through an atomic counter;
 *   args      indirect dispatch size for the passes below, and the indirect
 *             draw's instance count (the visible splats);
 *   per 8 key bits: histogram (per 2048-key block), one-workgroup scan,
 *             stable scatter ranked by a per-digit ballot bitmask.
 *
 * Passes alternate A -> B -> A; 16 and 32 bit both end in A, which the
 * vertex stage reads in order. Keys sort ascending, so callers encode the
 * farthest splat as the smallest key (back-to-front blending).
 */
import { StorageBufferAttribute, IndirectStorageBufferAttribute } from 'three/webgpu';
import {
    Fn, If, Loop, uint, storage, instanceIndex, workgroupId, invocationLocalIndex,
    workgroupArray, workgroupBarrier, atomicAdd, atomicOr, atomicLoad, atomicStore, countOneBits,
} from 'three/tsl';

export const RADIX_WORKGROUP = 256;
export const RADIX_ITEMS = 8;
export const RADIX_BLOCK = RADIX_WORKGROUP * RADIX_ITEMS;
export const RADIX_KEY_BITS = Object.freeze([16, 32]);

/** Index count of the splat quad (two triangles), the indexed draw's first word. */
const QUAD_INDEX_COUNT = 6;

export function createSplatRadixSort({ count, keyBits = 16 }) {
    if (!RADIX_KEY_BITS.includes(keyBits)) throw new Error('splat_sort_key_bits_invalid');
    if (!Number.isSafeInteger(count) || count <= 0) throw new Error('splat_sort_count_invalid');
    const passes = keyBits / 8;
    const maxBlocks = Math.max(1, Math.ceil(count / RADIX_BLOCK));
    const u32 = (length) => new StorageBufferAttribute(new Uint32Array(length), 1);

    const keysA = u32(count), keysB = u32(count), valsA = u32(count), valsB = u32(count);
    // Until the first sort runs, draw every splat in file order.
    for (let i = 0; i < count; i++) valsA.array[i] = i;
    const histAttribute = u32(256 * maxBlocks);
    const counterAttribute = u32(4);
    const dispatchAttribute = new IndirectStorageBufferAttribute(new Uint32Array([maxBlocks, 1, 1]), 1);
    const drawAttribute = new IndirectStorageBufferAttribute(new Uint32Array([QUAD_INDEX_COUNT, count, 0, 0, 0]), 1);

    const node = (attribute, length) => storage(attribute, 'uint', length);
    const counterAtomic = node(counterAttribute, 4).toAtomic();
    const counterRead = node(counterAttribute, 4).toReadOnly();
    const hist = node(histAttribute, 256 * maxBlocks);
    const histRead = node(histAttribute, 256 * maxBlocks).toReadOnly();
    const buffers = {
        A: { keys: node(keysA, count), vals: node(valsA, count), keysRead: node(keysA, count).toReadOnly(), valsRead: node(valsA, count).toReadOnly() },
        B: { keys: node(keysB, count), vals: node(valsB, count), keysRead: node(keysB, count).toReadOnly(), valsRead: node(valsB, count).toReadOnly() },
    };

    const resetNode = Fn(() => {
        atomicStore(counterAtomic.element(0), uint(0));
    })().compute(1).setName('HomeSplatSortReset');

    const argsNode = Fn(() => {
        const visible = counterRead.element(0).toVar('visible');
        const dispatch = node(dispatchAttribute, 3), draw = node(drawAttribute, 5);
        dispatch.element(0).assign(visible.add(RADIX_BLOCK - 1).div(RADIX_BLOCK));
        dispatch.element(1).assign(uint(1));
        dispatch.element(2).assign(uint(1));
        draw.element(0).assign(uint(QUAD_INDEX_COUNT));
        draw.element(1).assign(visible);
        draw.element(2).assign(uint(0));
        draw.element(3).assign(uint(0));
        draw.element(4).assign(uint(0));
    })().compute(1).setName('HomeSplatSortArgs');

    function histogramNode(input, shift) {
        const local = workgroupArray('uint', 256).setAtomic(true);
        return Fn(() => {
            const li = invocationLocalIndex;
            const block = workgroupId.x;
            atomicStore(local.element(li), uint(0));
            workgroupBarrier();
            const visible = counterRead.element(0);
            Loop({ start: uint(0), end: uint(RADIX_ITEMS), type: 'uint', condition: '<', name: 'item' }, ({ item }) => {
                const index = block.mul(RADIX_BLOCK).add(item.mul(RADIX_WORKGROUP)).add(li).toVar('index');
                If(index.lessThan(visible), () => {
                    const digit = input.keysRead.element(index).shiftRight(uint(shift)).bitAnd(uint(255));
                    atomicAdd(local.element(digit), uint(1));
                });
            });
            workgroupBarrier();
            hist.element(block.mul(256).add(li)).assign(atomicLoad(local.element(li)));
        })().compute(maxBlocks * RADIX_WORKGROUP, [RADIX_WORKGROUP]).setName(`HomeSplatSortHistogram${shift}`);
    }

    /* Exclusive prefix over (digit, block): each thread owns one digit and
     * walks the blocks; thread 0 then scans the 256 digit totals. */
    const totals = workgroupArray('uint', 256);
    const scanNode = Fn(() => {
        const digit = invocationLocalIndex;
        const blocks = counterRead.element(0).add(RADIX_BLOCK - 1).div(RADIX_BLOCK).toVar('blocks');
        const running = uint(0).toVar('running');
        Loop({ start: uint(0), end: blocks, type: 'uint', condition: '<', name: 'b' }, ({ b }) => {
            const at = b.mul(256).add(digit);
            const value = hist.element(at).toVar('value');
            hist.element(at).assign(running);
            running.addAssign(value);
        });
        totals.element(digit).assign(running);
        workgroupBarrier();
        If(digit.equal(uint(0)), () => {
            const sum = uint(0).toVar('sum');
            Loop({ start: uint(0), end: uint(256), type: 'uint', condition: '<', name: 'j' }, ({ j }) => {
                const value = totals.element(j).toVar('total');
                totals.element(j).assign(sum);
                sum.addAssign(value);
            });
        });
        workgroupBarrier();
        const base = totals.element(digit).toVar('base');
        Loop({ start: uint(0), end: blocks, type: 'uint', condition: '<', name: 'b2' }, ({ b2 }) => {
            hist.element(b2.mul(256).add(digit)).addAssign(base);
        });
    })().compute(RADIX_WORKGROUP, [RADIX_WORKGROUP]).setName('HomeSplatSortScan');

    /* Stable within a block: a key's rank among equal digits is the count of
     * lower-numbered threads with that digit (a 256-bit ballot per digit),
     * and the block's items are taken in thread order, 256 at a time. */
    function scatterNode(input, output, shift) {
        const offsets = workgroupArray('uint', 256);
        const ballot = workgroupArray('uint', 256 * 8).setAtomic(true);
        return Fn(() => {
            const li = invocationLocalIndex;
            const block = workgroupId.x;
            offsets.element(li).assign(histRead.element(block.mul(256).add(li)));
            const visible = counterRead.element(0);
            const word = li.div(32).toVar('word');
            const bit = li.bitAnd(uint(31)).toVar('bit');
            Loop({ start: uint(0), end: uint(RADIX_ITEMS), type: 'uint', condition: '<', name: 'item' }, ({ item }) => {
                Loop({ start: uint(0), end: uint(8), type: 'uint', condition: '<', name: 'clear' }, ({ clear }) => {
                    atomicStore(ballot.element(li.mul(8).add(clear)), uint(0));
                });
                workgroupBarrier();
                const index = block.mul(RADIX_BLOCK).add(item.mul(RADIX_WORKGROUP)).add(li).toVar('index');
                const valid = index.lessThan(visible).toVar('valid');
                const key = uint(0).toVar('key');
                const digit = uint(0).toVar('digit');
                If(valid, () => {
                    key.assign(input.keysRead.element(index));
                    digit.assign(key.shiftRight(uint(shift)).bitAnd(uint(255)));
                    atomicOr(ballot.element(digit.mul(8).add(word)), uint(1).shiftLeft(bit));
                });
                workgroupBarrier();
                If(valid, () => {
                    const rank = uint(0).toVar('rank');
                    Loop({ start: uint(0), end: word, type: 'uint', condition: '<', name: 'w' }, ({ w }) => {
                        rank.addAssign(countOneBits(atomicLoad(ballot.element(digit.mul(8).add(w)))));
                    });
                    const lower = uint(1).shiftLeft(bit).sub(uint(1));
                    rank.addAssign(countOneBits(atomicLoad(ballot.element(digit.mul(8).add(word))).bitAnd(lower)));
                    const target = offsets.element(digit).add(rank).toVar('destination');
                    output.keys.element(target).assign(key);
                    output.vals.element(target).assign(input.valsRead.element(index));
                });
                workgroupBarrier();
                const taken = uint(0).toVar('taken');
                Loop({ start: uint(0), end: uint(8), type: 'uint', condition: '<', name: 'own' }, ({ own }) => {
                    taken.addAssign(countOneBits(atomicLoad(ballot.element(li.mul(8).add(own)))));
                });
                offsets.element(li).addAssign(taken);
                workgroupBarrier();
            });
        })().compute(maxBlocks * RADIX_WORKGROUP, [RADIX_WORKGROUP]).setName(`HomeSplatSortScatter${shift}`);
    }

    const sortPasses = [];
    for (let pass = 0; pass < passes; pass++) {
        const [input, output] = pass % 2 === 0 ? [buffers.A, buffers.B] : [buffers.B, buffers.A];
        sortPasses.push({ histogram: histogramNode(input, pass * 8), scatter: scatterNode(input, output, pass * 8) });
    }

    let keyNode = null;
    const attributes = [keysA, keysB, valsA, valsB, histAttribute, counterAttribute, dispatchAttribute, drawAttribute];

    return {
        count,
        keyBits,
        passes,
        maxBlocks,
        /** The sorted splat indices, back to front, for the vertex stage. */
        orderRead: buffers.A.valsRead,
        /** Sorted keys, for verification readback. */
        keysAttribute: keysA,
        orderAttribute: valsA,
        counterAttribute,
        /** geometry.setIndirect(): indexed draw of the visible splats. */
        drawAttribute,
        /**
         * Builds the keys pass. `build(emit)` returns a TSL Fn body run once per
         * splat (instanceIndex); calling emit(keyNode) inside it appends that
         * splat. Splats that never emit are culled from sort and draw.
         */
        setKeyPass(build) {
            const emit = (key) => {
                const slot = atomicAdd(counterAtomic.element(0), uint(1)).toVar('slot');
                buffers.A.keys.element(slot).assign(key);
                buffers.A.vals.element(slot).assign(instanceIndex);
            };
            keyNode = Fn(() => {
                If(instanceIndex.lessThan(uint(count)), () => { build(emit); });
            })().compute(count, [RADIX_WORKGROUP]).setName('HomeSplatSortKeys');
        },
        /** One full sort on the GPU; queue order makes it precede the next render. */
        compute(renderer) {
            if (!keyNode) throw new Error('splat_sort_key_pass_missing');
            renderer.compute(resetNode);
            renderer.compute(keyNode);
            renderer.compute(argsNode);
            for (const pass of sortPasses) {
                renderer.compute(pass.histogram, dispatchAttribute);
                renderer.compute(scanNode);
                renderer.compute(pass.scatter, dispatchAttribute);
            }
        },
        attributes,
        dispose(renderer) {
            for (const attribute of attributes) {
                try { renderer?.backend?.destroyAttribute?.(attribute); } catch (_) { /* best effort */ }
            }
        },
    };
}
