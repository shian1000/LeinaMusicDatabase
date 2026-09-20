import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.youtube import manage_youtube_playlists as m
from utils.youtube import yt_cache


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
    monkeypatch.setattr(
        m, "search_video_ytdlp",
        lambda artist, title, max_results=8, artist_synonyms=None, language=None: None,
    )

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
    #
    # "2001" is short enough that every candidate ties at 1.0
    # relevance/artist_relevance, so quality alone decides — and the real
    # official video used to lose to several completely uninformative
    # titles (a random lyric video, an unlabeled "'2001'" upload) that
    # scored a "clean" 0 purely by not saying anything, while the real
    # video's correct "[Official Music Video]" label triggered only the
    # VIDEO_KEYWORDS format penalty with no offsetting signal. HQ_KEYWORDS
    # gaining bare "official" (a genuine trust signal distinct from the
    # audio/video format preference) fixed this — asserted as an exact
    # pick now, not just "isn't the remix".
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

    assert video_id == "ydBQz3SecaE"  # the real official music video


def _mock_ytdlp_search(monkeypatch, candidates):
    """candidates: list of dicts with id/title/channel/view_count (view_count optional).
    Returns the captured search command's args list for assertions on query text."""
    stdout = "\n".join(json.dumps(c) for c in candidates)
    captured_command = []

    class FakeCompletedProcess:
        def __init__(self, stdout):
            self.returncode = 0
            self.stdout = stdout

    def fake_run(command, **kwargs):
        captured_command.extend(command)
        return FakeCompletedProcess(stdout)

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    return captured_command


# Real case: appending the literal word "audio" to every query (the old
# `f"{artist} - {title} audio"` template) actively hurt results rather than
# helping — dropping it entirely (per-user decision, not just for this one
# song) fixed several unrelated failures at once:
#
# - Slaughter To Prevail - Bratva: relevance already tied at 1.0 between the
#   real track and an "(Instrumental)" reupload, so the pick came down to
#   view count — 8.3M vs 60K. This is what `_popularity_bonus()` (score_result's
#   new `view_count` param, free from yt-dlp's JSON) fixes.
# - Passive Voice - Тебе пам'ятаю: "Passive Voice" reads as a generic English
#   grammar term, and appending "audio" made YouTube's search drift entirely
#   to unrelated grammar/audiobook content — none of the real candidates were
#   even in the result set with "audio" in the query.
# - Ten Preston - 71: the real video (3.26M views, public, unrestricted)
#   didn't appear in search results *at all* with "audio" in the query, at
#   any of 5+ phrasings tried, including a widened ytsearch20 — dropping
#   "audio" alone put it in first place.
#
# Not every song reported alongside these was fixed by this change — e.g. a
# Belarusian/Russian spelling variant blocks one case at the relevance stage,
# before quality/popularity are ever consulted, and a viral live performance
# can still out-view a studio original. Those aren't covered here since they
# aren't actually fixed by this change (see docs/agent-notes).


def test_search_video_ytdlp_prefers_popular_original_over_instrumental(monkeypatch):
    # Real yt-dlp candidates for "SLAUGHTER TO PREVAIL - Bratva" (no "audio" suffix).
    candidates = [
        {"id": "iUZRLYfHEgA", "title": "Slaughter To Prevail - Bratva (Live In Moscow)", "channel": "SUMERIAN", "view_count": 3737158},
        {"id": "XFzB3TXoG4A", "title": "Slaughter To Prevail - Bratva", "channel": "SUMERIAN", "view_count": 8369100},
        {"id": "zRJSZKW4r1Q", "title": "Slaughter To Prevail - BRATVA - Live @ INKcarceration Fest 2023 @AlexTerrible", "channel": "EvilVox", "view_count": 224622},
        {"id": "CMoSzybZ2BQ", "title": "SLAUGHTER TO PREVAIL - Bratva (Lyrics/перевод)", "channel": "Hard Rock World", "view_count": 60425},
        {"id": "WKb0gB1lLww", "title": "SLAUGHTER TO PREVAIL  HELLFEST LIVE BIGGEST WALL OF DEATH 2024", "channel": "Alex Terrible", "view_count": 1010008},
        {"id": "4GXlOS486fs", "title": "Slaughter To Prevail - Bratva (Lyric Video) (HQ)", "channel": "Black Fire", "view_count": 73481},
        {"id": "87K_mGzACpc", "title": "Musician's Slaughter To Prevail reaction - Bratva", "channel": "Steve O'G", "view_count": 9555},
    ]
    captured_command = _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("SLAUGHTER TO PREVAIL", "Bratva")

    assert video_id == "XFzB3TXoG4A"
    assert not any("audio" in arg.lower() for arg in captured_command)


def test_search_video_ytdlp_finds_passive_voice_without_audio_suffix(monkeypatch):
    # Real yt-dlp candidates for "Passive Voice - Тебе пам'ятаю" (no "audio"
    # suffix) — with "audio" appended, none of these were even returned;
    # YouTube's search drifted entirely to unrelated English-language content.
    candidates = [
        {"id": "Vd5epmcIamY", "title": "Passive Voice - Тебе пам’ятаю", "channel": "Passive Voice", "view_count": 347},
        {"id": "jJbAmWBLs80", "title": "Passive Voice – Тебе пам’ятаю @ TNT Rock Club, Мінск, 02.11.2025", "channel": "ochtilno", "view_count": 15},
        {"id": "3sG_TPI-eiA", "title": "Black Line Studio | Passive Voice | Live", "channel": "Black Line Studio", "view_count": 10048},
        {"id": "_ezASsdYwmY", "title": "Нажаль | Passive Voice", "channel": "Passive Voice", "view_count": 3809},
    ]
    captured_command = _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Passive Voice", "Тебе пам'ятаю")

    assert video_id == "Vd5epmcIamY"
    assert not any("audio" in arg.lower() for arg in captured_command)


def test_search_video_ytdlp_finds_ten_preston_without_audio_suffix(monkeypatch):
    # Real yt-dlp candidates for "Ten Preston feat. Sitek - 71 (prod. lil
    # aloes)" (no "audio" suffix) — with "audio" appended, this video never
    # appeared in results at all, at any query phrasing tried (including a
    # widened ytsearch20); only decorated reuploads (Bass Boosted) showed up.
    candidates = [
        {"id": "R5eMExxWu1M", "title": "Ten Preston feat. Sitek - 71 (prod. lil aloes)", "channel": "chillwagon", "view_count": 3260870},
        {"id": "RZVKNq7OwoI", "title": "Ten Preston feat. Sitek - 71 (prod. lil aloes) (Bass Boosted)", "channel": "FistachNation", "view_count": 1994},
        {"id": "0PWBdfKU5Xs", "title": "Ten Preston - SHEESH (prod. lil aloes)", "channel": "chillwagon", "view_count": 2154422},
        {"id": "31VKUYwRjQY", "title": "Ten Preston feat. Sitek - 71 (prod. lil aloes) (Bass Boosted)", "channel": "Bass Boosted", "view_count": 518},
        {"id": "kzqzV_TNVMw", "title": "Ten Preston - Fan (prod. lil aloes)", "channel": "chillwagon", "view_count": 140285},
    ]
    captured_command = _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Ten Preston feat. Sitek", "71 (prod. lil aloes)")

    assert video_id == "R5eMExxWu1M"
    assert not any("audio" in arg.lower() for arg in captured_command)


# Real cases: apostrophe stripping in `_relevance_text()`, a containment-based
# fallback for `artist_relevance` (mirroring the one title relevance already
# had), and `LQ_KEYWORDS` gaining "react"/"review"/"teaser"/"eurovision
# version"/"high tone". Not every song reported alongside these was fixed:
# a double-letter spelling variant ("Pompei" vs "Pompeii") and a script
# mismatch (Cyrillic artist name vs. a Latin-alphabet channel name with no
# textual overlap at all) both block matching before these fixes can help —
# see docs/agent-notes/youtube-search-matching.md.


