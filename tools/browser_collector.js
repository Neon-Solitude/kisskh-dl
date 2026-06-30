/**
 * KissKH URL Collector
 *
 * Paste this once into your browser's DevTools Console while on any kisskh.nl page.
 * Because kisskh.nl is an Angular SPA, the hook persists across all episode navigation
 * without needing to paste it again.
 *
 * Workflow:
 *   1. Open https://kisskh.nl in your browser
 *   2. Navigate to Episode 1 of your drama
 *   3. Open DevTools (F12) → Console
 *   4. Paste this entire script and press Enter
 *   5. Click through each episode — the collector captures URLs automatically
 *      (you don't need to press Play; the API calls fire when the episode loads)
 *   6. When done, either:
 *        • click "Copy" to copy the manifest JSON to your clipboard, or
 *        • click "Download" to save it as <drama>_manifest.json (lands in your
 *          browser's Downloads folder — move it into the repo's manifests/ folder)
 *   7. Run: kissget dl --from-manifest manifests/<drama>_manifest.json -o "C:\Users\you\Downloads"
 *
 * Data is stored in localStorage, so it survives accidental page refreshes.
 * Click "Clear" in the overlay to start over for a different show.
 */
(function () {
  const STORAGE_KEY = "kissget_collector";

  // ── Helpers ─────────────────────────────────────────────────────────────

  function loadData() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY)) || { drama: "", episodes: {} };
    } catch {
      return { drama: "", episodes: {} };
    }
  }

  function saveData(data) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  }

  function currentEpisodeNumber() {
    const m = window.location.href.match(/[\/\?&]Episode[_-](\d+(?:\.\d+)?)/i);
    return m ? parseFloat(m[1]) : null;
  }

  function currentDramaSlug() {
    const m = window.location.pathname.match(/\/Drama\/([^\/]+)/i);
    if (!m) return "";
    // Collapse consecutive dashes and strip trailing punctuation that
    // kisskh adds when drama titles contain parentheses (e.g. "Deep-In--2026-")
    return m[1].replace(/--+/g, "-").replace(/[-_]+$/, "");
  }

  // ── Capture functions ────────────────────────────────────────────────────

  function captureStream(url) {
    // Try to extract episode number from the CDN URL path first.
    // Handles both old format (-Ep3/ or _Ep3/) and new format (/Ep3.)
    const epMatch = url.match(/[_\-\/]Ep(\d+(?:\.\d+)?)[\/\.]/i);
    const epNum = epMatch ? parseFloat(epMatch[1]) : currentEpisodeNumber();
    if (epNum === null) return;

    const data = loadData();
    const drama = currentDramaSlug();
    if (drama) data.drama = drama;
    if (!data.episodes[epNum]) data.episodes[epNum] = { number: epNum };
    if (!data.episodes[epNum].stream_url) {
      data.episodes[epNum].stream_url = url;
      saveData(data);
      console.log(
        `%c[kissget-collector] ✅ E${epNum} stream captured`,
        "color:lime;font-weight:bold",
        url.substring(0, 100) + (url.length > 100 ? "..." : "")
      );
      renderOverlay();
    }
  }

  function captureSubtitles(body) {
    const epNum = currentEpisodeNumber();
    if (!Array.isArray(body) || body.length === 0 || epNum === null) return;

    const subs = body
      .filter((s) => s.src)
      .map((s) => ({
        lang: s.land || s.lang || s.language || "",
        label: s.label || s.language || s.land || "",
        src: s.src,
      }));

    if (subs.length === 0) return;

    const data = loadData();
    const drama = currentDramaSlug();
    if (drama) data.drama = drama;
    if (!data.episodes[epNum]) data.episodes[epNum] = { number: epNum };
    data.episodes[epNum].subtitles = subs;
    saveData(data);

    console.log(
      `%c[kissget-collector] ✅ E${epNum} ${subs.length} sub(s) captured`,
      "color:lime;font-weight:bold",
      subs.map((s) => s.lang).join(", ")
    );
    renderOverlay();
  }

  // ── PerformanceObserver: catches m3u8 from ANY requester ────────────────
  // This sees video element requests, fetch, XHR — everything.
  // buffered:true also captures resources that already loaded before this ran,
  // so Episode 1's stream URL is captured immediately on paste.

  if (!window.__kisskh_po_installed) {
    window.__kisskh_po_installed = true;
    try {
      const po = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          const url = entry.name;
          if (
            (url.includes("index.m3u8") || url.match(/\bstream\b.*\.m3u8/i)) &&
            !url.includes(window.location.hostname)
          ) {
            captureStream(url);
          }
        }
      });
      po.observe({ type: "resource", buffered: true });
      console.log(
        "%c[kissget-collector] PerformanceObserver installed — already-loaded URLs captured too.",
        "color:lime;font-weight:bold"
      );
    } catch (e) {
      console.warn("[kissget-collector] PerformanceObserver unavailable:", e);
    }
  }

  // ── fetch hook: catches subtitle API responses ───────────────────────────

  if (!window.__kisskh_fetch_hooked) {
    window.__kisskh_fetch_hooked = true;
    const _originalFetch = window.fetch;

    window.fetch = async function (...args) {
      const response = await _originalFetch.apply(this, args);
      const url = (typeof args[0] === "string" ? args[0] : args[0]?.url) || "";

      // Stream URL via API response (belt-and-suspenders alongside PerformanceObserver)
      if (url.includes("/api/DramaList/Episode/") && url.includes(".png")) {
        response
          .clone()
          .json()
          .then((body) => {
            if (body?.Video) captureStream(body.Video);
          })
          .catch(() => {});
      }

      // Subtitle metadata
      if (url.includes("/api/Sub/")) {
        response.clone().json().then(captureSubtitles).catch(() => {});
      }

      return response;
    };

    console.log("%c[kissget-collector] fetch hook installed.", "color:lime;font-weight:bold");
  }

  // ── XHR hook: same as above but for Angular's HttpClient (uses XHR) ─────

  if (!window.__kisskh_xhr_hooked) {
    window.__kisskh_xhr_hooked = true;

    const _open = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url, ...rest) {
      this.__kisskh_url = String(url);
      return _open.apply(this, [method, url, ...rest]);
    };

    const _send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function (...args) {
      const url = this.__kisskh_url || "";
      if (url.includes("/api/DramaList/Episode/") || url.includes("/api/Sub/")) {
        this.addEventListener("load", () => {
          try {
            const body = JSON.parse(this.responseText);
            if (url.includes("/api/DramaList/Episode/") && body?.Video) {
              captureStream(body.Video);
            }
            if (url.includes("/api/Sub/")) {
              captureSubtitles(body);
            }
          } catch {}
        });
      }
      return _send.apply(this, args);
    };

    console.log("%c[kissget-collector] XHR hook installed.", "color:lime;font-weight:bold");
  }

  // ── Overlay UI ──────────────────────────────────────────────────────────

  function buildManifest() {
    const data = loadData();
    const episodes = Object.values(data.episodes)
      .sort((a, b) => a.number - b.number)
      .map((ep) => ({
        number: ep.number,
        stream_url: ep.stream_url || null,
        subtitles: ep.subtitles || [],
      }));
    return { drama: data.drama, episodes };
  }

  function renderOverlay() {
    const data = loadData();
    const eps = Object.values(data.episodes);
    const withStream = eps.filter((e) => e.stream_url).length;
    const withSubs = eps.filter((e) => e.subtitles && e.subtitles.length > 0).length;

    let overlay = document.getElementById("__kisskh_overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "__kisskh_overlay";
      overlay.style.cssText = [
        "position:fixed", "top:16px", "right:16px", "z-index:2147483647",
        "background:#0d0d0d", "color:#e0e0e0", "font-family:monospace",
        "font-size:13px", "border:1px solid #333", "border-radius:10px",
        "padding:14px 18px", "min-width:280px",
        "box-shadow:0 6px 30px rgba(0,0,0,0.7)", "line-height:1.6",
      ].join(";");
      document.body.appendChild(overlay);
    }

    overlay.innerHTML = `
      <div style="font-weight:bold;color:#00e676;font-size:14px;margin-bottom:8px">
        🎬 KissKH Collector
      </div>
      <div>Drama: <span style="color:#ffd740">${data.drama || "(not detected yet)"}</span></div>
      <div>Episodes captured: <span style="color:#40c4ff">${eps.length}</span></div>
      <div>Stream URLs: <span style="color:#40c4ff">${withStream}</span>
           &nbsp;·&nbsp; Subs: <span style="color:#40c4ff">${withSubs}</span></div>
      <div style="color:#888;font-size:11px;margin:8px 0 10px">
        Navigate to each episode — data captures automatically.<br>
        You don't need to press Play.
      </div>
      <div style="display:flex;gap:8px">
        <button id="__kisskh_copy" style="
          flex:1;background:#00897b;color:#fff;border:none;
          padding:7px 10px;border-radius:6px;cursor:pointer;font-weight:bold
        ">📋 Copy</button>
        <button id="__kisskh_download" style="
          flex:1;background:#1565c0;color:#fff;border:none;
          padding:7px 10px;border-radius:6px;cursor:pointer;font-weight:bold
        ">💾 Download</button>
        <button id="__kisskh_clear" style="
          background:#b71c1c;color:#fff;border:none;
          padding:7px 10px;border-radius:6px;cursor:pointer
        ">🗑</button>
        <button id="__kisskh_close" style="
          background:#333;color:#aaa;border:none;
          padding:7px 10px;border-radius:6px;cursor:pointer
        ">✕</button>
      </div>
    `;

    document.getElementById("__kisskh_copy").onclick = function () {
      const manifest = buildManifest();
      const json = JSON.stringify(manifest, null, 2);
      navigator.clipboard.writeText(json).then(() => {
        this.textContent = "✅ Copied!";
        setTimeout(() => { this.textContent = "📋 Copy"; }, 2500);
        console.log(
          "%c[kissget-collector] Manifest copied to clipboard.",
          "color:lime;font-weight:bold"
        );
        console.log(json);
      }).catch(() => {
        console.log(
          "%c[kissget-collector] Clipboard blocked. Manifest logged below:",
          "color:orange;font-weight:bold"
        );
        console.log(json);
        alert("Clipboard blocked — manifest printed to console. Copy it from there.");
      });
    };

    document.getElementById("__kisskh_download").onclick = function () {
      const manifest = buildManifest();
      const json = JSON.stringify(manifest, null, 2);
      // Sanitize the drama slug into a safe filename component.
      const safe =
        (manifest.drama || "manifest")
          .replace(/[\\/:*?"<>|]+/g, "_")
          .replace(/-+/g, "-")
          .replace(/^[-_.]+|[-_.]+$/g, "") || "manifest";
      const filename = `${safe}_manifest.json`;
      const blob = new Blob([json], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      this.textContent = "✅ Saved!";
      setTimeout(() => { this.textContent = "💾 Download"; }, 2500);
      console.log(
        `%c[kissget-collector] Manifest downloaded as ${filename} → move it into the repo's manifests/ folder.`,
        "color:lime;font-weight:bold"
      );
    };

    document.getElementById("__kisskh_clear").onclick = function () {
      if (confirm("Clear all collected episode data?")) {
        saveData({ drama: "", episodes: {} });
        renderOverlay();
        console.log("%c[kissget-collector] Data cleared.", "color:orange");
      }
    };

    document.getElementById("__kisskh_close").onclick = function () {
      overlay.remove();
      console.log(
        "%c[kissget-collector] Overlay hidden — collector still active. " +
        "Re-run the script to show overlay again.",
        "color:yellow"
      );
    };
  }

  // Show or refresh the overlay immediately
  renderOverlay();
})();
