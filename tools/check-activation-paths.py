#!/usr/bin/env python3
"""check-activation-paths.py — keep this workstream off the Home Agent's
pinned activation source.

The Home Agent Phase-3 activation ceremony pins its source: the host
verifies `git diff --quiet <ACCEPTED_COMMIT> -- <ACTIVATION_PATHS>` over a
67-path list. **One byte from an unrelated branch reaching `main` under any
of those paths breaks the pin and stalls a live, TPM-backed ceremony that
has already failed fail-closed once.** The cost of a mistake here is not a
red CI run, it is someone's paused migration.

So this is a mechanical gate rather than a rule people remember.

The path list is parsed with `ast` straight from the ceremony's own source
of truth — `stack/home-agent-deploy/operator/phase3_activation_source_plan.py`
— never copied. A hardcoded copy would drift silently the moment the other
workstream adds a path, and a stale allowlist that still says PASS is worse
than no check at all.

It also warns about the PIN FREEZE window. Those paths are NOT forbidden;
merging them is only unsafe while the freeze is announced, because the E1
workflow cancels in-progress runs per-ref and the run being awaited is what
the new pin must reference.

Usage:
  tools/check-activation-paths.py                 # branch vs origin/main
  tools/check-activation-paths.py --staged        # what you are about to commit
  tools/check-activation-paths.py --range A..B
  tools/check-activation-paths.py --install-hook  # run it on every commit
  tools/check-activation-paths.py --pin-freeze    # treat freeze paths as fatal

Exit: 0 clean · 1 an ACTIVATION_PATHS violation (or a freeze hit with
      --pin-freeze) · 2 the path list could not be read (fail closed).
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
PLAN = REPO / "stack" / "home-agent-deploy" / "operator" / "phase3_activation_source_plan.py"

# The E1 workflow cancels in-progress runs per-ref; during a PIN FREEZE the
# awaited run is the one the new pin references. Read from the workflow so
# it cannot drift.
E1_WORKFLOW = REPO / ".github" / "workflows" / "home-agent-e1-postgres.yml"


def load_activation_paths() -> tuple[str, ...]:
    """Parse ACTIVATION_PATHS from the ceremony's own module, via ast.

    Deliberately not an import: the module has side-effect-free constants
    but importing drags in its dependencies, and this must work from a bare
    checkout on any machine.
    """
    if not PLAN.exists():
        print(f"FATAL: cannot find {PLAN}", file=sys.stderr)
        raise SystemExit(2)
    tree = ast.parse(PLAN.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "ACTIVATION_PATHS":
                    value = ast.literal_eval(node.value)
                    return tuple(str(v) for v in value)
    print("FATAL: ACTIVATION_PATHS not found in the source plan — the "
          "ceremony's shape changed. Failing closed rather than guessing.",
          file=sys.stderr)
    raise SystemExit(2)


def load_freeze_paths() -> list[str]:
    """Push-path filter of the E1 workflow, best-effort."""
    if not E1_WORKFLOW.exists():
        return []
    out, seen_paths = [], False
    for line in E1_WORKFLOW.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s == "paths:":
            seen_paths = True
            continue
        if seen_paths:
            if s.startswith("- "):
                out.append(s[2:].strip().strip('"').strip("'"))
            elif s and not s.startswith("#"):
                break
    return out


def changed_files(args) -> list[str]:
    def git(*a):
        r = subprocess.run(["git", "-C", str(REPO), *a],
                           capture_output=True, timeout=60)
        return r.stdout.decode(errors="replace").splitlines()

    if args.staged:
        return [f.strip() for f in git("diff", "--cached", "--name-only") if f.strip()]
    if args.range:
        return [f.strip() for f in git("diff", "--name-only", args.range) if f.strip()]
    subprocess.run(["git", "-C", str(REPO), "fetch", "-q", "origin", args.base],
                   capture_output=True, timeout=120)
    base = git("merge-base", "HEAD", f"origin/{args.base}")
    if not base:
        base = git("merge-base", "HEAD", args.base)
    if not base:
        print(f"FATAL: no merge-base with {args.base}", file=sys.stderr)
        raise SystemExit(2)
    return [f.strip() for f in git("diff", "--name-only", f"{base[0]}..HEAD") if f.strip()]


def matches(changed: str, pinned: str) -> bool:
    """A list entry is either an exact file or a directory prefix."""
    p = pinned.rstrip("/")
    return changed == p or changed.startswith(p + "/")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--range")
    ap.add_argument("--base", default="main")
    ap.add_argument("--pin-freeze", action="store_true",
                    help="a PIN FREEZE is active: treat E1 push-filter paths "
                         "as fatal too")
    ap.add_argument("--install-hook", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.install_hook:
        hook = REPO / ".git" / "hooks" / "pre-commit"
        body = ("#!/bin/sh\n"
                "# Home Agent activation-source guard (tools/check-activation-paths.py)\n"
                'exec python3 "$(git rev-parse --show-toplevel)"'
                "/tools/check-activation-paths.py --staged\n")
        if hook.exists() and "check-activation-paths" not in hook.read_text(errors="replace"):
            print(f"a pre-commit hook already exists at {hook};\n"
                  "add this line to it yourself:\n  " + body.splitlines()[-1])
            return 0
        hook.write_text(body, encoding="utf-8")
        hook.chmod(0o755)
        print(f"installed {hook}")
        return 0

    pinned = load_activation_paths()
    changed = changed_files(args)

    violations = [(c, p) for c in changed for p in pinned if matches(c, p)]
    freeze = load_freeze_paths()
    freeze_hits = [(c, p) for c in changed for p in freeze
                   if matches(c, p) and not any(c == v[0] for v in violations)]

    if not args.quiet:
        scope = ("staged changes" if args.staged
                 else args.range or f"branch vs origin/{args.base}")
        print(f"activation-source guard — {scope}")
        print(f"  pinned paths : {len(pinned)} (parsed from the ceremony's own module)")
        print(f"  files checked: {len(changed)}")

    if violations:
        print(f"\n\033[31mBLOCKED\033[0m — {len(violations)} file(s) touch the pinned "
              "activation source:")
        for c, p in violations:
            print(f"  {c}\n      matches pinned entry: {p}")
        print("\nThis breaks `git diff --quiet <ACCEPTED_COMMIT>` on the host and "
              "stalls a live activation ceremony. Move the change out of these "
              "paths, or coordinate with the Home Agent workstream first.")
        return 1

    if freeze_hits:
        tag = "\033[31mBLOCKED (pin freeze)\033[0m" if args.pin_freeze else "\033[33mNOTE\033[0m"
        print(f"\n{tag} — {len(freeze_hits)} file(s) match the E1 push filter:")
        for c, p in freeze_hits:
            print(f"  {c}  ({p})")
        if args.pin_freeze:
            print("\nA PIN FREEZE is active. The E1 workflow cancels in-progress "
                  "runs per-ref, and the run being awaited is what the new pin "
                  "must reference. Wait for PIN FREEZE END.")
            return 1
        print("  Safe to merge OUTSIDE a pin freeze. If one is announced, "
              "re-run with --pin-freeze and hold these back.")

    if not args.quiet:
        print("\n\033[32mCLEAN\033[0m — nothing touches the pinned activation source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
