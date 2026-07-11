# Home Agent native OAuth boundary

The Windows Home Agent surface authenticates directly with Home Assistant's
authorization-code flow with PKCE. OAuth tokens never enter webview JavaScript,
localStorage, command return values, logs, or BFF browser cookies. See the
[Home Assistant Authentication API](https://developers.home-assistant.io/docs/auth_api/)
for the upstream flow and native redirect metadata contract.

## Required Windows configuration

Set these non-secret values for the packaged `Home` process:

```text
HOME_NATIVE_HA_URL=https://homeassistant.example.internal
HOME_NATIVE_AGENT_BFF_URL=https://agent.home.example.internal
HOME_NATIVE_OAUTH_CLIENT_ID=https://agent.home.example.internal/native-oauth-client
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

The dedicated Agent origin serves `GET /native-oauth-client` without Basic authentication so
HA can inspect it. The page is a fixed, deny-all-CSP document under 10 KiB and
contains exactly:

```html
<link rel="redirect_uri" href="http://127.0.0.1:43821/oauth/callback">
```

Before opening a browser, the native client independently fetches the first
10 KiB of that page and requires the exact link. It then binds the loopback
listener, creates random state and a PKCE verifier, and opens HA in the system
browser. Callback state comparison is length-sensitive and constant-time; the
listener accepts a bounded HTTP/1.1 request on loopback only and expires after
five minutes.

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

At the dedicated HTTPS Agent origin there is no legacy gateway Basic-auth or
legacy UI/proxy surface. Exact native typed paths use the HA Bearer in
`Authorization`; the BFF immediately validates it with HA `whoami` and forwards
a trusted HA UUID using its server-held Core credential. Native ingress is an
explicit header allowlist: cookies,
Basic credentials, client actor/principal/forwarding headers, and client
request IDs are discarded. Native responses cannot set cookies or redirect;
an upstream 3xx is converted to a contained 502, and the Rust HTTP client also
has redirect following disabled. Non-native routes still require normal
Agent-origin routing. Browser Agent session routes exist only on the exact
dedicated host, while native typed bearer routes remain available during host
migration without acquiring browser cookie semantics.

Initiatives remain disabled. A valid HA user bearer identifies a principal;
it does not prove a particular reviewed application installation. Do not add
initiative read/claim routes until a separate per-install attestation design is
reviewed and implemented.

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
main-window command denial, and the exact public metadata document against the
registered private HTTPS origin.
