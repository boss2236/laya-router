# Contributing

Bug reports, typo cases and pull requests are welcome.

## The fastest useful contribution: a test typo

If laya-router got something wrong, add a line to [`eval/cases.tsv`](eval/cases.tsv):

```
<what you typed><TAB><what it should become>
```

Use the app name, folder name, or the full corrected command line (`git push`). Use `a|b` if either
answer is fine, and `-` if it should stay quiet. Then open an issue or PR. The dashboard's
"Typos it had no answer for" list is a good source.

## Working on the code

```
git clone https://github.com/boss2236/laya-router && cd laya-router
./install.sh                      # symlinks this checkout into place
.venv/bin/python eval/run.py -v   # accuracy check: must stay at 100% of cases
laya restart                      # after editing server.py
```

Ground rules:

- **Spelling stays edit distance; the model is for meaning.** Laya is bad at letter-level matching,
  and edit distance is near-perfect at it. See the table in the README.
- **The idle service stays small.** Don't import torch or laya at module level in `server.py`.
- **`client/laya-fix`, `client/laya`, `learn.py` and `dashboard/` use only the standard library.** They run on
  the system Python, before the venv exists.
- **Commands never run unconfirmed by default.** Anything that changes when the router acts on its own
  needs a note in [SECURITY.md](SECURITY.md).
- Match the surrounding style: short comments that say *why*, no new dependencies without a reason.

## Security issues

Not in public issues, please: see [SECURITY.md](SECURITY.md).

## License

By contributing you agree your work is released under the [AGPL-3.0-or-later](LICENSE).
