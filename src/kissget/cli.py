import json
import logging
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import click
import validators
from dotenv import load_dotenv

from kissget.downloader import Downloader, NetworkBlockError
from kissget.enums.quality import Quality
from kissget.helper.decrypt_subtitle import SubtitleDecrypter
from kissget.kisskh_api import KissKHApi
from kissget.manifest import ManifestReader
from kissget.models.search import DramaInfo, Search

load_dotenv()


def _resolve_base_url() -> str:
    """Return the base URL from env or default."""
    return os.getenv("KISSKH_BASE_URL", "https://kisskh.nl")


def _sanitize_path_component(name: str) -> str:
    """Sanitize a string for safe use as a single path segment.

    Removes path separators, parent directory references, and other
    characters that could enable path traversal attacks.
    """
    sanitized = re.sub(r'[\\/;:|*?"<>]', "_", name)
    sanitized = sanitized.replace("..", "_")
    return sanitized.strip(". ") or "_"


def _format_episode(num: float) -> str:
    """Format an episode number for use in filenames.

    Integer episodes → ``E01``, ``E16``; float/recap episodes → ``E16.1``, ``E16.2``.
    """
    if num == int(num):
        return f"E{int(num):02d}"
    return f"E{num}"


def _select_drama(dramas: Search, query: str) -> DramaInfo:
    """Pick one drama from search results.

    Selection is a CLI concern, so it lives here rather than in the API client.
    Behavior:
      * exactly one match  → auto-select it;
      * multiple + a TTY   → prompt the user to choose;
      * multiple + no TTY  → raise a clear error (instead of hanging on input()),
                             so scripts and piped runs fail loudly with guidance.
    """
    logger = logging.getLogger(__name__)
    if len(dramas) == 1:
        logger.info("One match for %r: %s", query, dramas[0].title)
        return dramas[0]

    listing = "\n".join(f"  {i}. {d.title}" for i, d in enumerate(dramas, start=1))
    if not sys.stdin.isatty():
        raise click.UsageError(
            f'Multiple dramas match "{query}", but there is no interactive terminal '
            "to choose one. Re-run with the drama URL instead, e.g.\n"
            '  kissget dl "https://kisskh.nl/Drama/Show-Name?id=1234"\n\n'
            f"Matches:\n{listing}"
        )

    click.echo(listing)
    while True:
        try:
            choice = int(input("Select a drama from above: ") or "0")
        except ValueError:
            choice = 0
        if 1 <= choice <= len(dramas):
            return dramas[choice - 1]
        click.echo("Please enter a valid number.")


# ── Top-level CLI group ──────────────────────────────────────────────────


@click.group()
@click.option("-v", "--verbose", count=True, help="Increase log level verbosity")
def kissget(verbose):
    logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
    if verbose == 1:
        logging.getLogger().setLevel(logging.INFO)
    elif verbose >= 2:
        logging.getLogger().setLevel(logging.DEBUG)


# ── Download command ─────────────────────────────────────────────────────


