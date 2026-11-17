/* Den labels on the scan, in the den's apartment frame (z up, metres, floor at 0). Presentation only: no DOM, no network,
 * no lighting or identity API. home-den-labels-view.mjs holds the labels and calls set().
 *
 * Two looks. With the Labels tool open, each label is an upright box, a faint fill and its edges; the boxes share one unit
 * geometry, scaled per label, so a drag costs no new geometry. Outside the tool (the quiet look, the owner's pick of
 * 2026-11-14) each label is drawn by its corners alone: short ticks from each end of each edge (home-den-labels-core.mjs
 * labelEdges), brighter when the label is in use, and drawn in to whole edges with a faint fill while it has the pointer.
 * tick() eases between them. The fills are what a click or a hover hits to pick a label (meshes()), in both looks. */
import * as THREE from 'three';
import { labelEdges, edgeSegments } from '../home-den-labels-core.mjs';

const COLORS = Object.freeze({ label: 0xb8d8ff, focus: 0xddeaff, selected: 0x45dfff, check: 0xffb224 });
/* The quiet look: k is how far the ticks are drawn in (0 corners, 1 whole edges), a their opacity, f the fill's. */
export const QUIET = Object.freeze({
    rest: Object.freeze({ k: 0, a: .34, f: 0 }),
    use: Object.freeze({ k: .1, a: .62, f: 0 }),
    focus: Object.freeze({ k: 1, a: .9, f: .045 }),
    off: Object.freeze({ k: 0, a: 0, f: 0 }),
});
const unitBox = new THREE.BoxGeometry(1, 1, 1);
const unitEdges = new THREE.EdgesGeometry(unitBox);
// Seconds to most of the way there: drawing in is quicker than letting go, so a pass of the pointer leaves no flicker.
const RISE_S = .05, FALL_S = .085;

