# Chat header and producer unread review

Issues: #253 (C01, partial) and #257 (C05). Base: #285 at
`0636087c516f5538087f1e9fb216b8c05da55f95`. Reviewed on 2026-10-18.

Repair and Den released the narrow presentation window. Explain delegates to
the existing `/why` handler; New conversation arms the existing confirmation
card. Copy requires an explicit gesture and copies completed conversation text
locally. It excludes structured fields, private reviews/camera context,
background observations, onboarding, incomplete replies and the current voice
placeholder. Clipboard failure never reports success. Feedback from a retired
conversation/context or unmounted controller is ignored.

The existing proactive and lighting-assistant producer kinds now qualify for
unread under all retained identity, scope, history and visibility rules. This
changes notification presentation, not producer behavior or message storage.

## Standards

Independent review: **0 unresolved findings; 1 P3 resolved**. CSS-only changes
now run the focused header browser checks as well as the existing UI guards.
No production callback-ownership, lifetime, privacy or release-note findings.

## Spec

Independent review: **0 new actionable product findings**. Header callbacks
reuse existing owners, copied content has an explicit text-only scope, and
proactive/assistant unread preserves every prior gate.

C01 remains partial: its inherited generic Chat Undo/control Travel dispatch
gap is documented in `chat-retained-feature-audit.md`. Repair has released a
separate guard-fix window. This presentation change adds no output path and
does not claim that Apartment-only guard tests prove all Chat output safe.

## Verification

- Eight leaf tests; thirteen unread-state tests, including four tests that
  failed before the producer-kind correction.
- Forty mounted header browser checkpoints and 78 mounted unread checkpoints
  (the original 62 plus sixteen producer-kind cases). No HTTP, WebSocket or
  page errors in these authored component proofs.
- All 23 touched harnesses plus JSX parsing passed, retaining existing slash,
  streaming/deduplication, recovery, controller, review and Travel guard tests.
- Complete authored Home shell: all 28 checkpoints and 10 screenshots passed
  locally in 37.81 seconds, with the original 145-second deadline and 3cm
  collision guard. This does not establish Linux performance.
- Separate private accepted-Den checks passed three scene modes, four viewport
  sizes and header layout on desktop/tablet/phone. Selection/idle/popover
  Escape continued working. Registration stayed unchanged, with zero session
  starts, equipment saves or physical commands. Room evidence stays private.

See `wave3-chat-header-qa.md` for exact authored source pins. Hosted checks must
pass on this PR's committed source before it is ready for review. Browser
presentation, installed behavior and physical qualification remain distinct.

F05/F06 remain contract preparation only. No merge, deployment or production
host operation is authorized.

Hosted CI caught a copy-feedback scope race at the existing scope-change
assertion. Copy lifetime invalidation now runs during the layout commit,
clearing pending feedback before a replacement conversation is presented.
The same forty focused browser assertions and deadlines are retained.
