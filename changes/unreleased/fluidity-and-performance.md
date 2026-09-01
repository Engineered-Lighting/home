---
title: Smoother chat, honest sliders, and a fully offline UI
target: web
type: fixed
---

The chat feed no longer yanks you to the bottom while a reply streams — it
auto-follows only when you're already at the bottom, and mobile now lands on
the latest message at boot. Long conversations stay fast: the feed windows
to the last 120 turns with a "show earlier" control, turn blocks skip
re-rendering during streams, bulk commands emit one message instead of
hundreds, and saving is throttled. Sliders stop fighting your finger — the
device readback no longer snaps the thumb mid-drag, and aim sliders send one
smoothed command stream instead of one service call per pixel. The 3D
camera settles from exactly where you released it instead of jumping back.
Buttons finally press (a subtle scale on touch/click), and the UI typeface
is now bundled with the app — the last remote dependency is gone, so the
interface renders identically offline.
