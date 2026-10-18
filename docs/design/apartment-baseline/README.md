# Apartment frontend prerequisite for PR #216

This branch restores the frontend foundation that the local Apartment already
uses. GitHub main (`41a6ce7f`) is older than that foundation. It must be reviewed
and merged before the Phase 1 shell PR is retargeted to main. No merge or
deployment is implied by these commits.

## Sources and scope

`source-provenance.json` records the frozen source hashes and every restored
path. The ordinary Apartment renderer and its transitive Cloud/Photo/Mesh,
equipment, site-session, camera and perception dependencies come from accepted
frontend08's captured public source. The five primary UI/data files instead use
the later read-only production capture (manifest
`9320545403299f36e15faf7bede3ad4a52821dbe6aa97e1f9d9bbdeb85a93021`).
That capture preserves the accepted desk-parent callback, authentication,
remember-login and background-perception behavior. Pending resident/shadow and
offline-rehearsal experiments from the former review baseline are excluded.

Four accepted companion files (security, native preference transport, camera
query and people polling) stay with their accepted controller contracts. The
Lab stylesheet and two canonical registration-test dependencies come from the
independently pinned accepted Lab source. No private room pack, household image,
recording, token, installed configuration or runtime data is published here.

The accepted index has exactly three removals: the unused Cesium import-map
entry and two unused legacy Apartment memory/perception boot entries. Cesium's
incomplete vendor tree and optional world-zoom imports are excluded. World zoom
remains off by default; this restoration does not qualify that optional path.
No renderer interface or geometry/frame binding is changed by authored code in
this restoration. The renderer files are frozen incumbent implementations.

## Integration boundary

This is a frontend foundation and authored registration-test prerequisite.
Stock main's gateway does not yet contain the installed content-pack,
registration or Perception Lab routes. The authored browser test supplies
loopback handlers and generated private test packs. Passing it proves ordinary
frontend composition on those contracts; it does not establish stock-main
gateway integration, live cameras, inference, physical control or deployment.
The repair owner retains the accepted installed gateway and live rollout.
Gateway reconciliation remains separate from this shell/CI work.

## Stack and rollback

Review this prerequisite before #216. Merge it first, then retarget #216 from
the recorded baseline branch to main and rerun the checks. Do not merge the
temporary baseline branch as an unreviewed shortcut. Reverting this prerequisite
requires reverting its dependent shell first; never reset the shared checkout.
The source capture hashes describe the frozen input bytes; normal Git line
ending conversion can change checkout byte hashes without changing source.
