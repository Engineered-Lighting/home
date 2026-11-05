#!/bin/sh
# Fetch again and list which branches moved since the harness's worktrees were made.
SP=$SCRATCH
git -C /home/user/home fetch -q origin 2>/dev/null
while read b s rest; do
  now=$(git -C /home/user/home rev-parse -q --verify "origin/claude/$b" 2>/dev/null || echo MISSING)
  if [ "$s" = "MISSING" ] && [ "$now" = "MISSING" ]; then echo "$b: still absent";
  elif [ "$s" = "MISSING" ]; then echo "$b: NEW on origin since then: ${now%${now#????????}} ($(git -C /home/user/home log --format=%s -1 $now 2>/dev/null | cut -c1-90))";
  elif [ "$s" = "$now" ]; then echo "$b: unchanged ${s%${s#????????}}";
  else echo "$b: MOVED ${s%${s#????????}} -> ${now%${now#????????}} ($(git -C /home/user/home log --oneline $s..$now 2>/dev/null | wc -l) commits: $(git -C /home/user/home log --format=%s -1 $now 2>/dev/null | cut -c1-90))"; fi
done < ${1:-$SP/seamwt/SHAS_START.txt}
