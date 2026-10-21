---
title: Align Den worker and monitor admission for firmware metadata failures
target: backend
type: fixed
---

Allow the diagnosed Jetson firmware-metadata downloader failure through the Den worker's preflight, matching its existing monitor classification. Unknown failed services, kernel faults and storage, memory and thermal gates still block launch.

Keep command provenance stable across bounded recording rolls by comparing the actual executable, arguments and error policy separately from systemd's changing PID/timestamps. Working directories, artifact hashes, restart counters and required unit checks remain enforced.
