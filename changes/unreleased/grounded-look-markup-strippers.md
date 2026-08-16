---
title: Stop raw box markup rendering in the grounded-look trace
target: desktop
type: fixed
---

The /look drawer stripped only the canonical `<box>x1,y1,x2,y2</box>` form
from a grounded-reasoning trace, while the vision sidecar's parser had long
since been widened to also accept Qwen3-VL's bracketed
`<box>[[x1,y1,x2,y2]]</box>` and a bare `>` close. Any trace using those
forms parsed cleanly on the server and still rendered raw markup to the
reader.

Measured on the live stack, the form that actually reached the screen was a
different one: when generation stops at the token cap mid-markup, the
closing tag never arrives and the dangling `<box>` was rendered verbatim.

Both strippers now share one pattern covering every form the sidecar
accepts, plus a tag truncated before its own `>`. Prose that merely looks
like markup — a less-than sign, an unrelated tag — is left alone.
