import logging
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests

from kissget.enums.quality import Quality
from kissget.helper.decrypt_subtitle import SubtitleDecrypter
from kissget.models.sub import SubItem

logger = logging.getLogger(__name__)


class NetworkBlockError(Exception):
    """Raised when the video CDN host appears to be blocked or intercepted by the
    local network/ISP (e.g. a content filter such as CUJO / Spectrum Security
    Shield). Signals the caller to stop and surface clear guidance instead of
    letting a download backend spin on TLS errors."""


# Quality ladder (heights) derived from the Quality enum, ascending: [360, 480, 540, 720, 1080]
_QUALITY_HEIGHTS = sorted(int(q.value.rstrip("p")) for q in Quality)

# Well-known locations for N_m3u8DL-RE.exe on Windows
_N_M3U8DL_RE_SEARCH_PATHS = [
    # Adjacent to this project (common dev layout)
    Path(__file__).resolve().parents[3] / "N_m3u8DL_RE_GUI-main" / "N_m3u8DL-RE.exe",
    # User's GitHub folder
    Path.home() / "Programming" / "GitHub" / "N_m3u8DL_RE_GUI-main" / "N_m3u8DL-RE.exe",
]


def _find_n_m3u8dl_re() -> str | None:
    """Auto-detect N_m3u8DL-RE.exe from PATH or well-known locations."""
    # Check PATH first
    found = shutil.which("N_m3u8DL-RE") or shutil.which("N_m3u8DL-RE.exe")
    if found:
        return found

    # Check well-known locations
    for candidate in _N_M3U8DL_RE_SEARCH_PATHS:
        if candidate.exists():
            return str(candidate)

    return None


def _find_ffmpeg(n_m3u8dl_re_path: str | None = None) -> str | None:
    """Find ffmpeg, checking next to N_m3u8DL-RE first, then PATH."""
    if n_m3u8dl_re_path:
        sibling = Path(n_m3u8dl_re_path).parent / "ffmpeg.exe"
        if sibling.exists():
            return str(sibling)

    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def _video_select_args(quality: str) -> list[str]:
    """Build N_m3u8DL-RE track-selection args that honor the requested quality.

    For the top of the ladder (default 1080p) we keep ``--auto-select`` so the
    common path is byte-identical to the previous behavior. For a lower quality
    we emit ``--select-video res="x(<=heights)$":for=best``, which matches the
    requested height and every lower one and picks the best of those — mirroring
    yt-dlp's ``height<=N`` then "best available" fallback. We add ``-sa best`` so
    audio is still selected once ``--auto-select`` is no longer in play.

    If the regex matches nothing (e.g. an unusual stream), N_m3u8DL-RE exits
    non-zero and the caller falls back to yt-dlp, which applies the same logic.
    """
    try:
        target = int(quality.rstrip("p"))
    except ValueError:
        return ["--auto-select"]

    # At/above the highest known tier there is nothing to constrain — take best.
    if target >= _QUALITY_HEIGHTS[-1]:
        return ["--auto-select"]

    eligible = [h for h in _QUALITY_HEIGHTS if h <= target]
    if not eligible:
        return ["--auto-select"]

    alternation = "|".join(str(h) for h in sorted(eligible, reverse=True))
    return ["--select-video", f'res="x({alternation})$":for=best', "--select-audio", "best"]


# Hostname/path fragments that mark an ISP/router content-filter block page.
_BLOCK_PAGE_MARKERS = (
    "cujo.io",
    "warn.html",
    "blockpage",
    "securityshield",
    "contentfilter",
    "fortiguard",
    "opendns",
    "/block",
)

# Per-host detection cache so a multi-episode batch probes each host at most once.
_block_cache: dict[str, str | None] = {}

_PROBE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


