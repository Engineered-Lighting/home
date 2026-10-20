// API/measurement checks with an authored WebGPU device double. These do not
// establish GPU performance; only the opt-in real-adapter run can do that.
import test from 'node:test';
import assert from 'node:assert/strict';
import { measureLitWebGpuTimestamps, classifyLitWebGpuGate } from './lib/lit-webgpu-timestamp-benchmark.mjs';

function fakeRuntime(t, { timestamp = true, durationNs = 100000n } = {}) {
  const originalWindow = globalThis.window, originalUsage = globalThis.GPUBufferUsage, originalMap = globalThis.GPUMapMode;
  globalThis.GPUBufferUsage = { STORAGE: 1, QUERY_RESOLVE: 2, COPY_SRC: 4, COPY_DST: 8, MAP_READ: 16 };
  globalThis.GPUMapMode = { READ: 1 };
  const trace = { flushes: 0, measured: 0, blend: 0, buffers: [], running: [], destroyed: 0, shaders: [] };
  let clock = 1000000n;
  const device = {
    features: new Set(timestamp ? ['timestamp-query'] : []),
    limits: { maxBufferSize: 256 * 1024 * 1024, maxStorageBufferBindingSize: 128 * 1024 * 1024 },
    pushErrorScope() {}, async popErrorScope() { return null; },
    createBuffer(descriptor) {
      trace.buffers.push(descriptor);
      return { descriptor, bytes: descriptor.usage & 16 ? new Uint8Array(descriptor.size) : null,
        async mapAsync() {}, getMappedRange() { return this.bytes.buffer; }, unmap() {}, destroy() { trace.destroyed++; } };
    },
    createQuerySet({ count }) { return { times: new BigUint64Array(count), destroy() { trace.destroyed++; } }; },
    createShaderModule(descriptor) { trace.shaders.push(descriptor); return descriptor; },
    createComputePipeline() { return { kind: 'flush', getBindGroupLayout() { return {}; } }; },
    createBindGroup(descriptor) { return descriptor; },
    createCommandEncoder() {
      const steps = [];
      return {
        beginComputePass(descriptor = {}) {
          let pipeline;
          return { setPipeline(value) { pipeline = value; }, setBindGroup() {},
            dispatchWorkgroups() { steps.push({ kind: pipeline.kind }); },
            end() { if (descriptor.timestampWrites) steps.push({ timestamp: descriptor.timestampWrites }); } };
        },
        resolveQuerySet(querySet, start, count, resolve) { steps.push({ resolve, querySet, start, count }); },
        copyBufferToBuffer(resolve, from, readback, to, size) { steps.push({ copy: true, resolve, readback, size }); },
        finish() { return steps; },
      };
    },
    queue: {
      async onSubmittedWorkDone() {},
      submit(buffers) {
        for (const steps of buffers) for (const step of steps) {
          if (step.kind === 'flush') trace.flushes++;
          if (step.kind === 'blend') trace.blend++;
          if (step.timestamp) {
            const q = step.timestamp;
            q.querySet.times[q.beginningOfPassWriteIndex] = clock;
            q.querySet.times[q.endOfPassWriteIndex] = clock + durationNs; clock += 1000000n; trace.measured++;
          }
          if (step.querySet) step.resolve.bytes = new Uint8Array(step.querySet.times.buffer.slice(0));
          if (step.copy) step.readback.bytes.set(step.resolve.bytes.subarray(0, step.size));
        }
      },
    },
  };
  const node = { authoredRuntimeNode: true };
  const renderer = { backend: { isWebGPUBackend: true, device },
    compute(value) {
      assert.equal(value, node, 'the actual node captured from the runtime hook is dispatched');
      const encoder = device.createCommandEncoder(), pass = encoder.beginComputePass();
      pass.setPipeline({ kind: 'blend' }); pass.dispatchWorkgroups(1446); pass.end(); device.queue.submit([encoder.finish()]);
    } };
  const originalCompute = renderer.compute, originalEncoder = device.createCommandEncoder;
  globalThis.window = { __APT_ENGINE: { renderer, gpu: 'WebGPU · NVIDIA GeForce RTX 4090',
    webgpuReadback: () => ({ adapter: { description: 'NVIDIA GeForce RTX 4090' } }),
    litState: () => ({ active: true, receivers: { count: 370001 } }),
    setRunning(value) { trace.running.push(value); },
    async litReceiversBenchmark() { for (let i = 0; i < 20; i++) renderer.compute(node); return [999]; },
  } };
  t.after(() => { globalThis.window = originalWindow; globalThis.GPUBufferUsage = originalUsage; globalThis.GPUMapMode = originalMap; });
  return { trace, renderer, device, originalCompute, originalEncoder };
}

