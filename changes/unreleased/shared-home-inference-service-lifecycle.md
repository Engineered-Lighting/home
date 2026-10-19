---
title: Coordinate private inference service startup and shutdown
target: backend
type: added
---

Join the private TLS listener and dispatch pump under one service owner, starting
with admission closed and reporting bounded shutdown progress without releasing
outstanding execution reservations. Production commissioning remains required.
