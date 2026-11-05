# den-n2: the N2 need test's scorer

N2 asks one question offline: do the den camera's pixels show what light one
person needs? It scores need v1's output on N1's frozen blind labels and gives
a verdict, `feasible`, `not_feasible` or `insufficient`. Nothing is promoted or
changed in the loop. The plan is `docs/plans/n2-need-test/PLAN.md`; this folder
is its step 1 (section 9).

| File | Does |
|---|---|
| `score.py` | Scores need logs against N1's labels: every metric with its interval, the sample gates, the cases and the verdict. |
| `GATE-V1.md` | The gate: fixed values and empty slots, in one fenced `gate-v1` block that `score.py` reads. |
| `RESULTS.md` | One line per held-out run, appended by an agent. |
| `test_den_n2.py` | Tests on synthetic N1-format labels and need logs only. |

Python 3.12 and the standard library only. `score.py` uses no GPU, camera or
network. It reads N1's folders through den-label's `n1.py`, and runs den-core's
`check.py` on every replay log before reading it.

Nothing here can run on real data yet. It needs N1's two freeze lines in
`tools/den-label/FREEZES.md`, need v1 and den-core's `n2_asks` adapter (step 2)
and the ask step (step 3).

## Privacy rules

- Everything N2 writes stays in `C:\den-takes\n2\`, outside git and OneDrive.
  `score.py` refuses an N2 folder inside a git checkout or OneDrive.
- The report it writes holds onset times; it stays on the PC. What it prints
  holds counts, rates, delays and hashes only, never a label time, so you can
  paste it to an agent.
- Development mode opens only development sessions' files. It never opens a
  held-out label, box file, ask file or log. The held-out set opens once, in
  the scoring run.

## Folders

```text
C:\den-takes\n1\labels-vN\, fixture-vN\           N1's frozen folders (read only)
C:\den-takes\n2\asks\<take>.jsonl                 the ask step's lines, one per slot (step 3)
C:\den-takes\n2\replay\<take>\seg-<k>\log.jsonl   den-core run.py --out, one per usable segment (step 2)
C:\den-takes\n2\eval\<split>-<UTC time>.json      score.py's report
```

`<k>` counts usable segments from 1, in the fixture's order.

**Ask lines** (one JSON object per line). `score.py` reads `slot`, `segment`,
`r` (s on the take's `t_s`), `frames` (oldest first, each with `t_s`),
`outcome` (`replied`, `no_box` or `failed`), `S` (s, or null), `attempts`,
`receipt` (the qwen-observation-1 receipt, or null) and **`asked_unix`**: the
wall-clock time the slot was asked. A line asked before the labels freeze
refuses the run.

**Need logs** are den-core `den-replay-log-1` files with a `need` policy. For
each `person_state` record `score.py` reads its time `t`, its
`focused_engagement` `state` and `confidence`, and its `visual_demand` `level`
and `confidence`. A record with a level must also carry
`field_support.visual_demand.last_support_capture_ts`: the level is fresh
while `t` is under that time plus 10 s. Times map to the take's clock as
`t_s = t - floor(started_unix)`, the adapter's rule.

## Running it (PowerShell 5.1)

As with den-label, use a git worktree:

```powershell
git -C C:\claude\home fetch origin claude/den-n2
```

```powershell
git -C C:\claude\home worktree add C:\claude\home\.claude\worktrees\den-n2 claude/den-n2
```

```powershell
Set-Location C:\claude\home\.claude\worktrees\den-n2
```

`--den-core` is the den-core folder that made the replay logs, so its
`check.py` replays them with the same code. Replace the example path with
yours:

```powershell
python -B tools\den-n2\score.py --split development --den-core C:\claude\home\.claude\worktrees\den-core\tools\den-core
```

Defaults: `--takes C:\den-takes`, `--n1 <takes>\n1`, `--n2 <takes>\n2`, and
the newest labels and fixture versions logged in FREEZES.md
(`--labels-version`, `--fixture-version`). If `python` is not found, use
`py -3.12`.

Exit codes: 0 = done, 2 = refused (nothing written).

## The order (plan section 7)

1. **N1's freeze lines** in `tools/den-label/FREEZES.md`. Until then
   `score.py` refuses.
2. **Development.** After the asks and replays of the development sessions,
   run `--split development`. It prints every metric with its interval, the
   sample gates, the cases, S, D_dev with w and d_on per onset, the onset rule
   and the largest pacing that fits, the case x row table and whether the
   prompt-2 trigger fires. It gives no verdict.
3. **GATE-V1.md**: the development step fills its slots, and the owner fills
   `scope`, the floors and the caps. Commit and push it before the first
   held-out ask line.
4. **The held-out set, once:**

```powershell
python -B tools\den-n2\score.py --split held_out --den-core C:\claude\home\.claude\worktrees\den-core\tools\den-core
```

It prints the verdict, its reasons and one `den-n2 held_out ...` line. Paste
that line to an agent, who appends it to `RESULTS.md` and pushes.

## Refusals

`score.py` writes nothing and exits 2 when:

- a labels or fixture manifest matches no freeze line, a file differs from its
  manifest, or the labels were frozen under another `PROTOCOL-V1.md`;
- an ask line was asked before the labels freeze, an ask file or replay log is
  missing, or den-core's `check.py` fails a log;
- `--split held_out` and a GATE-V1 slot is `null` (`detector` may stay `null`
  under `scope` `qwen_only`), or a stand-in differs from the gate's: the
  protocol, the pad, need's code or config, the tick, or a receipt's prompt
  version.

## Where the plan left a choice

- A second belongs to an interval or span when its sample point (k + 0.5)
  lies inside it. An uncertain span removes every second it touches.
- false_on counts quiet-span seconds only (the quiet criterion). False-fine
  counts every gold-none second outside the τ_off windows, quiet or not, plus
  the casual seconds past the grace.
- early_off and a unit's engagement label leave out [onset, onset + τ_on), for
  a missed interval too.
- A gated metric with no data counts as straddled (rule 4, insufficient).
- A unit with no confidence leaves that field's ECE. A unit is low-light when
  most of its seconds are; an onset is when its touch lies in a low_light span.
- A reply is `stale` when it arrives (r + S) at or after its latest frame plus
  10 s.
- The pacing prediction tries P from `pacing_s` down in 0.5 s steps.
- The per-second sample gates cover early_off, false_on, fine recall and
  false-fine.
- The gate's sha256 is taken over LF line ends, so a CRLF copy hashes the same.

## Tests

```powershell
python -B -m unittest discover -s tools/den-n2 -v
```

They build synthetic frozen N1 folders with `n1.py`'s own helpers, need logs
in den-core's format and a fake `check.py`. They take about 10 seconds and
touch nothing outside a temporary folder.
