/* Den labels on the scan: one upright box per label, a faint fill and its edges, in the den's apartment frame (z up,
 * metres, floor at 0). Presentation only: no DOM, no network, no lighting or identity API. home-den-labels-view.mjs
 * holds the labels and calls set(); boxes share one unit geometry, scaled per label, so a drag costs no new geometry.
 * The fills are what a click hits to pick a label (meshes()). */
import * as THREE from 'three';

const COLORS = Object.freeze({ label: 0xb8d8ff, selected: 0x45dfff, check: 0xffb224 });
const unitBox = new THREE.BoxGeometry(1, 1, 1);
const unitEdges = new THREE.EdgesGeometry(unitBox);

export function attachApartmentLabelsLayer(engine) {
    const parent = engine?.apartmentRoot;
    if (!parent || typeof parent.add !== 'function') throw Error('apartment_labels_root_missing');
    const root = new THREE.Group();
    root.name = 'home-den-labels-owned';
    root.userData.noLightingAuthority = true;
    parent.add(root);
    const boxes = new Map();
    let disposed = false;

    function make(id) {
        const group = new THREE.Group();
        const fill = new THREE.Mesh(unitBox, new THREE.MeshBasicMaterial({ color: COLORS.label, transparent: true, opacity: .06, depthWrite: false }));
        const edges = new THREE.LineSegments(unitEdges, new THREE.LineBasicMaterial({ color: COLORS.label, transparent: true, opacity: .7, depthWrite: false }));
        fill.userData.labelId = id;
        fill.renderOrder = edges.renderOrder = 3;
        group.add(fill, edges);
        root.add(group);
        return { group, fill, edges };
    }
    function drop(id) {
        const entry = boxes.get(id);
        if (!entry) return;
        entry.fill.material.dispose(); entry.edges.material.dispose();
        root.remove(entry.group);
        boxes.delete(id);
    }

    /* labels: den-room-labels-1 labels. selectedId is drawn bright; check draws them all amber ("check these");
     * faint is the look-only view's quieter drawing. */
    function set(labels, { selectedId = null, check = false, faint = false } = {}) {
        if (disposed) return;
        const live = new Set();
        for (const label of labels || []) {
            const { centre_m: c, size_m: s, yaw_deg: yaw } = label.box;
            live.add(label.id);
            const entry = boxes.get(label.id) || make(label.id);
            boxes.set(label.id, entry);
            entry.group.position.set(c[0], c[1], c[2]);
            entry.group.rotation.set(0, 0, yaw * Math.PI / 180);
            entry.group.scale.set(s[0], s[1], Math.max(s[2], .01));
            const selected = label.id === selectedId, color = check ? COLORS.check : selected ? COLORS.selected : COLORS.label;
            entry.fill.material.color.setHex(color); entry.edges.material.color.setHex(color);
            entry.fill.material.opacity = selected ? .13 : faint ? .035 : .06;
            entry.edges.material.opacity = selected ? 1 : faint ? .45 : .7;
        }
        for (const id of [...boxes.keys()]) if (!live.has(id)) drop(id);
    }

    return {
        set,
        meshes: () => [...boxes.values()].map(entry => entry.fill),
        count: () => boxes.size,
        dispose() {
            if (disposed) return;
            disposed = true;
            for (const id of [...boxes.keys()]) drop(id);
            parent.remove(root);
        },
    };
}
