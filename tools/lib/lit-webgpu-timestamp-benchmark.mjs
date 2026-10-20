// Opt-in benchmark instrumentation, serialized into a fixture browser by
// Playwright. Keep this function self-contained; it changes no app sources.
export async function measureLitWebGpuTimestamps({ iterations = 100, maxDurationMs = 60000 } = {}) {
  const engine = globalThis.window?.__APT_ENGINE, renderer = engine?.renderer, device = renderer?.backend?.device;
  const flushBytes = 256 * 1024 * 1024;
  const result = { status: 'unmeasured', method: 'timestamp-query-pass', source: 'actual-runtime-compute-node',
    iterations_requested: iterations, warmup_iterations: 100, wall_iterations: 100, cache_flush_bytes: flushBytes,
    dispatches_per_sample: 1, receiver_count: engine?.litState?.()?.receivers?.count ?? null,
    gpu: engine?.gpu ?? null, adapter: engine?.webgpuReadback?.()?.adapter ?? null, samples_ms: [] };
  const unsupported = reason => Object.assign(result, { reason });
  if (!renderer?.backend?.isWebGPUBackend || !device) return unsupported('webgpu_device_unavailable');
  if (!device.features?.has('timestamp-query')) return unsupported('timestamp_query_unavailable');
  if (!Number.isInteger(iterations) || iterations < 1 || iterations > 200 || !Number.isFinite(maxDurationMs)
    || maxDurationMs < 100 || maxDurationMs > 60000) return unsupported('benchmark_bounds_invalid');
  if (!engine.litState()?.active || typeof engine.litReceiversBenchmark !== 'function' || typeof engine.setRunning !== 'function')
    return unsupported('active_runtime_benchmark_hook_unavailable');
  if (device.limits.maxBufferSize < flushBytes || device.limits.maxStorageBufferBindingSize < 256)
    return unsupported('cache_flush_limits_unavailable');
  const started = performance.now(), deadline = started + maxDurationMs;
  const bounded = async promise => {
    const remaining = deadline - performance.now();
    if (remaining <= 0) throw Error('benchmark_time_budget_exceeded');
    let timer;
    try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(Error('benchmark_time_budget_exceeded')), remaining); })]); }
    finally { clearTimeout(timer); }
  };
  const originalCompute = renderer.compute, originalEncoder = device.createCommandEncoder, resources = [];
  let validationScope = false, memoryScope = false, mapped = null, dispatch = null, recorded = 0;
  try {
    engine.setRunning(false);
    result.frame_loop_paused = true;
    await bounded(device.queue.onSubmittedWorkDone());
    const captured = new Set(); let captureDispatches = 0;
    renderer.compute = function (node, ...args) { captured.add(node); captureDispatches++; return originalCompute.call(this, node, ...args); };
    try { await bounded(engine.litReceiversBenchmark(1)); }
    finally { renderer.compute = originalCompute; }
    if (captured.size !== 1) throw Error('runtime_compute_capture_ambiguous');
    const compute = [...captured][0];
    result.capture_dispatches = captureDispatches;
    device.pushErrorScope('out-of-memory'); memoryScope = true;
    device.pushErrorScope('validation'); validationScope = true;
    const buffer = descriptor => { const value = device.createBuffer(descriptor); resources.push(value); return value; };
    const flush = buffer({ label: 'lit-bench-cache-flush', size: flushBytes, usage: GPUBufferUsage.STORAGE });
    const querySet = device.createQuerySet({ type: 'timestamp', count: iterations * 2 }); resources.push(querySet);
    const resolve = buffer({ label: 'lit-bench-timestamp-resolve', size: iterations * 16, usage: GPUBufferUsage.QUERY_RESOLVE | GPUBufferUsage.COPY_SRC });
    const readback = buffer({ label: 'lit-bench-timestamp-readback', size: iterations * 16, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ });
    const module = device.createShaderModule({ label: 'lit-bench-cache-flush', code: `
      @group(0) @binding(0) var<storage, read_write> b: array<vec4<u32>>;
      @compute @workgroup_size(256) fn main(@builtin(global_invocation_id) g: vec3u, @builtin(num_workgroups) nw: vec3u) {
        let n = arrayLength(&b); for (var k = g.x; k < n; k += nw.x * 256u) { b[k] = b[k] + vec4<u32>(1u); }
      }` });
    const pipeline = device.createComputePipeline({ layout: 'auto', compute: { module, entryPoint: 'main' } });
    // The runtime device may have the default 128 MiB binding limit. Stream
    // the same 256 MiB buffer in ranges instead of requesting another device.
    const chunkBytes = Math.floor(Math.min(flushBytes, device.limits.maxStorageBufferBindingSize) / 256) * 256;
    const groups = [];
    for (let offset = 0; offset < flushBytes; offset += chunkBytes) groups.push(device.createBindGroup({
      layout: pipeline.getBindGroupLayout(0), entries: [{ binding: 0, resource: { buffer: flush, offset, size: Math.min(chunkBytes, flushBytes - offset) } }],
    }));
    result.cache_flush_chunks = groups.length;
    device.createCommandEncoder = function (...args) {
      const encoder = originalEncoder.apply(this, args), phase = dispatch;
      if (!phase) return encoder;
      const begin = encoder.beginComputePass, finish = encoder.finish;
      let seen = false;
      encoder.beginComputePass = function (descriptor = {}) {
        if (seen || descriptor.timestampWrites) throw Error('unexpected_compute_pass_during_measurement');
        seen = true;
        const clear = begin.call(this, { label: 'lit-bench-cache-flush' });
        clear.setPipeline(pipeline);
        for (const group of groups) { clear.setBindGroup(0, group); clear.dispatchWorkgroups(2048); }
        clear.end();
        recorded++;
        return begin.call(this, phase.measured ? { ...descriptor, timestampWrites: { querySet,
          beginningOfPassWriteIndex: phase.index * 2, endOfPassWriteIndex: phase.index * 2 + 1 } } : descriptor);
      };
      encoder.finish = function (...finishArgs) {
        if (seen && phase.measured && phase.index === iterations - 1) {
          this.resolveQuerySet(querySet, 0, iterations * 2, resolve, 0);
          this.copyBufferToBuffer(resolve, 0, readback, 0, iterations * 16);
        }
        return finish.apply(this, finishArgs);
      };
      return encoder;
    };
    const run = async (count, measured) => {
      const before = recorded;
      for (let index = 0; index < count; index++) {
        if (performance.now() >= deadline) throw Error('benchmark_time_budget_exceeded');
        dispatch = { index, measured };
        originalCompute.call(renderer, compute);
        dispatch = null;
      }
      await bounded(device.queue.onSubmittedWorkDone());
      if (recorded - before !== count) throw Error('runtime_dispatch_count_mismatch');
    };
    await run(100, false);
    const wallStarted = performance.now(); await run(100, false);
    result.wall_ms_per_iteration_including_flush = (performance.now() - wallStarted) / 100;
    await run(iterations, true);
    await bounded(readback.mapAsync(GPUMapMode.READ)); mapped = readback;
    const times = new BigUint64Array(readback.getMappedRange());
    for (let index = 0; index < iterations; index++) {
      const duration = times[index * 2 + 1] - times[index * 2];
      if (duration <= 0n) throw Error('invalid_or_quantized_gpu_timestamp');
      result.samples_ms.push(Number(duration) / 1e6);
    }
    readback.unmap(); mapped = null;
    const samples = [...result.samples_ms].sort((a, b) => a - b), percentile = q => samples[Math.min(samples.length - 1, Math.floor(samples.length * q))];
    result.gpu_ms = { median: percentile(.5), p10: percentile(.1), p90: percentile(.9), p95: percentile(.95), min: samples[0], max: samples.at(-1) };
    result.status = 'measured'; result.iterations_measured = result.samples_ms.length;
  } catch (error) { result.status = 'unmeasured'; result.reason = String(error?.message || error).slice(0, 180); }
  finally {
    dispatch = null; renderer.compute = originalCompute; device.createCommandEncoder = originalEncoder;
    try { mapped?.unmap(); } catch { /* A lost device may already unmap. */ }
    for (const scoped of [validationScope, memoryScope]) if (scoped) {
      try { const error = await bounded(device.popErrorScope()); if (error) { result.status = 'unmeasured'; result.reason = 'gpu_validation_or_allocation_error'; } }
      catch { result.status = 'unmeasured'; result.reason = 'gpu_error_scope_unavailable'; }
    }
    for (const resource of resources) { try { resource.destroy(); } catch { /* A lost device may already destroy it. */ } }
    engine.setRunning(true); result.elapsed_ms = performance.now() - started;
  }
  return result;
}

