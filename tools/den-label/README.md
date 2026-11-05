# den-label: the N1 labeller

den-label turns retained staged den takes from rung R (`tools/den-take`) into
N1's two artifacts: the **input fixture** (per-frame person boxes and a surface
map) and the **labels** (engagement intervals, demand bands, quiet and shortcut
cases). You make both by hand, blind, under [PROTOCOL-V1.md](PROTOCOL-V1.md).
The plan is `docs/plans/n1-reference/PLAN.md`.

| File | Runs on | Does |
|---|---|---|
| `label.py` | your PC | The one command. Serves the page on 127.0.0.1 only. |
| `n1.py` | your PC (imported) | Formats, the split rule, boxes, label checks, re-mark agreement, freezing. |
| `page.html` | your browser | Surfaces, boxes (with the audit) and labels. Keyboard-driven, autosaves. |
| `PROTOCOL-V1.md` | repo | The protocol, with its few settings in one block. |
| `FREEZES.md` | repo | The freeze lines, appended by an agent. |
| `test_den_label.py`, `test-page-browser.mjs` | PC or cloud | Tests on synthetic takes only. |

It needs Python 3.12 and the standard library only, plus a browser. It uses no
GPU, no detector, no network and no camera.

## Privacy rules

- Everything N1 makes stays in `C:\den-takes\n1\`, outside git and OneDrive.
  `label.py` refuses an N1 folder inside a git checkout or OneDrive.
- The page is served on 127.0.0.1 with a one-time token in its address. Frames
  never leave the PC. Nobody else can open the page.
- You draw only the tight box around the whole person. Never a face, a hand,
  an object or a work area.
- Agents never open frames or the page. Paste them only `label.py status`
  output and freeze lines: counts and hashes.

## Step 1, once: run from this branch

As with R, use a git worktree so your main checkout stays as it is. In
PowerShell 5.1:

```powershell
git -C C:\claude\home fetch origin claude/den-label
git -C C:\claude\home worktree add C:\claude\home\.claude\worktrees\den-label claude/den-label
Set-Location C:\claude\home\.claude\worktrees\den-label
```

Run every command below from that folder. If `python` is not found, use
`py -3.12`. Once the branch merges, run from a checkout of `main` instead.

## Step 2, at R's commissioning take: the 3 unscripted minutes

R's commissioning take (`take.py --script commission`, R's README step 4) has
3 minutes of free activity after "Begin.". Spend them like this, with nothing
spoken:

1. About 60 s at the desk with the **chair pushed back** from it: hold the
   mail **at chest height**, then pick the book up from the spot you mean to
   name as its place (never the desk top), read it at chest height, and put it
   back there.
2. About 60 s wiping the **counter** with a cloth (if you already know the
   counter is out of view, wipe the surface you would use instead).
3. About 60 s **standing at the counter using your phone**.

Then scan check.py's contact sheet (`review\index.html`) once. It answers every
in-view question: is the counter in view, is the wiping surface in view, are
the seated body and the book in view with the chair pushed back, and are the
book's pick-up and put-down at its place in view? Tell the agent only yes or
no for each, which spot to use if the counter is out of view, and the book's
place.

## Step 3, needs a go-ahead: okay the protocol (approval 1)

Read [PROTOCOL-V1.md](PROTOCOL-V1.md) (about 10 minutes) and okay it before
any staged take. Decide:

- the starting pad (20% and at least 16 px);
- **the phone band.** The default, `casual`, differs from the architecture: A13
  scores "on a phone at the counter" as no need, and A7's example gives a
  self-lit screen a visual demand of 0.2. If you pick `none`, phone use becomes
  a no-need quiet case, and each round's phone block becomes a tidying block,
  so casual still gets enough episodes;
- the spot for mail sorting, wiping and the phone (the counter if it is in
  view), and the book's place (an in-view surface within reach of the chair
  pushed back, never the desk top);
- the spoken cue text.

The agent then edits PROTOCOL-V1.md (only before the first label) and pushes.

## Step 4, no go-ahead: practice and the surface map

Practice once on the commissioning take (here `c1`). Nothing from it is ever
frozen:

```powershell
python -B tools\den-label\label.py boxes c1 --practice
python -B tools\den-label\label.py labels c1 --practice
```

Then draw the surface map once, on a clear commissioning frame (about 15 min).
Click the corners of each surface in view, give it an id, a type and its height
in cm measured with a tape, and press R on the frame you used:

```powershell
python -B tools\den-label\label.py surfaces c1
```

Each command prints the page's address and opens it. Leave the PowerShell
window open; the page ends the pass (Shift+Enter, or the End pass button).
Every change saves by itself. If the server refuses a save, the reason stays
in red at the top until a save is accepted, and End pass waits: fix the item
(or select it and press Del), or press Ctrl+C in the PowerShell window to stop
without it. End pass also names an item you have not finished (an onset
with no offset, band or case, or a span with no end) instead of dropping it.

## Step 5, after each staged take

Staged takes need take.py's `--script hard-a` and `--script hard-b`, which come
with approval 3 after R merges. After each one, run R's check (its README
steps 5 and 7), then:

```powershell
python -B tools\den-label\label.py sessions
python -B tools\den-label\label.py boxes <take>
python -B tools\den-label\label.py labels <take>
```

`sessions` registers new retained staged takes and prints each one's split.
That split never changes.

**Boxes (about 20 to 30 min).** Play at 2x or 4x. When the box stops fitting
your body, pause and drag it to fit (drag inside to move, near an edge to
resize, outside to draw a new one). That frame becomes a keyframe; straight
lines fill in between. Press X from the frame you leave the view. Never add a
keyframe because an activity starts. Then press A for the audit: play the
whole take at up to 4x with the padded (yellow) box. If your hands or the
thing you hold leave it, fix the keyframe there and replay from the keyframe
before. The status line shows "audited 100.0%" when done. If the pad is often
too small, raise it for every session (only up, only before the first
fixture freeze):

```powershell
python -B tools\den-label\label.py boxes --pad 25
```

**Labels (about 25 to 35 min).** The label page shows frames only, no boxes.
It opens only after the audit is complete, and from then on the session's
boxes are locked. At each engagement, step frame by frame to the first touch
and press O, press F or C for the band and a number for the case, then step to
the last contact and press P. Hold Shift with O or P for a no-touch end;
Caps Lock changes nothing. K changes a saved interval's nearest end between
touch (or release) and no touch. Mark quiet, low-light and uncertain spans with
Q, L and U at their start and end (a number sets a quiet span's case). Never
guess: N toggles "not sure" on the nearest end, B on the band, and an
uncertain span covers "is this engagement at all?". When done, press E to mark
the session labelled end to end.

| Key | Boxes | Labels |
|---|---|---|
| Space, Left/Right (Shift x10), Home/End, [ ] | play, step, speed | the same |
| G | brightness for dim frames | the same |
| X, Del, `,` `.`, A | not in view, remove keyframe, previous/next keyframe, audit | |
| O / Shift+O, P / Shift+P, K | | onset touch / no touch, offset release / no touch, change the nearest end's kind |
| F, C, 1-9, N, B | | fine, casual, case, toggle sure (end), toggle sure (band) |
| Q, L, U, Tab, Del, E | | quiet, low light, uncertain, select, delete, end to end |
| `!` | second person in view: ends the pass | the same |
| Shift+Enter | end the pass | the same |

## A second person R's check missed

If you see a second person in any pass, press `!`. The pass ends and the
window prints the spans typed in every earlier check of that take, for example
`Earlier spans: 00:40-00:45`, or `(no earlier spans)`. Re-run check.py on the
take and, at its prompt, type those spans plus the new ones (just the new ones
if there were none). label.py accepts the new check only if it covers every
earlier check's cuts; until then the session is blocked. Once it is accepted,
the next label.py command clips your drafts to the new cuts, prints each
change, and clears the end-to-end mark, so look again and press E again.

## Step 6: the re-mark, the freezes and a copy

At least 3 days after its label pass, re-mark the second development session
from scratch (label.py picks it; your first labels stay hidden):

```powershell
python -B tools\den-label\label.py remark
python -B tools\den-label\label.py status
```

When every session is audited and labelled end to end and the re-mark is done:

```powershell
python -B tools\den-label\label.py freeze fixture
python -B tools\den-label\label.py freeze labels
python -B tools\den-label\label.py verify
```

Each freeze prints one `den-n1 ...` line. Paste both to the agent, who appends
them to FREEZES.md and pushes. Only then may N2 run. A frozen folder is never
edited: any change, or sessions added later, makes `fixture-v2` and
`labels-v2`. After each freeze, copy `C:\den-takes\n1` to another household
computer or a USB drive.

Exit codes: 0 = done, 1 = `verify` found a problem, 2 = refused (nothing
written).

## Tests

```powershell
python -B -m unittest discover -s tools/den-label -v
```

They build synthetic takes with R's own helpers and take about 2 minutes. The
browser smoke test runs in the cloud with its preinstalled Chromium:
`PYTHON=python3.12 node tools/den-label/test-page-browser.mjs`.
