# laya-router

Mistype something in the terminal and it opens or runs what you meant:

```
❯ spotfy                    → opens Spotify
❯ cd projcts                → cd ~/Projects
❯ gti psuh                  → menu: git push / …   (↑↓ Enter, Esc cancels)
❯ music app                 → menu: Spotify / Spotube
```

It hooks bash's `command_not_found_handle`, so it only ever sees lines bash could not run.

## How it decides

| Step | What | Why |
|---|---|---|
| Spelling | edit distance (OSA) over desktop apps, folders (cwd, ~, Projects, zoxide) and `$PATH` commands; swapped letters rank first | edit distance is near-perfect at spelling; the model isn't |
| Arguments | `gti psuh -f` → fixes the command *and* a misspelled subcommand (git/systemctl/docker/npm/… plus whatever your bash history uses) | |
| Meaning | "web browser", "text editor": a shortlist of apps whose own description matches, ranked by the [Laya](https://huggingface.co/convaiinnovations/laya) decision model | the model is good at meaning, bad at letters |
| Learning | every pick / "none of these" goes to `~/.local/state/laya-router/picks.json` | stops asking once it knows |

When it acts without asking:

- an app or folder with one clear spelling match, or one you picked before for that exact line;
- a command only after you've confirmed the same fix **3 times**;
- "none of these" twice for a line (more often than you picked something) → it stays quiet for that line.

## Layout

```
server.py          the router (systemd socket-activated, exits after 15 min idle)
learn.py           picks.json: read by the server, written by the client
client/laya-fix    arrow-key menu, run from command_not_found_handle
shell/laya.bash    the bash hook (+ pay-respects `f` as fallback)
systemd/           laya-router.socket / .service
eval/              cases.tsv + run.py: the accuracy check
```

`install.sh` symlinks all of it into place (`~/.local/bin`, `~/.config/bash`, `~/.config/systemd/user`),
so editing this checkout edits the live setup. After changing `server.py`:
`systemctl --user restart laya-router.service`.

## Resources

The model is only loaded for a "meaning" question and dropped again after 3 idle minutes, in bf16.
Spelling-only use keeps the service at ~15–25 MB; with the model loaded it's ~1.2 GB.

## Checking accuracy

```
.venv/bin/python eval/run.py -v
```

Runs every line in `eval/cases.tsv` against an empty picks file. Add a line whenever it gets something wrong.
