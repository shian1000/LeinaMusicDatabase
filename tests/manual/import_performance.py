#!/usr/bin/env python3
"""Performance testing script for MP3 import."""

import sys
import time
from pathlib import Path
from datetime import datetime

# Setup path
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

# Monkey-patch to add timing information
original_time = time.time
timing_data = {
    'song_times': [],
    'current_song': None,
    'current_song_start': None,
}

from utils.common.debug import mlog, slog

def time_section(name: str):
    """Context manager for timing sections."""
    class Timer:
        def __enter__(self):
            self.start = time.time()
            return self
        
        def __exit__(self, *args):
            elapsed = time.time() - self.start
            mlog(f"⏱️  [{name}] {elapsed:.3f}s")
    
    return Timer()


from utils.discoveries.import_data_from_mp3_tags import import_data_from_mp3_tags

def main():
    """Run import with performance metrics."""
    import_dir = _ROOT / "import"
    
    print(f"\n{'='*60}")
    print(f"MP3 Import Performance Test")
    print(f"{'='*60}")
    print(f"Folder: {import_dir}")
    print(f"Files: {len(list(import_dir.glob('*.mp3')))}")
    print(f"{'='*60}\n")
    
    # Run the import with timing
    with time_section("Total Import Time"):
        results = import_data_from_mp3_tags(str(import_dir))
    
    print(f"\n{'='*60}")
    print(f"Import Complete!")
    print(f"{'='*60}")
    print(f"Added songs: {len(results) if results else 0}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
