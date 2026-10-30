"""Manage database -> Resolve duplicates -> "Resolve duplicated songs": the
near-duplicate songs of each artist (utils/database/song_duplicates.py) in
one checkbox list, ticked except where the titles name different versions,
then one title credited to two main artists, unticked; the ticked groups are
merged into the song with the most data, and the unticked ones can be
remembered so they aren't offered again."""

import questionary

from utils.database.artist_reassignment import is_unused, remove_unused_artist
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.database.song_artists import song_artist_label
from utils.database.song_duplicates import (
    clean_title,
    credit_feat_artists,
    find_cross_artist_groups,
    find_duplicate_groups,
    is_remembered_not_duplicate,
    load_not_duplicates,
    looks_like_versions,
    merge_songs,
    missing_feat_artists,
    remember_not_duplicates,
)
from utils.ui.prompts import ask_yes_no


def _label(group: list) -> str:
    kept, *dropped = group
    others = "; ".join(f"[{s.id}] {s.title}" for s in dropped)
    result = clean_title(kept.title)
    return f"{kept.artist.name}: keep [{kept.id}] {result}  <-  {others}"


def _cross_label(group: list) -> str:
    kept, *dropped = group
    others = "; ".join(f"[{s.id}] {song_artist_label(s)} - {s.title}" for s in dropped)
    return (f"keep [{kept.id}] {song_artist_label(kept)} - {clean_title(kept.title)}  <-  {others}"
            f"  (credited to {kept.artist.name} only)")


def _ask_feat_credits(groups: list) -> dict | None:
    """For the groups whose dropped titles credit featured artists the kept
    song lacks ("APT feat. Bruno Mars" folded into "APT."), one checkbox list:
    credit them on the kept song? {id(kept song): [names]}; None on cancel."""
    offers = [(g[0], name) for g in groups for name in missing_feat_artists(g)]
    if not offers:
        return {}
    print("\nThe songs folded away credit featured artists the kept ones don't:")
    choices = [questionary.Choice(title=f"{song_artist_label(kept)} - {clean_title(kept.title)}:  + {name} (feat.)",
                                  value=n, checked=True) for n, (kept, name) in enumerate(offers)]
    picked = questionary.checkbox("Credit which on the kept song?", choices=choices).ask()
    if picked is None:
        return None
    credits = {}
    for n in picked:
        kept, name = offers[n]
        credits.setdefault(id(kept), []).append(name)
    return credits


def _offer_to_remember(unticked: list) -> None:
    if unticked and ask_yes_no(f"Remember the {len(unticked)} unselected group(s) as different songs, "
                               "so they aren't proposed again?"):
        remember_not_duplicates(unticked)
        print(f"Remembered {len(unticked)} group(s).")


def resolve_duplicated_songs() -> None:
    session, _ = open_and_set_global_database_sessions()
    same_artist = find_duplicate_groups(session)
    cross = find_cross_artist_groups(session, skip_ids={s.id for g in same_artist for s in g})
    remembered = load_not_duplicates()
    hidden = sum(is_remembered_not_duplicate(g, remembered) for g in same_artist + cross)
    same_artist = [g for g in same_artist if not is_remembered_not_duplicate(g, remembered)]
    cross = [g for g in cross if not is_remembered_not_duplicate(g, remembered)]
    groups = same_artist + cross
    if hidden:
        print(f"Left out: {hidden} group(s) you said aren't one song.")
    if not groups:
        print("No duplicate songs found.")
        return
    print(f"{len(groups)} group(s) of songs that look like one song: the same artist, titles differing only in")
    print('a "feat." credit, spelling or an annotation like "(Visualiser)". The song with the most data is')
    print("kept and gets what the others have; tags, playlists and library files follow it.")
    print("Unticked: titles naming different versions (remix, cover, live, ...) - tick them only if they aren't.")
    if cross:
        print(f"Also unticked, last: {len(cross)} title(s) credited to different main artists who share another")
        print("artist or a link - tick one only if it's the same recording; the main artist of the song kept")
        print("stays, the other one's credit goes (use Song actions -> Change artist first to keep the other).")
    choices = [questionary.Choice(title=_label(g), value=i, checked=not looks_like_versions(g))
               for i, g in enumerate(same_artist)]
    choices += [questionary.Choice(title=_cross_label(g), value=len(same_artist) + i, checked=False)
                for i, g in enumerate(cross)]
    picked = questionary.checkbox("Merge which?", choices=choices).ask()
    if picked is None:
        print("Nothing merged.")
        return
    unticked = [g for i, g in enumerate(groups) if i not in set(picked)]
    if not picked:
        print("Nothing merged.")
        _offer_to_remember(unticked)
        return

    feats = _ask_feat_credits([groups[i] for i in picked])
    if feats is None:
        print("Nothing merged.")
        return
    emptied = []
    for i in picked:
        kept, *dropped = groups[i]
        credit_feat_artists(session, kept, feats.get(id(kept), []))
        for song in dropped:
            dropped_artist = song.artist
            merge_songs(session, kept, song)
            if dropped_artist is not kept.artist:
                emptied.append((dropped_artist, kept.artist))
    removed = []
    for artist, heir in emptied:
        if artist not in removed and is_unused(session, artist):
            remove_unused_artist(session, artist, heir, keep_names=False)
            removed.append(artist)
    submit_global_database_session()
    print(f"Merged {sum(len(groups[i]) - 1 for i in picked)} duplicate song(s) in {len(picked)} group(s).")
    if removed:
        print(f"Removed {len(removed)} artist(s) left without songs: {', '.join(a.name for a in removed)}")
    _offer_to_remember(unticked)
