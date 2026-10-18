# Photo recovery review

Related to APT-S03 / #228, against `557164592f916cc4f93b79214f560d24d6f797b6`.
This bounded change adds explicit Photo Retry and Back to Cloud actions through
the existing mode owner. Repair and Den agreed the toolbar window; Repair also
approved the narrow asynchronous request and cancellation ownership additions.

## Independent review

Standards: **0 findings** after the final cancellation additions. Spec: **one
initial P2, resolved; 0 remaining findings**. Both final reviews independently
ran all 46 source-derived cases. The final 44-checkpoint mounted browser receipt
matches the production source pins recorded in `wave5-photo-recovery-qa.md`.

The initial publication guard correctly ignored an old request, but same-home
travel cancellation could leave its loading indicator stranded. Importing a
home pack and canceling the picker exposed that path. Cancellation now clears
only its synchronously owned Promise/loading before awaiting. Late completion
cannot clear newer bookkeeping or publish an error/refit. Four new actual
cancellation regressions failed before this correction and pass afterward.

The existing mode-event listener also inherited a retired travel-intent guard.
Only its mode-presentation assignment now follows the installed engine's open
view, lifecycle, selected site, revision, frame and registration identity. The
current owner manifest uses the same registration precedence as the renderer.
Persistence and every other subscriber retain their previous guards. Tests
execute the actual renderer mode method and actual host subscription, proving
that recovered controls agree with the renderer without adding a mode call,
save authority or loader/generation change.

## Evidence and limits

- 46 source-derived tests cover current success/failure, explicit Cloud,
  locks/placeholders, unavailable owner context, stale home and A-to-B-to-A,
  Photo-to-Cloud-to-Photo races, cancellation and installed scene retirement.
- 44 mounted checks cover both themes, desktop and coarse phone, keyboard and
  native touch, plus the actual mode/cancellation publication paths. Authored
  promises and inert producers replace asset/transport work in this focused
  proof. Network, WebSocket attempts and page errors are zero.
- The source change includes no renderer, loader, rig, generation, camera/feed,
  selector, channel mapping, authority or backend schema modification.

S03 remains partial. Consolidated camera-pose selection, full fixture-overlap
acceptance and the inherited saved-view string-error path remain outside this
slice. Authored source/component evidence, full-shell CI, private accepted-room
verification, installed behavior and physical qualification are separate.
F05/F06 remain contract preparation only. No merge or deployment occurs here.
