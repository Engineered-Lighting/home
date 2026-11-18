---
title: Add the live den view's always-on service on the inference host
target: internal
type: added
---

Adds step 4 of the live den view: `den-live`, the step 3 loop without the page, as a systemd service on the LA host, with the owner's pastes that install and test it.
- `run_service.py` takes its private inputs (the room model, the camera token, step 2's receipt) from `LoadCredential`, and its settings from a file the installer writes. It refuses to start, with one sentence and no restart, when an input is missing or doesn't match.
- `stack/services/den-live/den-live.service` runs it under a dynamic user, held to 3 GB and one and a half cores. Its people stream is loopback only, and it may reach the den camera's one address.
- `release.py` builds a tarball, the same bytes each time, from this branch's commit, the pinned trees, the two pinned models and rtmlib's wheel. It refuses on any private string in a text file it adds.
- `install.sh` checks the tarball's sha256, unpacks it, and builds the detector's venv with exactly the lock's versions. It installs the private inputs root-only, then starts the service. `install_la.py` runs it as one paste, and removes the copied inputs from the host afterwards, also on failure.
- `first_check.py` prints, read only, what the host serves today and how fast it pulls the camera's frames. It says whether the camera relay is needed.
- `walk_test.py` measures the markers through deployed Home with no cookie: distance from four floor spots picked in the scan, the capture-to-stream lag, ten minutes of the empty den, and a second person.
- den-live now asks the camera for `/frame.npz` and `/calibration` only, and refuses any other path before a request is made. The stream's health line adds `transit_s_median`.
- When the camera token file is missing, each paste prints den-take's own three lines that save it.
