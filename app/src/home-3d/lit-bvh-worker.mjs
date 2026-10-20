/* home-3d/lit-bvh-worker.mjs — the L5 bounce bake off the main thread: BVH
 * build, WebGPU compute (or the CPU twin), layer composition and the lux
 * readout. The protocol and every algorithm live in lit-bvh-bake.mjs.
 * Messages that arrive while that module loads are held, then replayed. */
const early = [];
const hold = event => { early.push(event); };
self.addEventListener('message', hold);
const { attachBakeHost } = await import(`./lit-bvh-bake.mjs${new URL(import.meta.url).search}`);
self.removeEventListener('message', hold);
const host = attachBakeHost(self, { gpu: self.navigator?.gpu || null });
for (const event of early) await host.onMessage(event);
