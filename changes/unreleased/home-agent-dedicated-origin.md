---
title: Isolate the Home Agent web and OAuth origin
target: backend
type: added
---

Adds an internal-only, tailnet-fronted Home Agent origin as a separate service
and deployment lifecycle. It has a reviewed static address on an IPv4-only
Docker network, no Docker host port or external route, serves only the built
private Agent surface and explicit browser BFF routes, strips ambient authority
headers and unrelated cookies, rejects legacy/native proxy paths, and never
logs OAuth callback query strings. The browser origin deliberately omits native
OAuth metadata, and live startup requires an externally built, reviewed image
with Compose build and pull disabled on the quarantined Ubuntu host. The only
deployable web images come from the main-only hosted job in
`.github/workflows/home-agent-web-boundary.yml`: operators download the exact
source-commit artifact, verify all four signed subjects with repository,
workflow, branch, commit, and non-self-hosted-runner constraints, verify
`SHA256SUMS`, and validate the exact manifest and top-level image IDs. Ubuntu
archives the current images, imports the verified tarballs, compares both IDs,
and only then moves `:local`; `HOME_AGENT_BFF_IMAGE_ID` and
`HOME_AGENT_WEB_IMAGE_ID`, not mutable tags, pin the approved BFF and origin
content. The native host now denies every non-native HTTP route and every
websocket upgrade before any legacy proxy path.
