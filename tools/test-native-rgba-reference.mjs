import test from 'node:test';
import assert from 'node:assert/strict';
import { nativeRgbaReference } from './fixtures/native-rgba-reference.mjs';
const frame = () => ({ width: 100, height: 100, bottomUp: true, stateKey: 'fixed-camera-and-mode', pixels: new Uint8Array(40000).fill(128) });
const trio = () => [frame(), frame(), frame()];

test('native reference removes isolated MSAA coverage variation without changing the source frames', () => {
  const frames = trio(); frames[0].pixels[3] += 3; frames[1].pixels[7] -= 4; frames[2].pixels[12]++;
  const result = nativeRgbaReference(frames);
  assert.deepEqual(result.pixels, frame().pixels);
  assert.equal(result.raw.length, 3); assert.equal(result.raw[1].alphaMax, 4);
  assert.equal(frames[0].pixels[3], 131);
});

test('native reference preserves persistent two-level errors for the existing one-level comparison to reject', () => {
  const frames = trio(); for (const f of frames) { f.pixels[0] += 2; f.pixels[3] += 2; }
  const result = nativeRgbaReference(frames);
  assert.equal(Math.max(...result.pixels.map(value => Math.abs(value - 128))), 2);
});

test('a bad raw frame cannot be hidden by the median', () => {
  for (const [channel, amount] of [[0, 2], [3, 5]]) {
    const frames = trio(); frames[0].pixels[channel] += amount;
    assert.throws(() => nativeRgbaReference(frames), /unstable raw frame/);
  }
  const flash = trio(); flash[0].pixels.fill(129);
  assert.throws(() => nativeRgbaReference(flash), /unstable raw frame/);
});

test('native references reject movement, shape changes, missing samples and flipped readback orientation', () => {
  for (const alter of [f => f.stateKey = 'moved', f => f.width++, f => f.bottomUp = false, f => f.pixels = new Uint8Array(4)]) {
    const frames = trio(); alter(frames[2]); assert.throws(() => nativeRgbaReference(frames), /changed scene or dimensions/);
  }
  assert.throws(() => nativeRgbaReference(trio().slice(1)), /three frames/);
});
