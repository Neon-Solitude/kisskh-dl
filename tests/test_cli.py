import json

import click
import pytest
from click.testing import CliRunner

from kissget.cli import _select_drama, kissget
from kissget.models.search import DramaInfo, Search


def _drama(id_, title):
    return DramaInfo(episodesCount=1, label="", favoriteID=0, thumbnail="", id=id_, title=title)


def test_single_match_is_auto_selected(monkeypatch):
    # Even when stdin is not a TTY, a lone match needs no prompt.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    dramas = Search.model_validate([_drama(1, "Only Show")])

    chosen = _select_drama(dramas, "only")

    assert chosen.id == 1


def test_multiple_matches_without_tty_raises(monkeypatch):
    # Must fail loudly instead of blocking on input().
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    dramas = Search.model_validate([_drama(1, "Show A"), _drama(2, "Show B")])

    with pytest.raises(click.UsageError):
        _select_drama(dramas, "show")


def test_multiple_matches_with_tty_prompts(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "2")
    dramas = Search.model_validate([_drama(1, "Show A"), _drama(2, "Show B")])

    chosen = _select_drama(dramas, "show")

    assert chosen.id == 2


# ── End-to-end CLI wiring (manifest path + provider dispatch) ──────────────


def test_dl_from_manifest_applies_referer(monkeypatch, tmp_path):
    """The manifest path should download the stream and apply the manifest's Referer."""
    calls = {}

    class FakeDownloader:
        def __init__(self, referer, n_m3u8dl_re_path=None):
            self.referer = referer

        def download_video_from_stream_url(self, url, filepath, quality):
            calls["video"] = (url, self.referer)

        def download_subtitles(self, subs, filepath, decrypter=None):
            calls["subs"] = [s.src for s in subs]

    monkeypatch.setattr("kissget.cli.Downloader", FakeDownloader)

    manifest = {
        "drama": "Test-Show",
        "referer": "https://cdn.example/",
        "episodes": [
            {
                "number": 1,
                "stream_url": "http://cdn.example/e1.m3u8",
                "subtitles": [{"lang": "en", "label": "English", "src": "http://x/e1.vtt"}],
            }
        ],
    }
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    result = CliRunner().invoke(kissget, ["dl", "--from-manifest", str(path), "-o", str(tmp_path), "-s", "en"])

    assert result.exit_code == 0, result.output
    assert calls["video"] == ("http://cdn.example/e1.m3u8", "https://cdn.example/")
    assert calls["subs"] == ["http://x/e1.vtt"]


def test_dl_url_without_id_is_a_usage_error(monkeypatch):
    """A kisskh URL with no ?id= should fail cleanly via provider.parse_url."""

    class _Dummy:  # accepts a settable .referer, avoids real binary probing
        def __init__(self, *a, **k):
            self.referer = None

    monkeypatch.setattr("kissget.cli.Downloader", _Dummy)

    result = CliRunner().invoke(kissget, ["dl", "https://kisskh.nl/Drama/Show"])

    assert result.exit_code != 0
    assert "id=" in result.output.lower()
