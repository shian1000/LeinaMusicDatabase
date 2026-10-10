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


@dataclass
class YearDiscoveryResult:
    """Same contract as DiscoveryResult, for a module's get_release_year().

    matched_title is whatever release/recording title the module actually
    found the year on (an album title when the query was an album, a
    recording title when the query was a single's song title) - never an
    echo of the query, for the same reason as DiscoveryResult.

    needs_review marks a year that's only a guess (e.g. a YouTube upload
    date) - the caller shows it to the user instead of writing it straight
    away. source_url is what to show them alongside it.
    """
    year: int
    matched_title: Optional[str] = None
    matched_artist: Optional[str] = None
    needs_review: bool = False
    source_url: Optional[str] = None
