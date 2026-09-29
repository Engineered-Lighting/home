---
title: Enrolled face photos load in the People tab again
target: backend
type: fixed
---

Frigate 0.17 serves enrolled face images as a generic binary download, so Home
Assistant's face-photo route refused them and the People tab showed its
enrolled reference photos as unavailable. The route now recognises a WEBP, JPEG
or PNG from the image's own bytes, only when they match the file's extension.
Anything else, including SVG and HTML, is still refused.
The route also stops loading its TLS settings on Home Assistant's event loop,
which Home Assistant had logged as a blocking call.
