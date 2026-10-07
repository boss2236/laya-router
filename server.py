"""Laya typo router: turns a mistyped terminal line into the app, folder or command meant.

Runs as a socket-activated systemd user service (laya-router.socket). It loads the Laya
decision model once, answers JSON requests over the unix socket, and exits after
IDLE_EXIT seconds without a request so the ~2 GB it holds is only used while needed.

Request:  {"line": "opn spotfy", "cwd": "/home/me"}
Response: {"mode": "spelling"|"meaning", "auto": bool, "rest": "args kept after a fixed command",
           "candidates": [{"kind", "name", "target", "label", "score"}, ...]}  best first;
          no candidates = nothing sensible to suggest.
"""
import configparser
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from rapidfuzz import fuzz
from rapidfuzz.distance import OSA

IDLE_EXIT = 15 * 60
HOME = Path.home()

ANY, APPS_FOLDERS = {"app", "folder", "command"}, {"app", "folder"}
VERBS = {
    "open": APPS_FOLDERS, "launch": {"app"}, "start": {"app"}, "run": ANY, "show": APPS_FOLDERS,
    "cd": {"folder"}, "go": {"folder"}, "goto": {"folder"},
}
# Folders that are network mounts or huge: list the folder itself, never walk into it.
NO_WALK = {HOME / "GoogleDrive", HOME / "Windows"}

last_request = time.monotonic()


# ---------------------------------------------------------------- candidates
_cache = {}


def cached(name, ttl, build):
    hit = _cache.get(name)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = build()
    _cache[name] = (time.monotonic(), value)
    return value


def desktop_apps():
    dirs = [
        HOME / ".local/share/applications",
        HOME / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
        Path("/usr/share/applications"),
    ]
    apps, seen_ids, seen_names = [], set(), set()
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.desktop")):
            if f.name in seen_ids:
                continue
            seen_ids.add(f.name)
            cp = configparser.RawConfigParser(strict=False, interpolation=None)
            try:
                cp.read(f, encoding="utf-8")
                e = cp["Desktop Entry"]
            except Exception:
                continue
            if e.get("Type", "Application") != "Application":
                continue
            if e.get("NoDisplay", "").lower() == "true" or e.get("Hidden", "").lower() == "true":
                continue
            name = e.get("Name", "").strip()
            if not name or name.lower() in seen_names:
                continue
            seen_names.add(name.lower())
            extra = " ".join(filter(None, [
                e.get("GenericName", ""), e.get("Keywords", "").replace(";", " "),
                e.get("Categories", "").replace(";", " "), e.get("Comment", ""),
            ]))
            apps.append({
                "kind": "app", "name": name, "target": f.name,
                "label": f"{name} (app)",
                "desc": f"app {name}" + (f": {extra[:120]}" if extra else ""),
                "extra": extra.lower(),
            })
    return apps


def path_commands():
    names = set()
    for d in os.environ.get("PATH", "").split(":"):
        try:
            for entry in os.scandir(d):
                if entry.is_file() and os.access(entry.path, os.X_OK):
                    names.add(entry.name)
        except OSError:
            continue
    return [{"kind": "command", "name": n, "target": n, "label": f"{n} (command)",
             "desc": f"terminal command {n}", "extra": ""} for n in sorted(names)]


def _subdirs(p):
    try:
        return [e.path for e in os.scandir(p) if e.is_dir() and not e.name.startswith(".")]
    except OSError:
        return []


def folders(cwd):
    dirs = set(_subdirs(cwd))
    dirs.update(_subdirs(HOME))
    for p in ("Projects", "Documents", "Downloads", "Work"):
        base = HOME / p
        if base not in NO_WALK:
            for sub in _subdirs(base):
                dirs.add(sub)
                if p == "Projects":
                    dirs.update(_subdirs(sub))

    def zoxide():
        try:
            out = subprocess.run(["zoxide", "query", "-l"], capture_output=True, text=True, timeout=2).stdout
            return out.split("\n")[:300]
        except Exception:
            return []

    dirs.update(d for d in cached("zoxide", 60, zoxide) if d)
    out = []
    for d in dirs:
        pretty = d.replace(str(HOME), "~", 1) if d.startswith(str(HOME)) else d
        name = os.path.basename(d.rstrip("/")) or d
        out.append({"kind": "folder", "name": name, "target": d, "label": f"{pretty} (folder)",
                     "desc": f"folder {pretty}", "extra": pretty.lower()})
    return out


KIND_RANK = {"app": 0, "folder": 1, "command": 2}
# Words that say "an app" without saying which; dropped before matching on meaning.
FILLER = {"app", "apps", "application", "program", "the", "a", "an", "my", "some", "me", "please"}


def history_counts():
    """How often each command starts a line in ~/.bash_history: breaks ties toward what you use."""
    counts = {}
    try:
        with open(HOME / ".bash_history", errors="ignore") as f:
            for line in f:
                w = line.split(maxsplit=1)
                if w:
                    counts[w[0]] = counts.get(w[0], 0) + 1
    except OSError:
        pass
    return counts


