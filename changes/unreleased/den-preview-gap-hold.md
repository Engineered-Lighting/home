---
title: A lit den spot no longer blinks on a short camera gap
target: deploy
type: fixed
---

The den spotlight preview (`tools/den-live/preview.py`) keeps a lit spot lit through camera gaps. LA's first harvest found the camera's newest frame going 1.5–2.4 s without changing about 5 times a minute, and each gap darkened the spot. On a real fixture that's a dark blink.
- **Staying lit** now needs the person's newest fresh frame to be at most 3.0 s old: the same 3.0 s at which a camera epoch ends. Keep-lit goes on through the gap, and the snapshot's validity never passes the hold.
- **Lighting** still needs a pose at most 1.5 s old. The hold keeps a lit spot; it never lights one.
- **These still darken at once:** Off, Stop, an expired answer, the phone patience, a person-binding change, and standing up, leaving or a second person seen on a fresh frame.
- A snapshot with no new frame, or with a frame past the 1.5 s pose expiry, no longer darkens by itself.
- The Decision's proposal says `gap_hold: true` while a spot is held.
- A replay of the harvest's 25 gaps in 5 minutes gives no blinks (25 before).
