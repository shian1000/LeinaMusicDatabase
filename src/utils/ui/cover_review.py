"""Manage database -> "Find covers": the stored songs whose titles say they're
covers (utils/database/covers.py) in one checkbox list, and Song actions -> "Set
covered artist". The list goes through songs not looked at yet (COVER_CHECKED_TAG
in tag.db)."""
import questionary

from config.constants import COVER_CHECKED_TAG

from utils.common.cover_names import cover_credit, covered_artist_name
from utils.common.output import error, review_header, say
from utils.database.covers import apply_cover_proposal, artist_named, find_covers, set_covered_artist
from utils.database.database_sessions import require_global_database_sessions, submit_global_database_session
from utils.database.datatables import Song
from utils.database.tags_management import add_tag_to_songs, song_ids_with_tag
from utils.ui.display_utils import display_songs
from utils.ui.song_colors import song_tokens


def review_covers() -> None:
    """The songs not looked at yet saying they're covers, in one checkbox
    list; those naming a stored artist are ticked. Songs only saying "cover"
    are listed for "Set covered artist" and stay unchecked."""
    session, _ = require_global_database_sessions()
    checked = song_ids_with_tag(COVER_CHECKED_TAG)
    proposals, unnamed = find_covers(session, checked)
    if proposals:
        review_header(f"{len(proposals)} song(s) are covers",
                      "Ticked ones are marked, and renamed as shown after the arrow.",
                      "Unticked: the name isn't an artist in the database yet, or is only a guess.",
                      "Songs left unticked aren't offered again.")
        choices = []
        for i, proposal in enumerate(proposals):
            title = [*song_tokens(proposal.song.artist.name, proposal.song.title), ("", "  ->  "),
                     *song_tokens(proposal.performer or proposal.song.artist.name,
                                  f"{proposal.title} {cover_credit(proposal.covered_name)}", proposal.note)]
            choices.append(questionary.Choice(title=title, value=i, checked=proposal.certain))
        picked = questionary.checkbox("Which songs do you want to mark?", choices=choices).ask()
        if picked is None:
            say("Cancelled, nothing marked as checked.")
            return
        for i in picked:
            apply_cover_proposal(session, proposals[i])
        add_tag_to_songs([proposal.song.id for proposal in proposals], COVER_CHECKED_TAG)
        submit_global_database_session()
        say(f"Marked {len(picked)} song(s)." if picked else "Nothing picked.")
    else:
        say("No new covers found by their titles.")
    if unnamed:
        say(f"{len(unnamed)} song(s) say they're covers but not whose - use Song actions -> \"Set covered artist\":")
        display_songs(unnamed)


def set_covered_artist_of_songs(songs: list[Song]) -> None:
    """Song actions -> "Set covered artist", and the song editor for one
    song. Empty answer: no change; "-": not a cover. The title stays as it is."""
    session, _ = require_global_database_sessions()
    display_songs(songs)
    current = covered_artist_name(songs[0]) if len(songs) == 1 else None
    answer = (questionary.text("Whose song is it a cover of? ('-' = not a cover, empty = cancel)",
                               default=current or "").ask() or "").strip()
    if not answer or answer == current:
        say("Aborted")
        return
    artist = None if answer == "-" else artist_named(session, answer)
    try:
        set_covered_artist(songs, artist)
    except ValueError as exc:
        session.rollback()
        error(str(exc))
        return
    submit_global_database_session()
    say(f"{len(songs)} song(s): {f'cover of {artist.name}' if artist else 'not a cover'}.")
