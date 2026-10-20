// Mesh frames retain every original pose and pixel threshold. Part 2 retains
// picking/projection/fixture, capture and the full 120-frame timing contracts.
// Keep those phases in separate processes within the original 145 s deadline.
globalThis.__HOME_WEBGPU_PROOF_MODES = ['mesh'];
globalThis.__HOME_WEBGPU_PROOF_PHASE = 'frames';
await import('./test-apartment-webgpu-renderer-browser.mjs');
