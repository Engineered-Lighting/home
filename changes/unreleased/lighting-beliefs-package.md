---
title: Lighting belief publisher core package (offline scaffold)
target: backend
type: added
---

Adds `stack/services/lighting-publisher/`, the `lighting_beliefs` package that
will drive the Living Lights TV and sleep stories from Jev beliefs: the five
questions as reviewable data, probability-only reduction (booleans and
duplicate level keys rejected), the house-level packet builder that is
fail-closed on its own (strict allow-list with list-vs-mapping checks, every
key and value scanned against the leak patterns, ASCII-only text capped at
240 characters with format characters stripped, ages above a day rejected,
count caps on cameras, zones, media rows and people, name collisions after
capping refused, error messages that never echo a leaking value or key), a
leak guard that blocks entity ids in any case, addresses, bare LAN hostnames,
tokens, model names, usernames, digit runs and separated digit groups, ISO and
slashed dates, times of day, zero-width and non-ASCII characters and household
names by whole name and name part (leaking keys reported by placeholder), and
an egress gate that only permits a call when the signed record (regular file,
not group/other-writable, no symlink), the `TYPESAFE_EGRESS` flag and the
Home Assistant kill switch (finite, non-negative age strictly under 300 s) all
agree, at most six calls a minute with one in flight and a 120 s in-flight
expiry, every decision journaled. No network code ships yet; nothing is
deployed and no household data leaves the house. A draft ADR proposes amending
the "no other outbound traffic" statement in the architecture document.
