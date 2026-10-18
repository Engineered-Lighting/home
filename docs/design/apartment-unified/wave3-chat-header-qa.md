# Composer and header QA — APT-C05

The remaining header affordances use the existing explanation and guarded
conversation-clear owners. Copy is an explicit local conversation-text action.
This report records focused authored software evidence for issue #257, not
installed behavior, actual-room visual acceptance or physical acceptance.

## Focused browser proof

`NODE_PATH=<existing Playwright runtime> node --test tools/test-apartment-chat-header-browser.mjs`
passed **40 checkpoints** across dark/light desktop (1366 × 768, fine pointer)
and dark/light phone (390 × 844, coarse pointer).

The local receipt is
`.tmp/apartment-chat-header-browser/run-1792362784813/receipt.json`.
It records zero HTTP requests, WebSocket attempts and page errors. No room
assets, registration, household content, model, camera or service is loaded.
The browser was closed after the proof. No screenshots were captured.

Exact source SHA-256 values at this check:

| Source | SHA-256 |
| --- | --- |
| `home-app.jsx` | `0fa87bd8e22048c4af757f7e573cadce905a10e0e32f2abd857c3e289d71080a` |
| `home-chat-header-actions.jsx` | `496cf080e03e88f80d373179c33990e0ac4913f54e6925b12096960e5c2ed559` |
| `home-tokens.css` | `0f44fcbeb87b89ee53a4025007f1db337823f8a50d6ad1e9ceee83fb8835fc9f` |
| `home-overlay.js` | `526732928cc62cc072f557d981b1918ddb117fe5ff6ff962fba3921e9de16975` |

## What the test mounts

The proof extracts the actual `HomeHeader` declaration and actual controller
prop expressions from `HomeNormalApp`. It loads the real header action leaf and
production CSS. It also extracts and mounts the copy state, refs, callback,
disabled-reason expression and lifetime effect. These are production source
slices selected through the AST, with fail-closed uniqueness checks.

The surrounding feed and source identity are authored. A bounded clipboard
adapter records text and supplies success, denial, unavailability or a pending
Promise; it never writes to the operating-system clipboard. Existing
`requestClearConfirm`, `cancelAction`, `confirmAction` and `/undo-clear` bodies
operate on authored React state with inert reset/stream adapters. This verifies
their header delegation and controller state transitions without executing Home
services. It does not qualify the real services behind those owners.

| Requirement | Evidence |
| --- | --- |
| Header actions | All three native, accessible buttons and the visible “Conversation text only” scope render in each scenario |
| Keyboard and touch | Visible keyboard focus; Enter/Space reach Explain; 32px desktop and at least 44px coarse targets; no horizontal overflow |
| Explain | Actual header prop delegates to the existing `/why` command handler |
| New conversation | Existing pending-confirm card is added; no immediate clear, stream stop or reset; cancellation retains the original authored feed |
| Confirm and undo | Only confirmation clears; the existing one-shot undo restores conversation text, keeps retired private imagery unavailable and refuses a second restore |
| Copy scope | Exact plain text includes completed eligible conversation events; excludes private-camera interpretation, background perception, structured tools, incomplete streaming text and the known unclaimed voice placeholder |
| Copy feedback | Pending is visible; overlap refused; success appears only after a successful write; rejection and explicit failure never claim success |
| Disabled reasons | Missing clipboard, no completed text and active voice turn have accessible reasons; unrelated actions remain available |
| Currentness | Source/conversation change invalidates a pending attempt and permits the new feed to copy; stale completion after scope change or unmount makes no further status-setter call |

## Separate regression evidence

The final touched-source batch also passed **8 header-leaf unit tests** and
**13 unread-state unit tests**. The retained unread mounted suite passed
**78 checkpoints**, including all 62 prior checkpoints plus 16 for the existing
`assistant` and `proactive` reply producers. Its separate local receipt is
`.tmp/apartment-unread-integration/run-1792362792514/receipt.json`; the result
is `passed`, with zero HTTP/WebSocket attempts and page errors, and the same
`home-app.jsx` hash listed above. These checks are separate from the 40 header
browser checkpoints and do not expand the header proof into a full Home mount.

## Acceptance boundaries

This is a mounted source-component/controller proof, not a full Home mount or a
live clipboard-permission trial. It does not execute explanation inference,
voice transport, room selection, lighting output or any physical command.

An idle voice session can copy completed conversation text; an active utterance
cannot copy its unclaimed placeholder. Existing slash completion, microphone
and Stop behavior remain owned by their existing controller paths. Their
broader regressions are separate from this focused header proof. F05/F06 remain
contract preparation only.
