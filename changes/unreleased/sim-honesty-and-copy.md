---
title: Simulation mode keeps its privacy promise; error copy grows up
target: web
type: fixed
---

Simulation mode now honors its own documentation: the persona is Alex
everywhere (the maintainer's real name appeared in the apartment presence
chip and dozens of chat/world-state fixtures), the residual health probes
that kept hitting the real backend under sim are gated off, and the healthy
scenario mocks the AI-stack supervisor instead of leaking a real
"STACK_TOKEN not configured" warning. Boot failures now lead with plain
language and always offer a Reload button. Raw transport strings ("HTTP
status 502, 0 bytes", "auth invalid") are translated at the source with the
technical detail preserved in parentheses. Lighting explanations no longer
render as blank turns, a failed voice session resets cleanly instead of
freezing on "listening…", the People empty-state glyph is no longer
mojibake, and the slash palette lists everyday commands first.
