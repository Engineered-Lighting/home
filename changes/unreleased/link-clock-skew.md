---
title: Account linking works when a browser clock runs slightly behind the server
target: web
type: fixed
---

Starting account linking, creating a Victoria connection code and reviewing
preference sharing rejected valid server responses whenever the browser's
clock was even a fraction of a second behind the server's, showing "did not
return a verified result". These checks now allow up to 30 seconds of clock
difference; the server still enforces every expiry.
