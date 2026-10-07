"""What you picked before: the memory that lets Laya stop asking.

Shared by the server (reads it to rank and decide) and laya-fix (writes a pick or a "none of
these"). Standard library only, because laya-fix runs on the system python.

picks.json:
  {"lines":   {"gti status": {"command:git": 3}},   what each typed line turned into
   "heads":   {"gti": {"command:git": 3}},          same, for a command's first word alone
   "targets": {"command:git": 3},                   how often anything resolved to it
   "rejects": {"lauch firefox": 1}}                 "none of these" per line
"""
import json
import os
from pathlib import Path

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "laya-router"
FILE = STATE / "picks.json"

# Confirmations needed before a fix happens without asking.
AUTO_OPEN = 1  # apps and folders: opening the wrong one is harmless
AUTO_RUN = 3   # commands: they run with your arguments, so be surer
GIVE_UP = 2    # "none of these" this often (and more than picks) -> stay quiet for that line


def key(c):
    return f"{c['kind']}:{c['target']}"


def norm(line):
    return " ".join(line.lower().split())


def load():
    try:
        with open(FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    for k in ("lines", "heads", "targets", "rejects"):
        data.setdefault(k, {})
    return data


def _bump(table, name, sub=None):
    if sub is None:
        table[name] = table.get(name, 0) + 1
    else:
        row = table.setdefault(name, {})
        row[sub] = row.get(sub, 0) + 1


def record(line, cand):
    """cand = the candidate dict picked, or None for "none of these"."""
    data = load()
    line = norm(line)
    if cand is None:
        _bump(data["rejects"], line)
    else:
        k = key(cand)
        _bump(data["lines"], line, k)
        _bump(data["targets"], k)
        if cand["kind"] == "command":
            _bump(data["heads"], line.split()[0], k)
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, FILE)