def _detect_network_block(url: str) -> str | None:
    """Best-effort check for a network/ISP-level block of the CDN host.

    Returns a short human-readable reason when a block is detected, else None.
    Two high-confidence signals, either of which is enough:

      1. an HTTP request to the host is redirected to a known filter/block page;
      2. the HTTPS handshake fails with the 'wrong version number' family, which
         means a transparent interceptor is sitting in front of the host.

    Results are cached per host. Anything ambiguous (timeouts, ordinary
    connection errors) returns None, so a transient hiccup is never mistaken for
    a block — the download backend is still allowed to try.
    """
    host = urlparse(url).hostname or ""
    if not host or "kisskh" in host:
        return None
    if host in _block_cache:
        return _block_cache[host]

    reason: str | None = None
    headers = {"User-Agent": _PROBE_UA}

    # Signal 1: HTTP redirected to a filter/block page.
    try:
        resp = requests.get(f"http://{host}/", headers=headers, allow_redirects=False, timeout=6)
        location = resp.headers.get("Location", "")
        if 300 <= resp.status_code < 400 and any(m in location.lower() for m in _BLOCK_PAGE_MARKERS):
            redirect_host = urlparse(location).hostname or location
            reason = f"the network redirected '{host}' to a filter/block page ({redirect_host})"
    except requests.RequestException:
        pass

    # Signal 2: HTTPS handshake broken by a transparent interceptor.
    if reason is None:
        try:
            requests.head(f"https://{host}/", headers=headers, timeout=6)
        except requests.exceptions.SSLError as e:
            msg = str(e).lower()
            if "wrong version number" in msg or "unknown protocol" in msg or "sslv3" in msg:
                reason = f"the TLS connection to '{host}' is being broken (likely a transparent network interceptor)"
        except requests.RequestException:
            pass

    _block_cache[host] = reason
    return reason


def _network_block_error(reason: str) -> NetworkBlockError:
    """Build a NetworkBlockError carrying actionable, user-facing guidance."""
    return NetworkBlockError(
        f"Cannot reach the video CDN — {reason}.\n"
        "This is a network-level block, not a problem with kissget or the file.\n"
        "Fix: connect a SYSTEM-level VPN (a desktop app, not a browser extension) and retry, or\n"
        "turn off your ISP/router content filter (e.g. Spectrum Security Shield / CUJO).\n"
        "See the README 'Troubleshooting' section for details."
    )


