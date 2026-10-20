# Browser Lab read adapter

Source: `web-gateway/perception-lab-route.mjs`. The UI integration mounts this
module in `server.mjs` after the existing authentication and Agent boundaries.
Missing private configuration refuses access. Source integration and the
matching full-gateway fixture proof do not establish provisioning or deployment.

## Reviewed factory interface

```javascript
createPerceptionLabReadHandler({
  origin,                 // trusted server config: one fixed HTTPS origin
  ca,                     // bounded pinned CA PEM bytes, server-side only
  credential,             // synchronous callback reading the private read grant
  authenticate,           // current Home authentication check, checked twice
  timeoutMs: 5000,
  maxConcurrent: 4,
});
```

The returned synchronous handler returns true when it owns the Lab namespace
and handles response completion asynchronously. It returns false for unrelated
paths. Invoke it after the existing dedicated/native-agent Host/Origin policy
and Home authentication boundary, before generic routing/static fallbacks.
Do not change native/Tauri authority to support this browser feature.

Recommended private server configuration names:

- `HOME_WEB_DEN_LAB_ORIGIN`;
- `HOME_WEB_DEN_LAB_CA_FILE`;
- `HOME_WEB_DEN_LAB_READ_TOKEN_FILE`.

Use explicit absolute paths and bounded safe reads, no credential defaults or
fallback to the record/admin token. Provision a Victoria upstream grant with
only `read`; verify the upstream site's capabilities before activation. Do not
write these values into renderer assets, bundles, logs or tracked files.
Missing or invalid configuration leaves the route unavailable. Keep TLS
certificate verification on. The adapter accepts no caller-provided origin,
credential, redirect destination, forwarding header or general proxy path.

## Supported reads and limits

All requests require the current Home session, exact
`X-Home-Lab-Site: victoria`, GET and no request body. Echo and absent site
selection refuse. The adapter forwards only its own read grant, Victoria site,
accept/identity encoding headers and a validated media Range.

The exact allowlist covers capabilities, activity-loop status, sessions list,
Den preview/video/context/structured-context, session detail/live/graph/events,
saved-session findings/timeline/replay, and bounded media
manifest/descriptor/range reads. Each query has an explicit
key set, range and duplicate check. Media generation zero is valid. No stop,
resume, cadence, recording, model switch, job, export, refresh or write route
is accepted. Out-of-scope Lab URLs receive JSON refusal instead of Home HTML.

Timeline accepts optional ordered `from_ms`/`to_ms`; replay requires `at_ms`.
All three cursors are integers from zero through 1,800,000 milliseconds;
findings accepts no query. These are existing read-only ledger projections,
not model execution. Findings, timeline and replay share the 1 MiB graph limit.

Four requests maximum run concurrently, with a five-second absolute deadline
and no retries. JSON is capped at 4 MiB, graph/events at 1 MiB, and binary range
reads at 4 MiB. Upstream media must return a matching 206 range and an allowed
binary content type. JSON must parse to an object. Redirects, compressed/HTML,
oversized or interrupted responses refuse; no partial body is returned as
success. Responses are private/no-store with no cookie/CORS/header tunnel.

Every adapter response carries the constant `X-Home-Lab-Access: read-only`.
This describes the browser gateway's effective access; upstream
`recording_allowed` still describes runtime readiness. The browser transport
and UI must keep those separate, disable recording/model/destructive controls
for this adapter and show the read-only reason. Native/full-authority paths do
not inherit this browser header. Never infer browser write permission merely
from the service's available capabilities.

Request abort, response close and timeout destroy the upstream read and release
its reservation. Home authentication is rechecked before private bytes return.
Upstream credential failures become unavailable rather than an instruction to
log the owner out. Other bounded Lab refusal JSON remains visible to the UI.

## Integration verification

`node --test tools/test-web-gateway-perception-lab.mjs` uses authored loopback
fake endpoints; it does not establish real TLS trust or live-host acceptance.
The UI mount must additionally prove the actual gateway process rejects
unauthenticated/wrong-site/agent-origin requests, cannot mutate Lab, uses the
private pinned upstream and returns real Victoria observations. Add the test
to touched-surface CI selection when wiring the module. Include resource use
under simultaneous video/body/model reads in the new deployment's soak.

Provisioning a read grant, transporting CA/token bytes, service environment
changes and gateway/Lab restarts require the exact reviewed owner approval and
rollback from this goal's deployment plan. Source preparation is not approval.
