// Mesh parity uses every pose, picking/projection/fixture contract and pixel
// threshold from the Cloud entry point. Keep each format in its own process
// so hosted software GL fits the original 145-second suite deadline.
globalThis.__HOME_WEBGPU_PROOF_MODES = ['mesh'];
await import('./test-apartment-webgpu-renderer-browser.mjs');
