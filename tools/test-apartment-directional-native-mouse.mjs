import test from 'node:test';
import assert from 'node:assert/strict';
import { createDirectionalNativeMouse, assertDirectionalNativeMouseLedger } from './fixtures/apartment-directional-native-mouse.mjs';

function fixture(send = async () => {}) {
  const packets = [], records = [];
  const mouse = createDirectionalNativeMouse({ send: async (method, packet) => {
    packets.push({ method, packet: structuredClone(packet) }); await send(method, packet);
  }, onRecord: row => records.push(row) });
  return { mouse, packets, records };
}
function nativeRows(ledger) {
  return ledger.expected.map(({ packet }, index) => ({ sequence: index + 1, isTrusted: true,
    type: ({ mouseMoved: 'pointermove', mousePressed: 'pointerdown', mouseReleased: 'pointerup', mouseWheel: 'wheel' })[packet.type],
    x: packet.type === 'mouseWheel' ? Math.floor(packet.x) : Math.fround(packet.x),
    y: packet.type === 'mouseWheel' ? Math.floor(packet.y) : Math.fround(packet.y), modifiers: { alt: false, control: false, meta: false, shift: false },
    pointerType: 'mouse', pointerId: 7, buttons: packet.buttons, button: packet.type === 'mouseMoved' ? -1 : 0,
    pressure: packet.buttons ? .5 : 0, deltaX: packet.deltaX, deltaY: packet.deltaY }));
}
async function gesture() {
  const f = fixture(); await f.mouse.move(10.125, 20.25); await f.mouse.down();
  await f.mouse.move(18.125, 28.25, { steps: 8 }); await f.mouse.wheel(0, -180); await f.mouse.up(); return f;
}

test('native pressed interpolation preserves all eight exact pinned Chromium packets and the final release point', async () => {
  const f = await gesture();
  assert.equal(f.packets.length, 12);
  assert.deepEqual(f.packets[0], { method: 'Input.dispatchMouseEvent', packet: {
    type: 'mouseMoved', button: 'none', buttons: 0, x: 10.125, y: 20.25, modifiers: 0, force: 0 } });
  assert.deepEqual(f.packets[1].packet, { type: 'mousePressed', button: 'left', buttons: 1,
    x: 10.125, y: 20.25, modifiers: 0, clickCount: 1, force: .5 });
  for (let index = 1; index <= 8; index++) assert.deepEqual(f.packets[index + 1].packet, {
    type: 'mouseMoved', button: 'left', buttons: 1, x: 10.125 + index, y: 20.25 + index, modifiers: 0, force: .5 });
  assert.deepEqual(f.packets[10].packet, { type: 'mouseWheel', x: 18.125, y: 28.25, modifiers: 0, deltaX: 0, deltaY: -180 });
  assert.deepEqual(f.packets[11].packet, { type: 'mouseReleased', button: 'left', buttons: 0,
    x: 18.125, y: 28.25, modifiers: 0, clickCount: 1 });
  assert.deepEqual(f.mouse.readback().dispatched, f.mouse.readback().expected);
});

test('each sample waits for acknowledgement before dispatching the next sample', async () => {
  const releases = [];
  const f = fixture(() => new Promise(resolve => releases.push(resolve)));
  const moving = f.mouse.move(8, 16, { steps: 4 });
  assert.equal(f.packets.length, 1);
  for (let index = 0; index < 4; index++) {
    assert.equal(f.packets.length, index + 1); releases[index]();
    // Drain only promises; no browser, timer or synthetic input.
    for (let turn = 0; turn < 8; turn++) await Promise.resolve();
  }
  await moving; assert.deepEqual(f.packets.map(row => [row.packet.x, row.packet.y]), [[2, 4], [4, 8], [6, 12], [8, 16]]);
});

