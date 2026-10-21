// Cloud shares the original backend, pose, image and interaction proof.
// Keep frames and contracts in separate 145-second processes, as for Mesh/Photo.
globalThis.__HOME_WEBGPU_PROOF_MODES = ['points'];
globalThis.__HOME_WEBGPU_PROOF_PHASE = 'contracts';
await import('./test-apartment-webgpu-renderer-browser.mjs');
