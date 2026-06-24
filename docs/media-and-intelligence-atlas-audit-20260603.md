# Media And Intelligence Atlas Audit - 2026-06-03

This is a bounded audit of captured image/video data found locally and on `hav-ubuntu`. It is intended to prevent repeat mining and to identify overlooked training sources.

## Bottom Line

- I had undercounted the available data. The remote gesture store has about **862,497 media files** and **81.48 GB** under `/opt/home-ai-voice/gesture-control-data`.
- The Home app's **Intelligence Atlas** has captured still-frame footage in `/opt/home-ai-voice/intelligence-data/multimodal`.
- Intelligence Atlas appears to store still images, not video, in the current data directory: **456 JPGs**, **0 videos**, **35.42 MB**.
- The Atlas database has **140 observation packets**, **412 frame records**, and **411 frame files still present**.
- Atlas valid labels include **38 `person_present` packets**, so yes, it has captured footage of a person in the home.
- The biggest active non-Atlas source is `live-observer-assets`, with **191,039 JPGs** and fresh files still appearing during the scan.

## Local Media Snapshot

### `C:\Claude\home`

- Media files: **11,205**
- Images: **11,201**
- Videos: **4**
- Size: **3.47 GB**
- Date range: `2026-05-11T10:42:48` to `2026-06-03T04:09:53`

Major local buckets:

- `C:\Claude\home\docs`: **9,496 images**, **3.05 GB**
- `C:\Claude\home\tmp`: **1,206 media**, **385 MB**, including 2 MP4s
- `C:\Claude\home\tools\calibration-frames`: **272 images**, **28 MB**
- `C:\Claude\home\gesture-review-missed-wakes`: 2 MP4s

Important local videos:

- `C:\Claude\home\tmp\gesture-analysis\gdebug-e01902482afe483e.mp4`
- `C:\Claude\home\tmp\gesture-analysis\gdebug-70d3f2c6bb634567.mp4`
- `C:\Claude\home\gesture-review-missed-wakes\gclip-95a4cc9560934068.mp4`
- `C:\Claude\home\gesture-review-missed-wakes\gclip-37718af303ad43d0.mp4`

### `C:\Users\Marcelo\Documents\Codex`

- Media files: **51**
- Images: **51**
- Videos: **0**
- Size: **33.7 MB**
- Date range: `2026-05-27T00:34:52` to `2026-06-02T05:01:35`

These are mostly review sheets and focused scrub artifacts from the Codex workspace.

## Remote Gesture Store

Root: `/opt/home-ai-voice/gesture-control-data`

- Media files: **862,497**
- Images: **862,451**
- Videos: **46**
- Size: **81.48 GB**
- Date range: `2026-05-28T09:33:15` to `2026-06-03T08:41:15`

Top-level distribution:

| Path | Files | Videos | Size |
|---|---:|---:|---:|
| `experiments` | 333,160 | 0 | 18.04 GB |
| `evaluations` | 299,505 | 16 | 22.36 GB |
| `live-observer-assets` | 191,039 | 0 | 35.26 GB |
| `derived` | 28,227 | 0 | 3.18 GB |
| `evidence` | 6,770 | 0 | 1.15 GB |
| `clips` | 3,553 | 25 | 1.36 GB |

High-value subtrees:

- `evaluations/gesture-v64/intentional-clips-2x-20260530T223321Z`: **255,744+ files**, intentional/scripted clip corpus.
- `evaluations/gesture-v92-hybrid-passive-replay-shadow-1h-20260602Tnow`: **22,041 files**, includes living room, kitchen, and dining room source videos.
- `evaluations/gesture-v95-online-sft-source-video-20260602Tnow`: **7,639 frame-cache images**, likely source-video derived.
- `live-observer-assets`: **191k raw/hand-crop images**, current and likely useful for negatives, hard confusers, and camera/view distribution.
- `experiments/v7-online-motion-rtmw-8fps-20260531T0025Z`: **60,352 images**.
- `experiments/v7-online-fluid-rtmw-8fps-20260531T0025Z`: **34,468 images**.
- `derived/reviewed-fluid-segments-clean*`: reviewed derivative training crops.

Remote videos include source captures such as:

- `/opt/home-ai-voice/gesture-control-data/evaluations/gesture-v92-hybrid-passive-replay-shadow-1h-20260602Tnow/capture/clips/v91passive-20260602T193903Z-dining_room-95c99e12/source.mp4`
- `/opt/home-ai-voice/gesture-control-data/evaluations/gesture-v92-hybrid-passive-replay-shadow-1h-20260602Tnow/capture/clips/v91passive-20260602T191851Z-kitchen-f2650df4/source.mp4`
- `/opt/home-ai-voice/gesture-control-data/evaluations/gesture-v92-hybrid-passive-replay-shadow-1h-20260602Tnow/capture/clips/v91passive-20260602T185841Z-living_room-71b317f9/source.mp4`

## Intelligence Atlas Footage

Home app UI references:

- `C:\Claude\home\app\src\home-app.jsx`
- `C:\Claude\home\app\src\home-intelligence.jsx`

Atlas service/data wiring:

