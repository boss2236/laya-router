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
import time
from pathlib import Path

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "laya-router"
FILE = STATE / "picks.json"

LOG = STATE / "log.jsonl"  # what happened, newest last; the dashboard's "recent" list
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "laya-router/config.json"

DEFAULTS = {
    "auto_open": 1,      # confirmations before an app / folder opens without asking (harmless if wrong)
    "auto_run": 3,       # same for a command: it runs with your arguments, so be surer
    "give_up": 2,        # "none of these" this often (and more than picks) -> stay quiet for that line
    "meaning": True,     # use the Laya model for descriptions ("music app"); off = spelling only
    "model_idle_min": 3, # drop the model (~1.2 GB) after this many idle minutes
}


def config():
    try:
        with open(CONFIG) as f:
            mine = json.load(f)
    except (OSError, ValueError):
        mine = {}
    return DEFAULTS | {k: v for k, v in mine.items() if k in DEFAULTS}


def save_config(values):
    cfg = config() | {k: type(DEFAULTS[k])(v) for k, v in values.items() if k in DEFAULTS}
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    _write(CONFIG, cfg)
    return cfg


def _write(path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


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


def record(line, cand, how="picked"):
    """cand = the candidate dict picked, or None for "none of these". how: picked | auto."""
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
    _write(FILE, data)
    log(line, "rejected" if cand is None else how, cand)


def log(line, what, cand=None):
    """One line per decision; kept to the last 1000."""
    entry = {"t": int(time.time()), "line": line, "what": what}
    if cand:
        entry |= {"kind": cand["kind"], "to": cand.get("run") or cand["name"]}
    STATE.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    if LOG.stat().st_size > 400_000:
        rows = LOG.read_text().splitlines()[-1000:]
        LOG.write_text("\n".join(rows) + "\n")


def forget(line):
    """Drop everything learned about one typed line."""
    data, line = load(), norm(line)
    for k in data["lines"].pop(line, {}):
        data["targets"][k] = data["targets"].get(k, 1) - 1
        if data["targets"][k] <= 0:
            del data["targets"][k]
    data["rejects"].pop(line, None)
    _write(FILE, data)
