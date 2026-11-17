---
title: The den lighting coordinator's switch, reasons and settings in Home's den view
target: web
type: added
---

Home's live den view gets a "Lighting coordinator" section under its status line. It reads den-live's coordinator (`home-den-coordinator-panel.mjs`) and stays hidden when den-live runs none.
- **What it shows:**
  - on or off, and whether a fixture's Stop holds the spots;
  - the situation in words, for example "A close-up task, evening";
  - "Why": each spot's job, and each fixture it didn't use and why ("F1 not used: in front of you");
  - the bulb plan it last sent den_lighting;
  - what it has learned from your changes, per situation and light;
  - when Home Assistant stops answering it.
- **Signed in, it also has:**
  - **Turn on / Turn off**, the "Den lighting coordinator" switch;
  - **Start desk test (3 h)**;
  - **Clear** for what it learned, which asks once more first;
  - **Settings:** move style, dim level, fades, the shift distance and hold, phone patience, pre-aim, artwork level and following people walking. Save sends only what changed.
- **Look-only** shows the same state with no button, and sends nothing but its GET.
- **The gateway** (`web-gateway/den-live-route.mjs`):
  - `GET /api/den-live/v1/homes/den/coordinator` is a den-live read, also on the look-only list.
  - Four writes pass for a signed-in session only: `POST .../coordinator/{switch,settings,learned/clear,desk}`. Each needs the `X-Home-Den-Live-CSRF: 1` header, JSON, and a Content-Length of at most 64 KB. den-live gets the body's headers and no cookie, credential or CSRF header.
  - Every other write to den-live is still refused.
- Tests: `tools/test-den-coordinator-panel.mjs`, the gateway's look-only harness, and the den-live browser test against a fake coordinator.
