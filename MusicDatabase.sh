#!/bin/bash

# Go to the project directory (important for double-click)
cd "$(dirname "$0")"

# Activate virtual environment
source venv/bin/activate

# Run your script
python main.py
