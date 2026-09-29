---
title: Keep the Home Agent sign-in through Home Assistant restarts
target: backend
type: fixed
---

A Home Assistant restart, update or network blip no longer signs the owner out
of the Home Agent and revokes its token. The Agent now answers 503
"home_assistant_unavailable" and keeps the session; only a definite HA denial
(bad token, inactive or changed user) ends it. The HA re-check interval is now
configurable (`HOME_AGENT_PRINCIPAL_REVALIDATE_MS`, up to 15 minutes), and new
lighting consents last a year.
