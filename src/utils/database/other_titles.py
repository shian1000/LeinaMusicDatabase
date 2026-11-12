"""
Other titles of a song (migration 0023, songs.other_titles): what it is
looked for by besides its title and Latin form - the title in other
languages ("Херувимская песнь" is also "Hymn of the Cherubim"), a wrong
title people know it by better than the real one, or a line of its lyrics
("Lubiła tańczyć" for "Na jednej z dzikich plaż"). One text, the titles
separated by OTHER_TITLES_SEPARATOR. Search and matching count each as one
more title (latin_names.song_titles(), database_getter), merges keep both
songs' (song_duplicates.merge_songs()). Leina Radio reads the column for
its search: docs/handoffs/other-titles-for-musicdatabaseapp.md.
"""

from config.constants import OTHER_TITLES_SEPARATOR
from utils.common.normalizer import normalize


def split_other_titles(text: str | None) -> list[str]:
    return [t.strip() for t in (text or "").split(OTHER_TITLES_SEPARATOR) if t.strip()]


def other_titles(song) -> list[str]:
    return split_other_titles(getattr(song, "other_titles", None))


def set_other_titles(song, titles) -> None:
    """Store `titles` in order, each once (by normalize()), leaving out the
    song's own title and Latin form; none = NULL. Caller commits."""
    taken = {normalize(t) for t in (song.title, song.title_latin) if t}
    kept = []
    for title in titles:
        title = (title or "").replace(OTHER_TITLES_SEPARATOR, ",").strip()
        if title and normalize(title) not in taken:
            taken.add(normalize(title))
            kept.append(title)
    song.other_titles = f"{OTHER_TITLES_SEPARATOR} ".join(kept) or None


def add_other_titles(song, *titles) -> None:
    """The song's other titles plus `titles`. Caller commits."""
    set_other_titles(song, [*other_titles(song), *titles])


def keep_other_titles_on_merge(kept, dropped) -> None:
    """Merging two songs: the kept one gets the dropped one's other titles
    too. Caller commits."""
    add_other_titles(kept, *other_titles(dropped))
