"""Utility to generate the kkey authentication token for kisskh API requests.

Two modes:

  CDP mode (preferred):
    Connects to your real Chrome/Edge browser via the Chrome DevTools Protocol.
    No bot detection — the site sees your actual browser fingerprint and cookies.
    Use ``kisskh open-browser`` first to launch Chrome with CDP enabled,
    then pass ``--cdp-url http://localhost:9222`` to collect/dl.

  Playwright mode (fallback):
    Launches a Playwright-managed Chromium to load the episode page and
    intercept kkeys from network requests. This WILL be detected by kisskh.nl
    which currently redirects Playwright browsers to the homepage.

If you set KISSKH_STREAM_KEY and KISSKH_SUB_KEY environment variables,
neither mode is needed — the keys are used directly.
"""

from __future__ import annotations

import logging
import re
import tempfile
import time

logger = logging.getLogger(__name__)

_playwright_available = None
_stealth_available = None


def _check_playwright() -> bool:
    global _playwright_available  # noqa: PLW0603
    if _playwright_available is None:
        try:
            import playwright  # noqa: F401
            _playwright_available = True
        except ImportError:
            _playwright_available = False
    return _playwright_available


def _check_stealth() -> bool:
    global _stealth_available  # noqa: PLW0603
    if _stealth_available is None:
        try:
            import playwright_stealth  # noqa: F401
            _stealth_available = True
        except ImportError:
            _stealth_available = False
    return _stealth_available


# Play button selectors tried in order when automating video start
_PLAY_SELECTORS = [
    'button[aria-label*="lay"]',
    ".plyr__control--overlaid",
    ".vjs-big-play-button",
    '[class*="play-btn"]',
    '[class*="PlayBtn"]',
    '[class*="playButton"]',
    'button[class*="play"]',
    "video",
]


