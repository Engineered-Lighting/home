#!/usr/bin/env python3
"""Offline planner for deploying Home Assistant package files to the HA host.

This tool never contacts the host. Given a set of repository paths it writes,
under an output directory, a receipt skeleton (receipt.json), a deploy
checklist (deploy-checklist.md) and staged copies of the files, following the
protocol in the plan's Standing rules and the precedent under
vjepa-home/operations/story-s-deploy-2026-09-17/:

  hash compare -> partial backup -> .bak.<ts> copies -> scp -> hash verify ->
  ha core check -> REST reloads -> core restart only for custom-component
  Python -> post-check of alias-derived automation entities -> receipt.

For each file it records the sha256 of the working-tree version (after), the
sha256 of the base version via `git show <base>:<path>` (what the host is
expected to carry before), the byte size and the target path on the host.
YAML files are parsed with PyYAML: every automation is listed with its alias
and the alias-derived entity id (automation.<slug>, what Home Assistant
derives when the entity registry has no entry yet; an automation with an
`id:` keeps the entity id it was first registered under, so an alias change
between base and after is flagged as a rename), every script key, and every
input_boolean / input_number / input_text / input_datetime / input_select
helper with its declared initial value (applied when the helper is new on the
host or on a core restart, not on an input_*/reload of an existing helper).
Top-level domains the planner does not handle (mqtt, shell_command, sensor,
...) are recorded per file as `unhandled_domains` and warned about in the
checklist header. A file under ha-config/extended_openai_conversation/ ending
in .py flags a core restart.

Usage:
  python3 tools/ha-deploy-packages.py --plan --files <repo paths...> \
      --base origin/main --out <dir> \
      [--host-map ha-config/packages=/config/packages,...] [--host <name>]

The checklist prints placeholders for the host name and reads the API token
from an environment variable; the token itself never appears anywhere.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import yaml

SCHEMA = "home-ha-package-deploy/v1"
CUSTOM_COMPONENT_DIR = "ha-config/extended_openai_conversation"
HELPER_DOMAINS = ("input_boolean", "input_number", "input_text", "input_datetime",
                  "input_select")
RELOAD_ORDER = ("template", "automation", "script") + HELPER_DOMAINS
DEFAULT_HOST_MAP = {
    "ha-config/packages": "/config/packages",
    "ha-config/homeai_proactive.yaml": "/config/packages/homeai_proactive.yaml",
    CUSTOM_COMPONENT_DIR: "/config/custom_components/extended_openai_conversation",
}
DEFAULT_HOST = "<HA_HOST>"
DEFAULT_SSH_PORT = "22222"
TOKEN_ENV = "HA_TOKEN"


# ---------------------------------------------------------------------------
# Slugify: a stdlib re-implementation of homeassistant.util.slugify, which is
# python-slugify with separator "_" (apostrophes become separators, digit
# commas are dropped, unicode is transliterated to ASCII, everything that is
# not [a-z0-9] becomes "_", runs collapse, ends are trimmed, empty -> unknown).
# ---------------------------------------------------------------------------

# Covers what text_unidecode (what python-slugify uses) does for the Latin-1
# supplement letters and symbols, the Latin Extended-A letters used in this
# repository and the General Punctuation range, so cp1252-mojibake aliases
# such as "a\u20ac\u201d" (a mis-decoded em-dash) slug the way the host does.
# It is NOT a full unidecode: non-Latin scripts (CJK, Cyrillic, Greek, ...)
# are dropped here where the host would transliterate them, so the tool
# reports every character it dropped (see dropped_chars) and the checklist
# warns about such aliases instead of silently trusting the slug.
_TRANSLIT = {
    "\u00df": "ss", "\u00e6": "ae", "\u0153": "oe", "\u0152": "OE",
    "\u00f8": "o", "\u00d8": "O", "\u0111": "d", "\u0142": "l",
    "\u00fe": "th", "\u00de": "Th", "\u0131": "i", "\u0192": "f",
    "\u00d0": "D", "\u00f0": "d", "\u1e9e": "SS",
    "\u00a1": "!", "\u00a2": "C/", "\u00a3": "PS", "\u00a5": "Y=",
    "\u00a7": "SS", "\u00a9": "(c)", "\u00ab": "<<", "\u00ae": "(r)",
    "\u00b0": "deg", "\u00b1": "+-", "\u00b5": "u", "\u00b6": "P",
    "\u00b7": "*", "\u00bb": ">>", "\u00bc": "1/4", "\u00bd": "1/2",
    "\u00be": "3/4", "\u00bf": "?", "\u00d7": "x", "\u00f7": "/",
    "\u00a0": " ",
    "\u2013": "-", "\u2014": "--", "\u2015": "--", "\u2212": "-",
    "\u2018": "'", "\u2019": "'", "\u201a": ",", "\u201c": '"',
    "\u201d": '"', "\u201e": ",,", "\u2020": "+", "\u2021": "++",
    "\u2022": "*", "\u2026": "...", "\u2030": "%0", "\u2039": "<",
    "\u203a": ">", "\u20ac": "EUR", "\u2122": "tm", "\u02c6": "^",
    "\u02dc": "~", "\u2044": "/",
    # Arrows and geometric shapes (text_unidecode maps them to punctuation,
    # which slugify then turns into a separator).
    "\u2190": "-", "\u2191": "|", "\u2192": "-", "\u2193": "|", "\u2194": "-",
    "\u21d2": "=", "\u25cf": "*", "\u25a0": "#",
}


def _to_ascii(text: str, dropped: list[str] | None = None) -> str:
    """Approximate unidecode: translit table first (so U+00BD becomes "1/2"
    before NFKD could split it), then NFKD with combining marks stripped, then
    drop the rest (emoji, symbols, unmapped scripts). Every dropped character
    is appended to `dropped` when a list is given."""
    out = []
    for ch in text:
        if ord(ch) < 128:
            out.append(ch)
            continue
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
            continue
        decomposed = unicodedata.normalize("NFKD", ch)
        kept = "".join(c for c in decomposed
                       if ord(c) < 128 and not unicodedata.combining(c))
        if kept:
            out.append(kept)
        elif unicodedata.category(ch).startswith("P"):
            out.append("-")
        elif dropped is not None and not unicodedata.combining(ch):
            dropped.append(ch)
        # else: emoji, symbols, unmapped letters -> dropped
    return "".join(out)


def dropped_chars(text: str | None) -> str:
    """The characters slugify() drops from `text` (empty when none). A
    non-empty result means the slug may differ from the host's, which
    transliterates scripts this tool does not map."""
    if not text:
        return ""
    dropped: list[str] = []
    _to_ascii(re.sub(r"[']+", "-", str(text)), dropped)
    return "".join(dropped)


def slugify(text: str | None) -> str:
    """Return what Home Assistant derives from an alias for an entity id."""
    if text is None or text == "":
        return ""
    text = re.sub(r"[']+", "-", str(text))
    text = _to_ascii(text)
    text = unicodedata.normalize("NFKD", text).lower()
    text = re.sub(r"[']+", "", text)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    text = re.sub(r"[^-a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    text = text.replace("-", "_")
    return text or "unknown"


# ---------------------------------------------------------------------------
# YAML parsing tolerant of Home Assistant tags (!include, !secret, ...).
# ---------------------------------------------------------------------------


class _HALoader(yaml.SafeLoader):
    """SafeLoader that turns every custom tag into a placeholder string."""


def _tag_placeholder(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return f"<{tag_suffix} {node.value}>"
    return f"<{tag_suffix}>"


_HALoader.add_multi_constructor("!", _tag_placeholder)


def load_ha_yaml(text: str):
    """Parse a Home Assistant package; returns the document or None."""
    return yaml.load(text, Loader=_HALoader)


def extract_automations(doc) -> list[dict]:
    """Every automation with its alias, id and alias-derived entity id."""
    found: list[dict] = []
    if not isinstance(doc, dict):
        return found
    block = doc.get("automation")
    items = block if isinstance(block, list) else (
        list(block.values()) if isinstance(block, dict) else [])
    for item in items:
        if not isinstance(item, dict):
            continue
        alias = item.get("alias")
        entry = {
            "alias": alias,
            "id": item.get("id"),
            "entity_id": f"automation.{slugify(alias)}" if alias else None,
            "dropped_chars": dropped_chars(alias),
            "entity_id_at_base": None,
            "renamed": False,
        }
        found.append(entry)
    return found


def flag_renamed_automations(automations: list[dict], base_automations: list[dict]) -> list[dict]:
    """Mark automations whose alias slug differs from the base version.

    Automations are matched by `id:`. Home Assistant keys such automations in
    the entity registry by that id and keeps the entity id assigned at first
    registration, so when the alias slug changed the host still serves the
    entity under the base slug (`entity_id_at_base`). Returns the renamed
    entries; `automations` is updated in place.
    """
    at_base = {a["id"]: a["entity_id"] for a in base_automations
               if a.get("id") is not None and a.get("entity_id")}
    renamed = []
    for a in automations:
        base_entity = at_base.get(a.get("id")) if a.get("id") is not None else None
        a["entity_id_at_base"] = base_entity
        a["renamed"] = bool(base_entity and a["entity_id"] and base_entity != a["entity_id"])
        if a["renamed"]:
            renamed.append(a)
    return renamed


def extract_scripts(doc) -> list[dict]:
    """Every script key with its alias and entity id (script.<key>)."""
    found: list[dict] = []
    if not isinstance(doc, dict):
        return found
    block = doc.get("script")
    if isinstance(block, dict):
        for key, body in block.items():
            alias = body.get("alias") if isinstance(body, dict) else None
            found.append({"key": key, "alias": alias, "entity_id": f"script.{key}"})
    elif isinstance(block, list):
        for body in block:
            if isinstance(body, dict):
                alias = body.get("alias")
                key = slugify(alias) if alias else None
                found.append({"key": key, "alias": alias,
                              "entity_id": f"script.{key}" if key else None})
    return found


def extract_helpers(doc) -> list[dict]:
    """Every input_* helper with its declared initial value (None if absent).

    A declared `initial` is applied when the helper is new on the host or on
    a core restart (the entity is added and state restore is skipped); an
    `input_*/reload` of an existing helper goes through async_update_config
    and keeps the live value. The live value is still recorded before and
    after so a new helper or a restart shows up in the receipt.
    """
    found: list[dict] = []
    if not isinstance(doc, dict):
        return found
    for domain in HELPER_DOMAINS:
        block = doc.get(domain)
        if not isinstance(block, dict):
            continue
        for key, body in block.items():
            body = body if isinstance(body, dict) else {}
            found.append({
                "entity_id": f"{domain}.{key}",
                "domain": domain,
                "name": body.get("name"),
                "initial": body.get("initial"),
                "has_initial": "initial" in body,
            })
    return found


def reload_domains(doc) -> list[str]:
    """Which REST reload services a parsed package needs, in protocol order."""
    if not isinstance(doc, dict):
        return []
    needed = []
    for domain in RELOAD_ORDER:
        if domain in doc:
            needed.append(domain)
    return needed


def unhandled_domains(doc) -> list[str]:
    """Top-level keys the planner neither reloads nor records (mqtt,
    shell_command, sensor, ...); the operator must handle them by hand."""
    if not isinstance(doc, dict):
        return []
    return sorted(str(k) for k in doc if k not in RELOAD_ORDER)


# ---------------------------------------------------------------------------
# File facts.
# ---------------------------------------------------------------------------


def _norm(repo_path: str) -> str:
    """Repository-relative path with forward slashes and no leading ./ ."""
    p = repo_path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.strip("/")


def resolve_repo_path(repo: Path, given: str) -> str:
    """Repository-relative path for a --files argument. Absolute paths are
    resolved against the repository root and refused when outside it;
    relative ones are normalised as given."""
    if Path(given).is_absolute():
        try:
            rel = Path(given).resolve().relative_to(repo.resolve())
        except ValueError:
            raise SystemExit(f"--files path is outside the repository ({repo}): {given}")
        return rel.as_posix()
    return _norm(given)


def requires_core_restart(repo_path: str) -> bool:
    """True for a custom-component Python file (needs a core restart)."""
    p = _norm(repo_path)
    return p.startswith(CUSTOM_COMPONENT_DIR + "/") and p.endswith(".py")


def parse_host_map(spec: str | None) -> dict[str, str]:
    mapping = dict(DEFAULT_HOST_MAP)
    if spec:
        for pair in spec.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "=" not in pair:
                raise SystemExit(f"--host-map entry without '=': {pair!r}")
            src, dst = pair.split("=", 1)
            mapping[src.strip().strip("/")] = dst.strip()
    return mapping


def host_target(repo_path: str, host_map: dict[str, str]) -> str | None:
    """Map a repo path to its host path: exact match first, then the longest
    directory prefix; None when nothing matches."""
    p = _norm(repo_path)
    if p in host_map:
        return host_map[p]
    best = None
    for src, dst in host_map.items():
        if p.startswith(src.rstrip("/") + "/"):
            if best is None or len(src) > len(best[0]):
                best = (src, dst)
    if best is None:
        return None
    src, dst = best
    rest = p[len(src.rstrip("/")) + 1:]
    return dst.rstrip("/") + "/" + rest


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=False, check=False)


def base_blob(repo: Path, base: str, repo_path: str) -> bytes | None:
    """Bytes of <base>:<path>, or None when the file is absent at base."""
    proc = git(repo, "show", f"{base}:{repo_path}")
    if proc.returncode != 0:
        return None
    return proc.stdout


def describe_file(repo: Path, base: str, repo_path: str,
                  host_map: dict[str, str]) -> dict:
    """Everything the receipt records about one file."""
    abs_path = repo / repo_path
    data = abs_path.read_bytes()
    before = base_blob(repo, base, repo_path)
    entry: dict = {
        "target": host_target(repo_path, host_map),
        "sha256_expected_on_host_before": sha256_bytes(before) if before is not None else None,
        "sha256_after": sha256_bytes(data),
        "bytes_after": len(data),
        "new_at_base": before is None,
        "unchanged_from_base": before is not None and before == data,
        "sha256_observed_on_host": None,
        "host_matches_base": None,
        "core_restart_required": requires_core_restart(repo_path),
        "reloads": [],
        "unhandled_domains": [],
        "automations": [],
        "renamed_automations": [],
        "scripts": [],
        "helpers": [],
    }
    if repo_path.endswith((".yaml", ".yml")):
        try:
            doc = load_ha_yaml(data.decode("utf-8"))
        except yaml.YAMLError as exc:
            entry["yaml_error"] = str(exc).splitlines()[0]
            doc = None
        entry["reloads"] = reload_domains(doc)
        entry["unhandled_domains"] = unhandled_domains(doc)
        entry["automations"] = extract_automations(doc)
        entry["scripts"] = extract_scripts(doc)
        entry["helpers"] = extract_helpers(doc)
        base_doc = None
        if before is not None:
            try:
                base_doc = load_ha_yaml(before.decode("utf-8"))
            except (yaml.YAMLError, UnicodeDecodeError):
                base_doc = None
        renamed = flag_renamed_automations(entry["automations"], extract_automations(base_doc))
        entry["renamed_automations"] = [
            {"id": a["id"], "entity_id_at_base": a["entity_id_at_base"], "entity_id": a["entity_id"]}
            for a in renamed]
    return entry


# ---------------------------------------------------------------------------
# Receipt and checklist.
# ---------------------------------------------------------------------------


def _git_text(repo: Path, *args: str) -> str:
    proc = git(repo, *args)
    return proc.stdout.decode("utf-8", "replace").strip() if proc.returncode == 0 else ""


def build_receipt(repo: Path, base: str, files: list[str],
                  host_map: dict[str, str], host: str, out_dir: Path) -> dict:
    now = time.time()
    entries = {path: describe_file(repo, base, path, host_map) for path in files}
    reloads = [d for d in RELOAD_ORDER
               if any(d in e["reloads"] for e in entries.values())]
    automations = [a for e in entries.values() for a in e["automations"]]
    scripts = [s for e in entries.values() for s in e["scripts"]]
    helpers = [h for e in entries.values() for h in e["helpers"]]
    restart = any(e["core_restart_required"] for e in entries.values())
    unhandled = {path: e["unhandled_domains"] for path, e in entries.items()
                 if e["unhandled_domains"]}
    renamed = [dict(r, file=path) for path, e in entries.items()
               for r in e["renamed_automations"]]
    dropped = [{"file": path, "alias": a["alias"], "dropped_chars": a["dropped_chars"],
                "entity_id": a["entity_id"]}
               for path, e in entries.items() for a in e["automations"] if a["dropped_chars"]]
    return {
        "schema": SCHEMA,
        "prepared_at": now,
        "prepared_at_iso": dt.datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
        "branch": _git_text(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit": _git_text(repo, "rev-parse", "--short", "HEAD"),
        "base": base,
        "base_commit": _git_text(repo, "rev-parse", "--short", base),
        "host": host,
        "status": "PLANNED (not run)",
        "staged_dir": str(out_dir),
        "files": entries,
        "reloads": reloads,
        "unhandled_domains": unhandled,
        "core_restart_required": restart,
        "automation_entities": [a["entity_id"] for a in automations if a["entity_id"]],
        "renamed_automations": renamed,
        "alias_dropped_chars": dropped,
        "script_entities": [s["entity_id"] for s in scripts if s["entity_id"]],
        "helper_values": {
            h["entity_id"]: {"declared_initial": h["initial"],
                             "has_initial": h["has_initial"],
                             "before": None, "after": None}
            for h in helpers
        },
        "step_2_resolution": None,
        "deploy_log": [],
        "rollback": ("cp -p <target>.bak.<ts> <target> for every file, ha core check, "
                     "then the same reloads (" + ", ".join(reloads) + ")"
                     + ("; ha core restart (Supervisor)" if restart else "")
                     + "; re-set the helper values recorded before"),
    }


def format_initial(entity_id: str, meta: dict) -> str:
    """Show a declared initial the way Home Assistant will apply it."""
    if not meta["has_initial"]:
        return "(none declared)"
    value = meta["declared_initial"]
    if entity_id.startswith("input_boolean.") and isinstance(value, bool):
        return "on" if value else "off"
    return json.dumps(value)


def _shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def render_checklist(receipt: dict, staged: dict[str, str]) -> str:
    """The exact commands in protocol order, with placeholders only."""
    host = receipt["host"]
    port = DEFAULT_SSH_PORT
    ssh = f"ssh -p {port} root@{host}"
    files = receipt["files"]
    targets = [(p, e["target"]) for p, e in files.items()]
    reloads = receipt["reloads"]
    restart = receipt["core_restart_required"]
    date = receipt["prepared_at_iso"][:10]
    lines: list[str] = []
    add = lines.append

    add(f"# Deploy checklist: {len(files)} file(s) (prepared {date}, not yet run)")
    add("")
    add(f"Source: branch `{receipt['branch']}` at `{receipt['commit']}`, base `{receipt['base']}` "
        f"(`{receipt['base_commit']}`).")
    add("Staged copies and hashes are in this directory (`receipt.json`). "
        "Owner present for every step from 3 on.")
    add(f"Placeholders: `{host}` is the HA host; the API token is read from `${TOKEN_ENV}` "
        "on the workstation and is never printed or pasted.")
    add("Every step is read-only until step 4. Stop at the first mismatch.")
    add("")
    add("Files:")
    add("")
    add("| repo path | host target | bytes | restart |")
    add("|---|---|---|---|")
    for p, e in files.items():
        add(f"| `{p}` | `{e['target'] or 'UNMAPPED'}` | {e['bytes_after']} | "
            f"{'yes' if e['core_restart_required'] else 'no'} |")
    add("")
    unmapped = [p for p, t in targets if t is None]
    if unmapped:
        add("WARNING: no host target for: " + ", ".join(f"`{p}`" for p in unmapped)
            + ". Add a `--host-map` entry before running anything.")
        add("")
    unchanged = [p for p, e in files.items() if e["unchanged_from_base"]]
    if unchanged:
        add("Note: identical to base (deploying is a no-op unless the host drifted): "
            + ", ".join(f"`{p}`" for p in unchanged))
        add("")
    for p, domains in receipt["unhandled_domains"].items():
        add(f"WARNING: unhandled top-level domain(s) in `{p}`: "
            + ", ".join(f"`{d}`" for d in domains)
            + ". No reload is planned for them and their entities are not recorded; if their "
            "blocks changed against base, reload them by hand (`<domain>/reload` where the "
            "domain offers it) or restart core, and post-check them yourself.")
        add("")
    for r in receipt["renamed_automations"]:
        add(f"WARNING: automation id `{r['id']}` in `{r['file']}` changed its alias slug: "
            f"`{r['entity_id_at_base']}` at base -> `{r['entity_id']}` after. The entity registry "
            "keeps the id assigned at first registration, so the host may still serve it as "
            f"`{r['entity_id_at_base']}` (see step 9).")
        add("")
    for d in receipt["alias_dropped_chars"]:
        codes = " ".join(f"U+{ord(c):04X}" for c in d["dropped_chars"])
        add(f"WARNING: characters dropped from alias `{d['alias']}` in `{d['file']}` ({codes}); "
            f"the host transliterates scripts this planner does not map, so `{d['entity_id']}` "
            "may not be the live entity id. Confirm it in step 9 with the `attributes.id` "
            "fallback.")
        add("")

    add("## 1. Host state (read-only)")
    add("")
    add("```")
    add(f'{ssh} "ha core info | grep -E \\"version|state\\"; df -h /config"')
    for _, t in targets:
        if t:
            add(f'{ssh} "ls -l {t}"')
    add("```")
    add("Confirm each target exists (or, for `new_at_base` files, that it does not) and that the "
        "packages directory is loaded by `!include_dir_named` in `configuration.yaml`.")
    add("")

    add("## 2. Hash compare (read-only)")
    add("")
    add("Compare with `sha256_expected_on_host_before` in `receipt.json`. A mismatch means "
        "something other than the base is deployed: diff it first, do not overwrite. Write the "
        "outcome into `step_2_resolution` and `sha256_observed_on_host` per file.")
    add("")
    add("```")
    for p, e in files.items():
        if e["target"]:
            add(f"# {p}: expect {e['sha256_expected_on_host_before'] or 'ABSENT (new file at base)'}")
            add(f'{ssh} "sha256sum {e["target"]}"')
    add("```")
    add("")

    helper_values = receipt["helper_values"]
    add("## 3. Helper values before (read-only) and partial backup")
    add("")
    if helper_values:
        add("Record the live value of every helper in `helper_values.<entity>.before`. A declared "
            "`initial` is applied only when the helper is new on the host (`new_at_base`) or on a "
            "core restart (step 8); an `input_*/reload` (step 7) keeps the live value of an existing "
            "helper. Re-set in step 10 any helper whose value did not survive.")
        add("")
        add("```")
        add(f"# token read from the environment, never echoed")
        for entity, meta in helper_values.items():
            add(f"# {entity}: declared initial = {format_initial(entity, meta)}")
            add(f'curl -sS -H "Authorization: Bearer ${TOKEN_ENV}" '
                f'http://{host}:8123/api/states/{entity} | jq -r .state')
        add("```")
    else:
        add("No input helpers in this set; nothing to record.")
    add("")
    add("Partial backup (Home Assistant config only). Confirm the flag form on the host first; the "
        "full backup took 12 minutes and froze the Supervisor:")
    add("")
    add("```")
    add(f'{ssh} "ha backups new --help"   # confirm --homeassistant is accepted on this Supervisor')
    add(f'{ssh} "ha backups new --homeassistant --name deploy-pre-$(date +%Y%m%d-%H%M)"')
    add(f'{ssh} "ha backups list | tail -5"   # wait until it is listed')
    add("```")
    add("")

    add("## 4. `.bak.<ts>` copies on the host")
    add("")
    add("Not loaded by `!include_dir_named` (no `.yaml` suffix). Record `ts` in the receipt "
        "(`deploy_log`, step 4).")
    add("")
    add("```")
    cps = "; ".join(f"cp -p {t} {t}.bak.\\$ts" for _, t in targets if t)
    add(f'{ssh} "ts=\\$(date +%s); echo bak-ts=\\$ts; {cps}"')
    add("```")
    add("")

    add("## 5. Copy the staged files and verify the hashes")
    add("")
    add("```")
    for p, e in files.items():
        if e["target"]:
            add(f"scp -P {port} {_shell_quote(staged[p])} root@{host}:{e['target']}")
    for p, e in files.items():
        if e["target"]:
            add(f"# {p}: expect {e['sha256_after']}")
            add(f'{ssh} "sha256sum {e["target"]}"')
    add("```")
    add("")

    add("## 6. Config check")
    add("")
    add("```")
    add(f'{ssh} "ha core check"')
    add("```")
    add("On failure: restore the `.bak.<ts>` copies, `ha core check` again, stop.")
    add("")

    add("## 7. REST reloads (in this order)")
    add("")
    if reloads:
        add("```")
        for domain in reloads:
            add(f'curl -sS -o /dev/null -w "{domain}/reload %{{http_code}}\\n" -X POST '
                f'-H "Authorization: Bearer ${TOKEN_ENV}" -H "Content-Type: application/json" '
                f'http://{host}:8123/api/services/{domain}/reload')
        add("```")
        add("Every call must return 200. `ha core logs 2>&1 | grep -iE \"TemplateError|Invalid config\" | tail` "
            "must show nothing new.")
    else:
        add("No reloadable YAML domains in this set.")
    add("")

    add("## 8. Core restart")
    add("")
    if restart:
        add("REQUIRED: a custom-component Python file is in the set. Reloads do not re-import it.")
        add("")
        add("```")
        add(f'{ssh} "ha core restart"')
        add(f'{ssh} "ha core info | grep state"   # wait for running')
        add("```")
    else:
        add("Not required: no custom-component Python file in the set. Do not restart.")
    add("")

    add("## 9. Post-checks")
    add("")
    add("Each alias-derived automation entity must exist and be `on`; each script must exist. "
        "The entity id below is derived from the alias, not the `id:` key, and is what Home "
        "Assistant assigns at first registration. For an automation with an `id:` the entity "
        "registry keeps that first entity id, so the live id may differ when the automation was "
        "renamed (alias changed since it was first loaded, or renamed in the UI). When a check "
        "below returns 404, fall back to `GET /api/states` filtered by `attributes.id` and record "
        "the live id in the receipt.")
    add("")
    add("```")
    for a in receipt["automation_entities"]:
        add(f'curl -sS -H "Authorization: Bearer ${TOKEN_ENV}" '
            f'http://{host}:8123/api/states/{a} | jq -r \'[.entity_id, .state, .attributes.last_triggered] | @tsv\'')
    for s in receipt["script_entities"]:
        add(f'curl -sS -H "Authorization: Bearer ${TOKEN_ENV}" '
            f'http://{host}:8123/api/states/{s} | jq -r \'[.entity_id, .state] | @tsv\'')
    if not receipt["automation_entities"] and not receipt["script_entities"]:
        add("# no automations or scripts in this set")
    add("```")
    add("")
    automation_ids = [a["id"] for e in files.values() for a in e["automations"]
                      if a.get("id") is not None]
    if automation_ids:
        renamed_ids = [r["id"] for r in receipt["renamed_automations"]]
        add("Fallback when a post-check above returns 404 (the automation's `id:` is stable even "
            "when the alias is not)" + (": run it first for the renamed automation id(s) "
                                       + ", ".join(f"`{i}`" for i in renamed_ids)
                                       if renamed_ids else "") + ":")
        add("")
        add("```")
        for i in automation_ids:
            add(f'curl -sS -H "Authorization: Bearer ${TOKEN_ENV}" http://{host}:8123/api/states '
                f"| jq -r '.[] | select(.attributes.id == {json.dumps(str(i))}) "
                "| [.entity_id, .state] | @tsv'")
        add("```")
        add("")

    add("## 10. Helper values after")
    add("")
    if helper_values:
        add("Re-read every helper from step 3 into `helper_values.<entity>.after`. A reload of an "
            "existing helper keeps its value; where a declared `initial` was applied instead "
            "(helper new on the host, or a core restart in step 8) and the owner wants the live "
            "value back, set it with the matching service call (`input_boolean/turn_on|turn_off`, "
            "`input_number/set_value`, `input_text/set_value`, `input_datetime/set_datetime`, "
            "`input_select/select_option`), never by editing `initial:`.")
    else:
        add("Nothing to record.")
    add("")

    add("## 11. Receipt")
    add("")
    add("Fill `status`, `deploy_log` (one entry per step with a timestamp), "
        "`sha256_observed_on_host`, `host_matches_base`, `step_2_resolution` and the helper "
        "values, then commit the receipt directory (0600 files) under "
        "`vjepa-home/operations/`.")
    add("")
    add("Rollback: " + receipt["rollback"] + ".")
    add("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Staging and entry point.
# ---------------------------------------------------------------------------


def stage_files(repo: Path, files: list[str], out_dir: Path) -> dict[str, str]:
    """Copy each file into out_dir; basename, or the path with '/' -> '__'
    when two files share a basename."""
    names = [Path(p).name for p in files]
    staged: dict[str, str] = {}
    for p in files:
        name = Path(p).name
        if names.count(name) > 1:
            name = p.strip("/").replace("/", "__")
        dest = out_dir / name
        shutil.copy2(repo / p, dest)
        staged[p] = str(dest)
    return staged


def plan(repo: Path, base: str, files: list[str], out_dir: Path,
         host_map: dict[str, str], host: str) -> dict:
    """Write receipt.json, deploy-checklist.md and the staged copies."""
    for p in files:
        if not (repo / p).is_file():
            raise SystemExit(f"not a file in the worktree: {p}")
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt = build_receipt(repo, base, files, host_map, host, out_dir)
    staged = stage_files(repo, files, out_dir)
    for p, dest in staged.items():
        receipt["files"][p]["staged"] = dest
    (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    (out_dir / "deploy-checklist.md").write_text(render_checklist(receipt, staged))
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", action="store_true", required=True,
                        help="write the receipt skeleton, checklist and staged copies")
    parser.add_argument("--files", nargs="+", required=True, metavar="PATH",
                        help="repository-relative paths of the files to deploy")
    parser.add_argument("--base", default="origin/main",
                        help="git ref the host is expected to carry (default origin/main)")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--host-map", default=None,
                        help="comma-separated repo=host mappings added to the defaults")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="host name placeholder used in the checklist")
    parser.add_argument("--repo", default=None,
                        help="repository root (default: the parent of tools/)")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve() if args.repo else Path(__file__).resolve().parents[1]
    files = [resolve_repo_path(repo, p) for p in args.files]
    host_map = parse_host_map(args.host_map)
    receipt = plan(repo, args.base, files, Path(args.out).resolve(), host_map, args.host)

    unmapped = [p for p, e in receipt["files"].items() if e["target"] is None]
    print(f"wrote {args.out}/receipt.json and deploy-checklist.md; {len(files)} file(s) staged")
    print(f"reloads: {', '.join(receipt['reloads']) or 'none'}; "
          f"core restart: {'REQUIRED' if receipt['core_restart_required'] else 'no'}")
    for a in receipt["automation_entities"]:
        print(f"automation: {a}")
    for entity, meta in receipt["helper_values"].items():
        print(f"helper: {entity} initial={format_initial(entity, meta)}")
    for p, domains in receipt["unhandled_domains"].items():
        print(f"WARNING: unhandled domain(s) in {p}: {', '.join(domains)} "
              "(not reloaded, not recorded; see the checklist header)", file=sys.stderr)
    for r in receipt["renamed_automations"]:
        print(f"WARNING: automation id {r['id']} renamed: {r['entity_id_at_base']} -> "
              f"{r['entity_id']} (host may keep the base id; see step 9)", file=sys.stderr)
    for d in receipt["alias_dropped_chars"]:
        codes = " ".join(f"U+{ord(c):04X}" for c in d["dropped_chars"])
        print(f"WARNING: characters dropped from alias {d['alias']!r} ({codes}); "
              f"{d['entity_id']} may not match the host", file=sys.stderr)
    if unmapped:
        print("UNMAPPED (add --host-map): " + ", ".join(unmapped), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
