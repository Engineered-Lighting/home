---
title: Review shared preferences inline in the Home chat
target: web
type: changed
---

Remembering, correcting or forgetting a shared evening-lighting preference now
shows its review card inside the Home chat instead of a separate popup window.
One click on Confirm (or Forget) applies it, and the result ("Saved: … for both
homes", "Forgotten: …") appears as the chat reply; asking what you prefer
answers directly. The card is still served by the Agent origin. It can be
framed only by the Home origins listed in the new Origin setting
`HOME_AGENT_WEB_PREFERENCE_HOME_ORIGINS`, which is empty and fails closed by
default.
