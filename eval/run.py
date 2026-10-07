"""Score the router on eval/cases.tsv:  .venv/bin/python eval/run.py [-v]

Runs with an empty picks.json so learning never flatters the score.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(ROOT))
import server  # noqa: E402

verbose = "-v" in sys.argv
ok = total = 0
for raw in (ROOT / "eval/cases.tsv").read_text().splitlines():
    if not raw.strip() or raw.startswith("#"):
        continue
    line, want = raw.split("\t")
    t = time.time()
    r = server.decide(line, str(Path.home()))
    c = r["candidates"]
    got = "-" if not c else c[0].get("run") or c[0]["name"]
    good = got in want.split("|")
    ok += good
    total += 1
    if verbose or not good:
        alts = ", ".join(x.get("run") or x["name"] for x in c[1:])
        print(f"{'ok ' if good else 'BAD'} {line!r:34} -> {got!r}{' [auto]' if r.get('auto') else ''}"
              f"  (want {want}){'  also: ' + alts if alts else ''}  {time.time() - t:.2f}s")
print(f"\n{ok}/{total} right")