export function classifyLitWebGpuGate(measurement, { browserGpuDevices = [] } = {}) {
  const gate = { status: 'unmeasured', limit_ms: 0.2, statistic: 'median', required_receiver_count: 370001,
    required_iterations: 100, required_cache_flush_bytes: 256 * 1024 * 1024, required_adapter: 'RTX 4090' };
  if (measurement?.status !== 'measured' || measurement.method !== 'timestamp-query-pass') return { ...gate, reason: measurement?.reason || 'gpu_timestamps_unmeasured' };
  if (measurement.receiver_count !== gate.required_receiver_count) return { ...gate, reason: 'receiver_count_not_comparable' };
  if (measurement.iterations_measured !== 100 || measurement.samples_ms?.length !== 100
    || measurement.cache_flush_bytes !== gate.required_cache_flush_bytes || measurement.dispatches_per_sample !== 1
    || measurement.source !== 'actual-runtime-compute-node' || measurement.frame_loop_paused !== true
    || measurement.warmup_iterations !== 100 || measurement.wall_iterations !== 100)
    return { ...gate, reason: 'measurement_method_not_comparable' };
  const name = [measurement.gpu, measurement.adapter?.description].filter(Boolean).join(' ');
  let identity = /\bRTX\s*4090\b/i.test(name) ? { source: 'webgpu_adapter_name' } : null;
  // Chrome may reduce GPUAdapterInfo to vendor + architecture. In that case,
  // identify the same browser's sole device for that vendor; multiple NVIDIA
  // devices, another reported model, or software adapters remain ambiguous.
  if (!identity && !/\bRTX\s*\d+/i.test(name) && measurement.adapter?.vendor?.toLowerCase() === 'nvidia') {
    const candidates = browserGpuDevices.filter(device => device.vendorId === 0x10de || /nvidia/i.test(device.vendorString || ''));
    if (candidates.length === 1 && /\bRTX\s*4090\b/i.test(candidates[0].deviceString || ''))
      identity = { source: 'same_browser_unique_vendor_device', device: candidates[0] };
  }
  if (!identity) return { ...gate, reason: 'required_adapter_not_identified' };
  if (!measurement.samples_ms.every(value => Number.isFinite(value) && value > 0) || !Number.isFinite(measurement.gpu_ms?.median))
    return { ...gate, reason: 'gpu_timestamps_invalid' };
  const median = [...measurement.samples_ms].sort((a, b) => a - b)[50];
  return { ...gate, status: median <= gate.limit_ms ? 'passed' : 'failed', measured_median_ms: median, adapter_identity_evidence: identity };
}
