// Protected-equivalence fingerprint for the symbolic fixture's orb cosmetics.
// Everything the orb must not change (readback, descriptors, pick inventory,
// sources, beam, receivers and their buffers) is recorded by value; only the
// drawn body, its halo layers and the radial sector tint carriers are skipped.
// The pinned JSON beside this file was recorded from the cylinder fixture at
// 4a374e78 with protectedScenario() below.
import crypto from 'node:crypto';

// Drawn-body nodes the orb may replace. Their own contracts (sector visibility
// and .1 x level opacity, the opaque core) are asserted directly in tests.
export const ORB_COSMETIC_NAME = /^(?:symbolic-fixture-housing|tubular-diffuser|diffuser-(?:top|base)|radial-diffuser-sector-[1-6]|fixture-orb(?:-core)?|orb-halo-(?:downward|spotlight))$/;

// Numbers are compared at 9 significant digits: a real protected change is far
// larger, while the last bit of Math.pow/cos may differ between JS engines.
const round = value => typeof value === 'number' && Number.isFinite(value) ? Number(value.toPrecision(9)) : value;
const clone = value => JSON.parse(JSON.stringify(value), (_, item) => round(item));
const digest = array => crypto.createHash('sha256').update(JSON.stringify(Array.from(array, round))).digest('hex');
function snapshot(value) {
  if (value?.isTexture) return { texture: value.constructor.name, width: value.image?.width ?? null, height: value.image?.height ?? null,
    data: value.image?.data ? digest(value.image.data) : null };
  if (value?.toArray) return value.toArray();
  if (Array.isArray(value)) return value.map(snapshot);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, snapshot(item)]));
  return value ?? null;
}
function geometry(value) {
  if (!value) return null;
  return { type: value.type, parameters: clone(value.parameters ?? null), drawRange: { ...value.drawRange }, groups: clone(value.groups),
    index: value.index ? digest(value.index.array) : null,
    attributes: Object.fromEntries(Object.entries(value.attributes).map(([name, attribute]) => [name,
      { itemSize: attribute.itemSize, normalized: attribute.normalized, count: attribute.count, data: digest(attribute.array) }])) };
}
function material(value) {
  return { type: value.type, visible: value.visible, color: value.color?.toArray() ?? null, opacity: value.opacity,
    transparent: value.transparent, depthWrite: value.depthWrite, depthTest: value.depthTest, side: value.side,
    blending: value.blending, toneMapped: value.toneMapped, map: value.map ? snapshot(value.map) : null,
    uniforms: value.uniforms ? Object.fromEntries(Object.entries(value.uniforms).map(([name, uniform]) => [name, snapshot(uniform.value)])) : null };
}

export function protectedFingerprint(api) {
  api.root.updateMatrixWorld(true);
  const fixtures = api.root.children.map(root => {
    const nodes = [];
    const visit = (node, path) => {
      if (ORB_COSMETIC_NAME.test(node.name)) return;
      const materials = Array.isArray(node.material) ? node.material : node.material ? [node.material] : [];
      nodes.push({ path, type: node.type, position: node.position.toArray(), quaternion: node.quaternion.toArray(), scale: node.scale.toArray(),
        visible: node.visible, renderOrder: node.renderOrder, userData: clone(node.userData),
        geometry: geometry(node.geometry), materials: materials.map(material) });
      for (const child of node.children) visit(child, path + '/' + child.name);
    };
    visit(root, root.name);
    return { fixture_id: root.userData.fixture_id, nodes };
  });
  const readback = api.readback();
  return clone({ readback,
    descriptors: readback.fixtures.map(item => api.interactionDescriptor(item.fixture_id)),
    picks: api.pickObjects().map(node => ({ name: node.name, userData: node.userData, position: node.position.toArray(),
      geometry: geometry(node.geometry), visible: node.material.visible })),
    fixtures });
}

// The pinned form: protected node paths per fixture, then one SHA-256 per
// recorded state and part, so any changed value fails while the pin stays small.
const sha = value => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
export function pinnedEquivalence(states) {
  const first = Object.values(states)[0];
  return { paths: Object.fromEntries(first.fixtures.map(item => [item.fixture_id, item.nodes.map(node => node.path)])),
    states: Object.fromEntries(Object.entries(states).map(([name, state]) => [name, { readback: sha(state.readback),
      descriptors: sha(state.descriptors), picks: sha(state.picks),
      fixtures: Object.fromEntries(state.fixtures.map(item => [item.fixture_id, sha(item.nodes)])) }])) };
}

// A fixed sequence through load, aim, full Spot/Down, radial-only (settled
// by the existing hide/resume snap) and restore. No clock or WebGL is needed.
export function protectedScenario(api, id) {
  const states = {};
  api.setSelected(id); states.loaded = protectedFingerprint(api);
  const saved = api.interactionDescriptor(id);
  if (!api.aimAtTarget(id, { point: [2, 2, .5], normal: [0, 0, 1], kind: 'authored_floor' }).ok) throw Error('authored aim refused');
  states.aimed = protectedFingerprint(api);
  if (!api.setFixtureState(id, { spotlight: { brightness: .4, color_temp_kelvin: 4000 }, downward: { on: false } }).ok) throw Error('authored full patch refused');
  states.spot_and_down = protectedFingerprint(api);
  if (!api.setFixtureState(id, { radials: [{ number: 2, on: true, brightness: .37, color_temp_kelvin: 4200 }, { number: 5, on: false }] }).ok) throw Error('authored radial patch refused');
  api.setVisible(false); api.setVisible(true); states.radials_settled = protectedFingerprint(api);
  if (!api.restoreAim(id, saved).ok) throw Error('authored restore refused');
  states.restored = protectedFingerprint(api);
  api.setSelected(null); states.deselected = protectedFingerprint(api);
  return states;
}
