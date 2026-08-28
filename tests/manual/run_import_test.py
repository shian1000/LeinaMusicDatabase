#!/usr/bin/env python3
"""Run MP3 import with timing analysis."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import time
from pathlib import Path

# Enable debug output
(Path(__file__).parent / ".debug").write_text("verbosity = 1\n")

from utils.discoveries.import_data_from_mp3_tags import import_data_from_mp3_tags

if __name__ == "__main__":
    import_dir = Path(__file__).parent / "import"
    
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
