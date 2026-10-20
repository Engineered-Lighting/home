---
title: Den lighting brain follows how close you are to each light
target: backend
type: changed
---

The den lighting brain no longer needs Frigate desk/sofa zones. Whole-room occupancy turns the lights on and off, and each fixture's ambient now rises as a person walks closer to it (from 10% far away to 60% close by, dimmer and warmer at night), with a flat 30% when no position is available. The sofa reading spotlight now works from the person's position near the marked sofa seat. State updates are smoothed and limited to four per second.