@kissget.command()
@click.argument("drama_url_or_name", default=None, required=False)
@click.option("--first", "-f", type=click.INT, default=1, help="Starting episode number.")
@click.option("--last", "-l", type=click.INT, default=sys.maxsize, help="Ending episode number.")
@click.option(
    "--quality",
    "-q",
    default="1080p",
    type=click.Choice([quality.value for quality in Quality]),
    help="Quality of the video to be downloaded.",
)
@click.option(
    "--sub-langs",
    "-s",
    default=("en",),
    multiple=True,
    help="Languages of the subtitles to download.",
)
@click.option(
    "--output-dir",
    "-o",
    default=Path.home() / "Downloads",
    help="Output directory where downloaded files will be store.",
)
@click.option(
    "--decrypt-subtitle",
    "-ds",
    is_flag=True,
    help="Decrypt the downloaded subtitle",
)
@click.option(
    "--key",
    "-k",
    default=None,
    help="Subtitle decryption key (or set KISSKH_KEY env var).",
)
@click.option(
    "--initialization-vector",
    "-iv",
    default=None,
    help="Initialization vector for subtitle decryption (or set KISSKH_INITIALIZATION_VECTOR env var).",
)
@click.option(
    "--stream-key",
    default=None,
    help="Pre-generated kkey for stream endpoint (or set KISSKH_STREAM_KEY env var). "
    "Skips browser-based kkey generation.",
)
@click.option(
    "--sub-key",
    default=None,
    help="Pre-generated kkey for subtitle endpoint (or set KISSKH_SUB_KEY env var). "
    "Skips browser-based kkey generation.",
)
@click.option(
    "--subs-only",
    "-so",
    is_flag=True,
    default=False,
    help="Download subtitles only, skip video download.",
)
@click.option(
    "--subs-first",
    "-sf",
    is_flag=True,
    default=False,
    help="Download all subtitles first (while the key is fresh), then download videos.",
)
@click.option(
    "--skip-recap",
    is_flag=True,
    default=False,
    help="Skip recap/special episodes (those with fractional episode numbers like 16.1, 16.2).",
)
@click.option(
    "--headed",
    is_flag=True,
    default=False,
    help="Run the Playwright browser in headed (visible) mode so you can solve Cloudflare captchas.",
)
@click.option(
    "--from-manifest",
    type=click.Path(exists=True),
    default=None,
    help="Read stream URLs and subtitles from a JSON manifest file "
    "(produced by the browser collector script) instead of hitting the API.",
)
@click.option(
    "--n-m3u8dl-re",
    default=None,
    help="Path to N_m3u8DL-RE.exe. If not set, auto-detected from PATH and well-known locations.",
)
def dl(
    drama_url_or_name: str,
    first: int,
    last: int,
    quality: str,
    sub_langs: list[str],
    output_dir: Path | str,
    decrypt_subtitle: bool,
    key: str | None,
    initialization_vector: str | None,
    stream_key: str | None,
    sub_key: str | None,
    subs_only: bool = False,
    subs_first: bool = False,
    skip_recap: bool = False,
    headed: bool = False,
    from_manifest: str | None = None,
    n_m3u8dl_re: str | None = None,
) -> None:
    """Download episodes from kisskh.

    DRAMA_URL_OR_NAME can be a full URL (e.g. https://kisskh.nl/Drama/Some-Show?id=1234)
    or a search query (e.g. "Stranger Things").
    """
    logger = logging.getLogger(__name__)

    if not from_manifest and not drama_url_or_name:
        raise click.UsageError(
            "Provide a drama URL/name, or use --from-manifest to download from a manifest file.\n\n"
            "Examples:\n"
            '  kissget dl "https://kisskh.nl/Drama/Some-Show?id=1234"\n'
            "  kissget dl --from-manifest manifest.json -o ~/Downloads"
        )

    # Resolve secrets from env vars if not passed via CLI
    key = key or os.getenv("KISSKH_KEY")
    initialization_vector = initialization_vector or os.getenv("KISSKH_INITIALIZATION_VECTOR")
    stream_key = stream_key or os.getenv("KISSKH_STREAM_KEY")
    sub_key = sub_key or os.getenv("KISSKH_SUB_KEY")

    if decrypt_subtitle and not (key and initialization_vector):
        raise click.UsageError(
            "--key and --initialization-vector must be provided when --decrypt-subtitle is set. "
            "Either pass them or set them via KISSKH_KEY and KISSKH_INITIALIZATION_VECTOR "
            "environment variables."
        )

    decrypter: SubtitleDecrypter | None = None
    if decrypt_subtitle:
        assert key is not None and initialization_vector is not None  # validated above
        decrypter = SubtitleDecrypter(key=key, initialization_vector=initialization_vector)

    base_url = _resolve_base_url()
    downloader = Downloader(referer=base_url, n_m3u8dl_re_path=n_m3u8dl_re)

    # ── Manifest-based download (no API / no kkeys needed) ────────────────
    if from_manifest:
        manifest = ManifestReader.from_file(from_manifest)
        drama_name = _sanitize_path_component(manifest.drama_name)

        decrypter_m: SubtitleDecrypter | None = None
        if decrypt_subtitle:
            assert key is not None and initialization_vector is not None
            decrypter_m = SubtitleDecrypter(key=key, initialization_vector=initialization_vector)

        for ep in manifest.episodes:
            if ep.number < first or ep.number > last:
                continue
            episode_tag = _format_episode(ep.number)
            filepath = f"{output_dir}/{drama_name}/{drama_name}_{episode_tag}"

            # Subtitles
            filtered_subs = (
                [s for s in ep.subtitles if s.land in sub_langs or "all" in sub_langs] if ep.subtitles else []
            )
            if filtered_subs:
                logger.info("Downloading subtitles for Episode %s...", episode_tag)
                downloader.download_subtitles(filtered_subs, filepath, decrypter_m)

            # Video
            if not subs_only and ep.stream_url:
                if "tickcounter" in ep.stream_url:
                    logger.warning("Episode %s still not released!", episode_tag)
                    continue
                logger.info("Downloading video for Episode %s...", episode_tag)
                try:
                    downloader.download_video_from_stream_url(ep.stream_url, filepath, quality)
                except NetworkBlockError as e:
                    logger.error("%s", e)
                    return
                except Exception as e:
                    logger.error("Failed to download video for Episode %s: %s — skipping.", episode_tag, e)
            elif not subs_only and not ep.stream_url:
                logger.warning("No stream URL in manifest for Episode %s — skipping video.", episode_tag)

        return

    # ── Standard API-based download path ──────────────────────────────────
    kisskh_api = KissKHApi(base_url=base_url, headed=headed)
    episode_ids: dict[float, int] = {}

    if validators.url(drama_url_or_name):
        parsed_url = urlparse(drama_url_or_name)
        ids = parse_qs(parsed_url.query).get("id")
        if ids is None:
            raise FileNotFoundError("Not a valid url for a drama!")
        drama_id = int(ids[0])
        episode_id = parse_qs(parsed_url.query).get("ep")
        episode_number = None
        if episode_string := re.search(r"Episode-(\d+)", parsed_url.path):
            episode_number = episode_string.group(1)
        if episode_id and episode_number:
            episode_ids = {float(episode_number): int(episode_id[0])}
        drama_name = _sanitize_path_component(parsed_url.path.split("/")[2]).replace("-", "_")
    else:
        dramas = kisskh_api.search_dramas_by_query(drama_url_or_name)
        if len(dramas) == 0:
            logger.warning("No drama found with the query provided...")
            return None
        drama = _select_drama(dramas, drama_url_or_name)
        drama_id = drama.id
        drama_name = _sanitize_path_component(drama.title)

    if not episode_ids:
        episode_ids = kisskh_api.get_episode_ids(drama_id=drama_id, start=first, stop=last, skip_recap=skip_recap)

    # Resolve the kkeys once (they come from env vars when set manually)
    if stream_key and sub_key:
        shared_kkeys: dict[str, str] | None = {"stream": stream_key, "sub": sub_key}
        logger.debug("Using kkey from command-line / environment variables")
    else:
        shared_kkeys = None  # will be generated per-episode by Playwright

    def _get_kkeys(episode_number: float, current_episode_id: int) -> dict[str, str] | None:
        """Return kkeys for this episode, generating via Playwright if needed."""
        if shared_kkeys is not None:
            return shared_kkeys
        episode_tag = _format_episode(episode_number)
        logger.info("Generating authentication token for Episode %s...", episode_tag)
        try:
            return kisskh_api.generate_kkeys(
                drama_id=drama_id,
                episode_id=current_episode_id,
                episode_number=int(episode_number),
                drama_title=drama_name,
            )
        except Exception as e:
            logger.error("Failed to generate authentication token for Episode %s: %s", episode_tag, e)
            logger.error(
                "Tip: Set KISSKH_STREAM_KEY and KISSKH_SUB_KEY environment variables "
                "to skip browser-based kkey generation."
            )
            return None

    if subs_first and not subs_only:
        # ── Pass 1: download all subtitles while the sub key is fresh ────────
        logger.info("[subs-first] Pass 1 of 2 — downloading all subtitles first...")
        for episode_number, current_episode_id in episode_ids.items():
            episode_tag = _format_episode(episode_number)
            kkeys = _get_kkeys(episode_number, current_episode_id)
            if kkeys is None:
                continue
            try:
                subtitles = kisskh_api.get_subtitles(current_episode_id, kkeys.get("sub", ""), *sub_langs)
            except Exception as e:
                logger.warning(
                    "Could not fetch subtitles for Episode %s (key may have expired): %s — skipping subs.",
                    episode_tag,
                    e,
                )
                continue
            filepath = f"{output_dir}/{drama_name}/{drama_name}_{episode_tag}"
            if subtitles:
                logger.info("Downloading subtitles for Episode %s...", episode_tag)
                downloader.download_subtitles(subtitles, filepath, decrypter)
            else:
                logger.warning("No subtitles found for Episode %s.", episode_tag)

        # ── Pass 2: download all videos ──────────────────────────────────────
        logger.info("[subs-first] Pass 2 of 2 — downloading all videos...")
        for episode_number, current_episode_id in episode_ids.items():
            episode_tag = _format_episode(episode_number)
            kkeys = _get_kkeys(episode_number, current_episode_id)
            if kkeys is None:
                continue
            try:
                video_stream_url = kisskh_api.get_stream_url(current_episode_id, kkeys.get("stream", ""))
            except Exception as e:
                logger.error("Could not fetch stream URL for Episode %s: %s — skipping.", episode_tag, e)
                continue
            if "tickcounter" in video_stream_url:
                logger.warning("Episode %s still not released!", episode_tag)
                continue
            filepath = f"{output_dir}/{drama_name}/{drama_name}_{episode_tag}"
            logger.debug("Using video url: %s", video_stream_url)
            try:
                downloader.download_video_from_stream_url(video_stream_url, filepath, quality)
            except NetworkBlockError as e:
                logger.error("%s", e)
                break
            except Exception as e:
                logger.error("Failed to download video for Episode %s: %s — skipping.", episode_tag, e)
                continue

    else:
        # ── Standard single-pass loop ─────────────────────────────────────────
        for episode_number, current_episode_id in episode_ids.items():
            episode_tag = _format_episode(episode_number)
            logger.info("Getting details for Episode %s...", episode_tag)

            kkeys = _get_kkeys(episode_number, current_episode_id)
            if kkeys is None:
                continue

            try:
                subtitles = kisskh_api.get_subtitles(current_episode_id, kkeys.get("sub", ""), *sub_langs)
            except Exception as e:
                logger.warning(
                    "Could not fetch subtitles for Episode %s (key may have expired): %s — skipping subs.",
                    episode_tag,
                    e,
                )
                subtitles = []

            if subs_only:
                if not subtitles:
                    logger.warning("No subtitles available for Episode %s, skipping.", episode_tag)
                else:
                    filepath = f"{output_dir}/{drama_name}/{drama_name}_{episode_tag}"
                    logger.info("Downloading subtitles for Episode %s...", episode_tag)
                    downloader.download_subtitles(subtitles, filepath, decrypter)
                continue

            try:
                video_stream_url = kisskh_api.get_stream_url(current_episode_id, kkeys.get("stream", ""))
            except Exception as e:
                logger.error("Could not fetch stream URL for Episode %s: %s — skipping.", episode_tag, e)
                continue

            if "tickcounter" in video_stream_url:
                logger.warning("Episode %s still not released!", episode_tag)
                continue

            filepath = f"{output_dir}/{drama_name}/{drama_name}_{episode_tag}"
            logger.debug("Using video url: %s", video_stream_url)
            try:
                downloader.download_video_from_stream_url(video_stream_url, filepath, quality)
            except NetworkBlockError as e:
                logger.error("%s", e)
                break
            except Exception as e:
                logger.error("Failed to download video for Episode %s: %s — skipping.", episode_tag, e)
                continue
            downloader.download_subtitles(subtitles, filepath, decrypter)

    kisskh_api.cleanup()


