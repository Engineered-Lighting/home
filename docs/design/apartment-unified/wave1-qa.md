# Home UI first-wave QA

This report covers the follow-up UI delta on PR #219 at
`b0230dc15034942584836877981d22a6c94da17b`. It does not reclassify the existing
Phase 1 work as shipped. PR #219's original design comparison remains in
`design-qa.md` and `evidence-20261018/README.md`.

## Agreed ownership

Repair and Den explicitly confirmed the narrow UI lane before shared edits:
the unread indicator and scoped tokens; new leaf components and tests; Aim
colors/badges; and backward-compatible toast presentation. The UI integrator
owns changes to existing shell and stylesheet files. Foundations owns its new
leaf files and provides narrow migration patches; QA owns new tests and this
report.

Renderer, picking, selectors, perception sources/UI, capability registries,
backend services, command semantics, Travel Mode and existing lifecycle
ownership stay with their current owners. Canonical Den planning documents
remain Den-owned. F05/F06 are contract preparation only. This wave authorizes
reviewable PRs; it does not authorize merge, deployment or host operations.

## Evidence boundaries

- The focused unread test renders the actual source hook, tab markup and stable
  Chat presenter with vendored React. Authored event injection isolates the
  change from household history, live services and backend timing.
- The unchanged authored shell browser suite exercises the complete Home app,
  production renderer, fixture selection, channel independence, canonical
  drag/Undo, radial restoration and responsive navigation with disposable data.
- Authored fixtures are regression inputs. They do not establish actual Den
  visual fidelity, installed behavior, camera/perception accuracy or physical
  operation.
- Actual Den assets, saved registration, room images and coordinates stay
  private. No private browser preview is started by the QA worker. The UI
  integrator owns any serialized actual-room verification window.

## First-wave acceptance

| Check | Required observation |
| --- | --- |
| Hidden Chat | One new assistant turn sets the Chat unread indicator. |
| Visible Chat | Opening Chat clears it; an arriving visible turn stays read. |
| Covering surfaces | Replies arriving under an existing feature/dialog overlay remain unread until Chat is exposed. |
| Retired content | Retiring the last eligible unread reply clears the dot; surviving unread replies keep it set. |
| Turn continuity | History restoration, rerender and additional stream patches do not create new unread turns. |
| Scope isolation | Conversation or endpoint change resets the baseline without carrying unread state across scopes. |
| Stable owners | Tab changes keep the same Chat surface, composer draft, consumed Undo and private-review iframe. |
| Accessible indicator | Unread has a nonvisual description; the decorative dot is hidden from accessibility APIs. |
| Themes and viewports | Changed tab state remains visible in both themes at desktop, tablet and phone widths. |
| Retained regression | Fixture/channel/drag/Undo and radial checks pass without broad runtime changes. |
| Foundations | Shared components retain text contrast, visible focus, coarse-pointer targets and one-shot actionable toasts. |

## Commands and results

Independent review identified two P2 cases: an unread private answer could
leave a dot after retirement, and an assistant reply covered by an existing
feature/dialog could be treated as read. Both were corrected. Nine deterministic
cases cover the tracker, including the reproduced retirement failure; the
expanded focused browser run passed on October 18, 2026. Remaining pending rows
are not passes.

| Command/check | Status |
| --- | --- |
| `node tools/test-apartment-unread-state.cjs` | Passed: 9 deterministic cases after the retirement correction |
| `node tools/test-apartment-unread-integration.mjs` | Passed on corrected source: 1 browser test, 62 checkpoints, no HTTP/WebSocket attempts or page errors |
| `HOME_APARTMENT_BROWSER_SCREENSHOTS=0 node tools/test-apartment-home-shell-browser.mjs` | Passed on corrected source: 1 browser test, 28 checkpoints, screenshots disabled |
| Foundations focused checks | Foundations lane reports 11 unit cases, 768 contrast combinations, 33 aiming checks, 174 3D checks and 28 authored shell checkpoints; separate PR/evidence |
| Actual Den private browser verification | Passed bounded read-only combined-composition smoke check at 1920x1080, 1366x768, 768x1024 and 390x844; four saved fixtures, selection and Chat/Controls navigation retained; zero page errors, output, saves or session starts |

The browser commands require the existing Playwright runtime, supplied through
`NODE_PATH` where it is not installed in the worktree. They do not install
dependencies. The focused test blocks HTTP and WebSocket traffic and uses no
room data. The authored shell test fences external traffic, physical commands,
survey writes and camera/model session operations.

The corrected-source focused browser receipt is local-only at
`.tmp/apartment-unread-integration/run-1792353334563/receipt.json`. It pins the
tested app, event presenter and stylesheet bytes. Its 62 checkpoints cover
hidden/visible and first-token arrivals, streaming/rerender continuity,
conversation/endpoint/credential isolation, initial conversation-ID adoption,
background-document visibility, phone peek/expand behavior, nonreply filtering,
the unread accessibility description, six theme/viewport combinations,
covering surfaces, reply retirement/sanitization and retained draft/Undo/review
identities. The review frame is authored `srcdoc`;
it contains no household content or private review network request.

The unchanged complete-app authored shell run is local-only at
`.tmp/apartment-home-shell-browser/run-1792353339818/receipt.json`. It completed
in 24.3 seconds after both review corrections, retaining 28 checkpoints,
including the ten radial power cases, both canonical drag/Undo paths and
controller/canvas/composer identity. It recorded no page errors, forbidden
requests, physical commands, survey writes or camera/model sessions. Its room
and registration remain disposable authored inputs, not actual Den acceptance.
