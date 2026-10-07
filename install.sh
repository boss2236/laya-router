#!/usr/bin/env bash
# Laya typo router installer. Safe to re-run; `--uninstall` removes it again.
#
#   curl -fsSL https://raw.githubusercontent.com/boss2236/laya-router/master/install.sh | bash
#
# Piped from curl it clones the repo first (to an existing install's checkout, else
# $LAYA_HOME, default ~/.local/share/laya-router), then runs the copy in the checkout.
# Everything installed is a symlink back into the checkout, so editing it edits the live setup.
set -euo pipefail

REPO_URL=${LAYA_REPO:-https://github.com/boss2236/laya-router.git}
REPO_SLUG=boss2236/laya-router
B=$'\033[1m' C=$'\033[36m' G=$'\033[32m' Y=$'\033[33m' D=$'\033[2m' R=$'\033[0m'
step() { printf '%s›%s %s\n' "$C" "$R" "$*"; }
ok() { printf '%s✓%s %s\n' "$G" "$R" "$*"; }
die() { printf '%s✗ %s%s\n' "$Y" "$*" "$R" >&2; exit 1; }

# ------------------------------------------------------------------ where is the checkout?
here=""
if [[ -n ${BASH_SOURCE[0]:-} && -f ${BASH_SOURCE[0]} ]]; then
  here=$(cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")" && pwd)
fi
if [[ -z $here || ! -f $here/server.py ]]; then
  # Running from a pipe: find or fetch a checkout, then hand over to its install.sh.
  if [[ -L ~/.local/bin/laya ]]; then
    dest=$(cd "$(dirname "$(realpath ~/.local/bin/laya)")/.." && pwd)   # already installed: reuse it
  else
    dest=${LAYA_HOME:-$HOME/.local/share/laya-router}
  fi
  command -v git >/dev/null || die "needs git"
  if [[ -d $dest/.git ]]; then
    step "updating $dest"
    git -C "$dest" pull --ff-only -q
  else
    if [[ -e $dest ]]; then
      mv "$dest" "$dest.old-$(date +%Y%m%d%H%M%S)"
      step "moved an old non-git $dest out of the way"
    fi
    step "cloning into $dest"
    mkdir -p "$(dirname "$dest")"
    GIT_TERMINAL_PROMPT=0 git clone -q "$REPO_URL" "$dest" 2>/dev/null ||
      { command -v gh >/dev/null && gh repo clone "$REPO_SLUG" "$dest" -- -q; } ||
      die "couldn't clone $REPO_URL (private repo? log in with: gh auth login)"
  fi
  exec bash "$dest/install.sh" "$@"
fi
cd "$here"
REPO=$here

LINKS=(
  "client/laya-fix:$HOME/.local/bin/laya-fix"
  "client/laya:$HOME/.local/bin/laya"
  "shell/laya.bash:$HOME/.config/bash/laya.bash"
  "systemd/laya-router.socket:$HOME/.config/systemd/user/laya-router.socket"
  "systemd/laya-router.service:$HOME/.config/systemd/user/laya-router.service"
)
DESKTOP=~/.local/share/applications/laya-dashboard.desktop
BASHRC_LINE='[[ -f ~/.config/bash/laya.bash ]] && source ~/.config/bash/laya.bash'

# ------------------------------------------------------------------ uninstall
if [[ ${1:-} == --uninstall ]]; then
  systemctl --user disable --now laya-router.socket laya-router.service 2>/dev/null || true
  for pair in "${LINKS[@]}"; do
    dst=${pair#*:}
    if [[ -L $dst && $(realpath "$dst") == "$REPO"/* ]]; then rm -f -- "$dst"; fi
  done
  rm -f -- "$DESKTOP"
  sed -i '/^# Laya typo router$/d; \#config/bash/laya.bash#d' ~/.bashrc 2>/dev/null || true
  systemctl --user daemon-reload
  ok "uninstalled. Kept: the code ($REPO) and what it learned (~/.local/state/laya-router)."
  exit 0
fi

# ------------------------------------------------------------------ install
printf '\n%slaya%s typo router\n\n' "$B$C" "$R"
command -v python3 >/dev/null || die "needs python3"
command -v systemctl >/dev/null || die "needs systemd (user services)"

if ! command -v uv >/dev/null && [[ ! -x ~/.local/bin/uv ]]; then
  step "installing uv (Python package manager, from astral.sh)"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
fi
UV=$(command -v uv || echo ~/.local/bin/uv)

step "Python environment (CPU-only torch + Laya; ~1 GB the first time)"
"$UV" sync --quiet
ok "environment ready"

step "linking into place"
mkdir -p ~/.local/bin ~/.config/bash ~/.config/systemd/user ~/.local/share/applications
for pair in "${LINKS[@]}"; do
  ln -sfn "$REPO/${pair%%:*}" "${pair#*:}"
done
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Laya Dashboard
Comment=What the typo router fixed, learned and missed
Exec=$HOME/.local/bin/laya dashboard
Icon=utilities-terminal
Categories=Utility;
Keywords=laya;typo;terminal;
EOF
grep -qF 'config/bash/laya.bash' ~/.bashrc 2>/dev/null || printf '\n# Laya typo router\n%s\n' "$BASHRC_LINE" >> ~/.bashrc
ok "laya, laya-fix, bash hook, systemd units, Laya Dashboard app"

step "starting the router"
systemctl --user daemon-reload
systemctl --user enable --now laya-router.socket >/dev/null 2>&1
systemctl --user restart laya-router.service 2>/dev/null || true   # pick up a new server.py
ok "router listening (wakes on the first typo)"

step "fetching the Laya model (~800 MB, once)"
.venv/bin/python -c 'from laya import Router; Router(device="cpu").load("english")' >/dev/null 2>&1 &&
  ok "model cached" || printf '%s! model download failed; it will retry on first use%s\n' "$Y" "$R"

case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) printf '%s! add ~/.local/bin to your PATH%s\n' "$Y" "$R" ;; esac
printf '\n%sDone.%s Open a new terminal and mistype something (e.g. %sspotfy%s).\n' "$G" "$R" "$B" "$R"
printf '  %slaya help%s          all commands\n  %slaya dashboard%s     the dashboard\n\n' "$B" "$R" "$B" "$R"
