#!/usr/bin/env python3
"""Run MP3 import with timing analysis."""

import sys
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

import time

# Enable debug output
(_ROOT / ".debug").write_text("verbosity = 1\n")

from utils.discoveries.import_data_from_mp3_tags import import_data_from_mp3_tags

if __name__ == "__main__":
    import_dir = _ROOT / "import"
    
    print("\n" + "="*70)
    print("STARTING MP3 IMPORT WITH PERFORMANCE ANALYSIS")
    print("="*70)
    
    overall_start = time.time()
    results = import_data_from_mp3_tags(str(import_dir))
    overall_time = time.time() - overall_start
    
    print("\n" + "="*70)
    print(f"OVERALL IMPORT TIME: {overall_time:.3f}s")
    print(f"Songs added: {len(results) if results else 0}")
    print("="*70)