def test_search_video_ytdlp_ignores_missing_apostrophe(monkeypatch):
    # Real candidates for "Gary Clark Jr. - Ain't Messin' 'Round". Before the
    # apostrophe fix, the two official uploads scored relevance ~0.69 (one
    # missing each of the DB title's two apostrophes) while an unrelated,
    # much-less-popular live recording matched exactly and won outright.
    candidates = [
        {"id": "fyBem5-Bfpg", "title": "Gary Clark Jr. - Ain't Messin' Round [Official Music Video]", "channel": "garyclarkjr", "view_count": 1029669},
        {"id": "EyFFuEY_S6Q", "title": "Gary Clark Jr - Ain't Messin 'Round [Official Audio]", "channel": "garyclarkjr", "view_count": 400470},
        {"id": "AdK8QEMIKN4", "title": "Gary Clark Jr - Ain't Messin' 'Round (Live at Farm Aid 2014)", "channel": "Farm Aid", "view_count": 64223},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Gary Clark Jr.", "Ain't Messin' 'Round")

    assert video_id == "EyFFuEY_S6Q"


def test_search_video_ytdlp_artist_containment_ignores_upload_branding(monkeypatch):
    # Real candidates for "WE BUTTER THE BREAD WITH BUTTER - N!CE". The real
    # official upload's title carries extra branding ("// Official Music
    # Video // AFM Records") that diluted plain similarity() enough to score
    # lower artist_relevance than a short, undecorated reaction video's
    # title — on top of "react" not being an LQ_KEYWORDS term at all yet.
    candidates = [
        {"id": "E49Qrhdk2cI", "title": "WE BUTTER THE BREAD WITH BUTTER - N!CE (2021) // Official Music Video // AFM Records", "channel": "AFM Records", "view_count": 890446},
        {"id": "qSNnl-w0_c8", "title": "We Butter The Bread With Butter - N!CE (React/Review)", "channel": "Mat and Chels React", "view_count": 13231},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("WE BUTTER THE BREAD WITH BUTTER", "N!CE")

    assert video_id == "E49Qrhdk2cI"


def test_search_video_ytdlp_deprioritizes_eurovision_version(monkeypatch):
    # Real candidates for "Måneskin - Zitti e buoni". The actual official
    # video (230M views) lost to an "(Eurovision Version)" reupload (8.9M
    # views) purely because "official video" tripped VIDEO_KEYWORDS while
    # "eurovision version" tripped nothing at all.
    candidates = [
        {"id": "QN1odfjtMoo", "title": "Måneskin - ZITTI E BUONI (Official Video – Sanremo & EUROVISION 2021 Winners)", "channel": "Måneskin Official", "view_count": 230071757},
        {"id": "0Upt-ddaw04", "title": "ZITTI E BUONI (Eurovision Version)", "channel": "Måneskin Official", "view_count": 8897851},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Måneskin", "Zitti e buoni")

    assert video_id == "QN1odfjtMoo"


def test_search_video_ytdlp_prefers_real_upload_over_reaction_video(monkeypatch):
    # Real candidates for "Bloodywood - INDIAN STREET METAL (Ari Ari ft.
    # Raoul Kerr)". A reaction video (4.6K views, channel "Alex N Channel")
    # beat the real upload (7.9M views, channel "Bloodywood") in production
    # because "reaction" wasn't in LQ_KEYWORDS at all. In practice this
    # resolves via multiple redundant signals now (the real view-count gap
    # via popularity, and a clean channel-name match via artist_relevance
    # containment), which is why the dedicated LQ_KEYWORDS("react") coverage
    # lives in test_score_result_penalizes_new_lq_keywords instead, with
    # view counts equalized to isolate that one signal — this test checks
    # the real, full candidate pool end-to-end instead.
    candidates = [
        {"id": "i4FqGPRQWFM", "title": 'INDIAN STREET METAL ("Ari Ari" ft. Raoul Kerr) - Bloodywood', "channel": "Bloodywood", "view_count": 7946578},
        {"id": "6uJoN_I9ebQ", "title": 'INDIAN FOLK METAL (Bloodywood - "Jee Veerey" ft. Raoul Kerr)', "channel": "Bloodywood", "view_count": 2536950},
        {"id": "1nvgTbEdH8E", "title": "Ari Ari", "channel": "Bloodywood", "view_count": 313093},
        {"id": "TcpgRq-bCTs", "title": 'INDIAN STREET METAL ("Ari Ari" ft. Raoul Kerr) - Bloodywood (REACTION)', "channel": "Alex N Channel", "view_count": 4556},
        {"id": "Fvu3uPNiWNk", "title": "Bloodywood - Ari Ari - Live at Wacken Open Air 2019", "channel": "WackenTV", "view_count": 290477},
        {"id": "LZcjfLMzDuw", "title": "Bloodywood & Raoul Kerr - Ari Ari", "channel": "All Kind Of Music", "view_count": 201},
        {"id": "a65A626Ed20", "title": "Bloodywood - Dana Dan (Indian Folk Metal)", "channel": "Bloodywood", "view_count": 9423555},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Bloodywood", "INDIAN STREET METAL (_Ari Ari_ ft. Raoul Kerr)")

    assert video_id == "i4FqGPRQWFM"


# Real cases that motivated adding "react", "review", "teaser", "eurovision
# version", and "high tone" to LQ_KEYWORDS: Bloodywood's "(REACTION)" video
# and Król's "(Teaser)" upload both beat the real thing in production. Both
# turned out, on closer inspection, to already be resolved by *other*
# existing signals once matched against real data (Bloodywood: a clean
# channel-name match already gives the real upload a decisive artist_relevance
# edge; Król: the huge pre-existing view-count gap alone is enough) — so an
# end-to-end test built from those exact songs wouldn't actually exercise the
# new keywords; it would pass with or without them. Testing the keywords
# directly against `score_result()` instead, holding everything else fixed,
# is what actually catches a regression if one of them is ever removed.
def test_score_result_penalizes_new_lq_keywords():
    baseline = m.score_result("Artist - Song", "Artist", "Song", "Artist", 50000)
    for decorated_title in [
        "Artist - Song (REACTION)",
        "Artist - Song (Review)",
        "Artist - Song (Teaser)",
        "Artist - Song (Eurovision Version)",
        "Artist - Song (High Tone)",
    ]:
        decorated = m.score_result(decorated_title, "Artist", "Song", "Artist", 50000)
        assert decorated[2] < baseline[2], f"{decorated_title!r} should score lower quality than the baseline"


def test_search_video_ytdlp_artist_containment_disambiguates_cover_artists(monkeypatch):
    # Real candidates for "Zob - Cantec de dragoste". A cover by a different
    # duo (Alexandra Usurelu & Dan Bordeianu, 563K views — far more popular
    # than any of Zob's own uploads) used to outscore the real artist's
    # uploads because plain similarity() didn't clearly separate "this
    # candidate is by the searched artist" from "this candidate happens to
    # share some characters with the artist name". Containment-based
    # artist_relevance fixes this: the cover artists' names share almost no
    # textual overlap with "Zob" once measured by containment, not raw ratio.
    candidates = [
        {"id": "KGPft935c00", "title": "Zob - Cantec De Dragoste (Official Video)", "channel": "Roton Hits", "view_count": 188747},
        {"id": "D398DGsBP1I", "title": "Zob & Mara - Cantec de dragoste", "channel": "Vali Moga", "view_count": 54880},
        {"id": "5n_cKrQYeoU", "title": "ZOB- 'Cântec de dragoste' la BTLive @Guerrilive Radio Session", "channel": "Radio Guerrilla", "view_count": 5904},
        {"id": "X86Vjl3SCpc", "title": "Alexandra Usurelu și Dan Bordeianu - Cantec de dragoste", "channel": "Alexandra Usurelu", "view_count": 563148},
        {"id": "-oDSUwRm64Y", "title": "Zob - Cantec de dragoste", "channel": "Alexandru Popa", "view_count": 1095},
        {"id": "-lssuVMpvFo", "title": "Zob-Cantec de dragoste ( Cristina Țițescu cover)", "channel": "Cristina Țițescu", "view_count": 1046},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Zob", "Cantec de dragoste")

    assert video_id in ("D398DGsBP1I", "KGPft935c00")  # either the band's own upload or the official video


def test_search_video_ytdlp_tolerates_doubled_letter_typo(monkeypatch):
    # Real case: DB title "Pompei" (missing one "i") vs the real "Pompeii" —
    # scored relevance 0.50 against a required 0.85 for a title this short,
    # rejecting every candidate outright. Both collapse to "Pompei" once
    # repeated letters are folded, restoring an exact match.
    candidates = [
        {"id": "F90Cw4l-8NY", "title": "Bastille - Pompeii (Official Music Video)", "channel": "BASTILLEvideos", "view_count": 839916374},
        {"id": "ilLEuwH4hws", "title": "Bastille - Pompeii (Lyric Video)", "channel": "BASTILLEvideos", "view_count": 32408712},
        {"id": "cvQ2LF3hyuY", "title": "Bastille - Pompeii (Lyrics)", "channel": "Cosmos Music", "view_count": 23270768},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Bastille", "Pompei")

    assert video_id == "F90Cw4l-8NY"


def test_search_video_ytdlp_tolerates_missing_diacritics(monkeypatch):
    # Real case: DB title "Niesmiertelnosc" (ASCII, missing Polish accents)
    # vs the real "Nieśmiertelność" — exact containment couldn't match
    # across the missing "ś"/"ć", and the fallback similarity() ratio,
    # further diluted by the "ABRADAB - " prefix on the candidate side,
    # dropped to 0.6 — below an unrelated "(Live)" recording's 0.8, so the
    # live version won outright. Folding diacritics via NFKD decomposition
    # makes both sides "niesmiertelnosc" and restores an exact match.
    candidates = [
        {"id": "vIGV7z31vj8", "title": "Nieśmiertelność (Live)", "channel": "Abradab - Topic", "view_count": 294},
        {"id": "T3bqVFKEoDA", "title": "ABRADAB - Nieśmiertelność (Muzyka daje) [OFFICIAL AUDIO]", "channel": "S.P. RECORDS", "view_count": 22699},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("ABRADAB", "Niesmiertelnosc (Muzyka Daje)")

    assert video_id == "T3bqVFKEoDA"


def test_search_video_ytdlp_tolerates_short_i_breve_typo(monkeypatch):
    # Real case: DB title "...набіраи" (a typo — Cyrillic "и") vs the real
    # "...набірай" (Cyrillic "й", short I) — yt-dlp returned zero results
    # for the typo'd query, and the correct video wasn't found at all under
    # the old code. "й" NFKD-decomposes to "и" + a combining breve, so the
    # same diacritic-folding fix above resolves this non-Latin-script case
    # too, without a script-specific rule.
    candidates = [
        {"id": "l8cJML7BJUg", "title": "bayski - набірай (acoustic)", "channel": "bayski", "view_count": 686},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("bayski", "набіраи (acoustic)")

    assert video_id == "l8cJML7BJUg"


def test_search_video_ytdlp_deprioritizes_track_by_track_promo(monkeypatch):
    # Real case: "Rival Sons - Darkfighter". A "Track by Track" promo video
    # (a band explaining/promoting the album, not the song itself) only
    # barely lost to the real official audio — margin 0.29 with real view
    # counts, the same fragile-near-tie shape as Daði Freyr before that fix,
    # here for "isn't music at all" rather than "isn't the canonical
    # arrangement". Asserted on the margin, not just the pick, since the old
    # weights already (barely) won this one by luck.
    real_score = m.score_result('Rival Sons - "DARKFIGHTER" (Official Audio)', "Rival Sons", "Darkfighter", "Rival Sons", 97112)
    promo_score = m.score_result("DARKFIGHTER Track by Track (Official)", "Rival Sons", "Darkfighter", "Rival Sons", 50000)
    assert real_score[2] - promo_score[2] > 2  # was ~0.29 before

    candidates = [
        {"id": "uX7ZXF2EyeE", "title": "DARKFIGHTER Track by Track (Official)", "channel": "Rival Sons", "view_count": 50000},
        {"id": "GEW7zR1aIUI", "title": 'Rival Sons - "DARKFIGHTER" (Official Audio)', "channel": "Rival Sons", "view_count": 97112},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Rival Sons", "Darkfighter")

    assert video_id == "GEW7zR1aIUI"


def test_search_video_ytdlp_ignores_seo_stuffed_tags(monkeypatch):
    # Real case: "Elvis Crespo - Suavemente". The real official Vevo upload's
    # tags include generic, broadly-cast SEO terms ("remix", "karaoke",
    # "instrumental", "en vivo"/"en directo" — Spanish for "live") that
    # don't describe *this* video's content at all, just adjacent searches
    # the label also wants to rank for. Scanning those against LQ_KEYWORDS
    # incorrectly penalized the real video (299M views) — and, since an LQ
    # hit suppresses the whole HQ bonus block, cost it the "official" bonus
    # too — enough to lose to an unrelated collab reupload with far fewer
    # views (19.6M, whose own tags include "En vivo" — a real live-recording
    # signal, unlike the noise on the correct video's tags). LQ_KEYWORDS/
    # HQ_KEYWORDS/VIDEO_KEYWORDS now scan the title only; STRONG_LQ_KEYWORDS
    # still scans tags (that's what OBERSCHLESIEN needs), so this doesn't
    # regress that fix — see test_search_video_ytdlp_prefers_studio_video_over_viral_live_clip.
    real_tags = [
        "suavemente álbum", "ElvisCrespovevo", "tu sonrisa", "official", "video",
        "vídeo musical", "music video", "elvis crespo en directo", "album",
        "Salsa tropical", "elvis crespo en vivo", "instrumental", "música",
        "dance", "karaoke", "remix", "en directo", "audio",
    ]
    wrong_tags = ["Suavemente", "Luck Ra", "Elvis Crespo", "Cuarteto", "Latin", "En vivo", "Fiesta"]
    candidates = [
        {"id": "UMYAdGEXOn0", "title": "Luck Ra, Elvis Crespo - SUAVEMENTE", "channel": "Luck Ra", "view_count": 19611031, "tags": wrong_tags},
        {"id": "WPiEbYSF9kE", "title": "Elvis Crespo - Suavemente", "channel": "Elvis Crespo", "view_count": 299876074, "tags": real_tags},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Elvis Crespo", "Suavemente")

    assert video_id == "WPiEbYSF9kE"


# Real cases: the artists table's `synonyms` column (a plain comma-separated
# string — see _parse_synonyms()) is now consulted for artist_relevance
# alongside the DB artist name, taking the max across all of them. This is
# the only fix that can bridge a genuine name change — no string-similarity
# technique can turn "Бумбокс" into "familyboombox", "Hall & Oates" into
# "Daryl Hall & John Oates", or "Junecapone" into "June". These four
# previously lived in docs/agent-notes/youtube-search-matching.md as an open,
# unfixed limitation; moved to the regression checklist now that the
# mechanism exists. (Stray Kids stayed out — that one's a title-formatting
# problem, not an artist-name mismatch, so it isn't fixed by this.)


def test_search_video_ytdlp_uses_artist_synonym_for_script_mismatch(monkeypatch):
    # Real case: "Бумбокс - Нездара". The real upload's channel,
    # `familyboombox`, shares zero characters with the Cyrillic artist name
    # `Бумбокс` — artist_relevance was 0.0 without a synonym.
    candidates = [
        {"id": "G7-lJGOOOIc", "title": "Бумбокс - Нездара", "channel": "Maria Matrunich", "view_count": 1154},
        {"id": "zDVzqqTzTDE", "title": "Нездара", "channel": "familyboombox", "view_count": 674464},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Бумбокс", "Нездара", artist_synonyms="familyboombox")

    assert video_id == "zDVzqqTzTDE"


def test_search_video_ytdlp_uses_artist_synonym_for_full_legal_name(monkeypatch):
    # Real case: "Hall & Oates - Maneater". The real official channel is
    # "Daryl Hall & John Oates" (full names), not the DB's shortened stage
    # name — artist_relevance was 0.69 without a synonym, versus 1.0 for an
    # unrelated lyric-video reupload from a generic channel that happened to
    # spell the shortened name correctly.
    candidates = [
        {"id": "IqF7S3zXl1A", "title": "Daryl Hall & John Oates - Maneater (Lyrics)", "channel": "7clouds", "view_count": 3999026},
        {"id": "yRYFKcMa_Ek", "title": "Daryl Hall & John Oates - Maneater (Official Video)", "channel": "Daryl Hall & John Oates", "view_count": 404412123},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Hall & Oates", "Maneater", artist_synonyms="Daryl Hall & John Oates")

    assert video_id == "yRYFKcMa_Ek"


def test_search_video_ytdlp_uses_artist_synonym_for_shortened_stage_name(monkeypatch):
    # Real case: "Junecapone - Depravity". The real official (Topic) channel
    # uses the artist's short name, "June", not the fuller stage handle
    # "Junecapone" the DB uses.
    candidates = [
        {"id": "qMmVQbH3sKQ", "title": "Depravity", "channel": "June - Topic", "view_count": 106},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Junecapone", "Depravity", artist_synonyms="June")

    assert video_id == "qMmVQbH3sKQ"


def test_search_video_ytdlp_uses_multiple_comma_separated_synonyms(monkeypatch):
    # Real case: "Плач Єремії - Вона". The band is fronted by (and uploads
    # under) Taras Chubai's own name — two synonyms needed at once (Latin
    # and Cyrillic spelling), verifying the comma-separated parsing handles
    # more than one alias.
    candidates = [
        {"id": "EaQEnpYoA2U", "title": "Вона", "channel": "Taras Chubai", "view_count": 55337789},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Плач Єремії", "Вона", artist_synonyms="Taras Chubai, Тарас Чубай")

    assert video_id == "EaQEnpYoA2U"


def test_search_video_ytdlp_prefers_official_video_over_live_recording(monkeypatch):
    # Real case: "Daði Freyr - Bitte". The official video (235K views) only
    # barely beat a live recording (23K views) at the old weights (VIDEO
    # penalty -2 for being a video, "live" penalty only -1) — 0.372 vs
    # 0.368, a coincidence away from picking the live version instead.
    # Lowering the video penalty and giving "live" its own bigger penalty
    # makes this decisive rather than a near-tie — asserting on the margin
    # itself, not just the final pick, since the old weights already won
    # this one (barely) by luck; a plain video_id assertion wouldn't catch
    # a regression back to that fragile near-tie.
    official_score = m.score_result("Daði Freyr - Bitte (Official Video)", "Daði Freyr", "Bitte", "Daði Freyr", 235334)
    live_score = m.score_result("Daði Freyr - Bitte (Live from Vikan með Gísla Marteini)", "Daði Freyr", "Bitte", "Daði Freyr", 23322)
    assert official_score[2] - live_score[2] > 2  # was ~0.004 before

    candidates = [
        {"id": "Wotwtc9tA-Q", "title": "Daði Freyr - Bitte (Official Video)", "channel": "Daði Freyr", "view_count": 235334},
        {"id": "2oDi4xLk1L0", "title": "Daði Freyr - Bitte (Live from Vikan með Gísla Marteini)", "channel": "Daði Freyr", "view_count": 23322},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Daði Freyr", "Bitte")

    assert video_id == "Wotwtc9tA-Q"


def test_search_video_ytdlp_prefers_studio_video_over_viral_live_clip(monkeypatch):
    # Real case: "OBERSCHLESIEN - Król Olch" — a Woodstock festival
    # recording (5.1M views) has *more* views than the actual studio video
    # (1.4M views), so it kept winning even after being correctly
    # tag-flagged as live ("na żywo" in its tags, not its title) at the old
    # -1 STRONG_LQ-equivalent weight. Left as a known, accepted limitation
    # when first found; the same fix that resolved Daði Freyr above (bigger
    # "live" penalty, smaller video penalty) turned out to resolve this too
    # — "na żywo" alone was already enough to flip the final pick (margin
    # ~1.4), so "woodstock" joining STRONG_LQ_KEYWORDS is asserted on the
    # margin directly below, not just the final video_id, since the pick
    # alone wouldn't catch a regression if "woodstock" were removed again.
    woodstock_title = "Oberschlesien - Król Olch #Woodstock2016"
    woodstock_tags = ["Oberschlesien Król Olch na żywo", "oberschlesien woodstock", "polandrock festival"]
    studio_title = "OBERSCHLESIEN - Król Olch [OFFICIAL VIDEO]"
    studio_tags = ["s.p. records", "OBERSCHLESIEN", "Król Olch"]

    woodstock_score = m.score_result(woodstock_title, "OBERSCHLESIEN", "Król Olch", "KręciołaTV", 5123817, woodstock_tags)
    studio_score = m.score_result(studio_title, "OBERSCHLESIEN", "Król Olch", "S.P. RECORDS", 1417183, studio_tags)
    assert studio_score[2] - woodstock_score[2] > 3  # was ~1.4 with "na żywo" alone, before "woodstock" joined STRONG_LQ_KEYWORDS

    candidates = [
        {"id": "NhPpu_WGAng", "title": woodstock_title, "channel": "KręciołaTV", "view_count": 5123817, "tags": woodstock_tags},
        {"id": "KPJPJzpk_QQ", "title": studio_title, "channel": "S.P. RECORDS", "view_count": 1417183, "tags": studio_tags},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("OBERSCHLESIEN", "Król Olch")

    assert video_id == "KPJPJzpk_QQ"


def test_search_video_ytdlp_prefers_official_video_over_jools_holland_session(monkeypatch):
    # Real case: "Benjamin Clementine - Cornerstone". The real official video
    # (7Dc5BQ31iLw, 6.7M views) lost to a BBC "Later... with Jools Holland"
    # TV performance clip (CJJNl1p-PGA, 1.2M views) at the old weights:
    # quality 4.83 vs 5.06, because the official video's own "(Official
    # Video)" label cost it VIDEO_PENALTY while the Jools Holland clip's
    # title mentions no English "live"/"concert"/"session" word at all (the
    # show's own name is the only signal it's a live performance) and even
    # picked up an HQ bonus from "HD" ("BBC Two HD"). Same shape as
    # "woodstock" joining STRONG_LQ_KEYWORDS above — asserting on the margin,
    # not just the final pick, since the old weights actually won this one
    # (wrongly) rather than merely tying.
    official_title = "Benjamin Clementine - Cornerstone (Official Video)"
    jools_holland_title = "Benjamin Clementine - Cornerstone - Later... with Jools Holland - BBC Two HD"

    official_score = m.score_result(official_title, "Benjamin Clementine", "Cornerstone", "Benjamin Clementine", 6706571)
    jools_holland_score = m.score_result(jools_holland_title, "Benjamin Clementine", "Cornerstone", "BBC", 1155234)
    assert official_score[2] - jools_holland_score[2] > 4  # was ~-0.24 before "jools holland" joined STRONG_LQ_KEYWORDS

    candidates = [
        {"id": "CJJNl1p-PGA", "title": jools_holland_title, "channel": "BBC", "view_count": 1155234},
        {"id": "7Dc5BQ31iLw", "title": official_title, "channel": "Benjamin Clementine", "view_count": 6706571},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Benjamin Clementine", "Cornerstone")

    assert video_id == "7Dc5BQ31iLw"


def test_search_video_ytdlp_matches_specific_requested_remix(monkeypatch):
    # Real case: "Valeria Stoica - Get Back (Lorin Rymbu & Denis Rynda Remix
    # Extended)". Bracket-stripping for relevance meant *any* remix scored
    # identical 1.0 relevance, so a completely unrelated remix
    # ("Deepshader's Reconstruction") won purely on quality/popularity.
    # SELECTOR_MATCH_BONUS rewards a candidate whose own title/tags mention
    # the specific named remixers asked for. Note the real matching
    # candidate's title says "Remix", not "Remix Extended" — the bonus
    # matches on the identifying names (Lorin Rymbu, Denis Rynda), not the
    # whole bracket phrase verbatim.
    candidates = [
        {"id": "YeQHuxFw31I", "title": "Valeria Stoica — Get Back (Deepshader's Reconstruction)", "channel": "Valeria Stoica", "view_count": 1202},
        {"id": "zliatPv0RGU", "title": "Valeria Stoica - Get Back (Lorin Rymbu & Denis Rynda Remix)", "channel": "Valeria Stoica", "view_count": 1938},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Valeria Stoica", "Get Back (Lorin Rymbu & Denis Rynda Remix Extended)")

    assert video_id == "zliatPv0RGU"


# Real cases from a live production run flagged by the user (2026-09-11), all
# captured directly from yt-dlp against the real, live YouTube search backend
# (not synthesized) — see docs/agent-notes/youtube-search-matching.md for the
# full investigation. Six cases; a seventh (Vinsent - "Praciahvaju Żyć") is
# deliberately not covered here — the DB title is a Latin-script
# transliteration of a Belarusian song whose only real upload is titled in
# Cyrillic only, a data-modeling problem the transliteration-retry mechanism
# above is aimed at, not this scoring layer — left out per user request.


def test_search_video_ytdlp_recognizes_official_release_without_topic_suffix(monkeypatch):
    # Real case: "Al Di Meola - Double Concerto". The real official upload's
    # yt-dlp `channel` field is literally "Al Di Meola" — no "- Topic" suffix
    # at all, even though the YouTube website displays it as "Al Di Meola –
    # Topic" — so _is_topic_channel() never recognized it, and a live
    # Budapest recording (91K views, no English "live"/"concert" keyword in
    # its title) won purely on view count. `track` (yt-dlp's free YouTube
    # Music metadata, populated only for genuinely label-distributed
    # releases) is what actually distinguishes them: every real official
    # upload here has it, the live recording and the two lower-effort
    # reuploads don't.
    candidates = [
        {"id": "3KLQfbHCYKU", "title": "Al Di Meola - Double Concerto - Live in Warsaw, 2000 [3]", "channel": "AhnDanil", "view_count": 39943},
        {"id": "qLXJTpgS-w4", "title": "Al di Meola - Double Concerto", "channel": "Harmonia Cordis A.", "view_count": 19824},
        {"id": "Hi1KxO32DFU", "title": "Double Concerto", "channel": "Al Di Meola", "view_count": 10883, "track": "Double Concerto"},
        {"id": "ua9iYiOtqEE", "title": "Al Di Meola - Horgas Eszter: Astor Piazzolla - Double concerto (Budapest, 2008)", "channel": "ttttunde", "view_count": 91489},
        {"id": "8fTeHHLcxYs", "title": "Double Concerto", "channel": "Al Di Meola", "view_count": 2294, "track": "Double Concerto"},
        {"id": "vUelO4lctg4", "title": "AL DI MEOLA   - Double Concerto", "channel": "Jazz 4 All", "view_count": 1474},
        {"id": "Q4g6Ln1kX50", "title": 'Al Di Meola in Sofia "Double Concerto"', "channel": "Dimitar Velichkov", "view_count": 7106},
        {"id": "hCUYSTjfDbw", "title": "Double Concerto", "channel": "Al Di Meola", "view_count": 1802, "track": "Double Concerto"},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Al Di Meola", "Double Concerto")

    assert video_id == "Hi1KxO32DFU"


def test_search_video_ytdlp_matches_channel_handle_concatenated_with_artist_name(monkeypatch):
    # Real case: "SunSay - В твоих глазах сияю я". The real channel handle
    # is "sunsaymusic" — the artist name concatenated with a suffix, no
    # separator — so _text_containment()'s whole-word-boundary requirement
    # never matches it (no boundary between "sunsay" and "music"), leaving
    # only a similarity() ratio of ~0.71. A wrong candidate — an "acoustic"
    # cover reupload (LQ_KEYWORDS-penalized) from an unrelated channel whose
    # own *title* spells "SunSay" as a separate word — scored a full 1.0
    # there instead. Since artist_relevance sorts before quality, that alone
    # picked the wrong video regardless of the quality gap. A channel-handle
    # prefix match (_artist_channel_handle_match) fixes this without
    # touching the sort order.
    candidates = [
        {"id": "tpOD1ZgP2Ik", "title": "SunSay – В твоих глазах сияю я | fairlane acoustic (2011)", "channel": "fairlane acoustic", "view_count": 34277},
        {"id": "xQejzb1JDQo", "title": "В твоих глазах сияю я", "channel": "sunsaymusic", "view_count": 16639, "track": "В твоих глазах сияю я"},
        {"id": "crnWx2Avou4", "title": "Sunsay - В твоих глазах сияю я", "channel": "MaximAndrosyuk", "view_count": 17387},
        {"id": "Jo0k5OkNk_Y", "title": "SunSay - В твоих глазах сияю я", "channel": "Алексей Лакиза", "view_count": 485},
        {"id": "bdGkqE1lUrs", "title": "В твоих глазах сияю я (Sunsay Cover)", "channel": "Леонид Оганесян", "view_count": 1933},
        {"id": "kQlW8ZIb94o", "title": "Sunsay - В твоих глазах сияю я", "channel": "Alexander Tokarev", "view_count": 108},
        {"id": "QQ6ckbaIaG8", "title": "SunSay - В Твоих Глазах Сияю Я", "channel": "marinellagomes", "view_count": 240},
        {"id": "exGnkkLBChQ", "title": "SunSay - В твоих глазах сияю я (cover)", "channel": "Алексей Леонов", "view_count": 1144},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("SunSay", "В твоих глазах сияю я")

    assert video_id == "xQejzb1JDQo"


def test_search_video_ytdlp_prefers_official_release_over_duplicate_title(monkeypatch):
    # Real case: "SunSay - Немовля". Two candidates share the real channel
    # handle "sunsaymusic" (see the channel-handle-prefix case above) and so
    # both tie on artist_relevance — the deciding factor is `quality`, where
    # only the genuine official/auto-generated upload carries `track`
    # metadata (the other is a fan reupload from a 2010 club show, its own
    # description reading "Spb, PlaceClub / 12.12.2010 by Ivan Goryachev").
    candidates = [
        {"id": "_edxVM3PylU", "title": "Немовля", "channel": "sunsaymusic", "view_count": 14724, "track": "Немовля"},
        {"id": "hahNWO_nIuA", "title": "SunSay  - Немовля", "channel": "sunsaymusic", "view_count": 16647},
        {"id": "0qRq6rscWfU", "title": "Sunsay - Немовля [MINUS vocal; Karaoke]", "channel": "MINUS vocal; Karaoke", "view_count": 18},
        {"id": "yPMAVzzfi4o", "title": "SunSay - Немовля", "channel": "Nelu Botnaru", "view_count": 3570},
        {"id": "bnsTeKZnxzo", "title": "SunSay - Немовля.mp4", "channel": "TheWillyamG", "view_count": 100},
        {"id": "N2DaKCUyX0M", "title": "Sunsay - Немовля", "channel": "IExistExist", "view_count": 810},
        {"id": "hsveP_cuA7g", "title": "SunSay - Немовля (Live)", "channel": "Dima Suchay", "view_count": 82},
        {"id": "K8ZmBpOIBjc", "title": "SunSay - Немовля [live in Cherkassy 19.04.2013]", "channel": "Vadym Sapatrylo", "view_count": 609},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("SunSay", "Немовля")

    assert video_id == "_edxVM3PylU"


def test_search_video_ytdlp_declines_when_only_match_is_a_translation_video(monkeypatch):
    # Real case: "Taco Hemingway - Fuck Your List". The real official upload
    # is age-restricted by YouTube's own community guidelines and is
    # invisible to anonymous yt-dlp search entirely — verified directly
    # against every yt-dlp player_client option (web, tv_embedded, android,
    # ios, mweb), none surface it, even though it's the #1 organic result on
    # youtube.com's own search for a signed-out session. This candidate pool
    # reflects the real (age-restricted-excluded) yt-dlp result set. Two
    # things must both hold for this to end in "no match" rather than a
    # confidently wrong pick:
    #   - the "Tłumaczenie ... | LyricsTranslationTV" video (a lyrics
    #     translation, not the song) title-contains the exact expected
    #     title and would otherwise score relevance 1.0 — NOT_THE_SONG_KEYWORDS
    #     forces it to 0 instead.
    #   - "Fuck ya list" by the entirely unrelated artist "Paccman chico"
    #     (relevance ~0.85 from coincidental character overlap) is rejected
    #     by the MIN_ARTIST_RELEVANCE floor (artist_relevance ~0.29).
    # With both gone, nothing clears the bar and the search correctly
    # reports no match rather than adding a wrong song.
    candidates = [
        {"id": "qNmEvnJS-kI", "title": "Tłumaczenie Taco Hemingway - Fuck Your List | LyricsTranslationTV", "channel": "LyricsTranslationTV", "view_count": 5496},
        {"id": "8cHBSpb2XE4", "title": "Taco Hemingway - YOUNG HEMS  - cały album", "channel": "Pełne Albumy", "view_count": 138371},
        {"id": "xiZSpazO_Jo", "title": "Fuck ya list", "channel": "Paccman chico - Topic", "view_count": 130, "track": "Fuck ya list"},
        {"id": "gZ4PX3NXRFs", "title": "Alfabet z Taco Hemingwayem - ABC (nie) dla dzieci", "channel": "Maciej Adamczyk", "view_count": 2010},
        {"id": "PT14Nu5bmXI", "title": "APPLY YOURSELF THEN TOUCH A BAG 8D", "channel": "Hey Lol", "view_count": 360},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Taco Hemingway", "Fuck Your List")

    assert video_id is None


def test_search_video_ytdlp_normalizes_comma_slash_artist_separators(monkeypatch):
    # Real case: "Waglewski, Fisz, Emade - Bóg". The DB spells the trio with
    # commas; the real Topic channel's display name uses slashes
    # ("Waglewski / Fisz / Emade - Topic") — plain similarity() rates that
    # punctuation-only difference (artist_relevance 0.74) *below* an
    # unrelated "(live Frytka Off)" reupload whose title happens to use
    # plain spaces with no punctuation at all (0.83), even though `quality`
    # correctly ranks them the other way around (+3.33 vs -2.77) via the
    # Topic-channel/track bonus and the STRONG_LQ_KEYWORDS "live" penalty.
    # Since artist_relevance sorts before quality, that was enough to pick
    # the live recording. _normalize_multi_artist_punctuation() treats
    # comma/slash/ampersand as interchangeable separators, so both tie at
    # 1.0 and quality decides correctly.
    candidates = [
        {"id": "tRHIqK_vy6M", "title": "Bóg", "channel": "Waglewski / Fisz / Emade - Topic", "view_count": 21231, "track": "Bóg"},
        {"id": "ZeQecB1KyCw", "title": "Waglewski Fisz Emade - Bóg/Ile jeszcze życia? - live in Toruń 14.11.2018", "channel": "Piotr Raczkowski", "view_count": 339},
        {"id": "ROTYmNckBCw", "title": "Waglewski Fisz Emade - Ojciec", "channel": "Next Music", "view_count": 1125464},
        {"id": "t7r6x_Ajuho", "title": "Waglewski Fisz Emade - Bóg (live Frytka Off)", "channel": "nothingsalright", "view_count": 1684},
        {"id": "J4_4dJawgM4", "title": "Waglewski Fisz Emade - Syn", "channel": "Next Music", "view_count": 397227},
        {"id": "RuIeM4Qu_NE", "title": "Waglewski Fisz Emade - Bóg - 26.11.2013", "channel": "slodzias", "view_count": 10561},
        {"id": "sySsenGiBUw", "title": "Waglewski Fisz Emade - Ziemia (Official Video)", "channel": "Mystic Production TV", "view_count": 619553},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Waglewski, Fisz, Emade", "Bóg")

    assert video_id == "tRHIqK_vy6M"


def test_search_video_ytdlp_prefers_topic_channel_over_reupload_with_more_context(monkeypatch):
    # Real case: "Вольны хор - Пагоня". Confirmed against real yt-dlp output
    # that the genuine Topic-channel release (full track/artists/album
    # metadata, 70.7K views) outranks a legitimate but non-canonical
    # reupload (symbal.by, 6.2K views, no official-release metadata) once
    # the official-release bonus is in play — the user's own manually-found
    # link was the symbal.by upload, confirmed via follow-up discussion to
    # accept the Topic-channel release as the better answer instead (more
    # views, full official metadata) rather than tuning scoring to force the
    # old pick.
    candidates = [
        {"id": "jn_vnj4_vQg", "title": "Вольны хор — Пагоня (запіс з анлайн-канцэрта «Муры»)", "channel": "symbal.by", "view_count": 6195},
        {"id": "x_52myRgoGM", "title": "Вольны хор: Нацыянальны гімн Беларусі «Пагоня» (калядны анлайн-канцэрт)", "channel": "Годна", "view_count": 129043},
        {"id": "suN9LYsC1qU", "title": "«Вольны хор» прэзентаваў альбом «Годныя песні»: Пагоня, Муры, Магутны Божа, Сцяг, Гэта мы, Нёман", "channel": "Годна", "view_count": 28623},
        {"id": "Nxs3TGwI07Q", "title": "Вольны хор — Жыве Беларусь (анлайн-канцэрт «Муры», 27.05.2021)", "channel": "Годна", "view_count": 4901},
        {"id": "FsskFUad88o", "title": 'VOLNY CHOR / ВОЛЬНЫ ХОР - Ой, шлі-прайшлі (анлайн-канцэрт "Муры", 27.05.2021)', "channel": "VOLNY CHOR / ВОЛЬНЫ ХОР", "view_count": 4899},
        {"id": "5kgvHgqr2aE", "title": "Пагоня", "channel": "Вольны Хор - Topic", "view_count": 70680, "track": "Пагоня"},
        {"id": "tpLfAhQUZy8", "title": "Вольны хор — Разбуры турмы муры (запіс з анлайн-канцэрта «Муры»)", "channel": "symbal.by", "view_count": 7311},
        {"id": "8o1-Zt5eN1Y", "title": "Вольны хор — Гэта мы (запіс з анлайн-канцэрта «Муры»)", "channel": "symbal.by", "view_count": 3437},
    ]
    _mock_ytdlp_search(monkeypatch, candidates)

    video_id = m.search_video_ytdlp("Вольны хор", "Пагоня (запіс з анлайн - канцэрта Муры)")

    assert video_id == "5kgvHgqr2aE"


# Real cases the alternate-script retry (transliteration.py) is meant for:
# a Belarusian song's DB title is in Cyrillic, but the only matching upload
# on YouTube spells it romanized (or vice versa) — or, the real case found
# testing Akute's catalog, the *official* upload exists under the other
# script but scores too low on relevance to be picked, leaving only live
# reuploads (which DO match the DB script) as the only accepted candidate.
# The retry triggers whenever the language is transliterable AND the current
# best pick isn't a confirmed official release (see _is_official_release())
# — including "found nothing at all", which trivially isn't official either.


def _fake_run_by_query(routes):
    """routes: {substring-of-query: candidates-list}. Returns a fake
    subprocess.run replacement keyed on which substring appears in the
    query; unmatched queries get an empty result."""
    def fake_run(command, **kwargs):
        query = command[-1]
        candidates = next((c for key, c in routes.items() if key in query), [])
        stdout = "\n".join(json.dumps(c) for c in candidates)

        class FakeCompletedProcess:
            def __init__(self, stdout):
                self.returncode = 0
                self.stdout = stdout

        return FakeCompletedProcess(stdout)
    return fake_run


def test_search_video_ytdlp_retries_transliterated_when_nothing_found_originally(monkeypatch):
    # Cyrillic query returns nothing usable at all; the same song only
    # turns up under its romanized (Lacinka) title.
    monkeypatch.setattr(m.subprocess, "run", _fake_run_by_query({
        "Адзіноцтва": [],
        "Adzinoctva": [{"id": "rightVideoId", "title": "Test Artist - Adzinoctva", "channel": "Test Artist - Topic"}],
    }))

    video_id = m.search_video_ytdlp("Test Artist", "Адзіноцтва", language="Belarusian")

    assert video_id == "rightVideoId"


def test_search_video_ytdlp_prefers_transliterated_official_release_over_non_official_original(monkeypatch):
    # Real case: "Akute - Cicha, jak maja śmierć"/"Ihołki". The Cyrillic
    # query matches fine but only turns up live reuploads (no official
    # release among them); the genuine official (Topic/track-metadata)
    # upload only appears under the transliterated Latin query.
    monkeypatch.setattr(m.subprocess, "run", _fake_run_by_query({
        "Адзіноцтва": [{"id": "liveVideoId", "title": "Test Artist - Адзіноцтва (live)", "channel": "Someone"}],
        "Adzinoctva": [{"id": "officialVideoId", "title": "Adzinoctva", "channel": "Test Artist - Topic"}],
    }))

    video_id = m.search_video_ytdlp("Test Artist", "Адзіноцтва", language="Belarusian")

    assert video_id == "officialVideoId"


def test_search_video_ytdlp_keeps_original_pick_when_transliterated_retry_is_also_not_official(monkeypatch):
    # Neither script turns up a confirmed official release — the original,
    # already-accepted pick should still win rather than being discarded for
    # an equally-unofficial alternate-script candidate.
    monkeypatch.setattr(m.subprocess, "run", _fake_run_by_query({
        "Адзіноцтва": [{"id": "originalVideoId", "title": "Test Artist - Адзіноцтва (live)", "channel": "Someone"}],
        "Adzinoctva": [{"id": "altVideoId", "title": "Test Artist - Adzinoctva (also live)", "channel": "Someone Else"}],
    }))

    video_id = m.search_video_ytdlp("Test Artist", "Адзіноцтва", language="Belarusian")

    assert video_id == "originalVideoId"


def test_search_video_ytdlp_does_not_retry_for_non_transliterable_language(monkeypatch):
    captured_queries = []

    def fake_run(command, **kwargs):
        captured_queries.append(command[-1])
        candidates = [{"id": "wrongVideoId", "title": "Unrelated video", "channel": "Someone"}]
        stdout = "\n".join(json.dumps(c) for c in candidates)

        class FakeCompletedProcess:
            def __init__(self, stdout):
                self.returncode = 0
                self.stdout = stdout

        return FakeCompletedProcess(stdout)

    monkeypatch.setattr(m.subprocess, "run", fake_run)

    video_id = m.search_video_ytdlp("Test Artist", "Адзіноцтва", language="Polish")

    assert video_id is None
    assert len(captured_queries) == 1  # no second attempt for a non-transliterable language


def test_score_result_selector_bonus_ignores_generic_bracket_content():
    # A plain "(Official Video)" bracket must never be treated as a
    # meaningful selector — every word in it is generic branding, not a
    # specific identifier, so it should reduce to no tokens at all and grant
    # no bonus to any random candidate that happens to also say "Official
    # Video" (which is nearly all of them).
    assert m._selector_tokens("Official Video") == set()


# ---------------------------------------------------------
# DB-reference regression: the same real songs, resolved via
# Song.youtube_video_id instead of a fresh search
# ---------------------------------------------------------
#
# Every test above proves the search/scoring engine (score_result(),
# search_video_ytdlp(), search_video()) resolves a given real song correctly
# with *no* database involved. But create_yt_playlist() (see
# manage_youtube_playlists.py) has a second path for the exact same songs:
# if Song.youtube_video_id already holds the right answer (confirmed by a
# prior run via save_video_id_to_song(), or set manually), it's reused
# directly — after is_video_id_valid() confirms it still resolves — instead
# of repeating the search. That path doesn't touch score_result() at all, so
# nothing above would catch a regression in it. This table re-runs the same
# real songs through create_yt_playlist() with the DB reference already
# populated, checking the other half of the same guarantee: "does this song
# still resolve to the video I approved" holds whether or not a fresh search
# is needed to get there.
DB_REFERENCE_REGRESSION_CASES = [
    ("Berlin - Sex (I'm A...)", BERLIN_SEX_ARTIST, BERLIN_SEX_TITLE, BERLIN_SEX_VIDEO_ID),
    ("Foals - 2001", "Foals", "2001", "ydBQz3SecaE"),
    ("SLAUGHTER TO PREVAIL - Bratva", "SLAUGHTER TO PREVAIL", "Bratva", "XFzB3TXoG4A"),
    ("Passive Voice - Тебе пам'ятаю", "Passive Voice", "Тебе пам'ятаю", "Vd5epmcIamY"),
    ("Ten Preston feat. Sitek - 71", "Ten Preston feat. Sitek", "71 (prod. lil aloes)", "R5eMExxWu1M"),
    ("Gary Clark Jr. - Ain't Messin' 'Round", "Gary Clark Jr.", "Ain't Messin' 'Round", "EyFFuEY_S6Q"),
    ("WE BUTTER THE BREAD WITH BUTTER - N!CE", "WE BUTTER THE BREAD WITH BUTTER", "N!CE", "E49Qrhdk2cI"),
    ("Måneskin - Zitti e buoni", "Måneskin", "Zitti e buoni", "QN1odfjtMoo"),
    ("Bloodywood - Ari Ari", "Bloodywood", "INDIAN STREET METAL (_Ari Ari_ ft. Raoul Kerr)", "i4FqGPRQWFM"),
    ("Zob - Cantec de dragoste", "Zob", "Cantec de dragoste", "D398DGsBP1I"),
    ("Bastille - Pompei", "Bastille", "Pompei", "F90Cw4l-8NY"),
    ("ABRADAB - Niesmiertelnosc", "ABRADAB", "Niesmiertelnosc (Muzyka Daje)", "T3bqVFKEoDA"),
    ("bayski - набіраи", "bayski", "набіраи (acoustic)", "l8cJML7BJUg"),
    ("Rival Sons - Darkfighter", "Rival Sons", "Darkfighter", "GEW7zR1aIUI"),
    ("Elvis Crespo - Suavemente", "Elvis Crespo", "Suavemente", "WPiEbYSF9kE"),
    ("Бумбокс - Нездара", "Бумбокс", "Нездара", "zDVzqqTzTDE"),
    ("Hall & Oates - Maneater", "Hall & Oates", "Maneater", "yRYFKcMa_Ek"),
    ("Junecapone - Depravity", "Junecapone", "Depravity", "qMmVQbH3sKQ"),
    ("Плач Єремії - Вона", "Плач Єремії", "Вона", "EaQEnpYoA2U"),
    ("Daði Freyr - Bitte", "Daði Freyr", "Bitte", "Wotwtc9tA-Q"),
    ("OBERSCHLESIEN - Król Olch", "OBERSCHLESIEN", "Król Olch", "KPJPJzpk_QQ"),
    ("Valeria Stoica - Get Back", "Valeria Stoica", "Get Back (Lorin Rymbu & Denis Rynda Remix Extended)", "zliatPv0RGU"),
    ("Al Di Meola - Double Concerto", "Al Di Meola", "Double Concerto", "Hi1KxO32DFU"),
    ("SunSay - В твоих глазах сияю я", "SunSay", "В твоих глазах сияю я", "xQejzb1JDQo"),
    ("SunSay - Немовля", "SunSay", "Немовля", "_edxVM3PylU"),
    ("Waglewski, Fisz, Emade - Bóg", "Waglewski, Fisz, Emade", "Bóg", "tRHIqK_vy6M"),
    ("Вольны хор - Пагоня", "Вольны хор", "Пагоня (запіс з анлайн - канцэрта Муры)", "5kgvHgqr2aE"),
    ("Benjamin Clementine - Cornerstone", "Benjamin Clementine", "Cornerstone", "7Dc5BQ31iLw"),
    # The one case where the without-DB-reference test above
    # (test_search_video_ytdlp_declines_when_only_match_is_a_translation_video)
    # asserts `video_id is None`: the real video is age-restricted and
    # invisible to every search backend this app uses (see that test's
    # docstring). Song.youtube_video_id is the *only* way this song ever
    # resolves at all — this case is exactly why the DB-reference path
    # exists, not just an alternate route to an answer search could find
    # anyway.
    ("Taco Hemingway - Fuck Your List (DB-only, search finds nothing)", "Taco Hemingway", "Fuck Your List", "bLOSjREikDc"),
]


def _resolve_via_db_reference(monkeypatch, artist: str, title: str, video_id: str) -> str:
    """Run create_yt_playlist() for a single song whose Song.youtube_video_id
    is already `video_id`, with every dependency except the DB-reference
    logic itself stubbed out, and search_video() wired to fail the test if
    it's ever called. Returns whatever video_id actually got added to the
    playlist, so the caller can assert it matches the approved id — proving
    the "check the DB, validate, reuse" path resolves the song correctly on
    its own, independent of the search/scoring logic the rest of this file
    exercises.
    """
    monkeypatch.setattr(m, "get_youtube_service", lambda: object())
    monkeypatch.setattr(m, "load_cache", lambda: None)
    monkeypatch.setattr(m, "create_playlist", lambda youtube, title, description="": "PLAYLIST1")
    monkeypatch.setattr(yt_cache, "save_cache", lambda cache: None)
    monkeypatch.setattr(m, "save_cache", lambda cache: None)
    monkeypatch.setattr(m, "clear_cache", lambda: None)
    monkeypatch.setattr(m, "is_video_id_valid", lambda vid, timeout=20: True)
    monkeypatch.setattr(m, "submit_global_database_session", lambda: None)

    def fail_if_searched(*a, **k):
        raise AssertionError(f"search_video should not run for {artist} - {title}: a valid DB reference was available")

    monkeypatch.setattr(m, "search_video", fail_if_searched)

    added = []
    monkeypatch.setattr(m, "add_video_to_playlist", lambda youtube, playlist_id, vid: added.append(vid) or True)

    artist_obj = SimpleNamespace(name=artist, synonyms=None)
    song = SimpleNamespace(artist=artist_obj, title=title, language=None, youtube_video_id=video_id)

    m.create_yt_playlist([song], "Regression Playlist")

    assert len(added) == 1, f"expected exactly one video added to the playlist for {artist} - {title}"
    return added[0]


@pytest.mark.parametrize(
    "artist, title, expected_video_id",
    [case[1:] for case in DB_REFERENCE_REGRESSION_CASES],
    ids=[case[0] for case in DB_REFERENCE_REGRESSION_CASES],
)
def test_regression_suite_resolves_via_db_reference(monkeypatch, artist, title, expected_video_id):
    resolved = _resolve_via_db_reference(monkeypatch, artist, title, expected_video_id)
    assert resolved == expected_video_id


# ---------------------------------------------------------
# Fresh-search regression: the same real songs, resolved with NO
# Song.youtube_video_id involved at all
# ---------------------------------------------------------
#
# DB_REFERENCE_REGRESSION_CASES above proves "if the DB already has the right
# answer, we keep reusing it" — but it deliberately never calls search_video()
# (that path fails the test on purpose, see _resolve_via_db_reference's
# fail_if_searched). Every individual test earlier in this file already
# proves the other half — that a *fresh* search resolves a given real song
# correctly on its own — but each one lives under its own bespoke name and
# docstring documenting the specific bug it caught; there was no single place
# to see "yes, every real case in the DB-reference list is still covered by
# an independent fresh-search path", the way the table above makes that
# explicit for the DB path. This section restates the *same* real candidate
# pools already used above (copied, not imported — this table is meant to be
# a standalone audit of coverage, not a refactor of the tests above, so a
# rename/removal of one of those tests' local variables can't silently shrink
# this list too) and re-verifies every one via search_video_ytdlp() (or
# search_video() for the one case — Berlin — whose bug lives in the Data API
# fallback, not yt-dlp), completely independent of whatever Song.youtube_video_id
# a real database might hold.
#
# One entry is deliberately not "resolves to the same id": Taco Hemingway -
# Fuck Your List is expected to resolve to None here — the real video is
# age-restricted and invisible to every search backend this app uses (see
# that song's dedicated test above), so Song.youtube_video_id is the *only*
# way it ever resolves. A fresh search correctly finding nothing is the
# right behavior, not a gap this table should paper over.
FRESH_SEARCH_REGRESSION_CASES = [
    (
        "Berlin - Sex (I'm A...)",
        BERLIN_SEX_ARTIST, BERLIN_SEX_TITLE,
        [],  # yt-dlp's anonymous search returns nothing for this query, always (see safeSearch case above)
        (BERLIN_SEX_VIDEO_ID,),
        {"api_items": [
            {"id": {"videoId": BERLIN_SEX_VIDEO_ID}, "snippet": {"title": "Berlin - Sex (I'm A...) [Official Audio]", "channelTitle": "Berlin - Topic"}},
        ]},
    ),
    (
        "Foals - 2001", "Foals", "2001",
        [
            {"id": "ydBQz3SecaE", "title": "FOALS - 2001 [Official Music Video]", "channel": "Foals"},
            {"id": "E2WD04MPkG4", "title": "FOALS x LONDON CONTEMPORARY ORCHESTRA - 2001 [Official Video]", "channel": "Foals"},
            {"id": "hhUaLCBqpWA", "title": "Foals - 2001 Demo Version (2001 Vibes)", "channel": "Isolarian"},
            {"id": "NK4uIxFkEL0", "title": "Foals - 2001 (Lyrics)", "channel": "Lyriclist"},
            {"id": "1dSy1psZhGE", "title": "FOALS: 2001 // Life Is Yours // The Colour Wheel Session", "channel": "Foals"},
            {"id": "n1zr1LBr51c", "title": "FOALS - '2001'", "channel": "ACID STAG"},
            {"id": "CYU9Yz2RayU", "title": MYD_REMIX_TITLE, "channel": "Foals"},
            {"id": "SVxLol5MIdA", "title": "Foals - 2001 (Glastonbury 2022)", "channel": "BBC Music"},
            {"id": "6Q_58jY9A0Q", "title": DAN_CAREY_DUB_TITLE, "channel": "Foals"},
        ],
        ("ydBQz3SecaE",), {},
    ),
    (
        "SLAUGHTER TO PREVAIL - Bratva", "SLAUGHTER TO PREVAIL", "Bratva",
        [
            {"id": "iUZRLYfHEgA", "title": "Slaughter To Prevail - Bratva (Live In Moscow)", "channel": "SUMERIAN", "view_count": 3737158},
            {"id": "XFzB3TXoG4A", "title": "Slaughter To Prevail - Bratva", "channel": "SUMERIAN", "view_count": 8369100},
            {"id": "zRJSZKW4r1Q", "title": "Slaughter To Prevail - BRATVA - Live @ INKcarceration Fest 2023 @AlexTerrible", "channel": "EvilVox", "view_count": 224622},
            {"id": "CMoSzybZ2BQ", "title": "SLAUGHTER TO PREVAIL - Bratva (Lyrics/перевод)", "channel": "Hard Rock World", "view_count": 60425},
            {"id": "WKb0gB1lLww", "title": "SLAUGHTER TO PREVAIL  HELLFEST LIVE BIGGEST WALL OF DEATH 2024", "channel": "Alex Terrible", "view_count": 1010008},
            {"id": "4GXlOS486fs", "title": "Slaughter To Prevail - Bratva (Lyric Video) (HQ)", "channel": "Black Fire", "view_count": 73481},
            {"id": "87K_mGzACpc", "title": "Musician's Slaughter To Prevail reaction - Bratva", "channel": "Steve O'G", "view_count": 9555},
        ],
        ("XFzB3TXoG4A",), {},
    ),
    (
        "Passive Voice - Тебе пам'ятаю", "Passive Voice", "Тебе пам'ятаю",
        [
            {"id": "Vd5epmcIamY", "title": "Passive Voice - Тебе пам’ятаю", "channel": "Passive Voice", "view_count": 347},
            {"id": "jJbAmWBLs80", "title": "Passive Voice – Тебе пам’ятаю @ TNT Rock Club, Мінск, 02.11.2025", "channel": "ochtilno", "view_count": 15},
            {"id": "3sG_TPI-eiA", "title": "Black Line Studio | Passive Voice | Live", "channel": "Black Line Studio", "view_count": 10048},
            {"id": "_ezASsdYwmY", "title": "Нажаль | Passive Voice", "channel": "Passive Voice", "view_count": 3809},
        ],
        ("Vd5epmcIamY",), {},
    ),
    (
        "Ten Preston feat. Sitek - 71", "Ten Preston feat. Sitek", "71 (prod. lil aloes)",
        [
            {"id": "R5eMExxWu1M", "title": "Ten Preston feat. Sitek - 71 (prod. lil aloes)", "channel": "chillwagon", "view_count": 3260870},
            {"id": "RZVKNq7OwoI", "title": "Ten Preston feat. Sitek - 71 (prod. lil aloes) (Bass Boosted)", "channel": "FistachNation", "view_count": 1994},
            {"id": "0PWBdfKU5Xs", "title": "Ten Preston - SHEESH (prod. lil aloes)", "channel": "chillwagon", "view_count": 2154422},
            {"id": "31VKUYwRjQY", "title": "Ten Preston feat. Sitek - 71 (prod. lil aloes) (Bass Boosted)", "channel": "Bass Boosted", "view_count": 518},
            {"id": "kzqzV_TNVMw", "title": "Ten Preston - Fan (prod. lil aloes)", "channel": "chillwagon", "view_count": 140285},
        ],
        ("R5eMExxWu1M",), {},
    ),
    (
        "Gary Clark Jr. - Ain't Messin' 'Round", "Gary Clark Jr.", "Ain't Messin' 'Round",
        [
            {"id": "fyBem5-Bfpg", "title": "Gary Clark Jr. - Ain't Messin' Round [Official Music Video]", "channel": "garyclarkjr", "view_count": 1029669},
            {"id": "EyFFuEY_S6Q", "title": "Gary Clark Jr - Ain't Messin 'Round [Official Audio]", "channel": "garyclarkjr", "view_count": 400470},
            {"id": "AdK8QEMIKN4", "title": "Gary Clark Jr - Ain't Messin' 'Round (Live at Farm Aid 2014)", "channel": "Farm Aid", "view_count": 64223},
        ],
        ("EyFFuEY_S6Q",), {},
    ),
    (
        "WE BUTTER THE BREAD WITH BUTTER - N!CE", "WE BUTTER THE BREAD WITH BUTTER", "N!CE",
        [
            {"id": "E49Qrhdk2cI", "title": "WE BUTTER THE BREAD WITH BUTTER - N!CE (2021) // Official Music Video // AFM Records", "channel": "AFM Records", "view_count": 890446},
            {"id": "qSNnl-w0_c8", "title": "We Butter The Bread With Butter - N!CE (React/Review)", "channel": "Mat and Chels React", "view_count": 13231},
        ],
        ("E49Qrhdk2cI",), {},
    ),
    (
        "Måneskin - Zitti e buoni", "Måneskin", "Zitti e buoni",
        [
            {"id": "QN1odfjtMoo", "title": "Måneskin - ZITTI E BUONI (Official Video – Sanremo & EUROVISION 2021 Winners)", "channel": "Måneskin Official", "view_count": 230071757},
            {"id": "0Upt-ddaw04", "title": "ZITTI E BUONI (Eurovision Version)", "channel": "Måneskin Official", "view_count": 8897851},
        ],
        ("QN1odfjtMoo",), {},
    ),
    (
        "Bloodywood - Ari Ari", "Bloodywood", "INDIAN STREET METAL (_Ari Ari_ ft. Raoul Kerr)",
        [
            {"id": "i4FqGPRQWFM", "title": 'INDIAN STREET METAL ("Ari Ari" ft. Raoul Kerr) - Bloodywood', "channel": "Bloodywood", "view_count": 7946578},
            {"id": "6uJoN_I9ebQ", "title": 'INDIAN FOLK METAL (Bloodywood - "Jee Veerey" ft. Raoul Kerr)', "channel": "Bloodywood", "view_count": 2536950},
            {"id": "1nvgTbEdH8E", "title": "Ari Ari", "channel": "Bloodywood", "view_count": 313093},
            {"id": "TcpgRq-bCTs", "title": 'INDIAN STREET METAL ("Ari Ari" ft. Raoul Kerr) - Bloodywood (REACTION)', "channel": "Alex N Channel", "view_count": 4556},
            {"id": "Fvu3uPNiWNk", "title": "Bloodywood - Ari Ari - Live at Wacken Open Air 2019", "channel": "WackenTV", "view_count": 290477},
            {"id": "LZcjfLMzDuw", "title": "Bloodywood & Raoul Kerr - Ari Ari", "channel": "All Kind Of Music", "view_count": 201},
            {"id": "a65A626Ed20", "title": "Bloodywood - Dana Dan (Indian Folk Metal)", "channel": "Bloodywood", "view_count": 9423555},
        ],
        ("i4FqGPRQWFM",), {},
    ),
    (
        # Either the band's own upload or the official video is accepted —
        # same tolerance as this song's dedicated test above, which found
        # both legitimate once containment-based artist_relevance was fixed.
        "Zob - Cantec de dragoste", "Zob", "Cantec de dragoste",
        [
            {"id": "KGPft935c00", "title": "Zob - Cantec De Dragoste (Official Video)", "channel": "Roton Hits", "view_count": 188747},
            {"id": "D398DGsBP1I", "title": "Zob & Mara - Cantec de dragoste", "channel": "Vali Moga", "view_count": 54880},
            {"id": "5n_cKrQYeoU", "title": "ZOB- 'Cântec de dragoste' la BTLive @Guerrilive Radio Session", "channel": "Radio Guerrilla", "view_count": 5904},
            {"id": "X86Vjl3SCpc", "title": "Alexandra Usurelu și Dan Bordeianu - Cantec de dragoste", "channel": "Alexandra Usurelu", "view_count": 563148},
            {"id": "-oDSUwRm64Y", "title": "Zob - Cantec de dragoste", "channel": "Alexandru Popa", "view_count": 1095},
            {"id": "-lssuVMpvFo", "title": "Zob-Cantec de dragoste ( Cristina Țițescu cover)", "channel": "Cristina Țițescu", "view_count": 1046},
        ],
        ("D398DGsBP1I", "KGPft935c00"), {},
    ),
    (
        "Bastille - Pompei", "Bastille", "Pompei",
        [
            {"id": "F90Cw4l-8NY", "title": "Bastille - Pompeii (Official Music Video)", "channel": "BASTILLEvideos", "view_count": 839916374},
            {"id": "ilLEuwH4hws", "title": "Bastille - Pompeii (Lyric Video)", "channel": "BASTILLEvideos", "view_count": 32408712},
            {"id": "cvQ2LF3hyuY", "title": "Bastille - Pompeii (Lyrics)", "channel": "Cosmos Music", "view_count": 23270768},
        ],
        ("F90Cw4l-8NY",), {},
    ),
    (
        "ABRADAB - Niesmiertelnosc", "ABRADAB", "Niesmiertelnosc (Muzyka Daje)",
        [
            {"id": "vIGV7z31vj8", "title": "Nieśmiertelność (Live)", "channel": "Abradab - Topic", "view_count": 294},
            {"id": "T3bqVFKEoDA", "title": "ABRADAB - Nieśmiertelność (Muzyka daje) [OFFICIAL AUDIO]", "channel": "S.P. RECORDS", "view_count": 22699},
        ],
        ("T3bqVFKEoDA",), {},
    ),
    (
        "bayski - набіраи", "bayski", "набіраи (acoustic)",
        [
            {"id": "l8cJML7BJUg", "title": "bayski - набірай (acoustic)", "channel": "bayski", "view_count": 686},
        ],
        ("l8cJML7BJUg",), {},
    ),
    (
        "Rival Sons - Darkfighter", "Rival Sons", "Darkfighter",
        [
            {"id": "uX7ZXF2EyeE", "title": "DARKFIGHTER Track by Track (Official)", "channel": "Rival Sons", "view_count": 50000},
            {"id": "GEW7zR1aIUI", "title": 'Rival Sons - "DARKFIGHTER" (Official Audio)', "channel": "Rival Sons", "view_count": 97112},
        ],
        ("GEW7zR1aIUI",), {},
    ),
    (
        "Elvis Crespo - Suavemente", "Elvis Crespo", "Suavemente",
        [
            {"id": "UMYAdGEXOn0", "title": "Luck Ra, Elvis Crespo - SUAVEMENTE", "channel": "Luck Ra", "view_count": 19611031, "tags": ["Suavemente", "Luck Ra", "Elvis Crespo", "Cuarteto", "Latin", "En vivo", "Fiesta"]},
            {"id": "WPiEbYSF9kE", "title": "Elvis Crespo - Suavemente", "channel": "Elvis Crespo", "view_count": 299876074, "tags": [
                "suavemente álbum", "ElvisCrespovevo", "tu sonrisa", "official", "video",
                "vídeo musical", "music video", "elvis crespo en directo", "album",
                "Salsa tropical", "elvis crespo en vivo", "instrumental", "música",
                "dance", "karaoke", "remix", "en directo", "audio",
            ]},
        ],
        ("WPiEbYSF9kE",), {},
    ),
    (
        "Бумбокс - Нездара", "Бумбокс", "Нездара",
        [
            {"id": "G7-lJGOOOIc", "title": "Бумбокс - Нездара", "channel": "Maria Matrunich", "view_count": 1154},
            {"id": "zDVzqqTzTDE", "title": "Нездара", "channel": "familyboombox", "view_count": 674464},
        ],
        ("zDVzqqTzTDE",), {"artist_synonyms": "familyboombox"},
    ),
    (
        "Hall & Oates - Maneater", "Hall & Oates", "Maneater",
        [
            {"id": "IqF7S3zXl1A", "title": "Daryl Hall & John Oates - Maneater (Lyrics)", "channel": "7clouds", "view_count": 3999026},
            {"id": "yRYFKcMa_Ek", "title": "Daryl Hall & John Oates - Maneater (Official Video)", "channel": "Daryl Hall & John Oates", "view_count": 404412123},
        ],
        ("yRYFKcMa_Ek",), {"artist_synonyms": "Daryl Hall & John Oates"},
    ),
    (
        "Junecapone - Depravity", "Junecapone", "Depravity",
        [
            {"id": "qMmVQbH3sKQ", "title": "Depravity", "channel": "June - Topic", "view_count": 106},
        ],
        ("qMmVQbH3sKQ",), {"artist_synonyms": "June"},
    ),
    (
        "Плач Єремії - Вона", "Плач Єремії", "Вона",
        [
            {"id": "EaQEnpYoA2U", "title": "Вона", "channel": "Taras Chubai", "view_count": 55337789},
        ],
        ("EaQEnpYoA2U",), {"artist_synonyms": "Taras Chubai, Тарас Чубай"},
    ),
    (
        "Daði Freyr - Bitte", "Daði Freyr", "Bitte",
        [
            {"id": "Wotwtc9tA-Q", "title": "Daði Freyr - Bitte (Official Video)", "channel": "Daði Freyr", "view_count": 235334},
            {"id": "2oDi4xLk1L0", "title": "Daði Freyr - Bitte (Live from Vikan með Gísla Marteini)", "channel": "Daði Freyr", "view_count": 23322},
        ],
        ("Wotwtc9tA-Q",), {},
    ),
    (
        "OBERSCHLESIEN - Król Olch", "OBERSCHLESIEN", "Król Olch",
        [
            {"id": "NhPpu_WGAng", "title": "Oberschlesien - Król Olch #Woodstock2016", "channel": "KręciołaTV", "view_count": 5123817, "tags": ["Oberschlesien Król Olch na żywo", "oberschlesien woodstock", "polandrock festival"]},
            {"id": "KPJPJzpk_QQ", "title": "OBERSCHLESIEN - Król Olch [OFFICIAL VIDEO]", "channel": "S.P. RECORDS", "view_count": 1417183, "tags": ["s.p. records", "OBERSCHLESIEN", "Król Olch"]},
        ],
        ("KPJPJzpk_QQ",), {},
    ),
    (
        "Benjamin Clementine - Cornerstone", "Benjamin Clementine", "Cornerstone",
        [
            {"id": "CJJNl1p-PGA", "title": "Benjamin Clementine - Cornerstone - Later... with Jools Holland - BBC Two HD", "channel": "BBC", "view_count": 1155234},
            {"id": "7Dc5BQ31iLw", "title": "Benjamin Clementine - Cornerstone (Official Video)", "channel": "Benjamin Clementine", "view_count": 6706571},
        ],
        ("7Dc5BQ31iLw",), {},
    ),
    (
        "Valeria Stoica - Get Back", "Valeria Stoica", "Get Back (Lorin Rymbu & Denis Rynda Remix Extended)",
        [
            {"id": "YeQHuxFw31I", "title": "Valeria Stoica — Get Back (Deepshader's Reconstruction)", "channel": "Valeria Stoica", "view_count": 1202},
            {"id": "zliatPv0RGU", "title": "Valeria Stoica - Get Back (Lorin Rymbu & Denis Rynda Remix)", "channel": "Valeria Stoica", "view_count": 1938},
        ],
        ("zliatPv0RGU",), {},
    ),
    (
        "Al Di Meola - Double Concerto", "Al Di Meola", "Double Concerto",
        [
            {"id": "3KLQfbHCYKU", "title": "Al Di Meola - Double Concerto - Live in Warsaw, 2000 [3]", "channel": "AhnDanil", "view_count": 39943},
            {"id": "qLXJTpgS-w4", "title": "Al di Meola - Double Concerto", "channel": "Harmonia Cordis A.", "view_count": 19824},
            {"id": "Hi1KxO32DFU", "title": "Double Concerto", "channel": "Al Di Meola", "view_count": 10883, "track": "Double Concerto"},
            {"id": "ua9iYiOtqEE", "title": "Al Di Meola - Horgas Eszter: Astor Piazzolla - Double concerto (Budapest, 2008)", "channel": "ttttunde", "view_count": 91489},
            {"id": "8fTeHHLcxYs", "title": "Double Concerto", "channel": "Al Di Meola", "view_count": 2294, "track": "Double Concerto"},
            {"id": "vUelO4lctg4", "title": "AL DI MEOLA   - Double Concerto", "channel": "Jazz 4 All", "view_count": 1474},
            {"id": "Q4g6Ln1kX50", "title": 'Al Di Meola in Sofia "Double Concerto"', "channel": "Dimitar Velichkov", "view_count": 7106},
            {"id": "hCUYSTjfDbw", "title": "Double Concerto", "channel": "Al Di Meola", "view_count": 1802, "track": "Double Concerto"},
        ],
        ("Hi1KxO32DFU",), {},
    ),
    (
        "SunSay - В твоих глазах сияю я", "SunSay", "В твоих глазах сияю я",
        [
            {"id": "tpOD1ZgP2Ik", "title": "SunSay – В твоих глазах сияю я | fairlane acoustic (2011)", "channel": "fairlane acoustic", "view_count": 34277},
            {"id": "xQejzb1JDQo", "title": "В твоих глазах сияю я", "channel": "sunsaymusic", "view_count": 16639, "track": "В твоих глазах сияю я"},
            {"id": "crnWx2Avou4", "title": "Sunsay - В твоих глазах сияю я", "channel": "MaximAndrosyuk", "view_count": 17387},
            {"id": "Jo0k5OkNk_Y", "title": "SunSay - В твоих глазах сияю я", "channel": "Алексей Лакиза", "view_count": 485},
            {"id": "bdGkqE1lUrs", "title": "В твоих глазах сияю я (Sunsay Cover)", "channel": "Леонид Оганесян", "view_count": 1933},
            {"id": "kQlW8ZIb94o", "title": "Sunsay - В твоих глазах сияю я", "channel": "Alexander Tokarev", "view_count": 108},
            {"id": "QQ6ckbaIaG8", "title": "SunSay - В Твоих Глазах Сияю Я", "channel": "marinellagomes", "view_count": 240},
            {"id": "exGnkkLBChQ", "title": "SunSay - В твоих глазах сияю я (cover)", "channel": "Алексей Леонов", "view_count": 1144},
        ],
        ("xQejzb1JDQo",), {},
    ),
    (
        "SunSay - Немовля", "SunSay", "Немовля",
        [
            {"id": "_edxVM3PylU", "title": "Немовля", "channel": "sunsaymusic", "view_count": 14724, "track": "Немовля"},
            {"id": "hahNWO_nIuA", "title": "SunSay  - Немовля", "channel": "sunsaymusic", "view_count": 16647},
            {"id": "0qRq6rscWfU", "title": "Sunsay - Немовля [MINUS vocal; Karaoke]", "channel": "MINUS vocal; Karaoke", "view_count": 18},
            {"id": "yPMAVzzfi4o", "title": "SunSay - Немовля", "channel": "Nelu Botnaru", "view_count": 3570},
            {"id": "bnsTeKZnxzo", "title": "SunSay - Немовля.mp4", "channel": "TheWillyamG", "view_count": 100},
            {"id": "N2DaKCUyX0M", "title": "Sunsay - Немовля", "channel": "IExistExist", "view_count": 810},
            {"id": "hsveP_cuA7g", "title": "SunSay - Немовля (Live)", "channel": "Dima Suchay", "view_count": 82},
            {"id": "K8ZmBpOIBjc", "title": "SunSay - Немовля [live in Cherkassy 19.04.2013]", "channel": "Vadym Sapatrylo", "view_count": 609},
        ],
        ("_edxVM3PylU",), {},
    ),
    (
        "Waglewski, Fisz, Emade - Bóg", "Waglewski, Fisz, Emade", "Bóg",
        [
            {"id": "tRHIqK_vy6M", "title": "Bóg", "channel": "Waglewski / Fisz / Emade - Topic", "view_count": 21231, "track": "Bóg"},
            {"id": "ZeQecB1KyCw", "title": "Waglewski Fisz Emade - Bóg/Ile jeszcze życia? - live in Toruń 14.11.2018", "channel": "Piotr Raczkowski", "view_count": 339},
            {"id": "ROTYmNckBCw", "title": "Waglewski Fisz Emade - Ojciec", "channel": "Next Music", "view_count": 1125464},
            {"id": "t7r6x_Ajuho", "title": "Waglewski Fisz Emade - Bóg (live Frytka Off)", "channel": "nothingsalright", "view_count": 1684},
            {"id": "J4_4dJawgM4", "title": "Waglewski Fisz Emade - Syn", "channel": "Next Music", "view_count": 397227},
            {"id": "RuIeM4Qu_NE", "title": "Waglewski Fisz Emade - Bóg - 26.11.2013", "channel": "slodzias", "view_count": 10561},
            {"id": "sySsenGiBUw", "title": "Waglewski Fisz Emade - Ziemia (Official Video)", "channel": "Mystic Production TV", "view_count": 619553},
        ],
        ("tRHIqK_vy6M",), {},
    ),
    (
        "Вольны хор - Пагоня", "Вольны хор", "Пагоня (запіс з анлайн - канцэрта Муры)",
        [
            {"id": "jn_vnj4_vQg", "title": "Вольны хор — Пагоня (запіс з анлайн-канцэрта «Муры»)", "channel": "symbal.by", "view_count": 6195},
            {"id": "x_52myRgoGM", "title": "Вольны хор: Нацыянальны гімн Беларусі «Пагоня» (калядны анлайн-канцэрт)", "channel": "Годна", "view_count": 129043},
            {"id": "suN9LYsC1qU", "title": "«Вольны хор» прэзентаваў альбом «Годныя песні»: Пагоня, Муры, Магутны Божа, Сцяг, Гэта мы, Нёман", "channel": "Годна", "view_count": 28623},
            {"id": "Nxs3TGwI07Q", "title": "Вольны хор — Жыве Беларусь (анлайн-канцэрт «Муры», 27.05.2021)", "channel": "Годна", "view_count": 4901},
            {"id": "FsskFUad88o", "title": 'VOLNY CHOR / ВОЛЬНЫ ХОР - Ой, шлі-прайшлі (анлайн-канцэрт "Муры", 27.05.2021)', "channel": "VOLNY CHOR / ВОЛЬНЫ ХОР", "view_count": 4899},
            {"id": "5kgvHgqr2aE", "title": "Пагоня", "channel": "Вольны Хор - Topic", "view_count": 70680, "track": "Пагоня"},
            {"id": "tpLfAhQUZy8", "title": "Вольны хор — Разбуры турмы муры (запіс з анлайн-канцэрта «Муры»)", "channel": "symbal.by", "view_count": 7311},
            {"id": "8o1-Zt5eN1Y", "title": "Вольны хор — Гэта мы (запіс з анлайн-канцэрта «Муры»)", "channel": "symbal.by", "view_count": 3437},
        ],
        ("5kgvHgqr2aE",), {},
    ),
    (
        # Deliberately NOT the DB-reference id — see this table's docstring.
        # The real video is age-restricted and invisible to every search
        # backend this app uses; a fresh search correctly finding nothing is
        # the expected, correct behavior here, not a gap.
        "Taco Hemingway - Fuck Your List (expected: unresolvable via search)", "Taco Hemingway", "Fuck Your List",
        [
            {"id": "qNmEvnJS-kI", "title": "Tłumaczenie Taco Hemingway - Fuck Your List | LyricsTranslationTV", "channel": "LyricsTranslationTV", "view_count": 5496},
            {"id": "8cHBSpb2XE4", "title": "Taco Hemingway - YOUNG HEMS  - cały album", "channel": "Pełne Albumy", "view_count": 138371},
            {"id": "xiZSpazO_Jo", "title": "Fuck ya list", "channel": "Paccman chico - Topic", "view_count": 130, "track": "Fuck ya list"},
            {"id": "gZ4PX3NXRFs", "title": "Alfabet z Taco Hemingwayem - ABC (nie) dla dzieci", "channel": "Maciej Adamczyk", "view_count": 2010},
            {"id": "PT14Nu5bmXI", "title": "APPLY YOURSELF THEN TOUCH A BAG 8D", "channel": "Hey Lol", "view_count": 360},
        ],
        (None,), {},
    ),
]


def _resolve_via_fresh_search(monkeypatch, artist, title, candidates, kwargs):
    """Resolve one song via a brand-new search — no Song.youtube_video_id
    involved anywhere. Ordinarily mocks yt-dlp with `candidates` and calls
    search_video_ytdlp() directly; for the one case (`api_items` present)
    whose bug lives in the Data API fallback instead, mocks yt-dlp to return
    nothing (its real behavior for that query) and the API to return
    `api_items`, then calls the top-level search_video() so the fallback
    path itself is exercised too.
    """
    kwargs = dict(kwargs)
    api_items = kwargs.pop("api_items", None)
    if api_items is not None:
        _mock_ytdlp_search(monkeypatch, [])
        youtube = FakeYoutube(api_items)
        return m.search_video(youtube, artist, title, **kwargs)
    _mock_ytdlp_search(monkeypatch, candidates)
    return m.search_video_ytdlp(artist, title, **kwargs)


@pytest.mark.parametrize(
    "artist, title, candidates, expected_ids, kwargs",
    [case[1:] for case in FRESH_SEARCH_REGRESSION_CASES],
    ids=[case[0] for case in FRESH_SEARCH_REGRESSION_CASES],
)
def test_regression_suite_resolves_via_fresh_search(monkeypatch, artist, title, candidates, expected_ids, kwargs):
    resolved = _resolve_via_fresh_search(monkeypatch, artist, title, candidates, kwargs)
    assert resolved in expected_ids
