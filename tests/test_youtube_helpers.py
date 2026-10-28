"""YouTube helpers below the playlist and search code, with yt-dlp and the
Data API faked (YT-17): get_video_release_year() (release date before upload
date), video_durations_via_api() (batches of 50, ISO 8601 lengths, the
lengths kept per batch when a later one fails), and reading a playlist via
yt-dlp's --flat-playlist or the API (deleted / private videos left out,
paging, "couldn't try" told apart from "empty")."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from utils.youtube import import_from_playlist as ifp
from utils.youtube import manage_youtube_playlists as m


def _ytdlp(monkeypatch, module, stdout="", stderr="", returncode=0, raises=None):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if raises:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(module, "run_ytdlp", run)
    return calls


# ==================== get_video_release_year ====================

@pytest.mark.parametrize("printed, year", [
    ("20160923|2016|20200101", 2016),   # YouTube Music's "Released on" wins
    ("NA|1999|20200101", 1999),          # only the year
    ("NA|NA|20200101", 2020),            # a reupload: only when it went up
    ("NA|NA|NA", None),
])
def test_release_date_before_upload_date(monkeypatch, printed, year):
    calls = _ytdlp(monkeypatch, m, stdout=f"[info] something\n{printed}\n")
    assert m.get_video_release_year("abc123") == year
    assert calls[0][-1] == "https://www.youtube.com/watch?v=abc123"


def test_no_video_means_no_lookup(monkeypatch):
    calls = _ytdlp(monkeypatch, m, stdout="20160923|2016|NA")
    assert m.get_video_release_year(None) is None
    assert m.get_video_release_year(m.NO_VIDEO_SENTINEL) is None
    assert calls == []


def test_a_video_that_doesnt_resolve_is_logged(monkeypatch):
    logged = []
    monkeypatch.setattr(m, "flog", logged.append)
    _ytdlp(monkeypatch, m, returncode=1, stderr="WARNING: x\nERROR: [youtube] abc: Video unavailable")

    assert m.get_video_release_year("abc") is None
    assert "Video unavailable" in logged[0]


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("yt-dlp", 30), FileNotFoundError("yt-dlp")])
def test_yt_dlp_missing_or_too_slow_gives_no_year(monkeypatch, error, capsys):
    monkeypatch.setattr(m, "flog", lambda msg: None)
    _ytdlp(monkeypatch, m, raises=error)
    assert m.get_video_release_year("abc") is None
    assert "yt-dlp unavailable" in capsys.readouterr().out


# ==================== video_durations_via_api ====================

class FakeVideos:
    def __init__(self, durations, fail_on_call=None):
        self.durations = durations
        self.calls = []
        self.fail_on_call = fail_on_call

    def list(self, part, id, maxResults):
        ids = id.split(",")
        self.calls.append(ids)
        if self.fail_on_call == len(self.calls):
            return SimpleNamespace(execute=lambda: (_ for _ in ()).throw(RuntimeError("quota")))
        items = [{"id": v, "contentDetails": {"duration": self.durations[v]}} for v in ids if v in self.durations]
        return SimpleNamespace(execute=lambda: {"items": items})


def _api(monkeypatch, videos):
    monkeypatch.setattr(m, "_read_only_youtube_service", lambda: SimpleNamespace(videos=lambda: videos))
    monkeypatch.setattr(m, "flog", lambda msg: None)


def test_durations_in_seconds_skipping_the_no_video_mark_and_duplicates(monkeypatch):
    videos = FakeVideos({"a": "PT3M45S", "b": "PT1H2M3S", "c": "P1DT1S", "live": "P0D"})
    _api(monkeypatch, videos)

    result = m.video_durations_via_api(["a", "b", "a", None, "N/A", "c", "live", "gone"])
    assert result == {"a": 225, "b": 3723, "c": 86401, "live": 0}
    assert videos.calls == [["a", "b", "c", "live", "gone"]]


def test_durations_are_asked_for_50_at_a_time_and_handed_on_per_batch(monkeypatch):
    ids = [f"v{i}" for i in range(120)]
    videos = FakeVideos({v: "PT1M" for v in ids})
    _api(monkeypatch, videos)
    batches = []

    assert len(m.video_durations_via_api(ids, on_found=batches.append)) == 120
    assert [len(c) for c in videos.calls] == [50, 50, 20]
    assert [len(b) for b in batches] == [50, 50, 20]


def test_a_failing_batch_keeps_what_came_before_through_on_found(monkeypatch):
    ids = [f"v{i}" for i in range(60)]
    _api(monkeypatch, FakeVideos({v: "PT1M" for v in ids}, fail_on_call=2))
    batches = []

    assert m.video_durations_via_api(ids, on_found=batches.append) is None
    assert [len(b) for b in batches] == [50]


def test_no_api_at_all_is_none_and_nothing_to_ask_is_empty(monkeypatch):
    monkeypatch.setattr(m, "_read_only_youtube_service", lambda: None)
    assert m.video_durations_via_api(["a"]) is None
    assert m.video_durations_via_api([None, "N/A"]) == {}


# ==================== a playlist via yt-dlp / the API ====================

def _flat(**info):
    return json.dumps(info)


def test_flat_playlist_items_without_deleted_and_private_videos(monkeypatch):
    stdout = "\n".join([
        _flat(id="v1", title="Teksański", channel="Hey", duration=215),
        _flat(id="v2", title="[Private video]", channel=None),
        _flat(id="v3", title="[Deleted video]"),
        "not json at all",
        _flat(id="v4", title="Moja i twoja nadzieja", uploader="Hey - Topic"),
        _flat(title="no id"),
    ])
    calls = _ytdlp(monkeypatch, ifp, stdout=stdout)

    assert ifp._fetch_playlist_items_ytdlp("PL1") == [
        {"video_id": "v1", "title": "Teksański", "channel": "Hey", "duration": 215},
        {"video_id": "v4", "title": "Moja i twoja nadzieja", "channel": "Hey - Topic", "duration": None},
    ]
    assert "--flat-playlist" in calls[0] and calls[0][-1] == "https://www.youtube.com/playlist?list=PL1"


def test_flat_playlist_that_couldnt_be_read_is_none_not_empty(monkeypatch):
    # None sends the caller to the API (a Private playlist looks like this); [] would be "empty"
    _ytdlp(monkeypatch, ifp, stdout="   \n")
    assert ifp._fetch_playlist_items_ytdlp("PL1") is None
    monkeypatch.setattr(ifp, "flog", lambda msg: None)
    _ytdlp(monkeypatch, ifp, raises=subprocess.TimeoutExpired("yt-dlp", 180))
    assert ifp._fetch_playlist_items_ytdlp("PL1") is None


def test_api_playlist_items_over_several_pages(monkeypatch):
    pages = {
        None: {"items": [
            {"snippet": {"title": "Teksański", "resourceId": {"videoId": "v1"}, "videoOwnerChannelTitle": "Hey"}},
            {"snippet": {"title": "Private video", "resourceId": {"videoId": "v2"}}},
        ], "nextPageToken": "p2"},
        "p2": {"items": [
            {"snippet": {"title": "Deleted video", "resourceId": {"videoId": "v3"}}},
            {"snippet": {"title": "No id", "resourceId": {}}},
            {"snippet": {"title": "Cisza", "resourceId": {"videoId": "v4"}, "videoOwnerChannelTitle": "Hey"}},
        ]},
    }
    asked = []

    class Items:
        def list(self, part, playlistId, maxResults, pageToken):
            asked.append(pageToken)
            return SimpleNamespace(execute=lambda: pages[pageToken])
    monkeypatch.setattr(ifp, "get_youtube_service", lambda: SimpleNamespace(playlistItems=lambda: Items()))
    monkeypatch.setattr(ifp, "video_durations_via_api", lambda ids: {"v1": 215})

    items = ifp._fetch_playlist_items_api("PL1")
    assert [(i["video_id"], i["title"], i["channel"]) for i in items] == [("v1", "Teksański", "Hey"),
                                                                         ("v4", "Cisza", "Hey")]
    assert asked == [None, "p2"]
    # YT-21: lengths from videos.list, so a private playlist's full albums are known too
    assert [i["duration"] for i in items] == [215, None]


def test_api_playlist_items_without_lengths_when_the_api_cant_say(monkeypatch):
    page = {"items": [{"snippet": {"title": "A", "resourceId": {"videoId": "v1"}}}]}

    class Items:
        def list(self, **kwargs):
            return SimpleNamespace(execute=lambda: page)
    monkeypatch.setattr(ifp, "get_youtube_service", lambda: SimpleNamespace(playlistItems=lambda: Items()))
    monkeypatch.setattr(ifp, "video_durations_via_api", lambda ids: None)
    logged = []
    monkeypatch.setattr(ifp, "flog", logged.append)

    assert [i["duration"] for i in ifp._fetch_playlist_items_api("PL1")] == [None]
    assert "no video lengths" in logged[0]
