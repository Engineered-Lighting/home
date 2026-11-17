---
title: A live caption and a pipeline view in the den, behind flags
target: web
type: added
---

With `HOME_WEB_DEN_CAPTION` on, the live den view puts a caption beside each tracked person: who they are (a name
tag, or "Someone"), what Qwen last said they're doing, with a ring that empties over the answer's 10 seconds, what
they hold, and what the virtual spot is doing for them. An answer disappears when it expires. Fields den-live doesn't
send yet are read when present and ignored otherwise. The flag is off by default.

With `HOME_WEB_DEN_PIPELINE` on, a switch in the den's status capsule shows the live pipeline across the top of
the room: occupancy, the camera, perception, Qwen and the light, with live values, wired to where the data lands in
the room. The room shrinks to make space, and grows back when it's turned off. That flag is also off by default.
