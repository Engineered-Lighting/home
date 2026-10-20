// Stationary native-MSAA references only. Individual coverage samples can
// vary at an isolated alpha edge. Retain the one-level comparison envelope
// on a three-frame median, and reject color changes, flashes or excessive
// alpha variation in any input instead of hiding them in the median.
export function nativeRgbaReference(frames) {
  if (!Array.isArray(frames) || frames.length !== 3) throw Error('native reference requires three frames');
  const first = frames[0], length = first?.width * first?.height * 4;
  if (!Number.isSafeInteger(length) || length <= 0 || frames.some(frame => !frame?.pixels
    || frame.width !== first.width || frame.height !== first.height || frame.pixels.length !== length
    || frame.bottomUp !== first.bottomUp || frame.stateKey !== first.stateKey)) throw Error('native reference changed scene or dimensions');
  const pixels = new Uint8Array(length);
  for (let i = 0; i < length; i++) {
    const a = frames[0].pixels[i], b = frames[1].pixels[i], c = frames[2].pixels[i];
    pixels[i] = a + b + c - Math.min(a, b, c) - Math.max(a, b, c);
  }
  const raw = frames.map(frame => {
    let total = 0, colorMax = 0, alphaMax = 0;
    for (let i = 0; i < length; i++) {
      const delta = Math.abs(frame.pixels[i] - pixels[i]); total += delta;
      if (i % 4 === 3) alphaMax = Math.max(alphaMax, delta); else colorMax = Math.max(colorMax, delta);
    }
    const mean = total / length;
    // Measured at this proof's quarter-size readback: native MSAA has rare
    // 2-4 level alpha excursions; RGB stays within one and mean below .001.
    if (colorMax > 1 || alphaMax > 4 || mean > .001) throw Error('native reference unstable raw frame: ' + JSON.stringify({ colorMax, alphaMax, mean }));
    return { colorMax, alphaMax, mean };
  });
  return { ...first, pixels, raw };
}
