"""Moved to musicdatabase_core (ADR-0006). This name stays for MDHS's own code."""

import sys
from pathlib import Path

# A sharing server started before the move has only this folder on sys.path
_SRC = str(Path(__file__).resolve().parents[2])
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from musicdatabase_core import listen_sync  # noqa: E402

sys.modules[__name__] = listen_sync
