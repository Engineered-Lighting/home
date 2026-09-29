---
title: Core image includes the HTTP client the lighting service needs
target: backend
type: fixed
---

The private lighting listener failed at start because its HTTP client
(httpx) was only a test dependency. It is now a hash-pinned production
requirement, and a new test fails if any Core module imports a package the
production image does not install.
