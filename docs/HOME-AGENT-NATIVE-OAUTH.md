# Home Agent native OAuth boundary

The Windows Home Agent surface authenticates directly with Home Assistant's
authorization-code flow through a pre-bound loopback callback. OAuth tokens
never enter webview JavaScript, localStorage, command return values, logs, or
BFF browser cookies. See the
[Home Assistant Authentication API](https://developers.home-assistant.io/docs/auth_api/)
for the upstream flow and native redirect metadata contract.

## Upstream limitation and acceptance boundary

Home Assistant Core 2026.7.1 does **not** enforce PKCE: its token view ignores
`code_challenge`/`code_verifier`, and the official Authentication API does not
specify them. This client intentionally omits those parameters rather than
presenting an ignored verifier as a security control. HA also provides no
client secret, so this installed application is not an OAuth confidential
client. Until HA adds and documents verifier enforcement, an authorization
code is a short-lived bearer credential until its first successful exchange.

The supported native boundary is consequently narrower: the HTTPS system
browser and local OS are trusted for the login ceremony; the redirect is an
exact registered loopback URI; Rust binds the fixed loopback listener before
opening the browser; a fresh 256-bit state is accepted once; callback Host,
path, query shape, state, and size are checked; and the code is exchanged
immediately without crossing the webview. A hostile browser extension or
local process able to read the callback URL remains outside this threat model.
Do not enable native login on an untrusted Windows account or browser profile.
Re-review this boundary before every HA major upgrade and migrate to enforced
PKCE when HA supplies it.

## Required Windows configuration

Set these non-secret values for the packaged `Home` process:

```text
HOME_NATIVE_HA_URL=https://homeassistant.example.internal
HOME_NATIVE_AGENT_BFF_URL=https://native-agent.home.example.internal
HOME_NATIVE_OAUTH_CLIENT_ID=https://native-agent.home.example.internal/native-oauth-client
HOME_NATIVE_OAUTH_REDIRECT_URI=http://127.0.0.1:43821/oauth/callback
```

All four values are mandatory. HA, gateway, and client-ID URLs must be HTTPS;
the callback is locked to the exact loopback host, port, and path above. The
client-ID URL cannot use a non-default port. Missing, malformed, non-HTTPS, or
off-Windows configuration disables login and all native semantic commands.
The Rust TLS client uses the Windows native certificate roots, so private CAs
must be installed in the appropriate Windows trust store; certificate bypasses
are not supported.

`HOME_NATIVE_AGENT_BFF_URL` is the private HTTPS web-gateway origin. Do not use
the loopback `http://127.0.0.1:8097` deployment BFF: the native client rejects
HTTP, and the BFF is intentionally not exposed from the Ubuntu host.

The Ubuntu BFF deployment must independently set
`HOME_AGENT_NATIVE_PUBLIC_ORIGIN` to the exact
`HOME_NATIVE_AGENT_BFF_URL` value above. The dedicated web gateway's
`HOME_WEB_NATIVE_AGENT_ORIGIN` value must match it too. This native proof
audience is not `HOME_WEB_AGENT_ORIGINS` or the browser
`HOME_AGENT_OAUTH_CLIENT_ID`; using a browser origin causes the challenge to
fail closed before Core. Production preflight requires the canonical HTTPS
native origin and rejects equality with the browser OAuth origin.
It also rejects membership anywhere in the browser allowed-origins set.

The separately configured native web-gateway origin serves
`GET /native-oauth-client` without Basic authentication so HA can inspect it.
The browser Agent origin deliberately returns 404 for this path. The metadata
page is a fixed, deny-all-CSP document under 10 KiB and contains exactly:

```html
<link rel="redirect_uri" href="http://127.0.0.1:43821/oauth/callback">
```

Before opening a browser, the native client independently fetches the first
10 KiB of that page and requires the exact link. It then binds the loopback
listener, creates random one-time state, and opens HA in the system browser.
Callback state comparison is length-sensitive and constant-time; the listener
accepts only the exact Host, path, and single `state`/`code` query pair in a
bounded HTTP/1.1 request on loopback, closes after the first valid callback,
and expires after five minutes. The response forbids referrer propagation,
framing, subresources, and caching.

## Credential lifecycle

- The refresh token is stored only as a Windows Generic Credential under a
  target derived from the HA origin and OAuth client ID.
- Access tokens exist only in `Zeroizing<String>` Rust memory and expire from
  the cache before HA's deadline.
- Login refuses to overwrite an active refresh credential. Sign out first so
  the existing HA authority can be revoked rather than orphaned.
- Invalid refresh responses (`400`, `401`, or `403`) clear the active
  credential and access cache and require a new login.
- Logout clears access memory first, copies the refresh token into a durable
  `revocation-pending` Credential Manager target, deletes the active target,
  and posts HA's official `token=<refresh>&action=revoke` request.
- A network/server failure leaves only the fail-closed pending credential.
  Login and semantic calls remain disabled; startup and the UI retry bounded
  revocation until HA acknowledges it.

Never manually delete a revocation-pending credential to make login work.
Restore HA connectivity and retry sign-out, or revoke that refresh-token
authority from HA before removing the local pending record.

## Native installation enrollment

OAuth user authority and native installation authority are independent. On
first startup, Rust creates one random installation UUID and P-256 signing key
under the Windows Credential Manager target
`EngineeredLighting.HomeAgent/installation-attestation/v1`. The private key is
never returned. `native_auth_status` returns only `installation_id` and its
public `public_jwk`, in addition to the existing categorical auth status, so a
trusted local operator can perform offline enrollment. It does not return an
HA token, private key, proof, nonce, or registry state.

The native-only Agent surface renders those two fields in a selectable
**Public installation enrollment material** card. It provides no clipboard API
or enrollment action and never appears in a browser session. Independently
obtain the exact HA user UUID from an authenticated HA `whoami` result; do not
infer it from a display name or accept a client-supplied actor value. Transfer
only the installation UUID, public JWK, and reviewed HA user UUID to the Ubuntu
operator. There is intentionally no online enrollment endpoint, and seeing the
card does not mean the installation is enrolled.

The authoritative schema-v1 registry is
`$HOME_AGENT_SECRETS_DIR/master/native_installations.json` on the encrypted
Home Agent volume. It is root-owned mode `0600`; the BFF receives a separate
read-only materialized copy. Fresh `bootstrap-secrets.sh` runs create the empty
registry; existing deployments that predate attestation initialize it once
with `manage_native_installations.py`. Enroll and revoke only with that same
tool, using bounded JSON on stdin. The exact operator procedure is in
`docs/HOME-AGENT-RUNBOOK.md`.
An empty or missing enrollment keeps native semantic routes fail-closed without
disabling browser OAuth.

The registry binds one installation UUID and public key to one exact HA user
UUID. Existing bindings cannot be replaced or reactivated. After credential
loss, reinstall, account reassignment, or key change, revoke the old UUID and
enroll the newly generated installation. Registry changes are not hot-reloaded:
rematerialize the BFF copy and force-recreate the BFF container after every
change. A plain restart retains the old bind-mounted inode and is insufficient.

For a corrupt or lost Windows installation credential, revoke the old registry
binding first. Then explicitly delete the Windows Credential Manager target
`EngineeredLighting.HomeAgent/installation-attestation/v1`, restart the signed
app so it generates a new UUID/key, and enroll the new public JWK offline. There
is no automatic key rotation, re-enrollment, or reactivation path.

## Window and network boundary

`/home-agent/` loads in a dedicated hidden-until-open `agent` WebviewWindow.
Its capability grants only Tauri event listen/unlisten, and its page uses a
local-only script CSP with no runtime Babel, inline script/style, frames, or
network wildcard. Every native auth or semantic Rust command independently
checks the unforgeable invoking window label. The legacy `main` window may
only ask Rust to show the Agent window; it cannot log in, read auth state,
mutate memory, navigate/evaluate the Agent document, or make generic native
HTTP requests.

The native Rust client compiles only typed methods for snapshot, the two
consent preferences, governed place-memory proposal/read/confirm, descriptor
correction/retraction previews and confirms, and scoped-forgetting preview and
confirm. It cannot choose a URL, method, header, principal, or confirmation
artifact ID. Place creation, parent confirmation, initiatives, cameras,
models, stack administration, and physical HA actions are absent.

Parent confirmation is also absent from the browser BFF. Explicit parent facts
require a future server-staged exact candidate set, a private digest-bound
preview, and one atomic authenticated commit; a client-supplied person UUID is
never accepted as relationship authority.

At the dedicated HTTPS Agent origin there is no legacy gateway Basic-auth or
legacy UI/proxy surface. Exact native typed paths use the HA Bearer in
`Authorization`; the BFF immediately validates it with HA `whoami`, requires a
single-use challenge-bound ES256 proof from the enrolled installation, and
forwards a trusted HA UUID plus a server-constructed attested channel using its
server-held Core credential. Native ingress is an explicit header allowlist: cookies,
Basic credentials, client actor/principal/forwarding headers, and client
request IDs are discarded. Native responses cannot set cookies or redirect;
an upstream 3xx is converted to a contained 502, and the Rust HTTP client also
has redirect following disabled. Non-native routes still require normal
Agent-origin routing. Browser Agent session routes exist only on the exact
dedicated host, while native typed bearer routes remain available during host
migration without acquiring browser cookie semantics.

The challenge and signed claims include an exact canonical `htu` built by Rust
from its configured HTTPS BFF origin plus the typed path. The BFF independently
requires that value to match its configured public origin and the actual
request. Rust never signs an audience supplied by a challenge response, which
prevents a staging or malicious BFF from relaying a production nonce/proof.

Initiatives remain disabled. Installation attestation narrows the remaining
native typed calls; it does not authorize a new initiative capability or its
presentation policy. Do not add initiative read/claim routes until a separate
initiative-capability and rollout review explicitly admits them.

The deployed browser/native client mirrors that boundary: `api.js`, the Agent
panel source, and its generated bundle contain no initiative list, claim, or
presentation path. Core may retain isolated initiative domain logic and tests
as future design evidence; that does not expose the capability to a client.

## Verification

Run from the repository root:

```text
npm run test:native-agent-security
node --test stack/services/home-agent-bff/test/bff.test.mjs
cargo test --manifest-path app/src-tauri/Cargo.toml
```

The final command requires a Windows Rust toolchain and compatible native
linker/build tools. Before live acceptance, also verify external-browser login,
restart refresh, invalid-refresh cleanup, HA-offline logout persistence/retry,
main-window command denial, the exact public metadata document against the
registered private HTTPS origin, and that the deployed HA version still has
the limitation documented above. Native activation remains a canary gate, not
a prerequisite for record-only ingest or the browser BFF.
