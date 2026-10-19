---
title: Home opens without compiling its code in the browser
target: web
type: changed
---

The web app and the desktop app now load JSX compiled ahead of time instead of compiling about 3 MB of it with Babel on every launch. On the workstation the Apartment view's first Cloud frame moves from about 8 s to about 1 s, and from over a minute to a few seconds at 4× CPU slowdown. Deploys of the web gateway and desktop installer builds run `node tools/build-app-jsx.mjs`; any file that is not precompiled, or has changed since, still compiles in the browser as before.
