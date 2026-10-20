// Lighting's HDR frame: an authored HDR test frame and the CPU-twin check for
// the real bloom and display passes (lit-frame.mjs proofPasses), shared by the
// browser proofs. Authored values only: a smooth gradient, a few hot pixels, a
// half-covered band, an uncovered corner and one tagged fixture face.
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const Display = await import(pathToFileURL(path.join(ROOT, 'app/src/home-3d/lit-display.mjs')).href);

export const PROOF_SIZE = Object.freeze({ width: 96, height: 64 });
// A white balance a little warm-to-neutral, as the camera look builds them.
export const PROOF_PARAMS = Object.freeze({ look: 'camera', operator: 'agx', gain: 2.5, agxPower: 1.35, preExposure: 0.038, seed: 1234,
  bloom: true, noise: true, whiteBalance: [[0.92, 0.06, 0.02], [0.03, 0.95, 0.02], [0.01, 0.07, 1.25]] });

/* The authored frame: Float32Array RGBA, premultiplied, pre-exposed. `scale`
 * dims every light at once (the monotone-dimming proof). */
export function authoredHdrFrame({ width = PROOF_SIZE.width, height = PROOF_SIZE.height, scale = 1 } = {}) {
  const image = new Float32Array(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const o = (y * width + x) * 4, u = x / (width - 1), v = y / (height - 1);
    let alpha = x > width - 12 && y > height - 10 ? 0 : y < 8 ? 0.5 : 1;
    const level = 0.002 * 2 ** (11 * u) * (0.6 + 0.4 * v);
    const rgb = [level * 1.1, level, level * 0.8];
    image[o] = rgb[0] * scale * alpha; image[o + 1] = rgb[1] * scale * alpha; image[o + 2] = rgb[2] * scale * alpha; image[o + 3] = alpha;
  }
  for (const [x, y, value] of [[20, 30, 40], [21, 30, 40], [60, 20, 300], [61, 44, 900]]) {
    const o = (y * width + x) * 4;
    image[o] = value * scale; image[o + 1] = value * 0.9 * scale; image[o + 2] = value * 0.7 * scale; image[o + 3] = 1;
  }
  // A fixture face whose true luminance is ten times the stored clamp.
  const face = Display.emitterPixel([6e5 * scale, 5.4e5 * scale, 4.2e5 * scale]);
  for (const [x, y] of [[40, 40], [41, 40], [40, 41], [41, 41]]) image.set(face, (y * width + x) * 4);
  return image;
}

/* The CPU twins over the frame as the GPU stored it (half float), against
 * the GPU's bloom mip 0 and 8-bit display. Returns the worst errors. */
export function compareFramePasses(result, params = PROOF_PARAMS) {
  const { width, height } = result;
  const frame = Float32Array.from(result.frame);
  const bloom = Display.bloomChain(frame, width, height, { strength: Display.LIT_BLOOM.strength });
  const gpuMip = Float32Array.from(result.mip.data);
  let mipRel = 0, mipAbs = 0;
  for (let i = 0; i < bloom.data.length; i++) {
    if (i % 4 === 3) continue;
    const d = Math.abs(gpuMip[i] - bloom.data[i]);
    mipAbs = Math.max(mipAbs, d);
    mipRel = Math.max(mipRel, d / Math.max(1e-3, Math.abs(bloom.data[i])));
  }
  // The noise hash takes the fragment's own coordinate, whose rows run either
  // way depending on the backend (the node renderer's WebGL2 backend counts
  // from the top); the twin follows whichever this backend uses.
  const displayErrors = flipped => {
    let max = 0, over = 0, luma = 0;
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      const o = (y * width + x) * 4;
      const scene = [frame[o], frame[o + 1], frame[o + 2], frame[o + 3]];
      const cpu = Display.litFramePixel(scene, Display.bloomAt(bloom, width, height, x, y), { ...params, x, y: flipped ? height - 1 - y : y,
        noise: params.noise ? { ...Display.LIT_NOISE, seed: params.seed } : null });
      for (let c = 0; c < 4; c++) {
        const d = Math.abs(result.display[o + c] - Math.min(255, Math.max(0, cpu[c] * 255)));
        max = Math.max(max, d); if (d > 2) over++;
      }
      luma += 0.2126 * result.display[o] + 0.7152 * result.display[o + 1] + 0.0722 * result.display[o + 2];
    }
    return { max, over, luma };
  };
  const upright = displayErrors(false), flipped = params.noise ? displayErrors(true) : upright;
  const best = flipped.max < upright.max ? flipped : upright;
  const displayMax = best.max, over2 = best.over, gpuLuma = best.luma, noiseRowsFlipped = best === flipped && flipped !== upright;
  return { mip_rel: mipRel, mip_abs: mipAbs, display_max: displayMax, display_over2: over2 / (width * height * 4), noise_rows_flipped: noiseRowsFlipped,
    mean_display_luma: gpuLuma / (width * height), sizes: result.sizes };
}

/* Bloom energy: the mip-0 sum (x 4 texels each) over the strength, against
 * the frame's own sum with each face counted at its true luminance. */
export function bloomEnergy(result) {
  const frame = Float32Array.from(result.frame), mip = Float32Array.from(result.mip.data);
  let scene = 0, glow = 0;
  for (let i = 0; i < frame.length; i += 4) {
    const k = frame[i + 3] >= Display.LIT_FRAME.emitterTag - 0.5 ? 2 ** (frame[i + 3] - Display.LIT_FRAME.emitterTag) : 1;
    scene += (0.2126 * frame[i] + 0.7152 * frame[i + 1] + 0.0722 * frame[i + 2]) * k;
  }
  for (let i = 0; i < mip.length; i += 4) glow += 0.2126 * mip[i] + 0.7152 * mip[i + 1] + 0.0722 * mip[i + 2];
  return { scene, bloom: glow * (result.width * result.height) / (result.mip.width * result.mip.height) / Display.LIT_BLOOM.strength };
}
