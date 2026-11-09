# den-detect: the den detector over the Lab's workers

`detect.py` runs YOLO26s and RTMPose-m over one take's usable frames and writes their rows beside the
take: `detections.jsonl` (`den-detections-1`, the rows den-observe reads) and `detector-run.json`
(versions, the weights' and `artifact.json`'s digests, the take's digest, the device, per-frame ms,
the counts, and that `authorize` was answered yes). It is hop 6 of the den simulation, recorded once
per take by `tools\den-sim\scene\prepare.py`, and it closes the review's
`scored-detector-rows-have-no-producer`.

No model code is new. The Lab's own `UltralyticsWorker` and `RTMPoseWorker` load with their own
checks: weight digests, versions and rtmlib's source hashes. Their `authorize` is answered yes,
because no Lab admission runs on this PC. Each frame goes through `check.read_bundle`, the Lab's
BT.601 decode, YOLO26s, the `distinct_people` duplicate-box merge, RTMPose-m on person boxes at 0.5
or more (at most two), and `from_perception_lab`. `available_ts` is the capture time plus that
frame's compute only. A frame with no detection still gets a row naming the box model, and a frame
over 1.0 s of compute is counted, because den-observe rejects it.

## Run it (PowerShell 5.1)

It runs in its own venv, `%USERPROFILE%\ml\den-detector`, outside every checkout. `requirements.lock`
holds every distribution in it at its exact version, the three weight digests, and the lines that
build it. `detect.py` refuses to run when the venv or the weights differ from the lock.

```powershell
$py = "$env:USERPROFILE\ml\den-detector\Scripts\python.exe"
& $py -B tools\den-detect\detect.py <take folder> --tools-root <folder> --state <folder>
```

- `--tools-root` holds `tools\den-take`, `tools\den-observe` and the Lab files in `WORKERS`, at one
  pin, as den-sim's `chain.py` archives them under `<data root>\tools\observe-<sha>`.
- `--state` is where Ultralytics' settings and caches, its `runs` folder and the process's TEMP go.
  Nothing goes under AppData. `prepare.py` names a work folder under the den-sim data root and
  deletes it when it's done.
- `--only N,N,N --against <rows>` runs the same steps on those frames, writes nothing, and checks
  that the person boxes match the recorded rows within 1 px.

Python sockets are refused through an audit hook. Ultralytics runs offline with no auto-install, and
onnxruntime's telemetry is off, because native code can't be hooked. On this PC's CPU a frame with a
person takes about 66 ms, and an empty one about 40 ms.

## The tests

```powershell
$env:DEN_SIM_SET = "$env:USERPROFILE\den-sim\prepared\<set>"
python -B -X utf8 tools\den-detect\test_detect.py
```

The model parts run in the venv and skip without it or without a set. They only read the set.
