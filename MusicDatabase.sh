#!/bin/bash

# Go to the project directory (important for double-click)
cd "$(dirname "$0")"

# Double-clicking in the file manager runs this with no TTY attached,
# so the interactive menu blocks silently. Relaunch inside a terminal.
if [ ! -t 0 ]; then
    exec konsole --workdir "$(pwd)" -e "$0"
fi

# Activate virtual environment
source venv/bin/activate

# Run your script
python main.py
