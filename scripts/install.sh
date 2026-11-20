#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
# Same user-level installation pattern as tg-auto; never silently restart downloads.
enable=0
restart=0
for arg in "$@"; do
  case "$arg" in
    --enable-service) enable=1 ;;
    --restart) restart=1 ;;
    -h|--help) echo 'Usage: bash scripts/install.sh [--enable-service] [--restart]'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
[[ "$(id -u)" -ne 0 ]] || { echo 'Run as ordinary tdl user, never sudo/root.' >&2; exit 1; }
for cmd in python3 systemctl; do
  command -v "$cmd" >/dev/null || { echo "Missing $cmd" >&2; exit 1; }
done
repo="$HOME/apps/tdl-bot"
config="$HOME/.config/tdl-bot"
state="$HOME/.local/state/tdl-bot"
units="$HOME/.config/systemd/user"
unit="$units/tdl-bot.service"
[[ -f "$repo/bot.py" && -f "$repo/tdl-bot.service" && -f "$repo/settings.env.example" ]] || {
  echo "Clone repository to $repo first." >&2; exit 1;
}
for path in "$repo" "$config" "$state" "$units"; do
  [[ ! -L "$path" ]] || { echo "Refusing symlinked directory $path" >&2; exit 1; }
done
mkdir -p "$config" "$state" "$units"
chmod 700 "$config" "$state"
if [[ ! -e "$config/settings.env" ]]; then
  cp "$repo/settings.env.example" "$config/settings.env"
  echo 'Created settings.env; review media directory, required mount and proxy.'
fi
for name in settings.env token allowed_user_id; do
  [[ ! -L "$config/$name" ]] || { echo "Refusing symlinked config $name" >&2; exit 1; }
done
chmod 600 "$config/settings.env"
if [[ ! -e "$config/token" ]]; then
  [[ -t 0 ]] || { echo 'Missing BotFather token; rerun interactively.' >&2; exit 1; }
  read -r -s -p 'BotFather bot token (hidden): ' value
  printf '\n'
  [[ "$value" =~ ^[0-9]+:[a-zA-Z0-9_-]+$ ]] || { echo 'Token format invalid.' >&2; exit 1; }
  printf '%s\n' "$value" > "$config/token"
  unset value
fi
if [[ ! -e "$config/allowed_user_id" ]]; then
  [[ -t 0 ]] || { echo 'Missing allowed user ID; rerun interactively.' >&2; exit 1; }
  read -r -p 'Allowed Telegram personal user ID: ' value
  [[ "$value" =~ ^[0-9]+$ ]] || { echo 'User ID must be numeric.' >&2; exit 1; }
  printf '%s\n' "$value" > "$config/allowed_user_id"
  unset value
fi
chmod 600 "$config/token" "$config/allowed_user_id"
[[ -s "$config/token" && -s "$config/allowed_user_id" ]] || { echo 'Credentials empty.' >&2; exit 1; }
grep -Eq '^[0-9]+$' "$config/allowed_user_id" || { echo 'allowed_user_id invalid.' >&2; exit 1; }
tdl_bin=$(sed -n 's/^TDL_BOT_TDL_BIN=//p' "$config/settings.env" | tail -n1 | tr -d '\r"')
tdl_bin=$(printf '%s' "$tdl_bin" | sed 's/^ *//;s/ *$//')
if [[ -z "$tdl_bin" ]]; then tdl_bin=/usr/local/bin/tdl; fi
[[ "$tdl_bin" = /* && -x "$tdl_bin" ]] || { echo "tdl not executable: $tdl_bin" >&2; exit 1; }
python3 -m py_compile "$repo/bot.py"
[[ ! -L "$unit" ]] || { echo 'Refusing symlinked service unit.' >&2; exit 1; }
if [[ -f "$unit" ]] && ! cmp -s "$repo/tdl-bot.service" "$unit"; then
  backup=$(mktemp "$unit.backup.XXXXXXXX")
  cp -p "$unit" "$backup"
  echo "Backed up old service unit: $backup"
fi
cp "$repo/tdl-bot.service" "$unit"
chmod 644 "$unit"
systemctl --user daemon-reload
echo 'Installed user service; config, session, queue and media retained.'
if [[ "$enable" -eq 1 ]]; then
  systemctl --user enable --now tdl-bot.service
elif [[ "$restart" -eq 1 ]]; then
  if systemctl --user is-active --quiet tdl-bot.service; then
    echo 'Restarting active service (this may interrupt a download).'
    systemctl --user restart tdl-bot.service
  fi
else
  echo 'No restart/start requested; to enable: systemctl --user enable --now tdl-bot.service'
fi
echo 'Inspect: systemctl --user status tdl-bot.service --no-pager'
