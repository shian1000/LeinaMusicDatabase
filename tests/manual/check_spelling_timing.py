#!/usr/bin/env python3
"""Test check_spelling performance."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import time
from pathlib import Path

# Enable debug
(Path(__file__).parent / ".debug").write_text("verbosity = 0\n")

from utils.common.text_utils import check_spelling

# Test with a few songs
test_cases = [
    ("Daft Punk", "Harder, Better, Faster, Stronger"),
    ("Chrono Cross Music", "Radical Dreamers - Unstolen Jewel"),
    ("Dawid Podsiadło", "POST"),
]

print("Testing check_spelling() performance...")
print("=" * 70)

for artist, title in test_cases:
    print(f"\nTesting: {artist} - {title}")
    start = time.time()
    try:
        result = check_spelling(artist, title)
        elapsed = time.time() - start
        print(f"  ✓ Result: {result['found']} | Time: {elapsed:.3f}s")
        if result['found']:
            print(f"    Corrected: {result['corrected']}")
            print(f"    Artist sim: {result['artist_similarity']}")
            print(f"    Title sim: {result['title_similarity']}")
    except Exception as e:
        elapsed = time.time() - start
        print(f"  ✗ Error after {elapsed:.3f}s: {e}")

print("\n" + "=" * 70)
