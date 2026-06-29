# kisskh-dl

<div align="center">
   <img src="https://i.imgur.com/nhQtOZa.png">
   <br>
   <strong><i>Simple downloader for https://kisskh.nl/</i></strong>
   <br>
   <a href="https://pypi.org/project/kisskh-downloader/">
   <img src="https://img.shields.io/pypi/v/kisskh-downloader?style=for-the-badge">
   </a>
   <img src="https://img.shields.io/github/actions/workflow/status/debakarr/kisskh-dl/pull-request.yml?style=for-the-badge">
   <img src="https://img.shields.io/pypi/dm/kisskh-downloader?style=for-the-badge">
</div>

---

Command-line tool for downloading dramas from [kisskh.nl](https://kisskh.nl/).

## Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Method A: Browser Collector Script (Recommended)](#method-a-browser-collector-script-recommended)
- [Method B: `kisskh collect` CLI](#method-b-kisskh-collect-cli)
- [Method C: Direct API Download](#method-c-direct-api-download)
- [Command Reference](#command-reference)
- [Authentication](#authentication)
- [N_m3u8DL-RE (Faster Downloads)](#n_m3u8dl-re-faster-downloads)
- [Troubleshooting](#troubleshooting)
- [Environment Variables](#environment-variables)

---

## Installation

```console
pip install -U kisskh-downloader
```

---

## Quick Start

kisskh.nl requires a short-lived `kkey` authentication token for its stream and subtitle APIs. Because these tokens expire in seconds, the most reliable workflow captures CDN URLs directly from your browser (which already has a valid session) and saves them to a manifest file. The downloader then uses the manifest — no kkeys needed.

**Recommended workflow:**

1. Open the drama in your browser and paste the [browser collector script](#method-a-browser-collector-script-recommended) into DevTools Console.
2. Click through each episode. URLs are captured automatically.
3. Click **Copy Manifest** in the overlay that appears.
4. Save the JSON to a file and run:

```console
kisskh dl --from-manifest manifest.json -s en -o "C:\Users\you\Videos"
```

---

## Method A: Browser Collector Script (Recommended)

This is the most reliable method. It works regardless of bot detection or kkey expiry because it captures URLs from inside your own browser session.

### How it works

The script in `tools/browser_collector.js` hooks into three interception layers:

- **PerformanceObserver** — catches m3u8 stream URLs from the native `<video>` element (and anything else), including resources that already loaded before the script ran
- **XHR hook** — catches Angular's `HttpClient` calls for subtitle metadata (`/api/Sub/`)
- **fetch hook** — belt-and-suspenders for any subtitle calls made via `window.fetch`

Data is saved to `localStorage` so it survives accidental page refreshes.

### Steps

1. Open your browser and navigate to the drama's first episode on kisskh.nl.
2. Open DevTools (F12) → **Console** tab.
3. Paste the entire contents of `tools/browser_collector.js` and press Enter.
4. A floating overlay appears in the top-right corner of the page.
5. Click through each episode. You do **not** need to press Play — the URLs are captured as soon as the episode page loads.
6. When all episodes show in the overlay counter, click **Copy Manifest**.
7. Paste the JSON into a file (e.g. `manifest.json`).

### Running the download

```console
kisskh dl --from-manifest manifest.json -s en -o "C:\Users\you\Videos\TV Shows"
```

Add `-s all` to download every available subtitle language, or `-s en -s ko` for multiple specific languages.

To download subtitles only (no video):

```console
kisskh dl --from-manifest manifest.json -s en --subs-only -o "C:\Users\you\Videos\TV Shows"
```

To download a subset of episodes from the manifest:

```console
kisskh dl --from-manifest manifest.json -s en -f 5 -l 8 -o "C:\Users\you\Videos\TV Shows"
```

### Re-using the overlay

The hooks persist for the lifetime of the page. If you close the overlay, re-paste the script to bring it back — no data is lost. Click **Clear** in the overlay to start fresh for a different show.

---

## Method B: `kisskh collect` CLI

`kisskh collect` automates the manifest-building step from the command line. It visits each episode page using a browser (Playwright or your own Chrome via CDP) to extract stream and subtitle URLs, then writes them to a manifest JSON file.

### Prerequisites

Install Playwright's Chromium browser:

```console
playwright install chromium
```

### Basic usage

```console
kisskh collect "https://kisskh.nl/Drama/Customized-Lover-(2026)?id=13191"
```

This produces `Customized-Lover--2026-_manifest.json` in the current directory.

### Using your own Chrome (bypasses bot detection)

If Playwright's headless browser is detected and blocked, connect to your real browser instead:

```console
# Step 1 — open Chrome with remote debugging
kisskh open-browser

# Step 2 — collect using that browser session
kisskh collect "https://kisskh.nl/Drama/Show-Name?id=1234" --cdp-url http://localhost:9222
```

The first time, log in or solve any CAPTCHA in the opened browser window. Cookies are saved to `~/.kisskh/browser_profile` for future runs.

### Collect options

| Flag | Default | Description |
|---|---|---|
| `-f / --first` | 1 | Starting episode number |
| `-l / --last` | (all) | Ending episode number |
| `-s / --sub-langs` | `en` | Subtitle language(s) to collect. Repeat for multiple: `-s en -s ko`. Use `-s all` for all languages. |
| `--skip-recap` | off | Skip recap/special episodes (fractional numbers like 16.1) |
| `--headed` | off | Show the Playwright browser window (useful for solving CAPTCHAs) |
| `--stream-key` | — | Pre-generated kkey for the stream endpoint |
| `--sub-key` | — | Pre-generated kkey for the subtitle endpoint |
| `--cdp-url` | — | Connect to a real Chrome/Edge via CDP |
| `-o / --output` | `<drama>_manifest.json` | Output file path |

### Then download

```console
kisskh dl --from-manifest "Show-Name_manifest.json" -s en -o "C:\Users\you\Videos"
```

---

## Method C: Direct API Download

This method hits the kisskh API directly and generates kkeys automatically via Playwright. It is simpler to start but more fragile — kkeys expire within seconds, which can cause failures on multi-episode downloads.

### Prerequisites

```console
playwright install chromium
```

### Download entire series by URL

```console
kisskh dl "https://kisskh.nl/Drama/Island-Season-2?id=7000" -o .
```

### Download by search query

```console
kisskh dl "Stranger Things" -o .
```

### Download specific episode range

```console
kisskh dl "https://kisskh.nl/Drama/Alchemy-of-Souls?id=5043" -f 4 -l 8 -q 720p -o .
```

### Download a single episode

```console
kisskh dl "https://kisskh.nl/Drama/A-Business-Proposal/Episode-3?id=4608&ep=86439&page=0&pageSize=100" -o .
```

### Download subtitles first (recommended for large batches)

When downloading many episodes, use `--subs-first` to download all subtitles in one pass (while the kkey is fresh), then download all videos:

```console
kisskh dl "https://kisskh.nl/Drama/Show-Name?id=1234" --subs-first -s en -o .
```

### Skip recap/special episodes

```console
kisskh dl "https://kisskh.nl/Drama/Show-Name?id=1234" --skip-recap -o .
```

### Headed mode (for CAPTCHA)

If the site blocks the headless browser with a CAPTCHA, run in headed mode so you can solve it manually:

```console
kisskh dl "https://kisskh.nl/Drama/Show-Name?id=1234" --headed -o .
```

---

## Command Reference

### `kisskh dl`

```
Usage: kisskh dl [OPTIONS] [DRAMA_URL_OR_NAME]

  Download episodes from kisskh.

  DRAMA_URL_OR_NAME can be a full URL (e.g. https://kisskh.nl/Drama/Some-Show?id=1234)
  or a search query (e.g. "Stranger Things").

Options:
  --from-manifest PATH            Read stream URLs and subtitles from a JSON
                                  manifest file instead of hitting the API.
  -f, --first INTEGER             Starting episode number.  [default: 1]
  -l, --last INTEGER              Ending episode number.
  -q, --quality [360p|480p|540p|720p|1080p]
                                  Quality of the video to be downloaded.
                                  [default: 1080p]
  -s, --sub-langs TEXT            Languages of the subtitles to download.
                                  Repeat for multiple: -s en -s ko.
                                  [default: en]
  -o, --output-dir TEXT           Output directory.  [default: ~/Downloads]
  -so, --subs-only                Download subtitles only, skip video.
  -sf, --subs-first               Download all subtitles first, then all
                                  videos (keeps kkey fresh).
  --skip-recap                    Skip recap/special episodes.
  --headed                        Run browser in visible mode for CAPTCHA.
  --stream-key TEXT               Pre-generated kkey for stream endpoint.
  --sub-key TEXT                  Pre-generated kkey for subtitle endpoint.
  --n-m3u8dl-re PATH              Path to N_m3u8DL-RE.exe (auto-detected
                                  from PATH if not set).
  -ds, --decrypt-subtitle         Decrypt the downloaded subtitle.
  -k, --key TEXT                  Subtitle decryption key.
  -iv, --initialization-vector TEXT
                                  Initialization vector for subtitle
                                  decryption.
  --help                          Show this message and exit.
```

### `kisskh collect`

```
Usage: kisskh collect [OPTIONS] DRAMA_URL_OR_NAME

  Capture stream and subtitle URLs into a JSON manifest for kkey-free downloads.

Options:
  -f, --first INTEGER             Starting episode number.  [default: 1]
  -l, --last INTEGER              Ending episode number.
  -s, --sub-langs TEXT            Subtitle language(s) to collect.
                                  [default: en]
  --skip-recap                    Skip recap/special episodes.
  --headed                        Show the Playwright browser window.
  --stream-key TEXT               Pre-generated kkey for stream endpoint.
  --sub-key TEXT                  Pre-generated kkey for subtitle endpoint.
  --cdp-url TEXT                  CDP URL to connect to a real Chrome/Edge
                                  (e.g. http://localhost:9222).
  -o, --output TEXT               Output manifest JSON file path.
  --help                          Show this message and exit.
```

### `kisskh get-key`

Generates and displays `kkey` tokens for a drama episode URL. Opens a headless browser to extract the authentication keys.

```console
kisskh get-key "https://kisskh.nl/Drama/A-Business-Proposal/Episode-1?id=4608&ep=86192&page=0&pageSize=100"
```

Output:

```
  Stream key:  <long_hex_string>
  Sub key:     <long_hex_string>

  To use these without a browser next time, set these env vars:

    set KISSKH_STREAM_KEY=<long_hex_string>
    set KISSKH_SUB_KEY=<long_hex_string>
```

### `kisskh open-browser`

Launches Chrome or Edge with CDP remote debugging enabled. Use this before `kisskh collect --cdp-url` to bypass bot detection.

```console
kisskh open-browser [--port 9222] [--browser-path PATH]
```

The browser opens a persistent profile at `~/.kisskh/browser_profile` — cookies and login sessions are preserved between runs.

---

## Authentication

kisskh.nl requires a short-lived `kkey` token for stream and subtitle API calls. There are several ways to provide it:

| Method | When to use |
|---|---|
| **Browser collector script** (`tools/browser_collector.js`) | Best: captures CDN URLs directly from your browser; no kkeys needed at all |
| **`kisskh collect` with CDP** (`--cdp-url`) | Good: uses your real browser session via `kisskh open-browser` |
| **`kisskh collect` with Playwright** | Automated: requires `playwright install chromium`; may be blocked by bot detection |
| **`kisskh get-key` + env vars** | Manual: generate keys once and export them; they expire within seconds |
| **Playwright per-episode** | Fallback: kkeys generated automatically per episode via headless browser |

### Environment variables for authentication

| Variable | Description |
|---|---|
| `KISSKH_STREAM_KEY` | Pre-generated kkey for the stream endpoint |
| `KISSKH_SUB_KEY` | Pre-generated kkey for the subtitle endpoint |

Set both to skip browser-based kkey generation entirely:

**Windows (cmd):**
```cmd
set KISSKH_STREAM_KEY=your_stream_key_here
set KISSKH_SUB_KEY=your_sub_key_here
kisskh dl "https://kisskh.nl/Drama/Show-Name?id=1234" -o .
```

**Linux / macOS:**
```bash
export KISSKH_STREAM_KEY=your_stream_key_here
export KISSKH_SUB_KEY=your_sub_key_here
kisskh dl "https://kisskh.nl/Drama/Show-Name?id=1234" -o .
```

---

## N_m3u8DL-RE (Faster Downloads)

[N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE) is a multi-threaded HLS downloader. When available, `kisskh dl` uses it automatically instead of yt-dlp, resulting in significantly faster downloads.

### Setup

Download `N_m3u8DL-RE.exe` and place it in one of these locations (auto-detected):

- Anywhere on your system `PATH`
- `~/Programming/GitHub/N_m3u8DL_RE_GUI-main/N_m3u8DL-RE.exe`

Or specify the path explicitly:

```console
kisskh dl --from-manifest manifest.json --n-m3u8dl-re "C:\tools\N_m3u8DL-RE.exe" -o .
```

If ffmpeg is in the same directory as N_m3u8DL-RE, it is detected automatically and used to mux the output to MP4.

---

## Decrypting Subtitles

Some subtitle files are encrypted. Pass the decryption key and IV with `--decrypt-subtitle`:

```console
kisskh dl "DRAMA_URL" --decrypt-subtitle --key "your_key" --initialization-vector "your_iv" -o .
```

Or set them as environment variables:

**Windows:**
```cmd
set KISSKH_KEY=your_key_here
set KISSKH_INITIALIZATION_VECTOR=your_iv_here
```

**Linux / macOS:**
```bash
export KISSKH_KEY=your_key_here
export KISSKH_INITIALIZATION_VECTOR=your_iv_here
```

Then use `--decrypt-subtitle` without the explicit flags:

```console
kisskh dl "DRAMA_URL" --decrypt-subtitle -o .
```

---

## Troubleshooting

### Downloads fail with "Connection refused" or get redirected to a block page

Your ISP may be blocking the CDN domain at the network level. The site streams video through CDN hosts that some ISPs intercept. To fix this, connect to a VPN **at the system level** (desktop app, not a browser extension). A browser extension only routes browser traffic — the downloader runs outside the browser and needs a system-wide VPN.

### SSL errors (`WRONG_VERSION_NUMBER`) on video or subtitle downloads

Some CDN hosts serve HTTP traffic on port 443. The tool handles this automatically by downgrading `https://` to `http://` for non-kisskh CDN hosts.

### kkey tokens expire too fast

kkeys are session-based and expire in seconds. Use the [browser collector script](#method-a-browser-collector-script-recommended) or `kisskh collect` to build a manifest of CDN URLs that don't require kkeys.

### Playwright is blocked by Cloudflare

Run with `--headed` to open the browser visibly so you can solve the CAPTCHA, or use `kisskh open-browser` + `--cdp-url` to connect to your own Chrome session.

### Quality fallback

If the selected quality is not available, the tool downloads the next lower available quality. If no matching quality exists, it falls back to the best available.

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `KISSKH_BASE_URL` | Site base URL | `https://kisskh.nl` |
| `KISSKH_STREAM_KEY` | Pre-generated kkey for stream endpoint | — |
| `KISSKH_SUB_KEY` | Pre-generated kkey for subtitle endpoint | — |
| `KISSKH_KEY` | Subtitle decryption key | — |
| `KISSKH_INITIALIZATION_VECTOR` | Subtitle decryption initialization vector | — |

---

## Debug Logging

Use `-v` for verbose output or `-vv` for full debug logging:

```console
kisskh -vv dl --from-manifest manifest.json -s en -o .
```

```console
kisskh -vv dl "https://kisskh.nl/Drama/A-Business-Proposal?id=4608" -f 3 -l 3 -q 720p
```
