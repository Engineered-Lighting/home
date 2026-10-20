// Photo's unchanged fixture/picking/fit, capture and 120-frame performance
// contracts. All three original pixel comparisons run in the first part.
globalThis.__HOME_WEBGPU_PROOF_MODES = ['splat'];
globalThis.__HOME_WEBGPU_PROOF_PHASE = 'contracts';
await import('./test-apartment-webgpu-renderer-browser.mjs');
