# Security

`home` is a self-hosted desktop client. It runs entirely on hardware
you own and talks only to a Home Assistant instance you control.

## Threat model

- **vLLM, Kokoro, Parakeet, and the metrics sidecar ship without
  authentication.** They're designed for private networks (LAN /
  Tailnet). Don't expose any of their ports to the public internet.
- **The Tauri webview stores your HA Long-Lived Access Token in
  `localStorage`.** That's protected by the OS user account that runs
  the app. If multiple people share a user profile on the same
  machine, they can read each other's tokens.
- **Conversation history is persisted in `localStorage` too.** It
  includes everything you've said to your home, in plaintext.

## Reporting a vulnerability

Please email **mrcloblima@gmail.com** with `[home security]` in the
subject. I'll respond within a few days. There's no bug bounty — this
is a personal project — but I'll credit you in the changelog if you
prefer.

## What I'm explicitly not promising

This is hobby-grade software running on hardware you control. There's
no SLA. There's no audit trail. There's no enterprise SSO. There's no
formal threat model. If your home automation needs production-grade
security, this isn't it.
