"""Laya typo router: turns a mistyped terminal line into the app, folder or command meant.

Runs as a socket-activated systemd user service (laya-router.socket). It answers JSON
requests over the unix socket and exits after
IDLE_EXIT seconds without a request. Spelling fixes (most requests) never touch the model: it is
loaded only for a "meaning" question ("music app") and dropped again after MODEL_IDLE seconds,
so the process normally sits at ~25 MB instead of ~2.8 GB.

What you pick in the menu is remembered (learn.py, ~/.local/state/laya-router/picks.json):
a fix you have chosen before goes first, and opens / runs without asking once you've confirmed it.

Request:  {"line": "opn spotfy", "cwd": "/home/me"}
Response: {"mode": "spelling"|"meaning", "auto": bool,
           "candidates": [{"kind", "name", "target", "label", "score", "run"?}, ...]}  best first;
          "run" (commands only) is the corrected line to execute; no candidates = stay quiet.
"""
import configparser
import ctypes
import gc
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from rapidfuzz import fuzz
from rapidfuzz.distance import OSA

import learn

IDLE_EXIT = 15 * 60
MODEL_IDLE = 3 * 60
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
            generic = " ".join([e.get("GenericName", ""), e.get("Keywords", ""), e.get("Categories", "")])
            apps.append({
                "kind": "app", "name": name, "target": f.name,
                "generic": set(re.findall(r"[a-z]{3,}", generic.lower())),
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
FILLER = {"app", "apps", "application", "program", "tool", "the", "a", "an", "my", "some", "me", "please", "for"}
# Side-by-side builds of the same app: on a tie, the stable one wins ("crhome" -> Google Chrome).
PRERELEASE = re.compile(r"\b(unstable|beta|dev|nightly|canary|preview|insiders)\b", re.I)
# Subcommands worth knowing before your history has taught us ("gti psuh" -> git push).
SUBCOMMANDS = {
    "git": "add bisect blame branch checkout cherry-pick clone commit config diff fetch init log merge mv "
           "pull push rebase remote reset restore revert rm show stash status switch tag worktree",
    "systemctl": "cat daemon-reload disable edit enable is-active list-units mask restart start status stop unmask",
    "docker": "build compose exec images inspect logs ps pull push rm rmi run start stop volume network",
    "npm": "ci init install link list outdated publish run start test uninstall update",
    "uv": "add init lock pip python remove run sync tool venv",
    "cargo": "add bench build check clean clippy doc fmt init install new publish run test update",
    "gh": "api auth browse gist issue pr release repo run workflow",
    "yay": "", "pacman": "",
}


def history():
    """Your ~/.bash_history as {command: uses} and {command: {subcommand: uses}}."""
    counts, subs = {}, {}
    try:
        with open(HOME / ".bash_history", errors="ignore") as f:
            for line in f:
                w = line.split()
                if not w:
                    continue
                counts[w[0]] = counts.get(w[0], 0) + 1
                if len(w) > 1 and re.fullmatch(r"[a-z][a-z-]+", w[1]):
                    row = subs.setdefault(w[0], {})
                    row[w[1]] = row.get(w[1], 0) + 1
    except OSError:
        pass
    return counts, subs


def known_subcommands(cmd):
    _, subs = cached("history", 300, history)
    mine = {w for w, n in subs.get(cmd, {}).items() if n >= 2}
    return mine | set(SUBCOMMANDS.get(cmd, "").split())


def fix_subcommand(cmd, word):
    """"psuh" -> "push" for git; None if `word` is fine or nothing is close."""
    known = known_subcommands(cmd)
    if not known or word in known or word.startswith("-"):
        return None
    best = max(known, key=lambda k: (OSA.normalized_similarity(word, k), -abs(len(k) - len(word))))
    if OSA.distance(word, best) <= (1 if len(word) <= 4 else 2):
        return best
    return None


def spelling(head, kinds, cwd, picks):
    """Items whose name is a near-spelling of `head` (letter swaps, drops, extras), best first.

    Spelling is letter-distance work, which plain edit distance does reliably and Laya does not.
    Ties go to what you have picked before, then apps over folders over commands, then the stable
    build of an app, then the command you type most.
    """
    q = head.lower()
    pool = []
    if "app" in kinds:
        pool += cached("apps", 60, desktop_apps)
    if "folder" in kinds:
        pool += folders(cwd)
    if "command" in kinds:
        pool += cached("cmds", 60, path_commands)
    used, _ = cached("history", 300, history)
    targets = picks["targets"]
    scored = []
    for c in pool:
        n = c["name"].lower()
        if c["kind"] == "command" and n == q:  # bash found no such command, so not this
            continue
        if len(n) < (2 if c["kind"] == "command" else 3):  # "sl" -> ls, but no 2-letter app/folder noise
            continue
        names = {n, *(w for w in n.split() if len(w) >= 4)} if c["kind"] == "app" else {n}
        sim = max(OSA.normalized_similarity(q, x) for x in names)
        dist = min(OSA.distance(q, x) for x in names)
        if sim >= 0.72 or (len(q) <= 4 and dist <= 1 and len(n) >= len(q) - 1):
            if any(sorted(q) == sorted(x) for x in names):
                sim = min(sim + 0.2, 0.99)  # same letters, swapped: the commonest typo ("sl" is ls, not psl)
            scored.append(((round(sim, 3), targets.get(learn.key(c), 0), -KIND_RANK[c["kind"]],
                            not PRERELEASE.search(c["name"]), used.get(c["name"], 0)), c))
    scored.sort(key=lambda t: t[0], reverse=True)
    out, seen = [], set()
    for (sim, *_), c in scored:
        key = c["target"] if c["kind"] == "folder" else c["name"].lower()  # app "Spotify" beats command "spotify"
        if key in seen:
            continue
        seen.add(key)
        out.append(c | {"score": sim})
        if len(out) == 4:
            break
    return out


def describes_an_app(words):
    """True if some word is how apps describe themselves ("music", "editor", "browser")."""
    apps = cached("apps", 60, desktop_apps)
    vocab = cached("vocab", 60, lambda: set().union(*(a["generic"] for a in apps)))
    return any(w in vocab or w.rstrip("s") in vocab for w in words)


def meaning(query):
    """Apps that fit a description ("music app", "web browser"), ranked by Laya."""
    words = [w for w in query.lower().split() if w not in FILLER]
    if not words or not describes_an_app(words):
        return []
    q = " ".join(words)
    apps = cached("apps", 60, desktop_apps)
    # Shortlist for Laya: fuzzy hit first, then how many of the words the app uses to describe
    # itself ("Text Editor" for Neovim) -- otherwise ties at 100 cut the list arbitrarily.
    ranked = sorted(((fuzz.partial_token_set_ratio(q, (c["name"] + " " + c["extra"]).lower()),
                      sum(w in c["generic"] or w.rstrip("s") in c["generic"] for w in words), c) for c in apps),
                    key=lambda t: (-t[0], -t[1]))
    if ranked and ranked[0][1]:  # some app calls itself this: don't let near-misses (Foot) in
        ranked = [t for t in ranked if t[1]]
    cands = [c for s, _, c in ranked[:8] if s >= 75]
    if not cands:
        return []
    criteria = {c["name"]: f"app {c['name']}: {c['extra'][:80]}" for c in cands}
    res = model().predict(
        {"request": query},
        {"target": {"type": "choice", "instructions": "Which app best fits what the user asked for?",
                    "criteria": criteria}},
        model="english",
    )
    probs = res["answers"]["target"].get("probabilities", {})
    out = sorted((c | {"score": float(probs.get(c["name"], 0.0))} for c in cands), key=lambda c: -c["score"])
    # Laya is sure about the right few and gives the rest crumbs: only offer real contenders.
    return [c for c in out if c["score"] >= max(0.05, out[0]["score"] * 0.25)]


def slim(c, rest=""):
    out = {k: c[k] for k in ("kind", "name", "target", "label", "score")}
    if c["kind"] == "command":
        out["run"] = (c["target"] + " " + rest).strip()
    return out


# ---------------------------------------------------------------- model
_router, _router_lock, model_used = None, threading.Lock(), 0.0


def model():
    """The Laya router, loaded on first use (a few seconds, ~1.2 GB) and dropped by idle_watch."""
    global _router, model_used
    with _router_lock:
        model_used = time.monotonic()
        if _router is None:
            os.environ.setdefault("LAYA_CPU_AMP", "bf16")
            import torch
            from laya import Router  # torch import alone is ~1.5 s, so only when needed

            r = Router(device="cpu")
            r.load("english")
            # bf16 weights: same rankings on eval/cases.tsv, ~1.2 GB resident instead of ~2 GB.
            for agent in r._agents.values():
                agent.model.to(torch.bfloat16)
            _router = r
            gc.collect()  # the fp32 copy
        return _router


def drop_model():
    global _router
    with _router_lock:
        if _router is None:
            return
        _router.unload()
        _router = None
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)  # hand the freed arena back to the system now
    except OSError:
        pass


# ---------------------------------------------------------------- deciding
def learned(line, cands, picks):
    """Put what you picked for this exact line first; returns (cands, times confirmed)."""
    nline = learn.norm(line)
    row = picks["lines"].get(nline, {})
    if not row:
        return cands, 0
    best = max(row, key=row.get)
    confirmed = row[best] - picks["rejects"].get(nline, 0)  # a pick you later kept rejecting was a slip
    if confirmed <= 0:
        return cands, 0
    for i, c in enumerate(cands):
        if learn.key(c) == best:
            return [c] + cands[:i] + cands[i + 1:], confirmed
    return cands, 0


def answer(mode, line, cands, picks, auto=False):
    cands, confirmed = learned(line, cands, picks)
    if confirmed:
        auto = confirmed >= (learn.AUTO_RUN if cands[0]["kind"] == "command" else learn.AUTO_OPEN)
    return {"mode": mode, "auto": auto, "candidates": cands}


def decide(line, cwd):
    picks = learn.load()
    nline = learn.norm(line)
    rejects = picks["rejects"].get(nline, 0)
    if rejects >= learn.GIVE_UP and rejects > sum(picks["lines"].get(nline, {}).values()):
        return {"candidates": []}  # you said "none of these" here before: let bash say not found

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

    # "gti status": a command with arguments -- the first word is the typo, maybe the second too.
    commands = []
    if verb is None and len(words) > 1:
        args = words[1:]
        for c in spelling(words[0], {"command"}, cwd, picks):
            fixed = fix_subcommand(c["target"], args[0].lower())
            rest = " ".join([fixed or args[0], *args[1:]])
            commands.append(slim(c, rest) | {"_sub": bool(fixed) or args[0].lower() in known_subcommands(c["target"])})
        looks_like_args = any(re.search(r"[-/.=~0-9]", a) for a in args)
        if commands and (looks_like_args or commands[0]["_sub"] or not describes_an_app(args)):
            return answer("spelling", line, [{k: v for k, v in c.items() if k != "_sub"} for c in commands], picks)
        commands = [{k: v for k, v in c.items() if k != "_sub"} for c in commands]
        kinds = {"app", "folder"}  # "web browser", "visual studo code": a phrase, not a command

    phrase = " ".join(words)
    cands = [slim(c) for c in spelling(phrase, kinds, cwd, picks)]
    if cands:
        top = cands[0]
        clear = len(cands) == 1 or top["score"] - cands[1]["score"] >= 0.08
        auto = clear and top["score"] >= 0.8 and top["kind"] != "command" and not commands
        return answer("spelling", line, (cands + commands)[:4], picks, auto)
    if "app" in kinds:
        cands = [slim(c) for c in meaning(phrase)[:3]]
        if cands:
            return answer("meaning", line, (cands + commands[:1])[:4], picks)
    if commands:
        return answer("spelling", line, commands, picks)
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
        now = time.monotonic()
        if now - last_request > IDLE_EXIT:
            os._exit(0)  # systemd's socket unit keeps listening and restarts us on demand
        if _router is not None and now - model_used > MODEL_IDLE:
            drop_model()


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
