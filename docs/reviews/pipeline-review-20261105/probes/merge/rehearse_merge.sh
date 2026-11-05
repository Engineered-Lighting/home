#!/bin/bash
# Merge-rehearsal probe. Usage: rehearse_merge.sh REPO SCRATCH_DIR BASE BRANCH...
# Creates a detached scratch worktree at BASE, merges each branch in order (no fast-forward),
# and on a conflict records the files and conflict-marker hunks, then resolves each conflicted
# file by taking the incoming branch's version ("theirs") so the rehearsal can continue.
# Never pushes. Prints a table and leaves the worktree for running suites.
set -u
REPO=$1; DIR=$2; BASE=$3; shift 3
rm -rf "$DIR"; git -C "$REPO" worktree prune
git -C "$REPO" worktree add -f --detach "$DIR" "$BASE" >/dev/null 2>&1 || { echo "cannot create worktree"; exit 2; }
cd "$DIR"
# identity is passed per command so the shared repo config is never written
G="git -c user.email=review@example.invalid -c user.name=review"
for b in "$@"; do
  sha=$(git rev-parse --short=12 "$b" 2>/dev/null) || { echo "MISSING $b"; continue; }
  if $G merge --no-ff --no-edit -q "$b" >/tmp/merge.$$ 2>&1; then
    echo "CLEAN    $b@$sha"
  else
    files=$(git diff --name-only --diff-filter=U)
    echo "CONFLICT $b@$sha"
    for f in $files; do
      h=$(grep -c '^<<<<<<<' "$f" 2>/dev/null || echo "?")
      echo "    $f  hunks=$h"
      git checkout --theirs -- "$f" 2>/dev/null || git rm -q -- "$f"
      git add -- "$f" 2>/dev/null
    done
    $G -c core.editor=true commit -q --no-edit -m "rehearsal: merge $b (conflicts resolved theirs)" || echo "    commit failed"
  fi
done
rm -f /tmp/merge.$$
echo "HEAD $(git rev-parse --short=12 HEAD)"
