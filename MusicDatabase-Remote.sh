#!/bin/bash
# Runs MusicDatabase on another computer (the server) over ssh.
#   ./MusicDatabase-Remote.sh [user@host]
# Without an argument the address comes from .env: REMOTE_LAN when connected
# to the Wi-Fi named REMOTE_HOME_SSID, otherwise REMOTE_TAILSCALE (see .env.example).

# Go to the project directory (important for double-click)
cd "$(dirname "$0")"

# Double-clicking in the file manager runs this with no TTY attached,
# so the interactive menu blocks silently. Relaunch inside a terminal.
if [ ! -t 0 ]; then
    exec konsole --workdir "$(pwd)" -e "$0" "$@"
fi

# Read single keys instead of sourcing .env: its passwords may hold shell characters
env_value() {
    [ -f .env ] && sed -n "s/^$1=//p" .env | tail -n 1 | sed 's/^["'\'']//; s/["'\'']$//'
}

target="$1"
if [ -z "$target" ]; then
    home_ssid=$(env_value REMOTE_HOME_SSID)
    current_ssid=$(nmcli -t -f active,ssid dev wifi 2>/dev/null | awk -F: '$1=="yes"{print $2; exit}')
    if [ -n "$home_ssid" ] && [ "$current_ssid" = "$home_ssid" ]; then
        target=$(env_value REMOTE_LAN)
    fi
    [ -z "$target" ] && target=$(env_value REMOTE_TAILSCALE)
fi
if [ -z "$target" ]; then
    echo "No server to connect to: pass user@host, or set REMOTE_TAILSCALE (and REMOTE_LAN) in .env." >&2
    read -r -p "Press Enter to close."
    exit 1
fi

repo_dir=$(env_value REMOTE_REPO_DIR)
ssh -t "$target" "cd ${repo_dir:-~/Documents/Code/MusicDatabase} && ./MusicDatabase.sh"