test('unsupported timestamp-query stays unmeasured without allocation or a wall-time pass', async t => {
  const f = fakeRuntime(t, { timestamp: false });
  const result = await measureLitWebGpuTimestamps();
  assert.equal(result.status, 'unmeasured'); assert.equal(result.reason, 'timestamp_query_unavailable');
  assert.equal(classifyLitWebGpuGate(result).status, 'unmeasured');
  assert.equal(f.trace.buffers.length, 0); assert.deepEqual(f.trace.running, []);
  assert.equal(classifyLitWebGpuGate({ status: 'measured', method: 'wall-time', gpu_ms: { median: 0.001 } }).status, 'unmeasured');
});

test('times the captured real compute node for 100 samples after a 256MiB flush each time', async t => {
  const f = fakeRuntime(t);
  const result = await measureLitWebGpuTimestamps();
  assert.equal(result.status, 'measured'); assert.equal(result.samples_ms.length, 100);
  assert.ok(result.samples_ms.every(value => value === 0.1)); assert.equal(result.gpu_ms.median, 0.1);
  assert.equal(result.cache_flush_chunks, 2); assert.equal(result.cache_flush_bytes, 256 * 1024 * 1024);
  assert.equal(f.trace.flushes, 600, 'two binding ranges across 100 warmup + 100 wall + 100 timed dispatches');
  assert.equal(f.trace.measured, 100); assert.equal(f.trace.blend, 320, '20 hook capture + 300 actual blend dispatches');
  assert.equal(f.trace.buffers.find(buffer => buffer.label === 'lit-bench-cache-flush').size, 256 * 1024 * 1024);
  assert.equal(f.trace.destroyed, 4); assert.deepEqual(f.trace.running, [false, true]);
  assert.equal(f.renderer.compute, f.originalCompute); assert.equal(f.device.createCommandEncoder, f.originalEncoder);
  assert.equal(classifyLitWebGpuGate(result).status, 'passed');
  for (const mutation of [
    { receiver_count: 876773 }, { iterations_measured: 50 }, { cache_flush_bytes: 128 * 1024 * 1024 },
    { gpu: 'another adapter', adapter: null }, { source: 'copy-of-shader' }, { frame_loop_paused: false },
  ]) assert.equal(classifyLitWebGpuGate({ ...result, ...mutation }).status, 'unmeasured');
  assert.equal(classifyLitWebGpuGate({ ...result, gpu_ms: { median: 0.001 }, samples_ms: Array(100).fill(.3) }).status, 'failed');
  const hidden = { ...result, gpu: 'WebGPU · nvidia lovelace', adapter: { vendor: 'nvidia', description: '' } };
  const card = { vendorId: 0x10de, deviceId: 0x2684, vendorString: 'NVIDIA', deviceString: 'NVIDIA GeForce RTX 4090' };
  assert.equal(classifyLitWebGpuGate(hidden).status, 'unmeasured');
  assert.equal(classifyLitWebGpuGate(hidden, { browserGpuDevices: [card] }).status, 'passed');
  assert.equal(classifyLitWebGpuGate(hidden, { browserGpuDevices: [card, { ...card, deviceString: 'NVIDIA RTX 4080' }] }).status, 'unmeasured');
  assert.equal(classifyLitWebGpuGate({ ...hidden, adapter: { vendor: 'google' } }, { browserGpuDevices: [card] }).status, 'unmeasured');
  assert.equal(classifyLitWebGpuGate({ ...hidden, gpu: 'WebGPU · NVIDIA RTX 4080' }, { browserGpuDevices: [card] }).status, 'unmeasured');
});

test('a partial iteration count cannot pass the historical gate', async t => {
  fakeRuntime(t);
  const result = await measureLitWebGpuTimestamps({ iterations: 50 });
  assert.equal(result.status, 'measured'); assert.equal(result.iterations_measured, 50);
  assert.equal(classifyLitWebGpuGate(result).status, 'unmeasured');
});

test('invalid timestamps restore the runtime and remain unmeasured', async t => {
  const f = fakeRuntime(t, { durationNs: 0n });
  const result = await measureLitWebGpuTimestamps();
  assert.equal(result.status, 'unmeasured'); assert.equal(result.reason, 'invalid_or_quantized_gpu_timestamp');
  assert.equal(classifyLitWebGpuGate(result).status, 'unmeasured');
  assert.deepEqual(f.trace.running, [false, true]); assert.equal(f.trace.destroyed, 4);
  assert.equal(f.renderer.compute, f.originalCompute); assert.equal(f.device.createCommandEncoder, f.originalEncoder);
});
