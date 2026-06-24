"""Find which HAOS core.config_entries backups have non-empty function_tools."""
import json
import subprocess
import sys
from pathlib import Path

BACKUPS = [
    "core.config_entries.bak.1778902006",
    "core.config_entries.bak.m3.1779049003",
    "core.config_entries.bak.1779120869",
    "core.config_entries.bak.preaddend30.1779138187",
    "core.config_entries.bak.preaddend30.1779175413",
]

TMP = Path("C:/Claude/home/tools/_tmp")
TMP.mkdir(parents=True, exist_ok=True)

for backup in BACKUPS:
    local = TMP / f"probe-{backup}.json"
    r = subprocess.run(
        ["scp", "-P", "22222",
         f"root@homeassistant.local:/config/.storage/{backup}",
         str(local)],
        capture_output=True, text=True, timeout=20,
    )
    if r.returncode != 0:
        print(f"  SCP failed for {backup}: {r.stderr.strip()[:80]}")
        continue
    try:
        with open(local, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        print(f"  parse failed for {backup}: {e}")
        continue
    for e in d.get("data", {}).get("entries", []):
        if e.get("domain") != "extended_openai_conversation":
            continue
        for s in e.get("subentries", []):
            ft = s["data"].get("function_tools", "")
            ft_chars = len(ft) if isinstance(ft, str) else None
            mark = "POPULATED" if ft_chars and ft_chars > 100 else "EMPTY"
            tool_count = ft.count("- spec:") if isinstance(ft, str) else 0
            print(f"  [{mark}] {backup} sub={s.get('subentry_id','')[:16]} chars={ft_chars} ~tools={tool_count}")
        break  # one entry per domain
