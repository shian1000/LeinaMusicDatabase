from dataclasses import dataclass
from typing import Optional


@dataclass
class DiscoveryResult:
    """What a discovery module found, plus what it actually matched.

    Modules that can identify which title/artist they matched against should
    report them here so discoveries_manager can independently verify the
    match instead of trusting the module's own (possibly buggy) matching
    logic. Leave a field None if the module has no reliable way to report it.
    """
    album: str
    matched_title: Optional[str] = None
    matched_artist: Optional[str] = None