test('a native click owns its explicit move and cannot leave stale interpolation state for the next gesture', async () => {
  const f = fixture(); await f.mouse.move(1, 2); await f.mouse.click(30, 40);
  await f.mouse.move(34, 48, { steps: 2 }); await f.mouse.down(); await f.mouse.up();
  assert.deepEqual(f.packets.slice(1, 4).map(row => [row.packet.type, row.packet.x, row.packet.y]),
    [['mouseMoved', 30, 40], ['mousePressed', 30, 40], ['mouseReleased', 30, 40]]);
  assert.deepEqual(f.packets.slice(4, 6).map(row => [row.packet.x, row.packet.y]), [[32, 44], [34, 48]]);
  assert.ok(f.packets.every(row => row.method === 'Input.dispatchMouseEvent'));
});

test('Escape and wheel while pressed do not invent a physical mouse release', async () => {
  const f = fixture(); await f.mouse.move(11, 12); await f.mouse.down();
  let keys = 0; await f.mouse.guardKeyboardPress('Escape', async () => { keys++; });
  assert.equal(keys, 1); assert.equal(f.mouse.readback().pressed, true);
  await f.mouse.wheel(0, -180); assert.equal(f.mouse.readback().pressed, true);
  await f.mouse.up(); assert.equal(f.mouse.readback().pressed, false);
  assert.equal(f.packets.filter(row => row.packet.type === 'mouseReleased').length, 1);
});

for (const [name, action] of [
  ['down before explicit move', mouse => mouse.down()],
  ['up without owned press', mouse => mouse.up()],
  ['wheel without owned position', mouse => mouse.wheel(0, 1)],
  ['nonfinite coordinates', mouse => mouse.move(NaN, 0)],
  ['unknown move option', mouse => mouse.move(1, 2, { forClick: true })],
  ['fractional steps', mouse => mouse.move(1, 2, { steps: 1.5 })],
  ['zero steps', mouse => mouse.move(1, 2, { steps: 0 })],
  ['excessive steps', mouse => mouse.move(1, 2, { steps: 101 })],
  ['modifier key', mouse => mouse.guardKeyboardPress('Control', async () => {})],
  ['modifier chord', mouse => mouse.guardKeyboardPress('Shift+A', async () => {})],
  ['nondefault click options', mouse => mouse.click(1, 2, { clickCount: 2 })],
]) test('unsupported state fails closed: ' + name, async () => {
  const f = fixture(); await assert.rejects(action(f.mouse)); assert.equal(f.packets.length, 0);
  await assert.rejects(f.mouse.move(1, 2), /retired/); assert.equal(f.packets.length, 0);
});

test('each fresh down after release requires another explicit unpressed move', async () => {
  const f = fixture(); await f.mouse.move(1, 2); await f.mouse.down(); await f.mouse.up();
  await assert.rejects(f.mouse.down(), /fresh unpressed move/); assert.equal(f.packets.length, 3);
});

test('transport rejection is durable and prevents the remaining interpolation samples', async () => {
  const f = fixture(async () => { throw Error('native transport rejected'); });
  await assert.rejects(f.mouse.move(10, 20, { steps: 8 }), /native transport rejected/);
  const ledger = f.mouse.readback(); assert.equal(ledger.expected.length, 1); assert.equal(ledger.dispatched.length, 0);
  assert.equal(ledger.failure, 'native transport rejected'); assert.equal(f.records.at(-1).kind, 'failure');
  await assert.rejects(f.mouse.click(1, 2), /retired/); assert.equal(f.packets.length, 1);
});

test('concurrent input retires before it can reorder or append another native sample', async () => {
  let release;
  const f = fixture(() => new Promise(resolve => { release = resolve; }));
  const moving = f.mouse.move(8, 16, { steps: 4 });
  await assert.rejects(f.mouse.down(), /concurrent/); release(); await assert.rejects(moving, /retired/);
  assert.equal(f.packets.length, 1); assert.equal(f.mouse.readback().failure, 'concurrent_native_input');
});

test('readback and durable observer records cannot mutate the helper input state or packet ledger', async () => {
  const f = await gesture(), before = f.mouse.readback();
  const first = f.mouse.readback(); first.expected[0].packet.x = -500; first.x = -1;
  f.records.find(row => row.kind === 'expected').value.packet.x = -900;
  assert.deepEqual(f.mouse.readback(), before);
});

