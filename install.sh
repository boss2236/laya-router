#!/usr/bin/env bash
# Install / reinstall the Laya typo router from this checkout. Safe to re-run.
# Everything installed is a symlink back here, so editing the repo is editing the live setup.
set -euo pipefail
cd "$(dirname "$(realpath "$0")")"
REPO=$PWD

command -v uv >/dev/null || { echo "needs uv (https://docs.astral.sh/uv/)"; exit 1; }
uv sync --quiet   # CPU-only torch + laya + rapidfuzz into .venv (~1 GB first time)

mkdir -p ~/.local/bin ~/.config/bash ~/.config/systemd/user
ln -sfn "$REPO/client/laya-fix" ~/.local/bin/laya-fix
ln -sfn "$REPO/shell/laya.bash" ~/.config/bash/laya.bash
ln -sfn "$REPO/systemd/laya-router.socket" ~/.config/systemd/user/laya-router.socket
ln -sfn "$REPO/systemd/laya-router.service" ~/.config/systemd/user/laya-router.service

grep -q 'config/bash/laya.bash' ~/.bashrc 2>/dev/null ||
  printf '\n# Laya typo router\n[[ -f ~/.config/bash/laya.bash ]] && source ~/.config/bash/laya.bash\n' >> ~/.bashrc

systemctl --user daemon-reload
systemctl --user enable --now laya-router.socket
systemctl --user restart laya-router.service 2>/dev/null || true   # pick up a new server.py

# The Laya checkpoint (~800 MB) is fetched once; the service itself runs offline.
.venv/bin/python -c 'from laya import Router; Router(device="cpu").load("english")' >/dev/null 2>&1
echo "installed. open a new terminal and mistype something (e.g. spotfy)."
