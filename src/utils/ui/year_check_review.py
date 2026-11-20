"""Fetch database data -> "Check stored years": a stored year more than
MUSICBRAINZ_REISSUE_AFTER_YEARS after the song first appeared on any release
MusicBrainz knows is a reissue's or a compilation's (très.b - "Ola" had the
year of Męskie Granie's 2013 compilation, MusicBrainz knows it from 2007) -
offered in one checkbox list, none ticked: a trial on 200 songs (2026-11-20)
gave 6, about half right - MusicBrainz also dates a live take, a later re-recording
("Re:Re:" for an anime, 2016) or a mis-dated edition earlier. A song MusicBrainz first knows later
than ours, or not at all, stays as it is: then MusicBrainz lacks the original
(Maanam's 1981 single "Cykady na Cykladach"), not we.

Goes through the songs not checked yet (YEAR_CHECKED_TAG in tag.db), at most
max-songs-per-fetch a run, one request each. Versions, a project's recordings
and full albums are left out (their titles would find the original's
recordings); a song MusicBrainz couldn't be asked about stays unchecked.
"""

from dataclasses import dataclass

from config.constants import MUSICBRAINZ_REISSUE_AFTER_YEARS, SONG_KIND_FULL_ALBUM, YEAR_CHECKED_TAG
from utils.common.debug import flog
from utils.common.output import escape, review_header, show
from utils.database.database_sessions import require_global_database_sessions, submit_global_database_session
from utils.database.datatables import Artist, Song
from utils.database.fetch_data_settings import load_max_songs_per_fetch
from utils.database.tags_management import add_tag_to_songs, song_ids_with_tag
from utils.discoveries.discovery_modules.music_brainz_fetcher import first_appearance_year
from utils.ui.prompts import decided, stop_requested, tick_items


@dataclass
class YearProposal:
    song: Song
    first_year: int

    def label(self) -> str:
        album = f" [{self.song.album}]" if self.song.album else ""
        return (f"{self.song.artist.name} - {self.song.title}{album}:  {self.song.year}  ->  {self.first_year}"
                f"  (MusicBrainz's first release)")


def songs_to_check(session, checked: set) -> list[Song]:
    """Songs with a year not checked yet, by artist then title."""
    songs = (session.query(Song).join(Song.artist).filter(Song.year.isnot(None))
             .order_by(Artist.name, Song.title).all())
    return [song for song in songs if song.id not in checked and not song.version_kind
            and song.project_id is None and song.kind != SONG_KIND_FULL_ALBUM]


def check_stored_years() -> int:
    """Returns how many years were changed."""
    session, _ = require_global_database_sessions()
    songs = songs_to_check(session, song_ids_with_tag(YEAR_CHECKED_TAG))
    if not songs:
        show("Every song's year has been checked.")
        return 0
    max_songs = load_max_songs_per_fetch()
    show(f"{len(songs)} song(s) with a year not checked yet" +
         (f" - checking the first {max_songs} (see Settings to change this)." if len(songs) > max_songs else "."))
    proposals, looked_at = [], []
    try:
        for position, song in enumerate(songs[:max_songs], 1):
            if stop_requested():
                break
            try:
                first = first_appearance_year(song.artist.name, song.title, raise_errors=True)
            except Exception as exc:  # no network, MusicBrainz busy: asked again next time
                flog(f"[Check stored years] couldn't ask about '{song.artist.name} - {song.title}': {exc}")
                show(f"[{position}] {escape(song.artist.name)} - {escape(song.title)}: [red]MusicBrainz didn't "
                     f"answer[/red]")
                continue
            looked_at.append(song)
            if first is not None and song.year > first + MUSICBRAINZ_REISSUE_AFTER_YEARS:
                proposals.append(YearProposal(song, first))
                show(f"[{position}] {escape(song.artist.name)} - {escape(song.title)}: [yellow]{song.year} -> "
                     f"{first}[/yellow]")
    except KeyboardInterrupt:
        show("\nStopped - the songs checked so far are kept.")
    asked = {proposal.song.id for proposal in proposals}
    picked = []
    if proposals:
        review_header(f"REVIEW: {len(proposals)} year(s) later than the song's first release",
                      "MusicBrainz knows these songs from an earlier release: the stored year is most likely "
                      "a reissue's or a compilation's.",
                      "None ticked: MusicBrainz is sometimes wrong too - tick the ones to change; songs left "
                      "unticked aren't offered again.")
        picked = tick_items("Tick the years to change (space toggles, enter confirms):", proposals,
                            YearProposal.label, checked=False)
        if picked is None:
            show("Aborted, no year changed; the songs without a question are marked as checked.")
            picked, proposals = [], []
    for proposal in picked:
        proposal.song.year = proposal.first_year
    done = [song.id for song in looked_at if song.id not in asked] + \
        [proposal.song.id for proposal in decided(proposals, picked)]
    add_tag_to_songs(done, YEAR_CHECKED_TAG)
    submit_global_database_session()
    show(f"Checked {len(looked_at)} song(s), changed {len(picked)} year(s).")
    return len(picked)