test('full ledger admits exact trusted packets with native coordinate float conversion only', async () => {
  const f = await gesture(), ledger = f.mouse.readback(), observed = nativeRows(ledger);
  assert.deepEqual(assertDirectionalNativeMouseLedger({ ledger, observed, dragstarts: [] }), {
    operations: 5, samples: 12, expected: 12, dispatched: 12, observed: 12, trusted_order_exact: true, html_dragstarts: 0 });
});

test('wheel client coordinates use the exact browser floor while pointer samples and sent packets preserve fractions', async () => {
  const f = fixture(), x = 421.78428036019704, y = 332.56453701450414;
  await f.mouse.move(x, y); await f.mouse.wheel(0, -180);
  const ledger = f.mouse.readback(), observed = nativeRows(ledger);
  assert.equal(ledger.expected[1].packet.x, x); assert.equal(ledger.dispatched[1].packet.y, y);
  assert.equal(observed[0].x, Math.fround(x)); assert.equal(observed[0].y, Math.fround(y));
  assert.deepEqual([observed[1].x, observed[1].y], [421, 332]);
  assert.equal(assertDirectionalNativeMouseLedger({ ledger, observed, dragstarts: [] }).samples, 2);
});

test('wheel floor includes negative fractions rather than truncation toward zero', async () => {
  const f = fixture(); await f.mouse.move(-.25, -.75); await f.mouse.wheel(0, -180);
  const ledger = f.mouse.readback(), observed = nativeRows(ledger);
  assert.deepEqual([observed[1].x, observed[1].y], [-1, -1]);
  assert.equal(assertDirectionalNativeMouseLedger({ ledger, observed, dragstarts: [] }).samples, 2);
  observed[1].x = 0;
  assert.throws(() => assertDirectionalNativeMouseLedger({ ledger, observed, dragstarts: [] }));
});

for (const field of ['x', 'y']) test('wheel coordinate oracle refuses the wrong exact integer ' + field, async () => {
  const f = await gesture(), ledger = f.mouse.readback(), observed = nativeRows(ledger);
  observed[10][field]++;
  assert.throws(() => assertDirectionalNativeMouseLedger({ ledger, observed, dragstarts: [] }));
});

test('wheel coordinate oracle refuses unquantized fractional MouseEvent coordinates', async () => {
  const f = await gesture(), ledger = f.mouse.readback(), observed = nativeRows(ledger);
  observed[10].x = ledger.expected[10].packet.x;
  assert.throws(() => assertDirectionalNativeMouseLedger({ ledger, observed, dragstarts: [] }));
});

for (const [name, change] of [
  ['dropped sample', (l, o) => o.splice(3, 1)], ['duplicate sample', (l, o) => o.push(structuredClone(o[0]))],
  ['reordered native input', (l, o) => [o[2], o[3]] = [o[3], o[2]]],
  ['untrusted DOM input', (l, o) => { o[2].isTrusted = false; }],
  ['wrong coordinate', (l, o) => { o[2].x += .02; }], ['wrong buttons', (l, o) => { o[2].buttons = 0; }],
  ['wrong pressure', (l, o) => { o[2].pressure = 0; }], ['wrong pointer identity', (l, o) => { o[2].pointerId++; }],
  ['held modifier', (l, o) => { o[2].modifiers.shift = true; }], ['wrong wheel', (l, o) => { o[10].deltaY++; }],
  ['unacknowledged packet', l => { l.dispatched.pop(); }],
]) test('native conservation rejects ' + name, async () => {
  const f = await gesture(), ledger = f.mouse.readback(), observed = nativeRows(ledger); change(ledger, observed);
  assert.throws(() => assertDirectionalNativeMouseLedger({ ledger, observed, dragstarts: [] }));
});

test('HTML dragstart remains a hard failure rather than an intercepted or prevented event', async () => {
  const f = await gesture(), ledger = f.mouse.readback();
  assert.throws(() => assertDirectionalNativeMouseLedger({ ledger, observed: nativeRows(ledger), dragstarts: [{ isTrusted: true }] }));
});
