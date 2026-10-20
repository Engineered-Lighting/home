// Mesh's unchanged picking/projection/fixture, capture and 120-frame timing
// contracts. All three original pixel comparisons run in the first part.
globalThis.__HOME_WEBGPU_PROOF_MODES = ['mesh'];
globalThis.__HOME_WEBGPU_PROOF_PHASE = 'contracts';
await import('./test-apartment-webgpu-renderer-browser.mjs');
