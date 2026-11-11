"""Manage database -> "Find versions": the stored songs whose titles say they're
versions (utils/database/versions.py) in one checkbox list, and Song actions ->
"Set version". The list goes through songs not looked at yet (VERSION_CHECKED_TAG
in tag.db)."""
import questionary

from config.constants import VERSION_CHECKED_TAG, VERSION_KIND_LABELS
from utils.common.output import error, review_header, say
from utils.common.version_names import read_version, title_with_version
from utils.database.database_sessions import require_global_database_sessions, submit_global_database_session
from utils.database.datatables import Song
from utils.database.tags_management import add_tag_to_songs, song_ids_with_tag
from utils.database.versions import apply_version_proposal, find_versions, original_of, set_version
from utils.ui.display_utils import display_songs
from utils.ui.prompts import ask_yes_no, tick_items
from utils.ui.song_colors import song_tokens


def _marker(kind: str, label: str | None) -> str:
    return f"({label or VERSION_KIND_LABELS[kind]})"


def _proposal_title(proposal) -> list:
    original = proposal.original
    note = f"{VERSION_KIND_LABELS[proposal.kind].lower()} of #{original.id} {original.title}" if original \
        else f"{VERSION_KIND_LABELS[proposal.kind].lower()}, no original in the database"
    return [*song_tokens(proposal.song.artist.name, proposal.song.title), ("", "  ->  "),
            *song_tokens(proposal.song.artist.name, f"{proposal.title} {_marker(proposal.kind, proposal.label)}",
                         note)]


def review_versions() -> None:
    """The songs not looked at yet whose titles say they're versions, in one
    checkbox list; those naming a clear kind are ticked. Ticked ones are
    marked, their title loses the marker; all of them aren't offered again."""
    session, _ = require_global_database_sessions()
    proposals = find_versions(session, song_ids_with_tag(VERSION_CHECKED_TAG))
    if not proposals:
        say("No new versions found by their titles.")
        return
    review_header(f"{len(proposals)} song(s) are versions of a song (live, remix, acoustic...)",
                  "Ticked ones are marked, and renamed as shown after the arrow - shown with the marker.",
                  "Unticked: the title only says \"version\" - which one is a guess.",
                  "Songs left unticked aren't offered again; Song actions -> \"Set version\" marks one by hand.")
    picked = tick_items("Which songs do you want to mark?", proposals, _proposal_title,
                        checked=lambda proposal: proposal.certain)
    if picked is None:
        say("Cancelled, nothing marked as checked.")
        return
    for proposal in picked:
        apply_version_proposal(session, proposal)
    add_tag_to_songs([proposal.song.id for proposal in proposals], VERSION_CHECKED_TAG)
    submit_global_database_session()
    say(f"Marked {len(picked)} song(s)." if picked else "Nothing picked.")


def _ask_original(session, song: Song, title: str) -> Song | None | bool:
    """The original `song` is a version of: the one the artist has under
    `title`, if the owner agrees, else a song id typed in. False: cancelled."""
    if (found := original_of(session, song, title)) is not None:
        answer = ask_yes_no(f"A version of #{found.id} {found.artist.name} - {found.title}?")
        if answer is None:
            return False
        if answer:
            return found
    typed = questionary.text("Id of the original song (empty = not in the database)").ask()
    if typed is None:
        return False
    if not typed.strip():
        return None
    original = session.get(Song, int(typed)) if typed.strip().isdigit() else None
    if original is None:
        error(f"No song with id '{typed.strip()}'.")
        return False
    return original


def set_version_of_songs(songs: list[Song]) -> None:
    """Song actions -> "Set version", and the song editor for one song: the
    kind, the marker shown and the original. A title still holding the marker
    loses it."""
    session, _ = require_global_database_sessions()
    display_songs(songs)
    first = songs[0]
    reading = read_version(first.title) if len(songs) == 1 else None
    current = first.version_kind or (reading.kind if reading else None)
    choices = [questionary.Choice(label, value=kind) for kind, label in VERSION_KIND_LABELS.items()]
    choices += [questionary.Choice("Not a version", value="-"), questionary.Choice("Cancel", value=None)]
    kind = questionary.select("What kind of version is it?", choices=choices,
                              default=next((c for c in choices if c.value == current), None)).ask()
    if kind is None:
        say("Aborted")
        return
    if kind == "-":
        set_version(songs, None)
        submit_global_database_session()
        say(f"{len(songs)} song(s): not a version.")
        return
    shown = first.version_label or (reading.label if reading else None) or ""
    label = questionary.text(f"Marker to show (empty = '{VERSION_KIND_LABELS[kind]}')", default=shown).ask()
    if label is None:
        say("Aborted")
        return
    title = reading.title if reading else first.title
    original = _ask_original(session, first, title) if len(songs) == 1 else first.original
    if original is False:
        say("Aborted")
        return
    try:
        set_version(songs, kind, label.strip() or None, original)
    except ValueError as exc:
        session.rollback()
        error(str(exc))
        return
    if reading:
        first.title = reading.title
    submit_global_database_session()
    say(f"{len(songs)} song(s): {', '.join(title_with_version(song) for song in songs)}"
        f"{f' - a version of #{original.id}' if original else ''}.")

