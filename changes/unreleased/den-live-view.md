---
title: Live people markers in the den's apartment view, and a look-only view for tailnet devices
target: web
type: added
---

Home's den apartment view can show a live marker for each person in the den, from den-live's people stream. Any device on the owner's tailnet can open that view without signing in, but can't change anything. Both are off until turned on in the gateway's env file.
- `HOME_WEB_DEN_LIVE=1` adds the gateway route `/api/den-live/v1/homes/den/{state,stream}` (`web-gateway/den-live-route.mjs`). It's reads only, proxied to den-live on this host's loopback, with no cookie or credential passed on.
- When the den's view is open, Home mounts the people layer on it and feeds it the stream. A small status line names the camera, how many people, and how far behind. A banner says when the stream stops, the camera is offline, or the stream is in another scan revision; the markers are drawn only on the scan the camera is registered to.
- `HOME_WEB_LOOK_ONLY=1` with `HOME_WEB_SERVE_PORT` (`web-gateway/look-only.mjs`): a device Tailscale Serve names at a tailnet address, with no session, may GET a fixed list and nothing else.
  - **The Serve listener:** look-only works only on that new listener, which takes loopback connections that start with a PROXY line. Serve forwards there with `--proxy-protocol=1`, so the line is always Serve's own. The ordinary port never gives look-only, and is unchanged.
  - **The list:** the app's code, the den's content pack, lighting layer and registration, den-live's state and stream, and the lighting brain's den state and stream.
  - **Redacted and refused:** the site registry is served with both homes' addresses and coordinates removed. The Home Agent, spatial-spike and test code isn't served at all. The packs index lists only the den's packs.
  - **Limits:** a request with a body is refused. Each device may hold two of den-live's streams, and look-only devices eight in all.
  - **Still behind sign-in:** every write, every other site's data, `/api/world/v1/config` and every WebSocket.
- In look-only, Home opens straight to the den's view, labelled "Den · live · look-only", with a "Sign in" link, and mounts no control that writes.
- `/auth/status` adds `lookOnly`, and the den's site when it's true.
- Tests: `tools/run-web-gateway-look-only-tests.mjs`, and a browser test of the look-only view against the real gateway behind a PROXY line.
