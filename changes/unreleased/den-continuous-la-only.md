---
title: Run continuous Den activity inference on LA without a duplicate Jetson model
target: backend
type: fixed
---

Commissioned automatic apartment-viewing sessions now prepare only the LA worker that supplies the lighting coordinator. They skip unused Jetson inference and JEV calls and explicitly identify their execution scope in worker, activation and live records. Ordinary Lab comparisons still prepare both workers. Original evidence expiry, reader binding, worker leases, cleanup and monitor admission remain enforced.

Deployment manifests and soak configurations can explicitly select the `den-continuous-la-1` inventory, which requires the LA user-scoped detector and its artifact hashes instead of the unused Jetson detector. Legacy paired inventories remain strict; profile mismatches and unknown profiles are rejected. Unit availability, occupied exposure and resource bounds are unchanged.
