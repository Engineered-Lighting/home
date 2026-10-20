---
title: Approximate den placement can follow one verified camera session
target: backend
type: added
---

The perception lab can now load an optional, separately stored successor to
the reviewed den view alignment. The successor names the one later camera
session in which the unchanged calibration and mount were verified, so that
session's metric frames can drive the approximate placement and the virtual
desk preview. The original refused fit, the reviewed epoch, and every timing,
identity and expiry check are unchanged, and any other camera session still
refuses. Separate reasons now name an unverified or changed camera epoch and a
calibration change. A generator writes the successor from local verification
files, and a pure compatibility check lets the operator preflight refuse a
mismatched live camera session. Removing the v2 policy file restores the
previous behavior, and older releases ignore it.
