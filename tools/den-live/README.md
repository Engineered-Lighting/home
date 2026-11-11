# den-live

Tools for the live den view: the den camera, live, to markers and a virtual reading spotlight in Home. Only step 1 is here so far: the sofa check.

## Step 1: the sofa check

Does the re-aimed den camera see a reader on the sofa? One paste and about three minutes of sitting answer it.

The check needs these, from earlier work on this PC:
- the detector's venv at `%USERPROFILE%\ml\den-detector`;
- the camera token in `C:\den-takes\private\camera-token.txt`;
- `ssh hav-ubuntu` working from your PowerShell, as the den simulation's Qwen paste needs.

### Once: the camera token

If `C:\den-takes\private\camera-token.txt` doesn't exist yet, follow `tools/den-take/README.md`, step 2. That's on `claude/rung-r` and in the pinned den-take tree under `%USERPROFILE%\den-live\tools\observe-*`. Its three lines save the token and print only its length, which should be 24 or more. Never open the file or paste the token anywhere.

### The check

With the room lit and nobody else in the den, paste:

```powershell
python -B C:\claude\home\.claude\worktrees\den-live\tools\den-live\sofa_check.py
```

It speaks the cues:
1. A 15 s countdown. Be on the sofa with your book and your phone.
2. Read, with the book at chest height, for about 30 s.
3. Your phone instead, for about 30 s.
4. Leave the den, or step out of the camera's view, until it says "done".

Then it looks at the frames for a minute or two, and asks Qwen on LA six times. Its preflight runs first, and no image is sent unless that passes. It prints one block ending in a verdict.

| Verdict | Means |
|---|---|
| SOFA SEEN | The detector sees you reading on 80% or more of the frames, with face, shoulders and torso depth. Qwen calls 2 or more of 3 reading requests reading. The spotlight part goes ahead |
| PERSON ONLY | The camera sees you, but Qwen doesn't see you reading. By your rule the spotlight part stops, and the markers go on |
| NOT SEEN | The detector part fails |
| CHECK AGAIN | A person showed while the sofa was meant to be empty. Paste again, out of view |
| DETECTOR ONLY | Qwen couldn't be asked, for example vLLM is down. The reason is printed |

"Seated" isn't judged here. It needs the floor, which the camera registration in step 2 gives.

### What's kept

Everything stays in `%USERPROFILE%\den-live\private\sofa-check-<time>\`. That's about 250 MB of raw, unblurred frames, from the three windows only. Alongside them are the six requests and Qwen's replies, the counts and `verdict.json`.

Agents never open that folder. Nothing goes into git, OneDrive or the cloud. The stills sent to Qwen cross to LA, as the Lab's own Qwen calls do. The LA folder is removed after the ask, though vLLM's image cache may hold them for a while.

### Tests

```powershell
python -B -X utf8 tools\den-live\test_sofa_check.py
```

- No camera, LA or ssh. A fake camera serves the den simulation's rendered takes, and the ask runs against the simulation's fake vLLM.
- The end-to-end part needs the detector's venv and a prepared den-sim set, either `DEN_SIM_SET` or the default set. It takes about 2 to 3 minutes.
