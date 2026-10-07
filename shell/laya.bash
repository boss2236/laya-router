# Laya typo router: a mistyped command becomes the app / folder / command you meant.
# Source: ~/Projects/hobby/laya-router (server.py, client/laya-fix); systemd --user laya-router.socket

# pay-respects: `f` fixes the last failed command (git comit -> git commit, missing sudo, ...).
# Its own command-not-found handler is kept as the fallback for anything Laya passes on.
if command -v pay-respects >/dev/null; then
  eval "$(pay-respects bash --alias f)"
  if declare -F command_not_found_handle >/dev/null; then
    eval "_laya_fallback_cnf() $(declare -f command_not_found_handle | tail -n +2)"
  fi
fi

command_not_found_handle() {
  # Runs in a subshell, so laya-fix leaves cd / corrected commands in a file for _laya_pending.
  if [[ -t 0 && -t 1 && -x ~/.local/bin/laya-fix ]]; then
    ~/.local/bin/laya-fix "$$" "$PWD" "$@"
    [[ $? -ne 10 ]] && return 0
  fi
  if declare -F _laya_fallback_cnf >/dev/null; then
    _laya_fallback_cnf "$@"
    return
  fi
  printf 'bash: %s: command not found\n' "$1" >&2
  return 127
}

# Runs before each prompt: carry out what laya-fix chose, in this shell (so cd sticks).
_laya_pending() {
  local f="${XDG_RUNTIME_DIR:-/run/user/$UID}/laya-pending.$$"
  [[ -f $f ]] || return 0
  local cmd
  cmd=$(<"$f")
  rm -f -- "$f"
  history -s -- "$cmd"
  eval -- "$cmd"
}
[[ " ${PROMPT_COMMAND[*]} " == *" _laya_pending "* ]] || PROMPT_COMMAND=(_laya_pending "${PROMPT_COMMAND[@]}")
