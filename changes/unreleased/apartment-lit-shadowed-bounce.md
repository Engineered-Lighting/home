---
title: Shadowed bounce light and modeled lux in the Apartment Lit view
target: web
type: changed
---

In Lit, bounced light is now ray traced against the scan, so it no longer leaks through walls. This replaces the eight-patch bounce and its "can leak up to 4.5 m" note. Aiming a spot updates its bounce once the Set sweep lands, and spot shadows are sharper. Named aims show modeled lux; nothing in Lit is measured.
