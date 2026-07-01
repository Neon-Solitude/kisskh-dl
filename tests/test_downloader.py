import pytest
import requests

from kissget import downloader as dl
from kissget.downloader import Downloader, NetworkBlockError, _video_select_args
from kissget.models.sub import SubItem


def test_top_tier_uses_auto_select():
    # Default/highest quality must stay byte-identical to the old behavior.
    assert _video_select_args("1080p") == ["--auto-select"]


def test_above_ladder_uses_auto_select():
    assert _video_select_args("2160p") == ["--auto-select"]


def test_invalid_quality_falls_back_to_auto_select():
    assert _video_select_args("best") == ["--auto-select"]


def test_mid_tier_selects_height_and_below_best():
    # 720p → match 720/540/480/360, pick the best of those, and still grab audio.
    assert _video_select_args("720p") == [
        "--select-video",
        'res="x(720|540|480|360)$":for=best',
        "--select-audio",
        "best",
    ]


def test_lowest_tier_matches_only_itself():
    assert _video_select_args("360p") == [
        "--select-video",
        'res="x(360)$":for=best',
        "--select-audio",
        "best",
    ]


class _FakeResponse:
    def __init__(self, content=b"", status_ok=True):
        self.content = content
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise requests.HTTPError("404 Not Found")


def test_failed_subtitle_is_skipped_not_written(monkeypatch, tmp_path):
    # A non-200 response must not produce a file, and must not raise.
    monkeypatch.setattr(
        "kissget.downloader.requests.get",
        lambda *a, **k: _FakeResponse(b"<html>blocked</html>", status_ok=False),
    )
    downloader = Downloader(referer="https://kisskh.nl")
    sub = SubItem(src="https://cdn.example/ep1.srt", label="English", land="en", default=False)
    base = tmp_path / "Show_E01"

    downloader.download_subtitles([sub], str(base))

    assert not (tmp_path / "Show_E01.en.srt").exists()


def test_successful_subtitle_is_written(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "kissget.downloader.requests.get",
        lambda *a, **k: _FakeResponse(b"1\n00:00:01,000 --> 00:00:02,000\nhi\n"),
    )
    downloader = Downloader(referer="https://kisskh.nl")
    sub = SubItem(src="https://cdn.example/ep1.srt", label="English", land="en", default=False)
    base = tmp_path / "Show_E01"

    downloader.download_subtitles([sub], str(base))

    written = tmp_path / "Show_E01.en.srt"
    assert written.exists()
    assert written.read_bytes().startswith(b"1\n")


class _ProbeResponse:
    def __init__(self, status_code=200, location=""):
        self.status_code = status_code
        self.headers = {"Location": location} if location else {}


def test_detect_block_via_http_redirect(monkeypatch):
    dl._block_cache.clear()
    monkeypatch.setattr(
        dl.requests,
        "get",
        lambda *a, **k: _ProbeResponse(302, "https://block.charter-prod.hosted.cujo.io/warn.html?url=x"),
    )
    monkeypatch.setattr(dl.requests, "head", lambda *a, **k: _ProbeResponse(200))

    reason = dl._detect_network_block("https://hls08.streamcdn1.site/ep.ts")
    assert reason is not None
    assert "streamcdn1.site" in reason and "cujo" in reason.lower()


def test_detect_block_via_tls_failure(monkeypatch):
    dl._block_cache.clear()
    monkeypatch.setattr(dl.requests, "get", lambda *a, **k: _ProbeResponse(200))  # no redirect

    def _raise_ssl(*a, **k):
        raise dl.requests.exceptions.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number")

    monkeypatch.setattr(dl.requests, "head", _raise_ssl)

    reason = dl._detect_network_block("https://hls09.streamcdn1.site/ep.ts")
    assert reason is not None
    assert "TLS" in reason


def test_detect_block_none_when_reachable(monkeypatch):
    dl._block_cache.clear()
    monkeypatch.setattr(dl.requests, "get", lambda *a, **k: _ProbeResponse(200))
    monkeypatch.setattr(dl.requests, "head", lambda *a, **k: _ProbeResponse(200))

    assert dl._detect_network_block("https://good-cdn.example/ep.ts") is None


def test_detect_block_skips_kisskh_without_probing(monkeypatch):
    dl._block_cache.clear()

    def _boom(*a, **k):
        raise AssertionError("kisskh host should never be probed")

    monkeypatch.setattr(dl.requests, "get", _boom)
    monkeypatch.setattr(dl.requests, "head", _boom)

    assert dl._detect_network_block("https://kisskh.nl/api/whatever") is None


def test_blocked_host_raises_instead_of_downloading(monkeypatch, tmp_path):
    # A detected block must abort with NetworkBlockError, never invoking yt-dlp.
    monkeypatch.setattr(dl, "_detect_network_block", lambda url: "the network redirected 'x' to a block page")
    downloader = Downloader(referer="https://kisskh.nl")
    downloader._n_m3u8dl_re = None  # force the yt-dlp-only branch

    called = {"yt_dlp": 0}
    monkeypatch.setattr(downloader, "_download_with_yt_dlp", lambda *a, **k: called.__setitem__("yt_dlp", 1))

    with pytest.raises(NetworkBlockError):
        downloader.download_video_from_stream_url("https://blocked.example/x.m3u8", str(tmp_path / "ep"), "1080p")

    assert called["yt_dlp"] == 0
