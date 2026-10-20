---
title: Keep the Lab recorder's RTSP reader credential out of FFmpeg's command line
target: backend
type: fixed
---

The Perception Lab recorder now gives FFmpeg the RTSP reader URL on stdin, as a
one-entry concat list, instead of in argv, so local users can no longer read the
reader credential with `ps` or `/proc/<pid>/cmdline`. Recording is still a TCP
copy-remux to fragmented-MP4 HLS. The display-only RTSP preview and live video
still pass the URL in argv. Rotate the reader password once this is deployed.
