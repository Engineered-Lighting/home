#!/bin/bash
# For each file under the shared folders, report whether the merged tree's blob equals the blob on
# at least one in-scope branch that touched it. A file matching no branch is a hybrid nobody tested.
# Usage: run inside the merge-rehearsal worktree: hybrid_check.sh BRANCH...
BR="$@"
for f in $(git ls-files tools/den-take tools/den-label tools/den-light tools/den-core tools/correction-capture tools/den-n2 tools/den-photometry docs/plans docs/architecture/live-v1 ha-config/custom_components/den_lighting tools/fixtures/den_lighting-1.0.4 changes/unreleased); do
  m=$(git rev-parse HEAD:$f 2>/dev/null)
  match=""; touched=""
  for b in $BR; do
    bb=$(git rev-parse $b:$f 2>/dev/null) || continue
    base=$(git rev-parse origin/main:$f 2>/dev/null)
    [ "$bb" != "$base" ] && touched="$touched ${b#origin/claude/}"
    [ "$bb" = "$m" ] && match="$match ${b#origin/claude/}"
  done
  if [ -z "$match" ]; then echo "HYBRID $f (touched by:$touched)"; fi
done
echo "hybrid check done"