class Downloader:
    def __init__(
        self,
        referer: str,
        n_m3u8dl_re_path: str | None = None,
    ) -> None:
        self.referer = referer

        # Resolve N_m3u8DL-RE path: explicit > auto-detect > None (fallback to yt-dlp)
        self._n_m3u8dl_re: str | None
        if n_m3u8dl_re_path:
            self._n_m3u8dl_re = n_m3u8dl_re_path
        else:
            self._n_m3u8dl_re = _find_n_m3u8dl_re()

        self._ffmpeg = _find_ffmpeg(self._n_m3u8dl_re)

        if self._n_m3u8dl_re:
            logger.info("Using N_m3u8DL-RE: %s", self._n_m3u8dl_re)
            if self._ffmpeg:
                logger.debug("Using ffmpeg: %s", self._ffmpeg)
        else:
            logger.debug("N_m3u8DL-RE not found, will use yt-dlp as fallback")

    @staticmethod
    def _normalize_stream_url(url: str) -> str:
        """Downgrade https:// → http:// for CDN hosts that are HTTP-only.

        Some video CDNs (e.g. cdnvideo11.shop) respond with a plain HTTP
        server on port 443, causing SSL WRONG_VERSION_NUMBER errors when
        contacted over HTTPS. Downgrading the scheme fixes the connection.
        """
        parsed = urlparse(url)
        # Only downgrade for non-kisskh hosts
        if parsed.scheme == "https" and "kisskh" not in parsed.netloc:
            logger.debug("Downgrading stream URL from https to http: %s", parsed.netloc)
            url = "http" + url[5:]
        return url

    def download_video_from_stream_url(self, video_stream_url: str, filepath: str, quality: str) -> None:
        """Download a video from stream url.

        Tries N_m3u8DL-RE first (faster, multi-threaded, better SSL).
        Falls back to yt-dlp if N_m3u8DL-RE is not available.

        :param video_stream_url: stream url (m3u8/mpd)
        :param filepath: file path where to download (without extension)
        :param quality: quality to select (e.g. "1080p")
        """
        if self._n_m3u8dl_re:
            # N_m3u8DL-RE fails fast on a blocked host; we classify the block in
            # its non-zero handler (below) to avoid a probe on the happy path.
            self._download_with_n_m3u8dl_re(video_stream_url, filepath, quality)
        else:
            # No N_m3u8DL-RE: probe up front so a block raises cleanly instead of
            # letting yt-dlp grind through its retry storm.
            blocked = _detect_network_block(video_stream_url)
            if blocked:
                raise _network_block_error(blocked)
            self._download_with_yt_dlp(video_stream_url, filepath, quality)

    def _download_with_n_m3u8dl_re(self, video_stream_url: str, filepath: str, quality: str) -> None:
        """Download using N_m3u8DL-RE.exe — multi-threaded, native muxing."""
        output_path = Path(filepath)
        save_dir = str(output_path.parent)
        save_name = output_path.name

        # Check if already downloaded
        mp4_path = Path(f"{filepath}.mp4")
        if mp4_path.exists() and mp4_path.stat().st_size > 0:
            logger.info("Already downloaded: %s", mp4_path.name)
            return

        binary = self._n_m3u8dl_re
        assert binary is not None  # caller only dispatches here when the binary is set

        cmd = [
            binary,
            video_stream_url,
            "--save-dir",
            save_dir,
            "--save-name",
            save_name,
            "--del-after-done",
            "--no-log",
            *_video_select_args(quality),
            "-H",
            f"Referer: {self.referer}",
            "-H",
            (
                "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/147.0.0.0 Safari/537.36"
            ),
        ]

        # Mux to mp4 if ffmpeg is available
        if self._ffmpeg:
            cmd.extend(["-M", "format=mp4"])
            cmd.extend(["--ffmpeg-binary-path", self._ffmpeg])

        logger.debug("Running: %s", " ".join(cmd[:4]) + " ...")

        try:
            # Vetted: cmd is a fixed argument list (no shell), built from the
            # detected binary path plus the stream URL/flags — not shell input.
            result = subprocess.run(  # noqa: S603
                cmd,
                capture_output=False,
                text=True,
                timeout=600,  # 10 minute timeout per episode
            )
            if result.returncode != 0:
                # A non-zero exit on a blocked CDN would just hand yt-dlp the same
                # doomed host. Classify first and fail fast with guidance instead.
                blocked = _detect_network_block(video_stream_url)
                if blocked:
                    raise _network_block_error(blocked)
                logger.warning("N_m3u8DL-RE exited with code %d, falling back to yt-dlp", result.returncode)
                self._download_with_yt_dlp(video_stream_url, filepath, quality)
        except FileNotFoundError:
            logger.warning("N_m3u8DL-RE not found at %s, falling back to yt-dlp", self._n_m3u8dl_re)
            self._n_m3u8dl_re = None  # Don't try again
            self._download_with_yt_dlp(video_stream_url, filepath, quality)
        except subprocess.TimeoutExpired:
            logger.error("N_m3u8DL-RE timed out after 10 minutes")
            raise

    def _download_with_yt_dlp(self, video_stream_url: str, filepath: str, quality: str) -> None:
        """Download using yt-dlp — single-threaded fallback."""
        import yt_dlp

        video_stream_url = self._normalize_stream_url(video_stream_url)
        ydl_opts = {
            "format": f"bestvideo[height<={quality[:-1]}]+bestaudio/best[height<={quality[:-1]}]/best",
            "concurrent_fragment_downloads": 15,
            "outtmpl": f"{filepath}.%(ext)s",
            "http_headers": {
                "Referer": self.referer,
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/147.0.0.0 Safari/537.36"
                ),
            },
            "verbose": logger.getEffectiveLevel() == logging.DEBUG,
            "retries": 10,
        }
        logger.debug("Calling yt-dlp with options: %s", ydl_opts)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download(video_stream_url)

    def download_subtitles(
        self, subtitles: list[SubItem], filepath: str, decrypter: SubtitleDecrypter | None = None
    ) -> None:
        """Download subtitles.

        :param subtitles: list of all subtitles
        :param filepath: file path where to download
        """
        for subtitle in subtitles:
            logger.info("Downloading %s sub...", subtitle.label)
            extension = os.path.splitext(urlparse(subtitle.src).path)[-1]
            sub_url = self._normalize_stream_url(subtitle.src)
            try:
                response = requests.get(sub_url, timeout=60)
                response.raise_for_status()
            except requests.RequestException as e:
                # Skip this subtitle rather than writing an error page as a .srt
                # and rather than aborting the whole episode/batch.
                logger.warning("Failed to download %s sub (%s) — skipping.", subtitle.label, e)
                continue
            output_path = Path(f"{filepath}.{subtitle.land}{extension}")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(response.content)
            if decrypter is not None:
                decrypted_subtitle = decrypter.decrypt_subtitles(output_path)
                decrypted_subtitle.save(output_path)
