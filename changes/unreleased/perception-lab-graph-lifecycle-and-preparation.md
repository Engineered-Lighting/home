---
title: Record JEV request lifecycle and expose preparation and frame age to the Lab graph
target: backend
type: added
---

Live JEV reasoning now writes a small `reasoning.lifecycle` receipt, with a
controlled reason, when a pending request is replaced, expires before
dispatch, is cancelled by stop, disable, shutdown or a controller restart, or
fails local dispatch. Receipts are best effort: a failed receipt never undoes
the state change or blocks Stop. Admission, freshness and budgets are
unchanged, and each live reasoning tick now reads only the evidence kinds it
uses instead of the whole session. The graph contract (revision 2,
same `perception-lab-graph-1` id) shows these as JEV timeline markers, with
replacements counted per arm, and as each arm's latest lifecycle. It also adds a
bounded per-arm preparation block copied from the saved request (pose geometry,
wrist-object relations, observations, source coverage and freshness), and a
live frame-age block that compares each arm's newest analyzed frame with the
newest recorded RGB camera frame on the controller clock; stereo views are no
longer shown as the camera frame. HA-unavailable timeline
items now carry an event id and kind, are capped, and use the ledger time when
no availability time was recorded.