# ── Collect command ──────────────────────────────────────────────────────


@kissget.command()
@click.argument("drama_url_or_name")
@click.option("--first", "-f", type=click.INT, default=1, help="Starting episode number.")
@click.option("--last", "-l", type=click.INT, default=sys.maxsize, help="Ending episode number.")
@click.option(
    "--sub-langs",
    "-s",
    default=("en",),
    multiple=True,
    help='Languages of the subtitles to collect. Use "all" to collect every available language.',
)
@click.option(
    "--skip-recap",
    is_flag=True,
    default=False,
    help="Skip recap/special episodes (those with fractional episode numbers like 16.1, 16.2).",
)
@click.option(
    "--headed",
    is_flag=True,
    default=False,
    help="Run the Playwright browser in headed (visible) mode so you can solve Cloudflare captchas.",
)
@click.option(
    "--stream-key",
    default=None,
    help="Pre-generated kkey for the stream endpoint (or set KISSKH_STREAM_KEY env var). "
    "Skips browser-based kkey generation.",
)
@click.option(
    "--sub-key",
    default=None,
    help="Pre-generated kkey for the subtitle endpoint (or set KISSKH_SUB_KEY env var). "
    "Skips browser-based kkey generation.",
)
@click.option(
    "--cdp-url",
    default=None,
    help="Connect to your real Chrome/Edge via CDP (e.g. http://localhost:9222). "
    "Run 'kisskh open-browser' first. This bypasses bot detection entirely.",
)
@click.option(
    "--output",
    "-o",
    default=None,
    help="Output manifest JSON file path. Default: <drama_name>_manifest.json in the current directory.",
)
def collect(
    drama_url_or_name: str,
    first: int,
    last: int,
    sub_langs: tuple[str, ...],
    skip_recap: bool,
    headed: bool,
    stream_key: str | None,
    sub_key: str | None,
    cdp_url: str | None,
    output: str | None,
) -> None:
    """Capture stream and subtitle URLs into a JSON manifest for kkey-free downloads.

    Visits each episode page once to extract CDN URLs, then writes them to a manifest
    file. The manifest can later be passed to ``kisskh dl --from-manifest`` to download
    without needing a browser or kkey tokens.

    Example:

        kisskh collect "https://kisskh.nl/Drama/Customized-Lover-(2026)?id=13191"

        kisskh dl --from-manifest "Customized_Lover__2026__manifest.json" -o "C:\\Users\\you\\Downloads"
    """
    logger = logging.getLogger(__name__)
    base_url = _resolve_base_url()

    # CLI flags take precedence over env vars; inject into env so generate_kkeys picks them up.
    if stream_key:
        os.environ["KISSKH_STREAM_KEY"] = stream_key
    if sub_key:
        os.environ["KISSKH_SUB_KEY"] = sub_key

    using_static_keys = bool(os.getenv("KISSKH_STREAM_KEY") and os.getenv("KISSKH_SUB_KEY"))
    if using_static_keys:
        click.echo(
            "Using pre-supplied kkeys for all episodes — no browser needed.\n"
            "Note: these keys are session-based and may expire. If API calls start failing,\n"
            "get fresh keys from your browser's DevTools and re-run.\n"
        )

    kisskh_api = KissKHApi(base_url=base_url, headed=headed, cdp_url=cdp_url)

    # ── Resolve drama ────────────────────────────────────────────────────
    if validators.url(drama_url_or_name):
        parsed_url = urlparse(drama_url_or_name)
        ids = parse_qs(parsed_url.query).get("id")
        if ids is None:
            raise click.UsageError("URL must contain a ?id= parameter.")
        drama_id = int(ids[0])
        drama_name = parsed_url.path.split("/")[2]  # keep display name for manifest
    else:
        dramas = kisskh_api.search_dramas_by_query(drama_url_or_name)
        if len(dramas) == 0:
            logger.warning("No drama found with the query provided...")
            kisskh_api.cleanup()
            return
        drama = _select_drama(dramas, drama_url_or_name)
        drama_id = drama.id
        drama_name = drama.title

    episode_ids = kisskh_api.get_episode_ids(drama_id=drama_id, start=first, stop=last, skip_recap=skip_recap)

    if not episode_ids:
        logger.warning("No episodes found in range %d–%s.", first, last if last != sys.maxsize else "end")
        kisskh_api.cleanup()
        return

    click.echo(f"Collecting {len(episode_ids)} episode(s) for: {drama_name}")

    # ── Visit each episode ───────────────────────────────────────────────
    manifest_episodes: list[dict] = []

    for episode_number, current_episode_id in episode_ids.items():
        episode_tag = _format_episode(episode_number)
        click.echo(f"  Episode {episode_tag}...", nl=False)

        # Get kkeys (Playwright or env vars)
        try:
            kkeys = kisskh_api.generate_kkeys(
                drama_id=drama_id,
                episode_id=current_episode_id,
                episode_number=int(episode_number),
                drama_title=drama_name,
            )
        except Exception as e:
            click.echo(f" FAILED (kkey): {e}")
            logger.debug("kkey error for Episode %s", episode_tag, exc_info=True)
            continue

        # Fetch stream URL
        stream_url: str | None = None
        try:
            stream_url = kisskh_api.get_stream_url(current_episode_id, kkeys.get("stream", ""))
            if stream_url and "tickcounter" in stream_url:
                click.echo(" (not yet released — skipping stream)")
                stream_url = None
        except Exception as e:
            logger.debug("Stream URL error for Episode %s: %s", episode_tag, e)

        # Fetch subtitles
        subs_data: list[dict] = []
        try:
            subtitles = kisskh_api.get_subtitles(current_episode_id, kkeys.get("sub", ""), *sub_langs)
            subs_data = [{"lang": sub.land, "label": sub.label, "src": sub.src} for sub in subtitles]
        except Exception as e:
            logger.debug("Subtitle error for Episode %s: %s", episode_tag, e)

        manifest_episodes.append(
            {
                "number": episode_number,
                "stream_url": stream_url,
                "subtitles": subs_data,
            }
        )

        stream_status = "stream ✓" if stream_url else "no stream"
        subs_status = f"{len(subs_data)} sub(s)" if subs_data else "no subs"
        click.echo(f" {stream_status}, {subs_status}")

    kisskh_api.cleanup()

    if not manifest_episodes:
        click.echo("No episodes collected — manifest not written.")
        return

    # ── Write manifest ───────────────────────────────────────────────────
    manifest_data = {"drama": drama_name, "episodes": manifest_episodes}

    if output is None:
        safe_name = _sanitize_path_component(drama_name)
        output = f"{safe_name}_manifest.json"

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")

    with_stream = sum(1 for e in manifest_episodes if e["stream_url"])
    with_subs = sum(1 for e in manifest_episodes if e["subtitles"])

    click.echo("")
    click.echo(f"Manifest saved → {output_path}")
    click.echo(f"  Episodes collected : {len(manifest_episodes)}")
    click.echo(f"  With stream URL    : {with_stream}")
    click.echo(f"  With subtitles     : {with_subs}")
    click.echo("")
    click.echo("Download with:")
    click.echo(f'  kisskh dl --from-manifest "{output_path}" -o "C:\\Users\\you\\Downloads"')
    click.echo("")


