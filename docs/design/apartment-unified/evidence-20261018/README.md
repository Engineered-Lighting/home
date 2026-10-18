# Design fidelity evidence

Each comparison places the supplied prototype on the left and the implementation
on the right. Both browser captures use the same CSS viewport at density 1.
The screenshots contain authored test equipment and existing simulation data;
they contain no household video or private preference content.

| Viewport | Home overview | Light inspector |
| --- | --- | --- |
| Desktop, 1366 x 768 | [Compare](desktop-overview-comparison.jpg) | [Compare](desktop-selected-comparison.jpg) |
| Tablet, 820 x 1180 | [Compare](tablet-overview-comparison.jpg) | [Compare](tablet-selected-comparison.jpg) |
| Phone, 390 x 844 | [Compare](phone-overview-comparison.jpg) | [Compare](phone-selected-comparison.jpg) |

[Desktop inspector at native scale](desktop-inspector-focus.jpg),
[desktop Chat](desktop-chat-comparison.jpg), and
[phone Chat](phone-chat-comparison.jpg) provide readable control and typography
comparisons. See the root [design QA report](../../../../design-qa.md).

## Scope and provenance

The UI follows `prototype/` from HomeRedesign.zip, SHA256
`9fcff2f8b0a73d0b0ea00ce9a984550ebec08ea9c94fe83335e63cace80375d8`.
The prototype room and scripted capabilities are reference examples. The current
renderer, fixture descriptors, command-home selector and perception component
remain authoritative; their data and supported actions differ from the mock.

The accepted-source composition used for visual and authored interaction checks
contains the frozen renderer hashes below. This PR does not edit those files.

| File | SHA256 |
| --- | --- |
| engine.js | `187dcc2c7268a7162c06d7fa8937c17e21a430a788016f626d7fe2320fea98f6` |
| picking.js | `da9ff80de476bf3b121ea9e17f4e03c12c59fe2bc5fde78679afd94fa6dbb17c` |
| registered-desk-target.mjs | `fac01892e1fbbdc38fa7b7d6068492bef301f541292f621e11c160294fb0931b` |

UI hashes: home-app `832e877c4c082a1fed424be44dfc0b8bf6d557a5de342667e9c07f41fd58ff4e`,
CSS `db498031887c85777e5168b1b2107584c6ad2f8331dc5ebd6d2dc3239dcc452d`.
The accepted-source Apartment UI is `66f243ff18322a4c47ba07e44f99fed4286a52360ca72aed20db45db44b968dc`.
The public PR Apartment UI is `7a7ccf30032aa018909b9f46c5ca246d86125a92151f4d4cea075ea25be43e91`:
it excludes the independently owned Den selector changes absent from main.
The UI-only patch applies to either baseline; applying the separate Den patch
to public main plus this UI reproduces the accepted composition byte-for-byte.

Authored accepted-source verification: 5 tests / 27 browser checkpoints, all
134 pins unchanged, zero physical commands, saves, camera/model session starts
or test screenshots. Visual screenshots above were captured separately through
the browser. Public-source browser acceptance also passes without a source overlay.

## Rollback and rollout

Use `?apartmentRedesign=0` or Menu > Previous Home for immediate rollback.
Use `?apartmentRedesign=1` to explicitly choose the redesign. Existing explicit
layout links retain precedence over the saved browser preference.

This evidence is local software verification, not a deployment or live/physical
acceptance. A rollout must recapture current installed sources and apply only
the reviewed UI deltas while preserving the repair and Den owners' changes.
Do not deploy the shared dirty checkout or substitute public-main renderer files.
