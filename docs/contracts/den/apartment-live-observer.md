# Den Apartment live observer

The owner wants one Victoria view showing observed body form, corner camera
video, rolling model results and coordinator-driven virtual spots. This
contract connects existing readers; it grants no recording or light authority.

## Ownership and routing

The UI chat owns camera/perception composition, the perception dock/model and
client-side activity-loop transport. The Den coordinator owns Lab/backend,
bound-reading, lighting-brain/follower and render-evidence contract regions.
Gateway mounts remain an explicitly coordinated integration window.

Browser calls retain the authenticated Home session and
`X-Home-Lab-Site: victoria`. Upstream credentials stay on the server. The
gateway must mount a qualified fixed-origin Lab read adapter; an HTML fallback
or a `200` response alone is not a working Lab route. No arbitrary upstream
URLs, caller credentials or general write proxy are permitted.

## Following the managed recording

`GET /api/perception-lab/v1/activity-loop` requires only the upstream `read`
capability. Stop/resume/cadence mutations require `record`; the observer never
calls them. The existing response includes:

- top-level `schema`, `state` (`running`, `paused`, `stopped`),
  `stopped_reason`, `health`, `position`, `commands_lights: false`;
- nullable `deep_stack`, with nullable `session_id`, process-local
  `producer_epoch`, `wanted`, `rolls`, `ended_externally`, and retry diagnostics.

Use serial, abortable reads. On session/producer-epoch change, retire all prior
observers, media reads and callbacks before resolving the new session. Resolve
the ID through the existing authenticated session read and pin its generation
and Victoria/Den identity. Session/media generation may be zero; model or
experiment generation is a different field and starts at one. Never substitute
one for the other. A new process resets the roll counter; count rolls only
within one producer epoch. Missing epoch data is unsupported, not continuity.

Paused/stopped, no current session, failed status/session read, or an incompatible
response withdraws current-looking body data and exposes its reason. Do not
start another analysis/recording, adopt an unrelated latest session, retain
retired data as current or resume an intentional stop to populate the view.

## Body, models and lights

Use the existing Lab observer/player/projection path. Show every joint/edge
actually supplied by `approximateRoomPose`, preserving null/missing joints,
source frame/capture/model context, registration and approximate-room labels.
Viewing association is anonymous correlation, not person identity. Browser
interpolation may smooth a presentation but must not renew source freshness.

The brain's separate `person_pose` shoulders/head contract remains unchanged.
Rendering fuller body geometry does not grant that viewing packet beam or
geometry authority. Reading uses trusted raw bound evidence as specified in
`bound-reading.md`; its original deadline is separate from a sampling display
hold or development dispatch deadline.

Keep the existing owner camera component mounted alongside perception and
virtual fixtures. Use bounded session events for rolling model history and
show actual model identity, source age and refusal/unavailable states. Never
relabel an old model result for a new recording. A missing stream is not a
vacant room. Observe coordinator requested/applied/rendered state through the
existing follower and opt-in render evidence; GPU completion does not prove
screen presentation or useful book illumination.

## Verification boundary

Exercise recording rolls, pending-read retirement, callbacks that finish after
abort, distinct session/model generations, stopped/paused/retry states,
unavailable routes and missing joints. Actual browser verification must prove
simultaneous real Victoria body/video/model/light sources and measure their
separate ages. Authored tests alone do not establish that result.
