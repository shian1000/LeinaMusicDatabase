"""The one place that cleans artist names, titles and albums before they are
stored or compared: every cleanup is a named step, run in one fixed order
for whatever a name comes from (an mp3's tags, a file name, a YouTube video,
a song request from Leina Radio, the stored catalog).

Two stages: "raw" steps work on a whole text before it is split into artist
and title (a video title: "A - B | Label #tag", a file name), "field" steps
on one field after. The splitting itself is not a step; it stays with its
source.

A step is on or off for every source at once (Settings -> "Name cleanup",
sanitize/settings.py; owner's choice 2026-11-01), so a name and the same
name already stored are cleaned alike. Where it runs follows from what it
works on (runs_for()), never from a choice; a name typed by hand ("manual")
is never changed. In the database a "review" step only offers its change:
sanitize_field() skips it there, sanitize_changes() reports it for the owner
to tick. The comparison keys (normalizer.normalize(), name_key(),
import_engine._title_core()) are not steps and never depend on these
settings.
"""
from utils.common.sanitize import settings  # noqa: F401 - Settings -> "Name cleanup"'s file
from utils.common.sanitize.text import *  # noqa: F401,F403
from utils.common.sanitize.pipeline import *  # noqa: F401,F403
