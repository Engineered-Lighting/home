---
title: The den camera vouches for each frame's capture age, so den-live's live figure can use it
target: backend
type: added
---

A new den camera release, `pilot-20261114-body`, is the slim release plus the
measured figure's capture-age receipt. With a commissioned clock profile, each
slim and full bundle answer carries `X-Den-Body-Capture-Age`: upper bounds on
how old that exact frame's colour and depth exposures are, and how far apart.
den-live's live figure needs the receipt and shows nothing without it; earlier,
only a release without the slim path had it. Without a profile the release
answers exactly as the slim release.
