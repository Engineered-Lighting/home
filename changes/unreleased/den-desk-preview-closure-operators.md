---
title: Keep the desk-preview metric frames and refuse a mismatched next test before arming
target: internal
type: added
---

The den viewing watchdog can now fetch up to six of the metric images that the live view announces as soon as they are registered, so the next desk-preview test keeps the frames needed as proof. Before this change, all ten arm-B images in the last trial had expired before the watchdog could fetch them. These images count against the existing 24-image, 26-file and 32 MiB limits, and they use at most 6 MiB in total. The fetch is off unless an operator turns it on. A new reusable operator set for the next bounded test reads its release, clock, namespace and pins from one closure file. Before arming, it refuses on a changed camera session or calibration, on an equipment registration that differs from the saved desk's, on a missing development timing flag, and on any clock other than the 1 s config. It also includes backend and frontend rollback. Nothing was deployed or run on a host.
