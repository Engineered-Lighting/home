# Escape and focus QA — APT-F04

This is authored software evidence for issue #223. It is not installed-source,
actual-room visual acceptance, camera/session behavior, or physical acceptance.
The tests load no room assets, registration, household content, or services.

## Test-first baseline

Baseline commit: `faeeb2b106bec12d883f29e3559c3ec370352463`.

Baseline `app/src/home-overlay.js` SHA-256:
`879ae7ad7bf994eb9b75f3340ca6cd01bb34b3814a19ff6c8c956d30a386fd34`.

`node --test tools/test-apartment-escape-stack.cjs` produced 22 cases: 11 passed
and 11 failed before the correction. The failures showed that connected hidden,
disabled, invisible or inert focus targets, including targets under hidden/inert
ancestors, prevented the usable fallback from receiving focus. An unavailable
last-resort target was also attempted. The stub models failed `focus()` calls and
asserts the resulting active element; a focus call alone is not success.

The passing baseline cases retained one capture listener, topmost-only dispatch,
claim/pass behavior, editable ownership inside versus behind a modal, passive
embedded idle behavior, safe non-top cleanup, and deferred-focus race handling.

## Coverage and evidence boundaries

- `tools/test-apartment-escape-stack.cjs` executes the actual `HomeOverlay` core
  in a VM with authored DOM nodes. It also extracts the actual Apartment
  cancellation callback, modal/embedded adapters, child editor callbacks and
  layer guards from their ASTs. It does not invent an Apartment dispatcher.
  The parent table proves which existing producer callback receives a press;
  producer geometry, persistence and lifecycle effects are outside that seam.
  Target Cancel is separately applied to an authored model to verify its exact
  mutation and preservation of unrelated model content.
- `tools/test-apartment-escape-browser.mjs` mounts React with the actual
  `HomeOverlay.useOverlayLayer` hook and overlay core. It additionally mounts
  the extracted parent callback, root event-capture props, operation-opener
  layout effect, deferred restoration and unmount cleanup. The child proof
  mounts the actual target/zone/tool layer declarations and Cancel bodies,
  existing Finish callback, and existing zone-name input. Only surrounding
  state, model mutation adapter and inactive producer values are authored.
  This is a focused component proof, not a complete Home mount.
- The browser proof aborts and records any HTTP or WebSocket attempt, records
  page errors, and writes only a local receipt. It captures no screenshots.

## Current results

`node --test tools/test-apartment-escape-stack.cjs` passed **47 cases**.

`NODE_PATH=<existing Playwright runtime> node --test tools/test-apartment-escape-browser.mjs`
passed **34 browser checkpoints**. The local receipt is
`.tmp/apartment-escape-browser/run-1792362227498/receipt.json`; it records zero
HTTP requests, WebSocket attempts and page errors. The browser was closed after
the proof. Exact tested source SHA-256 values:

| Source | SHA-256 |
| --- | --- |
| `home-overlay.js` | `526732928cc62cc072f557d981b1918ddb117fe5ff6ff962fba3921e9de16975` |
| `home-apartment.jsx` | `885bd4273c05a131f0f2083561b7d2cc302496e40ac72348f403d04be819233c` |
| `home-apartment-edit.jsx` | `5d72e524d1a668f9cc72794af4690bb148943d1d6b96c2308569d121d2064429` |

| Requirement | Focused evidence |
| --- | --- |
| Modal, popover and editable priority | Actual capture dispatcher, native editors including owned portal roots; one topmost callback per discrete press |
| Held Escape | Repeats consumed without advancing operations; owned editors retain repeats |
| Parent ordering | Desk capture, reticle drag, existing rehearsal/travel owner, tap aim, calibration guard, existing camera return, Setup, card, fixture selection, idle |
| Embedded idle | Actual embedded adapter passes and remains mounted; standalone modal adapter retains its separate close behavior |
| Calibration guard | First press warns without camera exit; five-second confirmation, expiry rearm and capture disarm retained |
| Target placement | Existing Cancel body removes only its draft; other authored targets, devices and unsaved model content stay intact |
| Zone naming and draft | First Escape cancels the existing name field while preserving the boundary; next cancels only the boundary draft and leaves Setup mounted |
| Armed Setup tools | Existing target move and add-device placement state clear; Measure uses the existing Select callback; model content remains untouched in the focused proof |
| Operation focus | Actual pointer/key capture and opener layout effect; post-commit restore, disconnected-opener viewport fallback, yield to a newer modal, pending-frame cleanup on unmount |
| Shared focus | Visible, connected, enabled targets; hidden/inert ancestors and incidental body excluded; actual focus success verified; real Tab wrapping |

## Remaining acceptance limits

There is no generic proposed-aim producer/discard contract in the existing
parent surface. This patch does not invent one. Full design behavior for a
future proposal must connect its owning guarded producer. Geometry edit drags
remain owned by their existing renderer/editor gesture handlers; this focused
proof does not establish rollback behavior for every geometry gesture.

The focused tests do not establish full Home rendering, actual-room fidelity,
installed-source behavior or physical acceptance. Those remain separate from
the tests above. Source-owned authority, calibration, travel, unsaved-work and
lifecycle guards remain requirements. Raw design instructions to close the
embedded Apartment do not override the issue's retained contract.
