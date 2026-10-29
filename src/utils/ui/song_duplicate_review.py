"""Manage database -> Resolve duplicates -> "Resolve duplicated songs": the
near-duplicate songs of each artist (utils/database/song_duplicates.py) in
one checkbox list, ticked except where the titles name different versions,
then one title credited to two main artists, unticked; the ticked groups are
merged into the song with the most data."""

import questionary

from utils.database.artist_reassignment import is_unused, remove_unused_artist
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.database.song_artists import song_artist_label
from utils.database.song_duplicates import (
    clean_title,
    credit_feat_artists,
    find_cross_artist_groups,
    find_duplicate_groups,
    looks_like_versions,
    merge_songs,
    missing_feat_artists,
)


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


def resolve_duplicated_songs() -> None:
    session, _ = open_and_set_global_database_sessions()
    same_artist = find_duplicate_groups(session)
    cross = find_cross_artist_groups(session, skip_ids={s.id for g in same_artist for s in g})
    groups = same_artist + cross
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
    if not picked:
        print("Nothing merged.")
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
