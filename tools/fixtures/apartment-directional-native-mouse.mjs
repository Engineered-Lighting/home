// Authored browser proof only. Match pinned Playwright's Chromium mouse
// packets without its unrelated HTML DragManager setup/cleanup per sample.
import assert from 'node:assert/strict';

const copy = value => structuredClone(value);
const finite = value => typeof value === 'number' && Number.isFinite(value);
const plainOptions = (options, allowed) => options && typeof options === 'object' && !Array.isArray(options)
  && Object.keys(options).every(key => allowed.includes(key));

export function createDirectionalNativeMouse({ send, onRecord = () => {} } = {}) {
  assert.equal(typeof send, 'function'); assert.equal(typeof onRecord, 'function');
  const expected = [], dispatched = [], operations = [];
  let x = 0, y = 0, pressed = false, positioned = false, busy = false, failure = null;
  const record = (kind, value) => onRecord({ kind, value: copy(value) });
  function retire(reason) { if (!failure) { failure = String(reason); record('failure', { reason: failure }); } }
  function require(condition, message) { if (!condition) throw Error(message); }
  async function packet(operation, value, step = null) {
    require(!failure, 'native mouse retired: ' + failure);
    const row = { id: expected.length + 1, operation: operation.id, type: operation.type, step, packet: copy(value) };
    expected.push(row); record('expected', row);
    await send('Input.dispatchMouseEvent', copy(value));
    dispatched.push(copy(row)); record('dispatched', row);
  }
  async function operation(type, args, action) {
    require(!failure, 'native mouse retired: ' + failure);
    if (busy) { retire('concurrent_native_input'); throw Error('concurrent native mouse or keyboard input'); }
    busy = true;
    const row = { id: operations.length + 1, type, args: copy(args), from: [x, y], pressed_before: pressed };
    operations.push(row); record('operation-start', row);
    try { await action(row); row.completed = true; row.to = [x, y]; row.pressed_after = pressed; record('operation-end', row); }
    catch (error) { retire(error.message); throw error; }
    finally { busy = false; }
  }
  const movePacket = (nextX, nextY) => ({ type: 'mouseMoved', button: pressed ? 'left' : 'none', buttons: pressed ? 1 : 0,
    x: nextX, y: nextY, modifiers: 0, force: pressed ? .5 : 0 });
  const downPacket = () => ({ type: 'mousePressed', button: 'left', buttons: 1, x, y, modifiers: 0, clickCount: 1, force: .5 });
  const upPacket = () => ({ type: 'mouseReleased', button: 'left', buttons: 0, x, y, modifiers: 0, clickCount: 1 });
  return Object.freeze({
    move(nextX, nextY, options = {}) { return operation('move', [nextX, nextY, options], async row => {
      require(finite(nextX) && finite(nextY), 'finite native mouse coordinates required');
      require(plainOptions(options, ['steps']), 'unsupported native move options');
      const steps = options.steps ?? 1;
      require(Number.isSafeInteger(steps) && steps >= 1 && steps <= 100, 'unsupported native interpolation count');
      const fromX = x, fromY = y;
      for (let index = 1; index <= steps; index++) {
        const targetX = fromX + (nextX - fromX) * (index / steps), targetY = fromY + (nextY - fromY) * (index / steps);
        await packet(row, movePacket(targetX, targetY), { index, count: steps, from: [fromX, fromY], to: [nextX, nextY] });
        x = targetX; y = targetY;
      }
      if (!pressed) positioned = true;
    }); },
    down(options = {}) { return operation('down', [options], async row => {
      require(plainOptions(options, []) && positioned && !pressed, 'native down requires a fresh unpressed move and default left button');
      await packet(row, downPacket()); pressed = true; positioned = false;
    }); },
    up(options = {}) { return operation('up', [options], async row => {
      require(plainOptions(options, []) && pressed, 'native up requires the owned pressed left button');
      await packet(row, upPacket()); pressed = false; positioned = false;
    }); },
    click(nextX, nextY, options = {}) { return operation('click', [nextX, nextY, options], async row => {
      require(finite(nextX) && finite(nextY) && plainOptions(options, []) && !pressed, 'unsupported native click or pressed click');
      // One explicit unpressed move owns the click position. Never combine
      // this state with Playwright Mouse's private coordinates/buttons.
      await packet(row, movePacket(nextX, nextY), { index: 1, count: 1, from: [x, y], to: [nextX, nextY] });
      x = nextX; y = nextY; await packet(row, downPacket()); pressed = true;
      await packet(row, upPacket()); pressed = false; positioned = false;
    }); },
    wheel(deltaX, deltaY) { return operation('wheel', [deltaX, deltaY], async row => {
      require(finite(deltaX) && finite(deltaY) && (positioned || pressed), 'native wheel requires an owned current position');
      // These are the exact pinned RawMouseImpl.wheel fields, including its
      // omission of button/buttons/force while a mouse button is held.
      await packet(row, { type: 'mouseWheel', x, y, modifiers: 0, deltaX, deltaY });
    }); },
    guardKeyboardPress(key, action) { return operation('keyboard', [key], async () => {
      require(typeof key === 'string' && (/^[a-z]$/.test(key)
        || ['Enter', 'Space', 'Escape', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'].includes(key)),
      'unsupported native keyboard key/modifier state');
      require(typeof action === 'function', 'native keyboard action required');
      // Playwright press completes both keydown and keyup. No held modifier
      // or concurrent mouse input can be hidden by a forced modifiers:0.
      await action();
    }); },
    retire,
    readback: () => copy({ expected, dispatched, operations, failure, x, y, pressed, positioned, busy }),
  });
}

export function assertDirectionalNativeMouseLedger({ ledger, observed, dragstarts }) {
  assert.equal(ledger.failure, null); assert.equal(ledger.busy, false); assert.equal(ledger.pressed, false);
  assert.deepEqual(ledger.dispatched, ledger.expected, 'every expected sample was dispatched once in exact source order');
  assert.deepEqual(dragstarts, [], 'these canvas/pointer-capture cases never start native HTML drag-and-drop');
  assert.equal(observed.length, ledger.expected.length, 'every dispatched sample has exactly one trusted native pointer/wheel witness');
  let mousePointerId = null;
  for (const [index, expected] of ledger.expected.entries()) {
    const event = observed[index], packet = expected.packet;
    assert.equal(event.sequence, index + 1); assert.equal(event.isTrusted, true);
    assert.equal(event.type, ({ mouseMoved: 'pointermove', mousePressed: 'pointerdown', mouseReleased: 'pointerup', mouseWheel: 'wheel' })[packet.type]);
    if (packet.type === 'mouseWheel') {
      // Blink WheelEvent inherits MouseEvent's floored client coordinates;
      // PointerEvent preserves fractional coordinates. Sent packets stay exact.
      assert.equal(event.x, Math.floor(packet.x)); assert.equal(event.y, Math.floor(packet.y));
    } else assert.ok(Math.abs(event.x - packet.x) < .01 && Math.abs(event.y - packet.y) < .01,
      'native pointer sample retains the exact CSS input coordinate within browser float precision');
    assert.deepEqual(event.modifiers, { alt: false, control: false, meta: false, shift: false });
    if (packet.type === 'mouseWheel') {
      assert.equal(event.deltaX, packet.deltaX); assert.equal(event.deltaY, packet.deltaY); continue;
    }
    assert.equal(event.pointerType, 'mouse'); assert.ok(Number.isInteger(event.pointerId) && event.pointerId >= 0);
    if (mousePointerId == null) mousePointerId = event.pointerId;
    assert.equal(event.pointerId, mousePointerId); assert.equal(event.buttons, packet.buttons);
    assert.equal(event.button, packet.type === 'mouseMoved' ? -1 : 0);
    assert.equal(event.pressure, packet.buttons ? .5 : 0);
  }
  return { operations: ledger.operations.filter(row => row.type !== 'keyboard').length,
    samples: ledger.expected.length, expected: ledger.expected.length, dispatched: ledger.dispatched.length,
    observed: observed.length, trusted_order_exact: true, html_dragstarts: 0 };
}