export function attachApartmentLabelsLayer(engine) {
    const parent = engine?.apartmentRoot;
    if (!parent || typeof parent.add !== 'function') throw Error('apartment_labels_root_missing');
    const root = new THREE.Group();
    root.name = 'home-den-labels-owned';
    root.userData.noLightingAuthority = true;
    parent.add(root);
    const boxes = new Map();
    let disposed = false, quiet = false;

    function make(id) {
        const group = new THREE.Group();
        const fill = new THREE.Mesh(unitBox, new THREE.MeshBasicMaterial({ color: COLORS.label, transparent: true, opacity: .06, depthWrite: false }));
        const edges = new THREE.LineSegments(unitEdges, new THREE.LineBasicMaterial({ color: COLORS.label, transparent: true, opacity: .7, depthWrite: false }));
        const tickGeometry = new THREE.BufferGeometry();
        const ticks = new THREE.LineSegments(tickGeometry, new THREE.LineBasicMaterial({ color: COLORS.label, transparent: true, opacity: 0, depthWrite: false }));
        ticks.frustumCulled = false; ticks.visible = false;
        fill.userData.labelId = id;
        fill.renderOrder = edges.renderOrder = ticks.renderOrder = 3;
        group.add(fill, edges);
        root.add(group, ticks);
        return { group, fill, edges, ticks, shape: '', lines: [], positions: null, drawnK: -1,
            k: 0, a: 0, f: 0, want: QUIET.off, focus: false };
    }
    function drop(id) {
        const entry = boxes.get(id);
        if (!entry) return;
        entry.fill.material.dispose(); entry.edges.material.dispose();
        entry.ticks.material.dispose(); entry.ticks.geometry.dispose();
        root.remove(entry.group, entry.ticks);
        boxes.delete(id);
    }
    function drawTicks(entry) {
        edgeSegments(entry.lines, entry.k, entry.positions);
        entry.ticks.geometry.attributes.position.needsUpdate = true;
        entry.drawnK = entry.k;
    }
    function paintQuiet(entry) {
        entry.ticks.material.opacity = entry.a;
        entry.ticks.visible = entry.a > .003;
        entry.ticks.material.color.setHex(entry.focus ? COLORS.focus : COLORS.label);
        entry.fill.material.opacity = entry.f;
        entry.fill.material.visible = entry.f > .002;
        if (Math.abs(entry.k - entry.drawnK) > .001) drawTicks(entry);
    }

    /* labels: den-room-labels-1 labels.
     *   Without quiet: the Labels tool's look. selectedId is drawn bright; check draws them all amber ("check these");
     *   faint is the quieter drawing.
     *   quiet: a Map of label id → 'rest' | 'use' | 'focus' | 'off' (a label missing from it is 'rest'). The quiet look
     *   eases there in tick(); a label's first look, and every change between the two looks, is drawn at once. */
    function set(labels, { selectedId = null, check = false, faint = false, quiet: states = null } = {}) {
        if (disposed) return;
        const live = new Set(), wasQuiet = quiet;
        quiet = states instanceof Map;
        for (const label of labels || []) {
            const { centre_m: c, size_m: s, yaw_deg: yaw } = label.box;
            live.add(label.id);
            const fresh = !boxes.has(label.id);
            const entry = boxes.get(label.id) || make(label.id);
            boxes.set(label.id, entry);
            entry.group.position.set(c[0], c[1], c[2]);
            entry.group.rotation.set(0, 0, yaw * Math.PI / 180);
            entry.group.scale.set(s[0], s[1], Math.max(s[2], .01));
            if (!quiet) {
                const selected = label.id === selectedId, color = check ? COLORS.check : selected ? COLORS.selected : COLORS.label;
                entry.fill.material.color.setHex(color); entry.edges.material.color.setHex(color);
                entry.fill.material.opacity = selected ? .13 : faint ? .035 : .06;
                entry.fill.material.visible = true;
                entry.edges.material.opacity = selected ? 1 : faint ? .45 : .7;
                entry.edges.visible = true; entry.ticks.visible = false;
                continue;
            }
            entry.edges.visible = false;
            entry.fill.material.color.setHex(COLORS.label);
            const shape = JSON.stringify([label.box, label.on_wall]);
            if (shape !== entry.shape) {
                entry.shape = shape;
                entry.lines = labelEdges(label);
                entry.positions = new Float32Array(entry.lines.length * 12);
                entry.ticks.geometry.setAttribute('position', new THREE.BufferAttribute(entry.positions, 3).setUsage(THREE.DynamicDrawUsage));
                entry.drawnK = -1;
            }
            const look = states.get(label.id) || 'rest';
            entry.want = QUIET[look] || QUIET.rest;
            entry.focus = look === 'focus';
            if (fresh || !wasQuiet) { entry.k = entry.want.k; entry.a = entry.want.a; entry.f = entry.want.f; }
            paintQuiet(entry);
        }
        for (const id of [...boxes.keys()]) if (!live.has(id)) drop(id);
    }

    /* Each frame: ease the quiet look towards what set() asked for. reduced (prefers-reduced-motion) jumps. */
    function tick(dt, { reduced = false } = {}) {
        if (disposed || !quiet) return;
        const step = (value, target) => {
            if (reduced || !(dt > 0)) return target;
            const next = value + (target - value) * (1 - Math.exp(-dt / (target > value ? RISE_S : FALL_S)));
            return Math.abs(next - target) < .002 ? target : next;
        };
        for (const entry of boxes.values()) {
            const { want } = entry;
            if (entry.k === want.k && entry.a === want.a && entry.f === want.f) continue;
            entry.k = step(entry.k, want.k); entry.a = step(entry.a, want.a); entry.f = step(entry.f, want.f);
            paintQuiet(entry);
        }
    }

    return {
        set,
        tick,
        meshes: () => [...boxes.values()].map(entry => entry.fill),
        count: () => boxes.size,
        /* For Home's tests: how each label is drawn now. */
        looks: () => Object.fromEntries([...boxes].map(([id, entry]) => [id, quiet
            ? { quiet: true, k: entry.k, opacity: entry.a, fill: entry.f, ticks: entry.ticks.visible, edges: entry.edges.visible }
            : { quiet: false, opacity: entry.edges.material.opacity, fill: entry.fill.material.opacity, ticks: entry.ticks.visible, edges: entry.edges.visible }])),
        dispose() {
            if (disposed) return;
            disposed = true;
            for (const id of [...boxes.keys()]) drop(id);
            parent.remove(root);
        },
    };
}
