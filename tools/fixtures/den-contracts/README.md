# Den contract fixtures (test copies)

These are byte-for-byte copies of Den's proposed UI contract files, taken on
2026-10-20 from the shared checkout's `docs/contracts/den/`. Den has not
committed them yet; its frontend PR will add them.

All of them are synthetic. They contain no household coordinates, imagery or
private digests.

| Copy | Den source | SHA-256 |
|---|---|---|
| `reason-vocabulary.json` | `docs/contracts/den/reason-vocabulary.json` (`den-reason-vocabulary-1`) | `407898737710103d47605a350d1219d970006e49385b4c01c3bc1f7fe67d77b2` |
| `reason-rows.json` | `docs/contracts/den/fixtures/reason-rows.json` | `8e1ad0bcc682bd009478cd7c03642d262a6bc61a5c39a8f637c2cfbe3b458b83` |
| `home-den-capabilities-victoria.json` | `docs/contracts/den/fixtures/home-den-capabilities-victoria.json` (proposed `home-den-capabilities-1`) | `a8dd813b89c53890aeaafe34d5f02cbc8077a7e2e1044fa4f8f723782369b131` |
| `den-ops-banner-silent.json` | `docs/contracts/den/fixtures/den-ops-banner-silent.json` | `26cb8791ebd4ad0ef1a85c71f873cc834d56f3b4cdb1c2412dc186fb4feab7b2` |
| `den-ops-banner-alert.json` | `docs/contracts/den/fixtures/den-ops-banner-alert.json` | `be9ca0ef811ae05cc9e9bf8334b7b5159e6f05bb56007e32c6bcb995a49325d9` |
| `activity-health-events.json` | `docs/contracts/den/fixtures/activity-health-events.json` | `4416937352373254a624d5ebec930c2940f92b6cebd155410831baede91e882d` |

Only `tools/test-apartment-states.cjs` and
`tools/test-apartment-status-rows-browser.mjs` read these copies. Once Den's
contracts land, point those tests at `docs/contracts/den/` and delete this
folder. Where a copy and Den's file differ, Den's file wins.
