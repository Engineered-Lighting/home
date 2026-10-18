# First-wave capability and state contract preparation

October 18, 2026. Preparation for [APT-F05 / #224](https://github.com/Engineered-Lighting/home/issues/224) and [APT-F06 / #225](https://github.com/Engineered-Lighting/home/issues/225).

**Proposed presentation mapping; no interface is activated by this document.**
This is a producer-to-UI crosswalk for later owner review. It changes no source,
schema, registry, session, persistence, inference, admission or execution path.
F05/F06 implementation and their authored, actual-room and installed acceptance
remain open. [PR #219](https://github.com/Engineered-Lighting/home/pull/219) at
`b0230dc15034942584836877981d22a6c94da17b` is the inspected public source baseline;
its implementation credit is not merge, deployment or live acceptance. The
same-day Repair handoff and Den implementation plan/backlog were read locally
for ownership and proposed successor boundaries; their private operational
receipts are not republished here.

## Capability crosswalk

Keep three questions separate: what the application supports, what is available
in the current home/scene/session, and what an existing authority permits now.
An answer to the first two cannot grant the third. A future shared presentation
adapter may consume the existing producers below; it must not move their checks
into a badge or duplicate their authority. The final object shape, names,
versioning and ownership are **unapproved**; this table defines concepts only.

| Presentation concept | Existing producer and current evidence | UI consumption and authority boundary | Owner decision before implementation |
|---|---|---|---|
| Home and assets | `HomeSpatialViewport` in [site registry](../../../app/src/home-site-registry.js): selected site, content revision/status, scene availability and registration version. `HomeApartmentSiteSession` in [site session](../../../app/src/home-apartment-site-session.js): identity and `idle/loading/ready/unavailable/error/invalidated`. | Explain each asset/load result in its own home. `sceneAvailable` means a compatible interior can be prepared; neither a ready scene nor confirmed parcel location permits device control. Missing/incompatible content cannot inherit another home's scene. | Registry/session owner agrees a read-only projection and replacement/invalidation notifications; UI owner agrees all variants consume it. No `capabilities` object currently exists on the site records. |
| Light controls | [Apartment data](../../../app/src/home-apartment-data.js) supplies model source, site-scoped states, registration read/write response and existing `callService` checks. Site session supplies `canControlEcho`. | Separate a supported control type, current mapped entities/connection, model-edit permission and physical-output permission. A writable private placement model is not a physical-control grant. Preserve current site/entity/currentness, rehearsal, Travel and commissioning/command checks at their existing dispatch boundaries. | Registry/data and UI owners agree fields for presentation only. Den/runtime owners approve any later execution-status consumer; this wave creates none. |
| Engineered fixture, aim and bench | Existing model/fixture modules describe channels and modeled preview; current aiming/bench paths carry their own supported profile, readiness and refusal. | Retain the design's `engineered` and `bench` distinctions as concepts. Show simulated, modeled or commissioned only with source support. Loopback connectivity, a visible fixture and a copied command preview cannot imply Den execution or installed qualification. | Fixture/aim/bench producers and UI agree capability/readiness mapping. The design enums are not an approved registry schema. |
| Cameras and analysis session | [Perception source](../../../app/src/home-apartment-perception-source.js) reads exact-session descriptors and `analysisAvailability`, `analysisState`; Start/Stop use the incumbent owner path. | Camera inventory, independently playing video, an available analysis recipe, an owned running session and a decoded analyzed image are separate states. Preserve explicit bounded Start/Stop, current scope, no automatic start/renewal and uncertain-cleanup reporting. No-session copy must not remove the incumbent authorized Start control merely because the prototype omitted it. | Repair/Perception owns source and lifecycle semantics. UI consumes agreed availability/reasons; no new session or camera authority is added. |
| Perception, JEV and trace | [Den Lab reader](../../../app/src/home-den-lab-stream.js) projects source/model rows into `home-apartment-perception-1`; [view projection](../../../app/src/home-apartment-perception-view.js) keeps exact frame association, original times, model status/reason and result missingness. | Detection, exact-frame pose and JEV are separately available. A connected stream or one successful model cannot stand for all of them. Preserve abstention, missing values and subject limits; a frame-local instance is not an anonymous track or identity. | Perception producer and Den reader owners agree any missing timing/instance/subject fields together before shared edits. UI does not create activity, proposal or execution records to complete the trace. |
| Body and room placement | View projection separately exposes admitted `roomPose`, `diagnosticRoomPose` and `approximateRoomPose`; player supplies their exact displayed-frame correspondence and receipt retirement. | Body support is distinct from a currently renderable body. Preserve **Approximate room placement**, supplied joints only, unknown error bounds, original registration/refusal and no Follow/lighting/geometry/physical authority. Missing joints stay missing. Metric refusal must remain separately explainable from valid 2D evidence. | Repair and Den agree any reader/projection successor. The planned multi-person `persons[]`/`bodies[]` and association work is not implemented by F05/F06. |

[Service capability health](../../../app/src/home-capabilities.js) is a separate
service-to-user-feature registry. Reuse its named-system health wording where
applicable; do not silently repurpose it as the per-home fixture/session model.
In particular its health severity and idle-service handling are not execution
permission or evidence freshness.

## Existing persistence and remaining presentation decisions

Preserve [SPEC: What persists](SPEC.md#what-persists) as the intended experience,
with the existing security and authority boundaries below. No new browser store
or persisted field is proposed here.

| State | Existing source behavior to retain | Remaining F05 decision/work |
|---|---|---|
| Selected home and navigation | Registry validates saved site selection. Data stores discrete coordinate-free view preferences per site, registration version and frame identity; cosmetic content revision is not persisted in that preference. | Agree an adapter readback that does not treat an old preference as a current scene or authority. Orbit refit on a home switch remains the intended design. |
| Unsaved model/placement workspace | In-memory workspaces are isolated by site, registration/frame and authority; Victoria also includes content revision. Authority replacement clears parked workspaces. Private models and credentials are not placed in a new UI browser store. | Preserve current unsaved-work round trips; audit required selection, tab, proposal/Undo and camera-choice round trips before proposing additional state. Do not claim the complete design persistence set already exists. |
| Authoritative saves | Echo keeps its existing model authority and legacy recovery behavior. Victoria private equipment registration uses verified binding, revision/conflict handling and producer read/write capability. Simulation/rehearsal and other-site restrictions remain. | UI renders saved/local/saving/conflict/error from results, preserves edits on failure/409 and exposes retry/review through existing paths. A failed save cannot become Saved or overwrite another home's state. |
| Shared chrome versus home-specific state | Theme/layers/dock/detail are intended shared preferences; selection/tab/proposals with Undo/drafts/named aims/camera choice are intended per-home state. Existing `home-apartment.jsx` owns consumers. | UI integrator audits each requested consumer with data/session owner. Agree lifetime and privacy of any missing state before adding persistence; changing selection resets channel/zone/name editing without discarding a light's proposal. |

## State and age crosswalk

F06 should centralize presentation wording over the producer results; it must
not replace producer clocks, expiry, scene lifetime or permission checks. Keep
connection, model/save state, session state, displayed-frame identity, age and
qualification as separate inputs, even when one compact status row presents
them. Preserve the producer reason in Details rather than collapsing unrelated
failures into Offline.

| Existing input | Proposed presentation treatment | Retained limit |
|---|---|---|
| HA connected/offline; last reported state/time when supplied | “Home Assistant connected” or “Home Assistant offline”; explicitly identify last reported values and their original time. If time is absent, say unavailable. | No queued output or optimistic live claim while unavailable; do not invent a report timestamp. Another home's connection is not applicable. |
| Site phase and model/save result | Loading, unavailable with reason, failed/retry, local draft, saving, saved or conflict as the specific producer supports. | A save result and a scene load are different facts. `equipment_registration` source metadata alone does not mean live device state. |
| Missing/terminal/recorded session; independent video state | No analysis session, stopped, replay/recorded with original date when available; separately state video playback. Keep current explicit Start/Stop behavior. | No-session, no-frame and no-person differ. A recording session alone does not prove current decoded pixels, pose or JEV. No automatic renewal or selection of an unrelated session. |
| Frame `captured_at`; pose/model `observed_at`; their `available_at` and `valid_until` | Preserve the original values. Use existing view `age.status`, `seconds`, `uncertainty_s` and reason without restamping on receipt/render. | Source time, availability and UI receipt are different. The optional design `analyzed_at` field is not present as a universal current contract; do not synthesize it from `available_at`. |
| View age with qualified Unix clock and finite uncertainty | Existing `age()` returns current only when required original timing is present, ordered and not future/overlapping or expired against the uncertainty bound. Show expired for `status: stale, reason: expired`; retain raw status/reason in Details. | Missing clock/original/expiry/required availability remains unavailable; invalid ordering remains invalid, and future/clock overlap remains explicit. Never infer zero uncertainty. |
| Current source `sampleClock()` | It supplies Unix `now_s` with `uncertainty_s: null`; the view consequently reports age unavailable/`clock_unavailable`. Exact-frame presentation may still be possible under its separate existing checks. | “Current source” transport/session metadata is not a qualified age claim. Do not add a numeric age or gate all 2D display on new clock admission in the UI. Clock qualification belongs to the producer owner. |
| Player `heldFrame` while a new image decodes | “Showing previous analyzed frame while loading” with that displayed frame's exact overlay/metadata. | This is a media transition state, not the prototype's 10-second–2-minute Held band or a reason to renew evidence. Decode failure cannot pair new pose with old pixels. |
| Live diagnostic/approximate receipt hold | Player validates event identity and supplied remaining hold, subtracts elapsed time, uses a monotonic retirement deadline and takes the earlier deadline for the same event. Preserve the existing maximum two-second live receipt window. | Display retirement only, not exposure freshness or a metric lease. Repeated polls/redraws do not renew it; Stop/disconnect, identity replacement and invalidated events withdraw it. Approximate projection retains unknown clock and error bounds. Recorded diagnostic rendering remains historical under its separate existing checks. |

The prototype's current ≤10 s / held ≤2 min bands are **not adopted**. A fresh
receipt cannot extend `valid_until`; expiry does not manufacture vacancy or an
empty room. Recorded data is historical and cannot gain live authority by
scrubbing. Existing player/view owners decide image retirement; F06 does not
introduce a second timer or hide valid exact-frame 2D because optional metric
placement was refused.

## Owner handoff and later acceptance cases

Repair retains renderer/picking, perception presentation/source/lifecycle and
backend ownership. Den retains producer/reader contracts, subject association,
geometry, journal/worker and command semantics. The UI integrator owns shared
shell presentation; foundation work supplies leaf components/status tokens.
Canonical Den planning documents stay with their planning owner. This new UI
preparation document does not amend those assignments or reopen completed Den
foundations [#198](https://github.com/Engineered-Lighting/home/issues/198),
[#200](https://github.com/Engineered-Lighting/home/issues/200) or
[#201](https://github.com/Engineered-Lighting/home/issues/201).

Before F05/F06 implementation, record producer/consumer agreement on:

- The final read-only capability shape and owning module; supported versus
  available versus permitted wording; provenance/reason and invalidation inputs.
  Consumers may branch on agreed capability values while producer site guards
  remain intact. No enum from the authored handoff is an approved wire schema.
- The authoritative sources for missing last-report, analysis completion,
  availability, clock-domain/uncertainty and original-expiry fields. Missing
  values remain unknown until the producer supplies an agreed successor.
- The per-home state audit above, allowed lifetime/privacy and conflict handling
  for each missing presentation field. No new private browser persistence.
- The exact shared-file window and accepted source composition. Additive
  perception/instance/track/subject schemas require their producer and consumer
  owners; F05/F06 must not preempt that work or duplicate current 2D delivery.

Later controlled acceptance should cover:

| Scenario | Expected observation |
|---|---|
| Echo draft → Victoria → Echo; authority/content changes during awaits | No cross-home devices/feed/aims; original compatible unsaved edits return. Superseded responses cannot restore old controls or state. |
| HA offline with a previous report | Named system offline; original report/time or explicit unknown; controls follow existing refusal, with nothing queued. |
| No session, then explicit Start/Stop | Honest empty analysis state beside any independent video; only the owned bounded session is started/stopped; navigation never renews it. |
| Unknown clock, missing expiry, invalid/future timing and original expiry reached | Respective unavailable/refusal reasons remain visible. No guessed age, prototype grace period or polling-based extension. |
| Repeated same-event approximate packet, Stop or changed scope/frame | Existing receipt retires without renewal; label remains exact while renderable; missing joints stay absent. No Follow/output authority appears. |
| Valid decoded exact-frame 2D plus retired/refused optional metric placement | 2D and its own missingness/status remain inspectable; 3D refusal is separate. This is a required producer/reader join case, not a claim that live acceptance already passed. |
| Late/reordered model result, decode failure, no-person and replay | No pose over unrelated live video; distinguish no-frame from no-person; recorded evidence stays explicitly historical. |
| Save conflict/failure, home switch, tab change and consumed Undo | Edits and intended per-home state survive only within their current authority; no false Saved state or resurrected Undo/action. |

These cases are a handoff, not test results. This documentation-only wave runs
no camera/model/host/browser trial and changes no operational admission,
physical I/O, merge or deployment. Public review must contain no household
imagery, coordinates, private registration data/digests or installed-server
pins; authored software checks, private actual-room verification and installed
or physical acceptance remain separately reported.
