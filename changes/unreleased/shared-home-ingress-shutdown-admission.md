---
title: Close admission before retiring inference listeners
target: backend
type: fixed
---

Prevent an already-authenticated, delayed request from entering inference after
its private TLS listener closes. Add bounded handler draining so listener
replacement can wait for old requests without discarding execution reservations.
