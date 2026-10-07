# Security policy

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Report them privately through
[GitHub's private vulnerability reporting](https://github.com/boss2236/laya-router/security/advisories/new)
(Security tab → "Report a vulnerability").

Include what you did, what happened, and the commit you tested (`git -C <checkout> rev-parse HEAD`).
You can expect a first reply within a week. Once a fix is out, the advisory is published with credit
to you unless you'd rather stay anonymous.

## Supported versions

Only the latest commit on `master` is supported. `laya update` brings an install up to date.

## What laya-router does, security-wise

laya-router sits in your shell and can open apps, change directory and run commands, so this is how it
is kept in bounds:

| Part | Exposure | Guard |
|---|---|---|
| Router (`server.py`) | unix socket `$XDG_RUNTIME_DIR/laya-router.sock` | socket is mode `0600` inside a `0700` runtime dir: only your user can talk to it. No network listener. |
| Running commands | a corrected line like `git push` is run in your shell | always shown in a menu first. It only runs without asking after you've confirmed **the same fix for the same typed line 3 times** (`auto_run`; lowering it to 1 is your call). The command name comes from your `$PATH` or bash builtins, the arguments are the ones you typed. |
| Hand-off to the shell | `$XDG_RUNTIME_DIR/laya-pending.<pid>` | in the private runtime dir, read and deleted by the shell right away. |
| Dashboard (`dashboard.py`) | HTTP on `127.0.0.1` only (port 7878 or a free one) | every API call needs a random per-run token (`X-Laya-Token`, compared in constant time). The token lives in the URL fragment, which browsers never send to servers, so other sites can't drive the API and it doesn't land in logs. The dashboard stops itself after 10 idle minutes. |
| Model | [convaiinnovations/laya](https://huggingface.co/convaiinnovations/laya), downloaded once at install | loaded from `safetensors` (no pickle, so loading it can't run code). The service runs with `HF_HUB_OFFLINE=1`, so it never reaches the network. Pin exact files with `LAYA_SHA256_DIGESTS` if you want. |
| Your data | `~/.local/state/laya-router/` (picks, log), `~/.config/laya-router/config.json`; reads `~/.bash_history` and `zoxide` for ranking | stays on your machine. Nothing is uploaded, and there is no telemetry. |

### The installer

`curl … | bash` runs a script from this repo as your user (never root, never `sudo`). If you'd rather
read it first:

```
curl -fsSL https://raw.githubusercontent.com/boss2236/laya-router/master/install.sh -o install.sh
less install.sh && bash install.sh
```

It installs [uv](https://docs.astral.sh/uv/) from astral.sh when missing, Python packages from PyPI
and the PyTorch CPU index, and the model from Hugging Face.

### In scope

- Anything that lets another local user or a web page talk to the router or the dashboard
- A way to make laya-router run or open something you didn't confirm (beyond the documented auto rules)
- Shell injection through file, folder, app or command names
- Installer problems (unsafe paths, overwriting files it didn't create)

### Out of scope

- Things you've explicitly configured, like `auto_run=1`
- Vulnerabilities in dependencies (torch, laya, rapidfuzz, uv). Report those upstream, though a heads-up here is welcome.
- Someone who already runs code as your user
