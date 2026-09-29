---
title: Keep the Home Agent sign-in for up to 400 days
target: backend
type: changed
---

The Home Agent BFF now accepts session lifetimes of up to 400 days
(`HOME_AGENT_SESSION_IDLE_MS`, `HOME_AGENT_SESSION_ABSOLUTE_MS`, passed through
Compose), and the session cookie lasts exactly as long as the configured
session instead of a fixed 12 hours. Defaults are unchanged. The inline
preference card now says "Your Home Agent sign-in has expired" when the Agent
answers 401, instead of reporting preferences as unavailable.
