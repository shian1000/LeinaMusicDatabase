"""After a playlist compared with a YouTube playlist is saved: its tracks'
videos offered for their songs in one checkbox list
(utils/database/additional_videos.py) - as the song's own link when it has
none, else as an additional video, so Leina Radio recognises the video as
the song even when its title names it differently. Ticked: the certain
pairings (the same song, picked by hand); unticked: those found only by
resembling names."""

import questionary

from config.constants import ADDITIONAL_VIDEO_FOLDER_PLAYLIST
from utils.common.output import ok, review_header, say, warn
from utils.database.additional_videos import apply_video_links, plan_video_links
from utils.database.database_sessions import submit_global_database_session
from utils.database.song_artists import song_artist_label
from utils.ui.song_colors import song_tokens


def _choice_title(link) -> list:
    kind = "its own link" if link.main else "additional video"
    return song_tokens(song_artist_label(link.song), link.song.title,
                       f"{kind}: https://youtu.be/{link.video_id}")


_UNSURE_NOTE = "Unticked: pairs found by resembling names only - tick them if the video is the song."


def review_video_links(session, pairs, unticked_note: str = _UNSURE_NOTE) -> bool:
    """Ask which of these (song id, video id, sure) pairs to save, save the
    ticked ones and commit. False when there was nothing to ask about."""
    plan = plan_video_links(session, pairs)
    if not plan.links and not plan.conflicts:
        return False
    if plan.links:
        review_header(f"{len(plan.links)} YouTube video(s) of the playlist new to their songs",
                      "A song without a link gets the video as its own; a song with one keeps it and gets",
                      "the video as an additional one, which only helps Leina Radio recognise it.",
                      unticked_note)
        choices = [questionary.Choice(title=_choice_title(link), value=i, checked=link.sure)
                   for i, link in enumerate(plan.links)]
        picked = questionary.checkbox("Save which?", choices=choices).ask()
        if picked:
            main, added = apply_video_links(session, [plan.links[i] for i in picked],
                                            ADDITIONAL_VIDEO_FOLDER_PLAYLIST)
            submit_global_database_session()
            ok(f"Saved {main} link(s) and {added} additional video(s).")
        else:
            say("No video saved on the songs.")
    if plan.conflicts:
        warn(f"{len(plan.conflicts)} video(s) not offered:")
        for song, video_id, why in plan.conflicts:
            say(f"  {song_artist_label(song)} - {song.title}: https://youtu.be/{video_id} - {why}")
    return True
