---
title: Perception Lab keeps den device facts fresh and replays them faithfully
target: backend
type: fixed
---

The Lab's HA reader now reuses one HTTPS connection for all the entity reads
of a snapshot instead of opening one per entity. On the den source a snapshot
took about 1.9 s to read, so it was usable for only about 1.1 s of its 3 s
validity. About 38% of the JEV packets in session 0dc34636 went out with every
device fact `stale` and the PC-input role `unknown`. The 3 s validity and the
freshness rules are unchanged.

The offline replay tools (`jev_replay.py`, `jev_cadence_replay.py`) now
recover HA bindings from facts-only v6 packets. They used to read the bindings
only from v5 packets, so a v6 session bound nothing and every device fact
replayed as `stale` for the whole session. The packet wire and question
versions are unchanged.
