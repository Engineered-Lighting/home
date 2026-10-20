// Photo on the WebGPU renderer preview (?renderer=webgpu) against Spark on the
// WebGL engine, in the real app shell on the Victoria fixture: the splat
// pipeline with the dollhouse cut, exterior cleanup and photo floor. The same
// proof as test-apartment-webgpu-renderer-browser.mjs (same backends, poses,
// all three original poses and pixel thresholds). Part 2 retains the original
// picking/fit/fixture/capture and 120-frame performance contracts. Splitting
// those phases keeps each proof within 145 seconds on software GL.
globalThis.__HOME_WEBGPU_PROOF_MODES = ['splat'];
globalThis.__HOME_WEBGPU_PROOF_PHASE = 'frames';
await import('./test-apartment-webgpu-renderer-browser.mjs');
