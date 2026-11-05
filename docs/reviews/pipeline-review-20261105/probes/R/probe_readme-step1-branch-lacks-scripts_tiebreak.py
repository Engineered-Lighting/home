#!/usr/bin/env python3
"""Tiebreak probe for finding readme-step1-branch-lacks-scripts (unit R).

What it checks
  On the staged-scripts branch, tools/den-take/README.md Step 1 names the branch the
  owner must put in a worktree and says to run every command from there; Step 8 then
  runs `take.py --script hard-a`. The probe:
    1. parses the Step 1 branch and the Step 8 command out of the README;
    2. takes read-only snapshots (git archive) of
         - the Step 1 branch at its remote-tracking ref (what `git worktree add` and a
           later `git pull` would give, since the remote ref is pinned),
         - origin/main (the README's "once the branch merges, use main" path),
         - the README's own branch HEAD (control);
    3. runs Step 8's command, translated for Linux, in each snapshot with --out in a
       temp dir, --camera http://127.0.0.1:9 and a token file that does not exist, and
       proxy variables removed. Nothing reaches a real host; the camera client is
       http.client to 127.0.0.1 only, and every run here is refused before it.
    4. reports whether the README mentions its own branch or a merge prerequisite.

How to run
  python3 -B probe_readme-step1-branch-lacks-scripts_tiebreak.py <den-take-script checkout root>

Output: PASS/FAIL lines per check, then RESULT REPRODUCED or RESULT NOT REPRODUCED.
Synthetic data only; temp dirs only; the checkout is only read (git archive, git rev-parse).
"""
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def git(root, *args, binary=False):
    out = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    return out.stdout if binary else out.stdout.decode().strip()


def snapshot(root, ref, dest):
    data = git(root, "archive", "--format=tar", ref, binary=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    return dest


def run_step8(tree, cmd_args, tmp):
    take_py = Path(tree) / "tools" / "den-take" / "take.py"
    args = list(cmd_args)
    # Step 8's --out is a Windows path; put it in a temp dir instead.
    out_i = args.index("--out")
    args[out_i + 1] = str(Path(tmp) / "take-out")
    args += ["--camera", "http://127.0.0.1:9", "--token-file", str(Path(tmp) / "no-token.txt")]
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    env["NO_PROXY"] = "*"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, "-B", str(take_py), *args], cwd=tree,
                          capture_output=True, text=True, timeout=60, env=env)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).resolve()
    readme = (root / "tools" / "den-take" / "README.md").read_text(encoding="utf-8")
    head = git(root, "rev-parse", "HEAD")
    print(f"README branch HEAD: {head[:12]}")

    step1 = re.search(r"## Step 1.*?(?=\n## )", readme, re.S).group(0)
    wt = re.search(r"worktree add \S+ (\S+)", step1)
    branch = wt.group(1)
    print(f"Step 1 worktree branch: {branch}")
    print("Step 1 says: " + " ".join(re.search(r"Run every command below.*?instead\.", step1, re.S)
                                     .group(0).split()))

    step8 = re.search(r"## Step 8.*?(?=\nExit codes|\n## )", readme, re.S).group(0)
    cmd = re.search(r"python -B (tools\\den-take\\take\.py [^\n]+)", step8).group(1)
    print(f"Step 8 command: python -B {cmd}")
    cmd_args = cmd.split()[1:]  # drop the take.py path; keep --script hard-a --out ...
    script_name = cmd_args[cmd_args.index("--script") + 1]

    own_branch_named = "den-take-script" in readme
    merge_note_in_step8 = bool(re.search(r"merge|approval 3|after R", step8, re.I))
    print(f"README names its own branch (den-take-script): {own_branch_named}")
    print(f"Step 8 states a merge prerequisite: {merge_note_in_step8}")

    remote_ref = f"origin/{branch}"
    step1_tip = git(root, "rev-parse", remote_ref)
    head_in_step1 = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", head, remote_ref]
                                   ).returncode == 0
    print(f"{remote_ref} = {step1_tip[:12]}; README commit reachable from it (git pull helps): {head_in_step1}")

    results = {}
    with tempfile.TemporaryDirectory(prefix="tiebreak-step1-") as tmp:
        for label, ref in (("step1-branch", remote_ref), ("origin/main", "origin/main"), ("readme-branch", head)):
            tree = snapshot(root, ref, Path(tmp) / label.replace("/", "_"))
            has_script = (tree / "tools" / "den-take" / "scripts" / f"{script_name}.json").is_file()
            has_take = (tree / "tools" / "den-take" / "take.py").is_file()
            if not has_take:
                print(f"[{label}] tools/den-take/take.py missing; Step 8 cannot start (python: can't open file)")
                results[label] = ("no take.py", None)
                continue
            work = Path(tmp) / (label.replace("/", "_") + "-run")
            work.mkdir()
            code, text = run_step8(tree, cmd_args, work)
            last = text.splitlines()[-1] if text else ""
            print(f"[{label}] scripts/{script_name}.json present: {has_script}; exit {code}; last line: {last}")
            results[label] = (code, text)

    rejected = results["step1-branch"][0] == 2 and "invalid choice" in (results["step1-branch"][1] or "")
    control_ok = results["readme-branch"][0] is not None and "invalid choice" not in results["readme-branch"][1] \
        and "no script" not in results["readme-branch"][1]
    camera_untouched = "cannot reach the camera" not in (results["step1-branch"][1] or "")
    print(("PASS" if rejected else "FAIL") + ": Step 8 in the Step 1 worktree is refused by argparse (exit 2)")
    print(("PASS" if camera_untouched else "FAIL") + ": the refusal happens before any camera contact")
    print(("PASS" if control_ok else "FAIL") + ": control: the README's own branch accepts the script name")
    print(("PASS" if results["origin/main"][0] == "no take.py" else "FAIL")
          + ": pre-merge, the 'use main' path has no take.py either")
    print("RESULT " + ("REPRODUCED" if rejected and control_ok and not head_in_step1 else "NOT REPRODUCED"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