# ── Get-key command ──────────────────────────────────────────────────────


@kissget.command(name="get-key")
@click.argument("drama_url")
def get_key(drama_url: str) -> None:
    """Generate and display kkey tokens for a drama episode URL.

    Opens a headless browser to extract the authentication keys that
    kisskh requires for stream and subtitle API calls.

    Example:

        kisskh get-key "https://kisskh.nl/Drama/A-Business-Proposal/Episode-1?id=4608&ep=86192&page=0&pageSize=100"

    After getting the keys, you can export them as environment variables
    and run ``kisskh dl`` without needing a browser each time:

        set KISSKH_STREAM_KEY=<stream_key>
        set KISSKH_SUB_KEY=<sub_key>
    """
    if not validators.url(drama_url):
        raise click.UsageError("A valid episode URL is required.")

    parsed_url = urlparse(drama_url)
    params = parse_qs(parsed_url.query)

    drama_id_str = params.get("id", [None])[0]
    episode_id_str = params.get("ep", [None])[0]
    if not drama_id_str or not episode_id_str:
        raise click.UsageError(
            "URL must contain both ?id=... and &ep=... parameters. "
            "Example: https://kisskh.nl/Drama/.../Episode-1?id=1234&ep=5678"
        )

    drama_id = int(drama_id_str)
    episode_id = int(episode_id_str)
    episode_number = 0
    if episode_string := re.search(r"Episode-(\d+)", parsed_url.path):
        episode_number = int(episode_string.group(1))

    drama_slug = _sanitize_path_component(parsed_url.path.split("/")[2]).replace("-", "_")

    base_url = _resolve_base_url()
    kisskh_api = KissKHApi(base_url=base_url)

    click.echo("Launching browser to extract kkey tokens...")
    try:
        kkeys = kisskh_api.generate_kkeys(
            drama_id=drama_id,
            episode_id=episode_id,
            episode_number=episode_number,
            drama_title=drama_slug,
        )
    except Exception as e:
        raise click.ClickException(f"Failed to generate kkey: {e}")
    finally:
        kisskh_api.cleanup()

    click.echo("")
    click.echo("─" * 50)
    click.echo("  kkey tokens generated successfully!")
    click.echo("─" * 50)
    click.echo("")
    click.echo(f"  Stream key:  {kkeys.get('stream', 'N/A')}")
    click.echo(f"  Sub key:     {kkeys.get('sub', 'N/A')}")
    click.echo("")
    click.echo("  To use these without a browser next time, set these env vars:")
    click.echo("")
    click.echo(f"    set KISSKH_STREAM_KEY={kkeys.get('stream', '')}")
    click.echo(f"    set KISSKH_SUB_KEY={kkeys.get('sub', '')}")
    click.echo("")
    click.echo("  Then run your download command as usual:")
    click.echo(f'    kisskh dl "{drama_url}" -o .')
    click.echo("")


