---
title: Setup and Lights / Travel Mode move into the Home menu
target: web
type: changed
---

The Home top bar now holds only Menu. Setup is the first item in the Home
menu and shows as on while setup is open; choosing it again leaves setup, the
same as Done. A read-only home still opens Setup read-only with its reason,
and a home where Setup can't open says why in the menu. Lights / Travel Mode
stays in the menu, listed once. Opening the menu puts focus on its first item,
so the arrow keys walk the menu straight away.

Fixed: while the Home menu was open, a faint line crossed the top bar and a
click below the bar didn't close the menu. The menu's click-away layer had
picked up the top-bar button style; it now covers the window with no border.
