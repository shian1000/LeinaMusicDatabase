"""Manage database -> Resolve duplicates -> "Resolve duplicated songs": the
near-duplicate songs of each artist (utils/database/song_duplicates.py) in
one checkbox list, ticked except where the titles name different versions;
the ticked groups are merged into the song with the most data."""

import questionary

from utils.database.database_sessions import open_and_set_global_database_sessions
from utils.database.song_artists import song_artist_label
from utils.database.song_duplicates import (
    clean_title,
    credit_feat_artists,
    find_duplicate_groups,
    looks_like_versions,
    merge_songs,
    missing_feat_artists,
)
from utils.database.song_references import apply_song_id_changes


def _label(group: list) -> str:
    kept, *dropped = group
    others = "; ".join(f"[{s.id}] {s.title}" for s in dropped)
    result = clean_title(kept.title)
    return f"{kept.artist.name}: keep [{kept.id}] {result}  <-  {others}"


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
    groups = find_duplicate_groups(session)
    if not groups:
        print("No duplicate songs found.")
        return
    print(f"{len(groups)} group(s) of songs that look like one song: the same artist, titles differing only in")
    print('a "feat." credit, spelling or an annotation like "(Visualiser)". The song with the most data is')
    print("kept and gets what the others have; tags, playlists and library files follow it.")
    print("Unticked: titles naming different versions (remix, cover, live, ...) - tick them only if they aren't.")
    choices = [questionary.Choice(title=_label(g), value=i, checked=not looks_like_versions(g))
               for i, g in enumerate(groups)]
    picked = questionary.checkbox("Merge which?", choices=choices).ask()
    if not picked:
        print("Nothing merged.")
        return

    feats = _ask_feat_credits([groups[i] for i in picked])
    if feats is None:
        print("Nothing merged.")
        return
    for i in picked:
        kept, *dropped = groups[i]
        credit_feat_artists(session, kept, feats.get(id(kept), []))
        for song in dropped:
            merge_songs(session, kept, song)
    session.commit()
    apply_song_id_changes()
    print(f"Merged {sum(len(groups[i]) - 1 for i in picked)} duplicate song(s) in {len(picked)} group(s).")
