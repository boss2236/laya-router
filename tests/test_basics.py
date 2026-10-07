"""Model-free checks, run by CI on every push:  python tests/test_basics.py

The full accuracy check (eval/run.py) needs your desktop apps and the model, so it runs locally.
"""
import os
import sys
import tempfile
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "config")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import learn  # noqa: E402
import server  # noqa: E402  (must not load torch: the model is lazy)


def test_model_is_lazy():
    assert "torch" not in sys.modules and server._router is None


def test_abbreviations():
    assert server.abbreviations("YouTube Music") == {"ym": "youtube music", "ytm": "youtube music", "yt": "youtube"}
    assert "vsc" in server.abbreviations("Visual Studio Code")


def test_subcommand_fix():
    assert server.fix_subcommand("git", "psuh") == "push"
    assert server.fix_subcommand("git", "comit") == "commit"
    assert server.fix_subcommand("git", "status") is None      # already right
    assert server.fix_subcommand("git", "-v") is None          # flags are left alone
    assert server.fix_subcommand("git", "zzzzzz") is None      # nothing close


def test_learning_puts_pick_first_and_goes_auto():
    a = {"kind": "app", "name": "A", "target": "a.desktop", "label": "A", "score": 0.8}
    b = {"kind": "app", "name": "B", "target": "b.desktop", "label": "B", "score": 0.8}
    r = server.answer("spelling", "xx", [a, b], learn.load())
    assert r["candidates"][0] is a and not r["auto"]
    learn.record("xx", b)
    r = server.answer("spelling", "xx", [a, b], learn.load())
    assert r["candidates"][0] is b and r["auto"]               # apps: 1 pick is enough


def test_commands_need_three_confirmations():
    c = {"kind": "command", "name": "git", "target": "git", "label": "git", "score": 0.7, "run": "git push"}
    for i in range(3):
        assert not server.answer("spelling", "gti psuh", [c], learn.load())["auto"]
        learn.record("gti psuh", c)
    assert server.answer("spelling", "gti psuh", [c], learn.load())["auto"]


def test_rejects_outweigh_a_slip_and_forget_works():
    c = {"kind": "command", "name": "cmp", "target": "cmp", "label": "cmp", "score": 0.7, "run": "cmp x"}
    learn.record("nmp x", c)
    learn.record("nmp x", None)
    learn.record("nmp x", None)
    assert server.answer("spelling", "nmp x", [c], learn.load())["auto"] is False
    assert server.decide("nmp x", tmp) == {"candidates": []}  # 2 rejects > 1 pick: stay quiet
    learn.forget("nmp x")
    assert "nmp x" not in learn.load()["lines"] and "nmp x" not in learn.load()["rejects"]


def test_config_validates():
    cfg = learn.save_config({"auto_run": "5", "meaning": False, "bogus": 1})
    assert cfg["auto_run"] == 5 and cfg["meaning"] is False and "bogus" not in cfg


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} passed")
