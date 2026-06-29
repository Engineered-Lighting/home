# Change Notes

Change-note fragments are the source of truth for user-facing release notes.
They let Codex, Claude, humans, and GitHub workflows describe changes in the
same durable format.

## When to Add a Fragment

Add a fragment for any user-facing, deploy-relevant, release-relevant, or
operationally meaningful change.

Do not add a fragment for typo-only docs edits, test-only changes, or internal
cleanup with no behavior impact. For PRs without a fragment, use the
`no-release-note` label and explain why in the PR body.

## File Location

Put unreleased fragments in:

```text
changes/unreleased/<short-kebab-name>.md
```

The release preparation workflow moves consumed fragments to:

```text
changes/archive/vX.Y.Z/
```

## Fragment Format

```markdown
---
title: Short user-facing title
target: desktop
type: added
---

One or two sentences describing what changed and why it matters to a user or
operator.
```

Allowed `target` values:

- `desktop`
- `web`
- `backend`
- `docs`
- `internal`

Allowed `type` values:

- `added`
- `changed`
- `fixed`
- `removed`

Keep descriptions factual. Do not include secrets, credentials, local-only
tokens, or unreleased implementation speculation.
