---
title: One overlay manager — predictable Escape and real focus management
target: web
type: fixed
---

Every drawer, dialog, overlay, and lightbox now registers with a single
overlay layer stack (window.HomeOverlay). Escape always closes exactly the
topmost layer — previously stacked surfaces double-closed (one press in the
avatar editor tore down the whole People overlay; Escape mid-zone-draw also
exited edit mode) and pressing Escape behind any overlay silently cancelled
pending action cards. Escape now works with focus in the chat composer
("close · esc" labels were lying before), the Remote-profile and
feature-loading dialogs gain Escape, keyboard focus moves into every dialog
on open and returns to the opener on close, Tab is trapped inside true
modals, and aria-modal only appears where a focus trap actually exists.
The /apartment command can no longer stack two full-screen takeovers, and
the four right-edge drawers are properly exclusive.
