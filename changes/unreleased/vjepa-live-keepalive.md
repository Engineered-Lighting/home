---
title: Victoria's V-JEPA clips reuse one connection and accept a faster cadence
target: backend
type: changed
---

The Lab controller's V-JEPA clip transport now keeps one HTTPS connection to the LA supervisor with the same reconnect rules as the Qwen transport, and a kept socket the supervisor had already closed is a typed skip that is not retried and does not count toward the three-failure backoff; a `thermal_shed` refusal is a typed skip. The pump accepts an interval down to 1 s and up to 60 clips a minute (defaults unchanged at 2 s and 40), and the lease polls worker readiness every 0.25 s, still requested only on a tick that found the room occupied, which with the occupancy reader is the tick an occupancy edge wakes.
