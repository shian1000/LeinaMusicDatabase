import json
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.youtube import manage_youtube_playlists as m


class FakeListRequest:
    def __init__(self, items):
        self._items = items

    def execute(self):
        return {"items": self._items}


class FakeSearchResource:
    def __init__(self, captured, items):
        self._captured = captured
        self._items = items

    def list(self, **kwargs):
        self._captured.update(kwargs)
        return FakeListRequest(self._items)


class FakeYoutube:
    """Stands in for the googleapiclient youtube service: youtube.search().list(**kw).execute()."""

    def __init__(self, items):
        self.captured = {}
        self._items = items

    def search(self):
        return FakeSearchResource(self.captured, self._items)


# Real case: YouTube's search backend returns zero results for short queries
# matching a "<name> Sex ..." pattern (verified against both yt-dlp's anonymous
# scraping and, prior to this fix, the unauthenticated-default Data API call)
# even though the video itself is ordinary, unrestricted music content. Confirmed
# this isn't specific to "Berlin": "Madonna Sex audio", "Paris Sex audio", and
# "London Sex audio" all hit the same zero-result wall, while longer titles that
# merely contain "sex" among other words (e.g. "Let's Talk About Sex") are
# unaffected. The API's `safeSearch` param defaults to "moderate"; passing
# "none" is the documented way to stop it from suppressing legitimate results.
BERLIN_SEX_ARTIST = "Berlin"
BERLIN_SEX_TITLE = "Sex (I'm A...)"
BERLIN_SEX_VIDEO_ID = "R_H_w0_-GSQ"


def test_search_video_disables_safe_search_on_api_fallback(monkeypatch):
    # yt-dlp exhausted first and (per the real bug) came back empty.
    monkeypatch.setattr(m, "search_video_ytdlp", lambda artist, title, max_results=8: None)

    items = [
        {
            "id": {"videoId": BERLIN_SEX_VIDEO_ID},
            "snippet": {"title": "Berlin - Sex (I'm A...) [Official Audio]", "channelTitle": "Berlin - Topic"},
        }
    ]
    youtube = FakeYoutube(items)

    video_id = m.search_video(youtube, BERLIN_SEX_ARTIST, BERLIN_SEX_TITLE)

    assert video_id == BERLIN_SEX_VIDEO_ID
    assert youtube.captured.get("safeSearch") == "none"


def test_search_video_cached_disables_safe_search(monkeypatch):
    monkeypatch.setattr(m, "save_cache", lambda cache: None)

    items = [
        {
            "id": {"videoId": BERLIN_SEX_VIDEO_ID},
            "snippet": {"title": "Berlin - Sex (I'm A...) [Official Audio]"},
        }
    ]
    youtube = FakeYoutube(items)

    key = m.make_song_key(BERLIN_SEX_ARTIST, BERLIN_SEX_TITLE)
    cache = {"songs": {key: {"artist": BERLIN_SEX_ARTIST, "title": BERLIN_SEX_TITLE, "video_id": None, "added": False}}}

    video_id = m.search_video_cached(youtube, cache, BERLIN_SEX_ARTIST, BERLIN_SEX_TITLE)

    assert video_id == BERLIN_SEX_VIDEO_ID
    assert youtube.captured.get("safeSearch") == "none"


# Real case: for a short, exact title ("2001"), every candidate — original,
# remix, dub, demo, live session — scores identical (1.0) title relevance via
# _title_containment, so the pick comes down entirely to `quality`. HQ_KEYWORDS
# contained both "audio" and "official audio", and since the latter is a
# substring of the former's match text, any "Official Audio"-branded upload
# was double-counted (+4 instead of +2) — this is what let a "(Dan Carey Dub)"
# reupload, tagged "Official Audio", outscore and replace the actual official
# video in a real run. Fixed by (a) not counting a keyword hit that's wholly
# contained in another matched keyword from the same list, and (b) not
# granting the HQ bonus at all when the title also carries an LQ_KEYWORDS
# alternate-version signal (remix/dub/demo/session/...) — a polished remix
# shouldn't out-earn a plain upload just for being well-produced.
DAN_CAREY_DUB_TITLE = "FOALS – 2001 (Dan Carey Dub) – Official Audio"
MYD_REMIX_TITLE = "FOALS - 2001 [Myd Remix] (Official Audio)"
OFFICIAL_VIDEO_TITLE = "FOALS - 2001 [Official Music Video]"


def test_score_result_does_not_double_count_official_audio_keyword():
    relevance, artist_relevance, quality = m.score_result(DAN_CAREY_DUB_TITLE, "Foals", "2001", "Foals")

    assert quality == -1  # was +4 before the fix: "audio" + "official audio" both matched, plus no dub penalty offset


def test_score_result_remix_no_longer_outscores_official_video():
    dub_score = m.score_result(DAN_CAREY_DUB_TITLE, "Foals", "2001", "Foals")
    myd_remix_score = m.score_result(MYD_REMIX_TITLE, "Foals", "2001", "Foals")
    official_video_score = m.score_result(OFFICIAL_VIDEO_TITLE, "Foals", "2001", "Foals")

    assert dub_score < official_video_score or dub_score[2] < 2
    assert myd_remix_score < official_video_score or myd_remix_score[2] < 2


def test_search_video_ytdlp_does_not_pick_the_remix_for_foals_2001(monkeypatch):
    # Real yt-dlp candidate list for "Foals - 2001 audio" (channel via
    # "channel" field), with the reported Dan Carey Dub reupload added in —
    # it wasn't in this particular sample run but is exactly the case that
    # triggered the wrong pick in production.
    candidates = [
        {"id": "ydBQz3SecaE", "title": "FOALS - 2001 [Official Music Video]", "channel": "Foals"},
        {"id": "E2WD04MPkG4", "title": "FOALS x LONDON CONTEMPORARY ORCHESTRA - 2001 [Official Video]", "channel": "Foals"},
        {"id": "hhUaLCBqpWA", "title": "Foals - 2001 Demo Version (2001 Vibes)", "channel": "Isolarian"},
        {"id": "NK4uIxFkEL0", "title": "Foals - 2001 (Lyrics)", "channel": "Lyriclist"},
        {"id": "1dSy1psZhGE", "title": "FOALS: 2001 // Life Is Yours // The Colour Wheel Session", "channel": "Foals"},
        {"id": "n1zr1LBr51c", "title": "FOALS - '2001'", "channel": "ACID STAG"},
        {"id": "CYU9Yz2RayU", "title": MYD_REMIX_TITLE, "channel": "Foals"},
        {"id": "SVxLol5MIdA", "title": "Foals - 2001 (Glastonbury 2022)", "channel": "BBC Music"},
        {"id": "6Q_58jY9A0Q", "title": DAN_CAREY_DUB_TITLE, "channel": "Foals"},
    ]
    stdout = "\n".join(json.dumps(c) for c in candidates)

    class FakeCompletedProcess:
        def __init__(self, stdout):
            self.returncode = 0
            self.stdout = stdout

    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: FakeCompletedProcess(stdout))

    video_id = m.search_video_ytdlp("Foals", "2001")

    assert video_id not in ("6Q_58jY9A0Q", "CYU9Yz2RayU")  # Dan Carey Dub / Myd Remix
