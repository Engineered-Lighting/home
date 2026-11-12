---
title: Label the objects in the den on its scan, in Home
target: web
type: added
---

Home's den view shows the den's labels as named boxes on its scan: the sofa, the desk, each painting, the 3D printer and so on. Signed in, a Labels button opens a tool to add and fit them. den-live keeps the labels, the lighting coordinator reads them, and later a short line built from them goes to Qwen with each ask about someone seated.
- **Adding a label.** Click anything in the scan and type its name. The usual den names are offered as you type, and the kind (seat, table or desk, picture, machine and so on) is guessed from the name.
- **Its box.** A box of a typical size appears where you clicked. A desk clicked on its top takes its fitted size. A picture, screen, door or window clicked on a wall lies flush with that wall.
- **Fitting.** Corner, height, turn and move handles fit the box, and the panel lists, renames, retypes and deletes labels.
- **The first time,** a checklist of usual den things leads.
- **Saving.** Save sends the whole document; unsaved changes stay in the browser until then. While the tool is open, a click on the scan labels instead of selecting a light. Nothing in it sends a light command.
- **Gateway.** With `HOME_WEB_DEN_LIVE=1`, `/api/den-live/v1/homes/den/labels` is readable by signed-in Home and by look-only devices. `PUT` saves the `den-room-labels-1` document, signed in only, with the `x-home-den-labels: 1` header and a JSON body of at most 64 KB. No cookie or credential reaches den-live. The page shell names it as `window.HomeDenLabels`.
- **Older scans.** Labels placed on an older scan of the den are drawn amber with "Check these", and aren't used until saved again on the current scan.
- **Tests.** `app/src/home-den-labels-core.test.mjs`, and the labels cases in `tools/run-web-gateway-look-only-tests.mjs`.
