---
title: Reconnect panels through the AI-box reboot window
target: web
type: changed
---

The People, World State, and Explain panels now load over a shared
retry-with-backoff helper: during an AI-box reboot they show a "Reconnecting…"
banner and keep retrying instead of failing immediately, and after retries are
exhausted they surface a persistent error with a "retry now" button.
