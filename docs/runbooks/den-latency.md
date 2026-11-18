# Den latency measurement

Use the same commissioned camera, scene registration, producer release and view
for both device runs. Record the gateway build, Den `RELEASE.json`, camera
release, head profile, browser version and whether the device is a real phone or
a desktop viewport test. Keep the records and captures on owner-controlled
systems. Physical lighting stays with HA/Hue during this virtual measurement.

## Collect local browser timings

1. Open Home with `?den-latency=1`, select the Den and keep its Apartment canvas
   visible. Append `&den-latency=1` if the address already has a query.
2. Let the figure and virtual fixtures update during the same seated movement
   and book cue on each device. Save the timing JSON from the Den chip. Copy the
   files to a private workstation folder as `desktop.json` and `phone.json`.
3. Run the local report from the matching Home checkout.

```powershell
node tools/den-latency-report.mjs --device desktop --trace '<private folder>/desktop.json' --device phone --trace '<private folder>/phone.json'
```

The export has a 600-entry rolling bound and reports dropped entries. Collect
several short runs while the canvas stays visible. Developer tools can also
read `HomeDenLatency.snapshot()`, `exportJson()` and `clear()`. Timing capture is
disabled on a normal visit. The export contains packet IDs and numeric timing
metadata. It excludes coordinates, images, activity text and credentials.

## Read the milestones

The report gives sample counts and p50, p95 and maximum durations for receive,
decode, scheduling, application, frame submission and GPU completion. A draw
witness must observe the exact target in the ordinary renderer. Frame IDs join
submission and completion. Missing, superseded, expired and unavailable samples
are counted, including snapshots masked by a manual fixture hold.

Browser-stage durations use the page's monotonic clock. Producer capture-age
estimates retain their unqualified status in the report. Head-shown production
frames can use arrival-based capture estimates. Qualifying those estimates
requires independent camera timing evidence for the installed release.

The existing `estimated_age_at_receive_ms` and `estimated_age_at_gpu_complete_ms`
fields retain the conservative producer-clock upper estimate. Current exports
also supply `clock_mapping` results with midpoint estimates, raw lower and upper
age bounds, and mapping uncertainty. The producer's bootstrap clock sample falls
between EventSource request start and clock callback entry. That whole setup
window, including connection queuing and asymmetric transit, bounds the mapping.
The calculation retains the existing 5 ms margin and 100 ppm drift allowance.
Every figure admission and expiry check continues to use the original upper
clock bound.

Raw bounds retain an uncertain negative lower endpoint. The report also gives
the interval's intersection with nonnegative elapsed time and counts affected
samples. A zero causal lower bound represents uncertainty. Endpoint percentile
summaries describe the lower and upper bounds separately. Older exports retain
their existing age distributions and count clock mapping as missing. Clock
mapping bounds do not qualify the camera's capture timestamp or establish display
presentation timing.

GPU completion establishes a completed submitted frame. The export records
`presentation_observed: false`. Establish camera-to-screen latency by correlating
the packet/frame identity with a browser compositor trace and a locally recorded
optical cue visible at both the camera and display. Record the measurement's
time resolution and clock uncertainty. Keep optical recordings private. Report
capture-to-visible latency only for samples with that observation.

The older `HomeDenPaintStamps` are retained for compatibility. Their timestamp
is a requestAnimationFrame callback. For an existing cue run, the report joins
the exact spotlight epoch and sequence.

```powershell
node tools/den-latency-report.mjs --cues '<private folder>/cues.json' --spots '<private folder>/spots.jsonl' --device desktop --paint '<private folder>/paint.json'
```

Cross-device wall-clock deltas remain unavailable until the operator supplies a
verified combined uncertainty with `--clock-bound-ms`. This reports an interval
for the legacy callback timestamp. Local browser-stage durations remain
available independently.

## Compare and recover

Record the deployed virtual ownership fix with the instrumented baseline,
then repeat with the same devices, cues and camera/producer configuration. Keep
frame loss and unavailable counts beside latency percentiles. A result from a
phone-sized desktop viewport is a synthetic browser check.

Verify manual aim, paint and levels through AL-1 updates, Stop, expiry,
reconnect and AL-1 pause expiry. AL-1 yields every virtual channel while Den
Live owns the fixtures and can resume when Den ownership is removed. A manual
hold ends through the existing explicit Resume or producer hand-back.
An older producer's HTTP 501 leaves the
browser hold active and learning marked unsupported. Verify zero physical
commands from this path.

Remove the query to stop diagnostic collection. Restore the recorded ownership
release and its service drop-in if the tracing release fails acceptance. Producer rollback
and gateway ingress use the existing Full Home Serve run sheet. Preserve its
HA/Hue ownership and `hue_overlay:false` settings.