- `C:\Claude\home\stack\docker-compose.yml`
- `C:\Claude\home\stack\services\intelligence\app\multimodal.py`
- `C:\Claude\home\stack\services\intelligence\app\db.py`

Remote Atlas data:

- Data root: `/opt/home-ai-voice/intelligence-data`
- Database: `/opt/home-ai-voice/intelligence-data/intelligence.sqlite`
- Packet frames: `/opt/home-ai-voice/intelligence-data/multimodal/frames`
- Current ring buffer: `/opt/home-ai-voice/intelligence-data/multimodal/ring/living_room`

Filesystem counts:

| Path | Files | Videos | Size | Date range |
|---|---:|---:|---:|---|
| `/opt/home-ai-voice/intelligence-data` | 456 JPGs | 0 | 35.42 MB | 2026-05-27 to 2026-06-03 |
| `/opt/home-ai-voice/intelligence-data/multimodal/frames` | 411 JPGs | 0 | 31.82 MB | 2026-05-27 to 2026-06-03 |
| `/opt/home-ai-voice/intelligence-data/multimodal/ring/living_room` | 45 JPGs | 0 | 3.60 MB | 2026-06-03 |

Database counts:

- `observation_packets`: **140**
- `observation_frames`: **412**
- `qwen_label_results`: **139**
- `training_clip_manifests`: **1**
- `label_eval_sets`: **1**
- `label_eval_items`: **2**
- `observation_audits`: **22**

Packet distribution:

- By camera: `living_room` **138**, `kitchen` **2**
- By zone: `office` **138**, `island_left` **2**
- By event type: `override_event` **101**, `pending_preference` **39**
- By status: `labeled` **98**, `label_failed` **39**, `packet_created` **2**, `frames_deleted` **1**
- By qwen status: `valid` **98**, `error` **23**, `invalid` **16**, `not_requested` **3**

Valid label summaries:

- `presence.label`: `empty` **53**, `person_present` **38**, `non_person_motion` **4**, `uncertain` **3**
- `primary_activity.label`: `unknown` **47**, `relaxing` **15**, `desk_work` **11**, `passing_through` **10**, `standing_idle` **7**, `arriving_or_leaving` **6**, `exercise` **1**, `watching_tv` **1**
- `posture.label`: `not_visible` **60**, `standing` **24**, `seated` **13**, `uncertain` **1**
- `image_quality.label`: `clear` **92**, `too_dark` **6**

Sample Atlas person-present packets:

- `/opt/home-ai-voice/intelligence-data/multimodal/frames/op-1780457539-876274/frame_1.jpg`
- `/opt/home-ai-voice/intelligence-data/multimodal/frames/op-1780415823-059348/frame_1.jpg`
- `/opt/home-ai-voice/intelligence-data/multimodal/frames/op-1780415817-235064/frame_1.jpg`
- `/opt/home-ai-voice/intelligence-data/multimodal/frames/op-1780275026-122490/frame_1.jpg`
- `/opt/home-ai-voice/intelligence-data/multimodal/frames/op-1780274779-642614/frame_1.jpg`

Each packet usually has `frame_1.jpg`, `frame_2.jpg`, and `frame_3.jpg`.

## What We Have Achieved So Far

- Built and iterated a continuous gesture recognition pipeline with explicit goals for zero false wakes and high observable wake recall.
- Produced a reviewed non-first38 region seed v2 dataset with **24 training-ready region labels**: **9 wake positives** and **15 no_gesture/confuser** labels across dining, kitchen, and living room.
- Verified that the current reviewed label set avoids locked first38 overlap and passes the split-policy check.
- Built contact-sheet and focused-scrub workflows for reviewing gesture clips without relying on raw giant listings.
- Found that the previous positive-mining conclusion was too narrow: it did not account for all remote media, `live-observer-assets`, source videos, or Intelligence Atlas packets.
- Identified Atlas as a separate still-frame capture system, separate from the gesture-control store.

## Avoid Repeating

- Do not rerun broad raw file listings. Use bounded counters and sampled paths.
- Do not treat repeated local `docs/gesture-non-living-room-review-sheets-*` exports as unique training data without de-duplicating by original clip id.
- Do not treat dependency media under virtualenvs as captured footage.
- Do not assume Atlas has no relevant data just because it has no videos. It has labeled still-frame observation packets with person presence.
- Do not assume the remote gesture-control store is already exhausted. The broad audit found several large sources that were outside the earlier narrow mining pass.

## Next Best Data Sources

1. Mine `/opt/home-ai-voice/gesture-control-data/live-observer-assets` by camera and timestamp for recent hard negatives, camera coverage, and possible unlabeled activations.
2. Use the three long v92 passive replay source videos for cross-camera kitchen/dining/living evaluation.
3. Reconcile `evaluations/gesture-v64/intentional-clips-2x-20260530T223321Z` against the reviewed seed manifests to avoid duplicate positives.
4. Treat Intelligence Atlas `person_present` packets as context/background/activity data first; only use as gesture positives if we visually verify a deliberate activation gesture.
5. Build a persistent media index keyed by source path, packet id, clip id, camera, timestamp, frame count, and label status.
