---
title: Home opens without compiling its code in the browser
target: web
type: changed
---

The web app and the desktop app now load JSX compiled ahead of time instead of compiling about 3 MB of it with Babel on every launch. On the workstation the Apartment view's first Cloud frame moved from about 8 s to about 0.5 s, and from about 73 s to about 1.5 s with a phone-sized window and 4× CPU slowdown. Web gateway deploys and desktop installer builds run `node tools/build-app-jsx.mjs`; a file that is not precompiled, or has changed since, still compiles in the browser as before.
