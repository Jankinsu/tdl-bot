#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
purge=0
yes=0
for arg in "$@"; do
  case "$arg" in
    --purge) purge=1 ;;
    --yes) yes=1 ;;
    -h|--help)
      echo 'Usage: bash scripts/uninstall.sh [--purge] [--yes]'
      echo 'Default removes user service only. Purge also removes local bot code/config/queue/logs.'
      echo 'Never deletes tdl login, downloaded media, mounted drives, or GitHub remote.'
      exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
[[ "$(id -u)" -ne 0 ]] || { echo 'Run as original ordinary user, never sudo/root.' >&2; exit 1; }
repo="$HOME/apps/tdl-bot"
config="$HOME/.config/tdl-bot"
state="$HOME/.local/state/tdl-bot"
units="$HOME/.config/systemd/user"
unit="$units/tdl-bot.service"
for path in "$repo" "$config" "$state" "$units" "$unit"; do
  [[ ! -L "$path" ]] || { echo "Refusing symlinked path $path" >&2; exit 1; }
done
if [[ "$purge" -eq 1 && "$yes" -ne 1 ]]; then
  [[ -t 0 ]] || { echo 'Noninteractive purge requires --purge --yes.' >&2; exit 1; }
  echo 'Purge destroys local tdl-bot code, Bot token, allowed user ID, settings, SQLite queue and logs:'
  printf '  %s\n' "$repo" "$config" "$state"
  echo 'Downloaded media and the separate tdl login are untouched.'
  read -r -p 'Type DELETE tdl-bot to confirm: ' response
  [[ "$response" == 'DELETE tdl-bot' ]] || { echo 'Cancelled, nothing removed.'; exit 1; }
fi
if command -v systemctl >/dev/null; then
  systemctl --user disable --now tdl-bot.service 2>/dev/null || true
  systemctl --user stop tdl-bot.service 2>/dev/null || true
fi
rm -f -- "$unit"
if command -v systemctl >/dev/null; then
  systemctl --user daemon-reload
  systemctl --user reset-failed tdl-bot.service 2>/dev/null || true
fi
echo 'Stopped and removed tdl-bot user service.'
if [[ "$purge" -eq 1 ]]; then
  cd "$HOME"
  rm -rf -- "$repo" "$config" "$state"
  for backup in "$units"/tdl-bot.service.backup.*; do
    [[ ! -L "$backup" && -f "$backup" ]] && rm -f -- "$backup"
  done
  echo 'Removed local code, bot config, SQLite queue and logs.'
else
  echo 'Preserved code, secrets, queue, logs and tdl session for reinstall.'
fi
echo 'Unchanged: downloaded media, tdl binary/login, Throne, tg-auto, QingLong, system packages, linger, GitHub and journal.'
