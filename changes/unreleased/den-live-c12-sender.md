---
title: The den spotlight's FC13 sender and a fake Home Assistant, offline
target: internal
type: added
---

den-live gains `c12_send.py`, the sender that will carry the preview's fsynced fixture trace to the den home's Home Assistant `c12` services (schema `c12-service-1`), and `fake_c12_ha.py`, a fake of that Home Assistant whose `c12` integration owns the wire `seq` and drives either the firmware's v2-a host simulator or a model of the P3 board.
- **Stop first:** Stop and Resume ride their own lane, so a move waiting for its MOVE_ACK never holds a Stop back, and a Stop cancels its fixture's unsent work.
- **Never stale:** den-live's trace `seq` never goes on the air, so a den-live restart, an integration restart (which reads the board's `last_seq`) or a board reboot is never refused as stale.
- **Safe drops:** a move past its `valid_ms` is never sent, and a keep-lit renews only the latest accepted lit move, by its wire `seq`.
- **Releases and the Stop copy:** a refused move, an expired one, or two failed keep-lits in a row ask the preview to release the row. A Stop den-live didn't send can become Home's Stop.
- **One connection:** `ha_link.HaSession` keeps one HTTPS connection per lane, never follows a redirect and never retries a POST.

Nothing builds a sender in the service yet: den-live stays virtual until the desk switch maps a fixture.
