#!/bin/bash

# Go to the project directory (important for double-click)
cd "$(dirname "$0")"
script="$(pwd)/$(basename "$0")"

# Double-clicking in the file manager runs this with no TTY attached,
# so the interactive menu blocks silently. Relaunch inside a terminal:
# the first one installed (Debian's alternatives entry first).
if [ ! -t 0 ]; then
    for terminal in x-terminal-emulator gnome-terminal konsole xfce4-terminal xterm; do
        if command -v "$terminal" >/dev/null 2>&1; then
            case "$terminal" in
                gnome-terminal) exec gnome-terminal --working-directory="$(pwd)" -- "$script" ;;
                konsole) exec konsole --workdir "$(pwd)" -e "$script" ;;
                xfce4-terminal) exec xfce4-terminal --working-directory="$(pwd)" -x "$script" ;;
                # x-terminal-emulator may be any of them; -e is the one option they all take
                *) exec "$terminal" -e "$script" ;;
            esac
        fi
    done
    message="No terminal emulator found (tried x-terminal-emulator, gnome-terminal, konsole, xfce4-terminal, xterm). Run ./MusicDatabase.sh from a terminal."
    command -v notify-send >/dev/null 2>&1 && notify-send "MusicDatabase" "$message"
    echo "$message" >&2
    exit 1
fi

if [ ! -x venv/bin/python ]; then
    echo "No venv here yet - see README.md -> Install (python3.13 -m venv venv, then pip install -r requirements.txt)." >&2
    read -r -p "Press Enter to close." _
    exit 1
fi

# Activate virtual environment
source venv/bin/activate

# Run your script
python main.py
