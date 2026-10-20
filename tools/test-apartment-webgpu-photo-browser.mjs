// Photo on the WebGPU renderer preview (?renderer=webgpu) against Spark on the
// WebGL engine, in the real app shell on the Victoria fixture: the splat
// pipeline with the dollhouse cut, exterior cleanup and photo floor. The same
// proof as test-apartment-webgpu-renderer-browser.mjs (same backends, poses,
// contracts and thresholds), run on Photo only so each harness fits the
// touched-harness deadline on SwiftShader.
globalThis.__HOME_WEBGPU_PROOF_MODES = ['splat'];
await import('./test-apartment-webgpu-renderer-browser.mjs');
