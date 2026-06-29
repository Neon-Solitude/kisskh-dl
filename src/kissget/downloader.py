import logging
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests

from kissget.helper.decrypt_subtitle import SubtitleDecrypter
from kissget.models.sub import SubItem

logger = logging.getLogger(__name__)

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


class Downloader:
    def __init__(
        self,
        referer: str,
        n_m3u8dl_re_path: str | None = None,
    ) -> None:
        self.referer = referer

        # Resolve N_m3u8DL-RE path: explicit > auto-detect > None (fallback to yt-dlp)
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
            self._download_with_n_m3u8dl_re(video_stream_url, filepath, quality)
        else:
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

        cmd = [
            self._n_m3u8dl_re,
            video_stream_url,
            "--save-dir", save_dir,
            "--save-name", save_name,
            "--del-after-done",
            "--no-log",
            "--auto-select",
            "-H", f"Referer: {self.referer}",
            "-H", (
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
            result = subprocess.run(
                cmd,
                capture_output=False,
                text=True,
                timeout=600,  # 10 minute timeout per episode
            )
            if result.returncode != 0:
                logger.warning(
                    "N_m3u8DL-RE exited with code %d, falling back to yt-dlp", result.returncode
                )
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
            response = requests.get(sub_url, timeout=60)
            output_path = Path(f"{filepath}.{subtitle.land}{extension}")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(response.content)
            if decrypter is not None:
                decrypted_subtitle = decrypter.decrypt_subtitles(output_path)
                decrypted_subtitle.save(output_path)
