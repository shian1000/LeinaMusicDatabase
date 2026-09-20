#!/bin/bash

# Go to the project directory (important for double-click)
cd "$(dirname "$0")"

HOME_SSID="home-wifi"

current_ssid=$(nmcli -t -f active,ssid dev wifi 2>/dev/null | awk -F: '$1=="yes"{print $2; exit}')

if [ "$current_ssid" = "$HOME_SSID" ]; then
    exec ./MusicDatabase-Remote-LAN.sh
else
    exec ./MusicDatabase-Remote-Tailscale.sh
fi