class KkeyProvider:
    """Generates kkey tokens by loading the episode page in a browser.

    When ``cdp_url`` is set, connects to an existing Chrome/Edge instance via
    CDP — this uses your real browser fingerprint and cookies, bypassing all
    bot detection.  Run ``kisskh open-browser`` first.

    Without ``cdp_url``, falls back to a Playwright-managed Chromium, which is
    currently detected and blocked by kisskh.nl.

    Requires Playwright:
        pip install playwright
        playwright install chromium
    """

    _playwright_started = False
    _context = None      # BrowserContext (Playwright persistent or CDP)
    _cdp_browser = None  # Browser object only in CDP mode
    _user_data_dir = None

    def __init__(
        self,
        headless: bool = True,
        playwright_timeout: int = 30000,
        cdp_url: str | None = None,
    ) -> None:
        self.headless = headless
        self.playwright_timeout = playwright_timeout
        self.cdp_url = cdp_url

    def _ensure_context(self):
        """Return a shared BrowserContext, creating it on first call."""
        if KkeyProvider._context is not None:
            return KkeyProvider._context

        if not _check_playwright():
            raise ImportError(
                "Playwright is required to generate kkey tokens, but it is not installed.\n"
                "Install it with:\n"
                "  pip install playwright\n"
                "  playwright install chromium\n\n"
                "Alternatively, set KISSKH_STREAM_KEY and KISSKH_SUB_KEY environment variables\n"
                "to skip browser-based kkey generation."
            )

        from playwright.sync_api import sync_playwright

        if not KkeyProvider._playwright_started:
            KkeyProvider._pw = sync_playwright().start()
            KkeyProvider._playwright_started = True

        if self.cdp_url:
            # ── CDP mode: attach to the user's real Chrome/Edge ──────────────
            logger.info("Connecting to browser via CDP at %s ...", self.cdp_url)
            try:
                KkeyProvider._cdp_browser = KkeyProvider._pw.chromium.connect_over_cdp(
                    self.cdp_url
                )
            except Exception as e:
                raise ConnectionError(
                    f"Could not connect to a browser at {self.cdp_url}.\n"
                    "Make sure Chrome/Edge is running with remote debugging enabled.\n"
                    "Run this first:  kisskh open-browser\n"
                    f"(Original error: {e})"
                ) from e

            contexts = KkeyProvider._cdp_browser.contexts
            KkeyProvider._context = (
                contexts[0] if contexts else KkeyProvider._cdp_browser.new_context()
            )
            logger.info("Connected — using existing browser session.")
        else:
            # ── Playwright mode: launch our own Chromium ─────────────────────
            if KkeyProvider._user_data_dir is None:
                KkeyProvider._user_data_dir = tempfile.mkdtemp(prefix="kisskh_browser_")

            mode_label = "headed" if not self.headless else "headless"
            logger.info("Launching Chromium (%s mode)...", mode_label)

            KkeyProvider._context = KkeyProvider._pw.chromium.launch_persistent_context(
                KkeyProvider._user_data_dir,
                headless=self.headless,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/147.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                viewport={"width": 1280, "height": 720},
                args=["--disable-blink-features=AutomationControlled"],
            )

        return KkeyProvider._context

    def get_kkeys(
        self,
        drama_id: int,
        episode_id: int,
        episode_number: int,
        drama_title: str,
        episode_page_url: str,
    ) -> dict[str, str]:
        """Load the episode page and capture kkeys from network requests.

        Returns a dict with keys ``stream`` and ``sub``.
        """
        context = self._ensure_context()
        page = context.new_page()

        # Apply stealth only in Playwright mode — real Chrome (CDP) doesn't need it.
        if not self.cdp_url:
            if _check_stealth():
                from playwright_stealth import Stealth
                Stealth().apply_stealth_sync(page)
                logger.debug("playwright-stealth applied")
            else:
                logger.debug("playwright-stealth not installed; bot detection possible")

        captured_kkeys: dict[str, str] = {}

        def _on_request(request) -> None:
            url = request.url
            if "kkey=" not in url:
                return
            m = re.search(r"[?&]kkey=([A-Fa-f0-9]+)", url)
            if not m:
                return
            kkey = m.group(1)
            if "/api/Sub/" in url:
                captured_kkeys["sub"] = kkey
                logger.debug("Captured sub kkey: %s...", kkey[:24])
            elif "/api/DramaList/Episode/" in url:
                captured_kkeys["stream"] = kkey
                logger.debug("Captured stream kkey: %s...", kkey[:24])

        try:
            page.on("request", _on_request)

            if not self.cdp_url:
                # In Playwright mode, visit the homepage first to build a navigation trail.
                site_root = episode_page_url.split("/Drama/")[0]
                logger.debug("Landing on homepage first: %s", site_root)
                page.goto(
                    site_root,
                    timeout=self.playwright_timeout,
                    wait_until="domcontentloaded",
                )
                page.wait_for_timeout(2000)

            logger.info("Loading episode page: %s", episode_page_url)
            page.goto(
                episode_page_url,
                timeout=self.playwright_timeout,
                wait_until="domcontentloaded",
            )

            # Give Angular time to bootstrap and fire initial API calls.
            # CDP mode needs less wait — real Chrome loads faster.
            boot_wait = 5000 if self.cdp_url else 8000
            page.wait_for_timeout(boot_wait)

            # Try clicking the episode number button if the page lists episodes
            try:
                btns = page.locator(f"button:has-text('{episode_number}')")
                if btns.count() > 0:
                    btns.first.click()
                    logger.debug("Clicked episode %s button", episode_number)
                    page.wait_for_timeout(2000)
            except Exception:
                pass

            def _try_click_play() -> bool:
                for selector in _PLAY_SELECTORS:
                    try:
                        el = page.locator(selector).first
                        if el.is_visible(timeout=400):
                            el.scroll_into_view_if_needed()
                            el.click()
                            logger.debug("Clicked play selector: %s", selector)
                            return True
                    except Exception:
                        continue
                return False

            _try_click_play()

            # Poll for kkeys; headed/CDP modes get longer timeout so the user
            # can click Play manually if our automation misses the button.
            poll_timeout = (
                60 if (self.cdp_url or not self.headless) else self.playwright_timeout / 1000
            )
            if self.cdp_url or not self.headless:
                logger.info(
                    "Waiting up to %.0fs for kkeys — click Play in the browser if needed...",
                    poll_timeout,
                )

            deadline = time.time() + poll_timeout
            while time.time() < deadline:
                if len(captured_kkeys) >= 2:
                    logger.debug(
                        "Captured kkeys for episode %s (stream: %s..., sub: %s...)",
                        episode_id,
                        captured_kkeys.get("stream", "")[:24],
                        captured_kkeys.get("sub", "")[:24],
                    )
                    break

                if not captured_kkeys:
                    _try_click_play()

                page.wait_for_timeout(1000)

        except Exception as e:
            logger.warning("Error while capturing kkeys: %s", e)
        finally:
            try:
                page.close()
            except Exception:
                pass

        if not captured_kkeys:
            if self.cdp_url:
                raise RuntimeError(
                    f"Failed to capture kkey for episode {episode_id} via CDP.\n\n"
                    "The episode page loaded in your browser but no kkey API calls were detected.\n"
                    "Try clicking Play manually in the browser window that just opened, then\n"
                    "re-run the collect command."
                )
            raise RuntimeError(
                f"Failed to capture kkey for episode {episode_id}.\n\n"
                "kisskh.nl is blocking the automated Playwright browser.\n"
                "Use your real Chrome/Edge instead:\n\n"
                "  kisskh open-browser          # launch Chrome with CDP\n"
                "  kisskh collect \"URL\" --cdp-url http://localhost:9222\n\n"
                "Or pass pre-captured keys directly:\n"
                "  kisskh collect \"URL\" --stream-key KEY --sub-key KEY"
            )

        return captured_kkeys

    @classmethod
    def cleanup(cls):
        """Disconnect from the browser and clean up Playwright resources."""
        if cls._context is not None and cls._cdp_browser is None:
            # Playwright-managed context: close it
            try:
                cls._context.close()
            except Exception:
                logger.debug("Error closing browser context", exc_info=True)
        cls._context = None

        if cls._cdp_browser is not None:
            # CDP mode: disconnect only — don't close the user's browser
            try:
                cls._cdp_browser.close()
            except Exception:
                logger.debug("Error disconnecting from CDP browser", exc_info=True)
            cls._cdp_browser = None

        if cls._playwright_started:
            try:
                cls._pw.stop()
            except Exception:
                logger.debug("Error stopping playwright", exc_info=True)
            cls._playwright_started = False
