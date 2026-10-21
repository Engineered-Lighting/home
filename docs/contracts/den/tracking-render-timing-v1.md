# Tracking render timing uncertainty

The existing draw/GPU receipt path reports SDK camera timestamp to GPU completion,
not synchronized exposure-to-display-photon latency. A received packet or a scene
update alone is never a render receipt.

After the actual GPU completion, the producer source-clock interval and the
browser's bounded clock sample give a raw latency interval:

```text
low  = completed_browser_seconds - clock.offsetHigh - source_high
high = completed_browser_seconds - clock.offsetLow  - source_low
```

Both source and offset intervals must be finite and ordered. A wholly negative
raw interval is inconsistent with the source-to-draw causal order and is refused.
An inverted or nonfinite interval is also refused. Refusal remains visible as
`gpu_receipt_invalid_clock` with a bounded reason.

A valid raw interval can cross zero when clock uncertainty is broad. That does
not show a negative physical latency or invalidate an actual GPU-completed draw.
Its intersection with the causal condition latency >= 0 is retained alongside
the complete raw interval:

| Receipt field | Meaning |
| --- | --- |
| `camera_to_gpu_raw_ms` | Original low/high bounds, including a negative lower bound |
| `camera_to_gpu_ms` | `[max(0, raw_low), raw_high]` |
| `camera_to_gpu_timing` | `clock_bounded` for unchanged nonnegative intervals; `causality_bounded` when intersected with causality |

For example, a raw interval of `[-200, 650]` ms is retained as raw evidence;
the causal bound is `[0, 650]` ms. Zero is only a lower bound from uncertainty,
never a measured latency, fastest update or justification for a tight performance
claim. Reports must retain the interval and qualification rather than replacing
them with a point estimate or dropping the uncertain completed update.

The live status also exposes `cameraToGpuRawMs` and `cameraToGpuTiming`.
`gpu_completed` diagnostics carry `timing_qualification`; completion receipts
continue to carry `expired_at_completion`. This change does not alter the
one-second clock RTT admission, clock age limit, request deadline, original
750-ms evidence expiry, scene/source binding, GPU deadline or authority flags.

Positive previously recorded latency bounds are unchanged. This correction
does not establish sustained live performance, anatomical accuracy, room
registration or physical timing qualification.