# ── Open-browser command ─────────────────────────────────────────────────


def _find_browser() -> str | None:
    """Return the path to a Chrome or Edge executable, or None."""
    import shutil

    for name in ("chrome", "google-chrome", "chromium-browser", "chromium", "msedge"):
        found = shutil.which(name)
        if found:
            return found

    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ]
    for p in candidates:
        try:
            if p.exists():
                return str(p)
        except Exception:
            pass
    return None


@kissget.command(name="open-browser")
@click.option("--port", default=9222, help="CDP debug port (default: 9222).")
@click.option("--browser-path", default=None, help="Path to Chrome/Edge executable.")
def open_browser(port: int, browser_path: str | None) -> None:
    """Launch Chrome/Edge with remote debugging so collect can capture kkeys.

    This opens a persistent kisskh browser profile at ~/.kisskh/browser_profile
    with CDP enabled on the specified port.  Cookies are saved between runs
    so you only need to solve any login/CAPTCHA the first time.

    Workflow:

    \b
        kisskh open-browser
        kisskh collect "DRAMA_URL" --cdp-url http://localhost:9222
        kisskh dl --from-manifest manifest.json -o "C:\\Users\\you\\Downloads"
    """
    import subprocess
    import urllib.request

    # Check if CDP is already available on that port
    try:
        urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=2)
        click.echo(f"A browser with CDP is already running on port {port}.")
        click.echo(f'Run: kisskh collect "DRAMA_URL" --cdp-url http://localhost:{port}')
        return
    except Exception:
        pass  # Not running — launch it

    exe = browser_path or _find_browser()
    if exe is None:
        raise click.ClickException(
            "Could not find Chrome or Edge. Install one of them, or use --browser-path to specify the executable."
        )

    profile_dir = Path.home() / ".kisskh" / "browser_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "https://kisskh.nl",
    ]

    # Vetted: cmd is a fixed argument list (no shell); exe is a resolved
    # browser path and the rest are constant flags — not shell input.
    subprocess.Popen(cmd)  # noqa: S603

    click.echo(f"Launched: {Path(exe).name}")
    click.echo(f"  Profile : {profile_dir}")
    click.echo(f"  CDP URL : http://localhost:{port}")
    click.echo("")
    click.echo("Chrome is opening https://kisskh.nl.")
    click.echo("If this is your first time, log in or dismiss any CAPTCHA.")
    click.echo("")
    click.echo("Then run:")
    click.echo(f'  kisskh collect "DRAMA_URL" --cdp-url http://localhost:{port}')
    click.echo("")


if __name__ == "__main__":
    kissget()
