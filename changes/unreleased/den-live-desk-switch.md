---
title: The den spotlight's desk switch maps one fixture to a real board, off by default
target: internal
type: added
---

den-live gains the desk switch (plan 05, step 05-4). When the service's credentials hold `desk.json` (`den-c12-desk-1`), one registered fixture's commands go to its board through the den home's Home Assistant. Without the file nothing changes: den-live stays virtual only, with no Home Assistant write client.
- **One fixture, its measured profile:** the board's own profile replaces the fictional one, and the trace's run header names the mapping.
- **A dark rehearsal:** `light: false` sends every move at spot level 0 and no keep-lit, while Home still shows the virtual spot. The replay checks the dark rule.
- **Readback:** optionally, the board's Home Assistant entities are read every second. They feed a `devices` key in the coordinator's snapshot, device rows in the trace when they change, and the Stop copy, so a Stop pressed in Home Assistant becomes Home's Stop.
- **Fail closed:** a desk file that doesn't check, or a desk with no Home Assistant link, keeps the coordinator off and says why.

- **Two checks in words:** `desk.py --check` tries a desk file against the geometry before the install, and `desk_report.py` on LA sums up a day's desk trace with a PASS or FAIL per rule: one fixture, the dark rule, the bytes, no stale seq, and Stop.
- **The installer brings it only when asked:** `install_la.py --desk <file>` checks the file on the workstation first, and install.sh installs it root-only. An install without `--desk` removes LA's old one, so den-live is virtual only again.