def spelling(head, kinds, cwd):
    """Items whose name is a near-spelling of `head` (letter swaps, drops, extras), best first.

    Spelling is letter-distance work, which plain edit distance does reliably and Laya does not.
    """
    q = head.lower()
    pool = []
    if "app" in kinds:
        pool += cached("apps", 60, desktop_apps)
    if "folder" in kinds:
        pool += folders(cwd)
    if "command" in kinds:
        pool += cached("cmds", 60, path_commands)
    used = cached("history", 300, history_counts)
    scored = []
    for c in pool:
        n = c["name"].lower()
        if len(n) < 3 or (c["kind"] == "command" and n == q):  # bash found no such command, so not this
            continue
        names = {n, *(w for w in n.split() if len(w) >= 4)} if c["kind"] == "app" else {n}
        sim = max(OSA.normalized_similarity(q, x) for x in names)
        dist = min(OSA.distance(q, x) for x in names)
        if sim >= 0.72 or (len(q) <= 4 and dist <= 1):
            scored.append((round(sim, 3), KIND_RANK[c["kind"]], -used.get(c["name"], 0), c))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    out, seen = [], set()
    for sim, _, _, c in scored:
        key = c["target"] if c["kind"] == "folder" else c["name"].lower()  # app "Spotify" beats command "spotify"
        if key in seen:
            continue
        seen.add(key)
        out.append(c | {"score": sim})
        if len(out) == 4:
            break
    return out


def meaning(query):
    """Apps that fit a description ("music app", "web browser"), ranked by Laya."""
    words = [w for w in query.lower().split() if w not in FILLER]
    if not words:
        return []
    q = " ".join(words)
    apps = cached("apps", 60, desktop_apps)
    ranked = sorted(((fuzz.partial_token_set_ratio(q, (c["name"] + " " + c["extra"]).lower()), c) for c in apps),
                    key=lambda t: -t[0])
    cands = [c for s, c in ranked[:6] if s >= 75]
    if not cands:
        return []
    criteria = {c["name"]: f"app {c['name']}: {c['extra'][:80]}" for c in cands}
    res = router.predict(
        {"request": query},
        {"target": {"type": "choice", "instructions": "Which app best fits what the user asked for?",
                    "criteria": criteria}},
        model="english",
    )
    probs = res["answers"]["target"].get("probabilities", {})
    return sorted((c | {"score": float(probs.get(c["name"], 0.0))} for c in cands), key=lambda c: -c["score"])


def slim(c):
    return {k: c[k] for k in ("kind", "name", "target", "label", "score")}


# ---------------------------------------------------------------- model
from laya import Router  # noqa: E402  (import after the cheap helpers; torch is slow)

router = Router(device="cpu")
router.load("english")


def decide(line, cwd):
    words = line.split()
    verb, kinds = None, {"app", "folder", "command"}
    if len(words) > 1:
        first = words[0].lower()
        best = max(VERBS, key=lambda v: fuzz.ratio(first, v))
        # "opn x" / "lauch x": a (possibly misspelled) verb in front of the real thing
        if first in VERBS or (len(first) >= 3 and fuzz.ratio(first, best) >= 75):
            verb, kinds = best, VERBS[best]
            words = words[1:]
            if verb == "go" and len(words) > 1 and words[0].lower() == "to":
                words = words[1:]
    if not words:
        return {"candidates": []}

    # "gti status": a command with arguments -- only the first word is the typo.
    if verb is None and len(words) > 1:
        cmds = spelling(words[0], {"command"}, cwd)
        if cmds:
            return {"mode": "spelling", "rest": " ".join(words[1:]), "auto": False,
                    "candidates": [slim(c) for c in cmds]}
        kinds = {"app", "folder"}  # "web browser", "visual studo code": a phrase, not a command

    phrase = " ".join(words)
    cands = spelling(phrase, kinds, cwd)
    if cands:
        top = cands[0]
        clear = len(cands) == 1 or top["score"] - cands[1]["score"] >= 0.08
        return {"mode": "spelling", "rest": "", "candidates": [slim(c) for c in cands],
                "auto": clear and top["score"] >= 0.8 and top["kind"] != "command"}
    if "app" in kinds:
        cands = meaning(phrase)
        if cands:
            return {"mode": "meaning", "rest": "", "auto": False, "candidates": [slim(c) for c in cands[:3]]}
    return {"candidates": []}


# ---------------------------------------------------------------- serving
def handle(conn):
    global last_request
    with conn:
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
        try:
            req = json.loads(data)
            reply = decide(req.get("line", ""), req.get("cwd") or str(HOME))
        except Exception as e:  # never leave the client hanging
            reply = {"error": repr(e), "candidates": []}
        conn.sendall((json.dumps(reply) + "\n").encode())
        last_request = time.monotonic()


def idle_watch():
    while True:
        time.sleep(30)
        if time.monotonic() - last_request > IDLE_EXIT:
            os._exit(0)  # systemd's socket unit keeps listening and restarts us on demand


def main():
    if os.environ.get("LISTEN_FDS") == "1":  # socket activation: our listener is fd 3
        srv = socket.socket(fileno=3)
    else:
        path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ["XDG_RUNTIME_DIR"], "laya-router.sock")
        if os.path.exists(path):
            os.unlink(path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(8)
    threading.Thread(target=idle_watch, daemon=True).start()
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
