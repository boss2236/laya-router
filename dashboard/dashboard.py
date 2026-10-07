#!/usr/bin/python3
"""The Laya dashboard: a local web page over picks.json, the activity log, settings and the service.

Started (detached) by `laya dashboard`. Listens on 127.0.0.1 only, and every API call must carry
the random token from the URL, so other web pages in your browser cannot drive it.
Exits on its own after IDLE_EXIT seconds without the page asking for anything.
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import learn  # noqa: E402

IDLE_EXIT = 10 * 60
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
SOCK = os.path.join(RUNTIME, "laya-router.sock")
STATE = Path(RUNTIME) / "laya-dashboard.json"  # {"url", "pid"}: lets `laya dashboard` reuse a running one
TOKEN = secrets.token_urlsafe(18)
last_hit = time.monotonic()


def router(req, timeout=60):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCK)
        s.sendall((json.dumps(req) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        return json.loads(data)
    finally:
        s.close()


def service():
    def show(unit, props):
        out = subprocess.run(["systemctl", "--user", "show", unit, "-p", ",".join(props)],
                             capture_output=True, text=True).stdout
        return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)

    svc = show("laya-router.service", ["ActiveState", "MemoryCurrent"])
    sock = show("laya-router.socket", ["ActiveState"])
    info = {"socket": sock.get("ActiveState"), "service": svc.get("ActiveState")}
    if svc.get("ActiveState") == "active":
        try:
            info |= router({"cmd": "status"}, timeout=3)
        except (OSError, ValueError):
            pass
    return info


def log_rows(limit=300):
    try:
        rows = learn.LOG.read_text().splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for r in rows:
        try:
            out.append(json.loads(r))
        except ValueError:
            pass
    return out[::-1]


def pretty(key):
    """"app:spotify.desktop" -> "Spotify", "folder:/home/me/x" -> "~/x"."""
    kind, _, target = key.partition(":")
    if kind == "app":
        for base in (Path.home() / ".local/share/applications", Path("/usr/share/applications"),
                     Path.home() / ".local/share/flatpak/exports/share/applications",
                     Path("/var/lib/flatpak/exports/share/applications")):
            try:
                for line in (base / target).read_text(errors="ignore").splitlines():
                    if line.startswith("Name="):
                        return line[5:].strip()
            except OSError:
                continue
    if kind == "folder" and target.startswith(str(Path.home())):
        return "~" + target[len(str(Path.home())):]
    return target


def learned():
    data = learn.load()
    lines = set(data["lines"]) | set(data["rejects"])
    out = []
    for line in sorted(lines):
        row = data["lines"].get(line, {})
        best = max(row, key=row.get) if row else None
        out.append({"line": line, "to": pretty(best) if best else None,
                    "kind": best.split(":", 1)[0] if best else None,
                    "picks": row.get(best, 0) if best else 0, "rejects": data["rejects"].get(line, 0)})
    return out


def run_eval():
    py = ROOT / ".venv/bin/python"
    r = subprocess.run([str(py), str(ROOT / "eval/run.py")], capture_output=True, text=True, timeout=300,
                       env=os.environ | {"PYTHONWARNINGS": "ignore", "HF_HUB_OFFLINE": "1"})
    lines = [x for x in (r.stdout + r.stderr).splitlines() if x.startswith(("ok", "BAD")) or "right" in x]
    return {"output": "\n".join(lines)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def authed(self):
        return secrets.compare_digest(self.headers.get("X-Laya-Token", ""), TOKEN)

    def do_GET(self):
        global last_hit
        path = self.path.split("?")[0]
        if path == "/":
            return self.send(200, (ROOT / "dashboard/index.html").read_bytes(), "text/html; charset=utf-8")
        if not self.authed():
            return self.send(403, {"error": "bad token"})
        last_hit = time.monotonic()
        if path == "/api/overview":
            return self.send(200, {"service": service(), "config": learn.config(), "defaults": learn.DEFAULTS,
                                   "learned": learned(), "log": log_rows(), "repo": str(ROOT)})
        self.send(404, {"error": "no such thing"})

    def do_POST(self):
        global last_hit
        if not self.authed():
            return self.send(403, {"error": "bad token"})
        last_hit = time.monotonic()
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        path = self.path.split("?")[0]
        try:
            if path == "/api/try":
                return self.send(200, router({"line": body.get("line", ""), "cwd": str(Path.home())}))
            if path == "/api/forget":
                learn.forget(body["line"])
                return self.send(200, {"ok": True})
            if path == "/api/config":
                return self.send(200, learn.save_config(body))
            if path == "/api/restart":
                subprocess.run(["systemctl", "--user", "restart", "laya-router.service"], check=True)
                return self.send(200, {"ok": True})
            if path == "/api/eval":
                return self.send(200, run_eval())
        except Exception as e:  # show it on the page rather than a dead request
            return self.send(500, {"error": repr(e)})
        self.send(404, {"error": "no such thing"})


def main():
    port = int(os.environ.get("LAYA_DASHBOARD_PORT", "7878"))
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)  # taken: any free port
    url = f"http://127.0.0.1:{srv.server_port}/#{TOKEN}"
    STATE.write_text(json.dumps({"url": url, "pid": os.getpid()}))
    print(url, flush=True)

    def idle():
        while time.monotonic() - last_hit < IDLE_EXIT:
            time.sleep(15)
        STATE.unlink(missing_ok=True)
        os._exit(0)

    threading.Thread(target=idle, daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
