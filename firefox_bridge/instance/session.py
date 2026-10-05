"""One long-lived IDX tab so a Cloudflare wait can resolve in-browser.

A download aimed straight at an ``instance.zip`` URL is a bare request: no page
origin, no Referer, nothing to mark it as part of an already-challenged browser
session. Cloudflare answers those with a 403 interstitial -- Firefox then creates
the file and immediately withdraws it -- and the result is a staging path that
was never written to, which the dead-window check in ``_await_archive`` can
detect but cannot on its own repair.

The page-driven program never sees this because it downloads from a page it just
loaded in a browser that has already answered the challenge. This program builds
its URLs from a template instead, so it has to buy that same condition itself:
keep a single tab on idx.co.id, wait for the challenge to clear, and re-point it
back at IDX whenever a download comes back empty-handed. The refresh is what
lets the next challenge -- if there is one -- play out in a tab instead of
killing the download outright, and it is always best-effort: a session that
cannot be opened leaves every download to fail in turn, but never aborts the run.
"""

from __future__ import annotations

import time
from typing import Any

from firefox_bridge.client import FirefoxBridgeClient, FirefoxBridgeClientError
from firefox_bridge.pacing import wait_before_step
from firefox_bridge.progress import notice, progress

from .urls import instance_url

IDX_URL = "https://www.idx.co.id/id"

# Substrings Cloudflare serves on a page that is still checking the client out.
CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "verifying you are human",
    "attention required",
)

# Substrings that let an archive-URL response be read as a 404 without parsing
# HTML: IDX and the upstream CDN both spell the refusal out on the error page.
NOT_FOUND_MARKERS = (
    "404",
    "not found",
    "halaman tidak ditemukan",
    "page not found",
    "tidak tersedia",
    "not available",
    "resource cannot be found",
)

# How long the warm-up tab is allowed to render when diagnosing a failed
# download. Bounded so a hung challenge cannot pin the diagnosis for long.
PROBE_WAIT_SECONDS = 6.0

# The real homepage is hundreds of characters; the only thing shorter is a
# page still rendering or a challenge interstitial, so a modest floor above a
# blank tab is enough to tell them apart without a string that a redesign could
# invalidate.
MIN_READY_CHARS = 40

# A fresh tab -- or one that just re-challenged -- can take a while to render.
# It is bounded so a hung challenge can never pin the run; best-effort, so a
# timeout here is reported and the first download is left to prove the point.
READY_TIMEOUT_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 1.0


def is_clear_page(page_text: str) -> bool:
    """Return True when ``page_text`` looks like IDX rather than a challenge."""
    lowered = page_text.lower()
    if any(marker in lowered for marker in CHALLENGE_MARKERS):
        return False
    return len(lowered.strip()) >= MIN_READY_CHARS


def classify_instance_page(page_text: str) -> str:
    """Classify a probed archive-URL response into a short reason string.

    Operates only on the page text Firefox already rendered, so it is unit
    tested without a browser; the tab navigation that fetched that text is the
    caller's concern. A dead-window download is identical for a 404, a
    Cloudflare refusal and a transient bridge error, which is the only thing
    this exists to tell apart.
    """
    lowered = (page_text or "").lower()
    if any(marker in lowered for marker in CHALLENGE_MARKERS):
        return "Cloudflare challenge"
    if any(marker in lowered for marker in NOT_FOUND_MARKERS):
        return "404 Not Found"
    # Not a challenge and not a recognized 404, but too short to be the real
    # IDX homepage: some other interstitial (e.g. a 403 body) rather than a
    # clean success.
    if not is_clear_page(page_text):
        return "Cloudflare/other error"
    # A full IDX homepage means the URL did not 404 and did not challenge --
    # the archive itself served, yet downloads.download still wrote nothing.
    # Reported only: not salvaged, keeping the "no fallback" contract.
    return "200 (file exists; downloads.download failed)"


class IdxSession:
    """One kept-open IDX tab, opened on demand and re-pointed on refusal."""

    def __init__(self, client: FirefoxBridgeClient) -> None:
        self._client = client
        self._tab_id: Any = None

    @property
    def tab_id(self) -> Any:
        """The live tab, or None if one is not currently open."""
        return self._tab_id

    def ensure(self) -> None:
        """Open the tab if it is gone, point it at IDX, and wait for the page.

        Best effort throughout: never raises. A cleared session makes downloads
        likely to succeed; a missing one leaves each download to fail in turn.
        """
        if self._tab_id is None:
            self._open()
        else:
            self._navigate()
        self._wait_cleared()

    def refresh(self) -> None:
        """Re-point the tab at IDX so any pending challenge clears here.

        Called after a download came back with nothing: the cleared state may
        have lapsed, and re-navigating gives Cloudflare a tab to run its check
        in before the next attempt fires.
        """
        if self._tab_id is None:
            self.ensure()
            return
        try:
            wait_before_step("menyegarkan tab IDX (cloudflare)")
            self._client.navigate(self._tab_id, IDX_URL)
        except FirefoxBridgeClientError as error:
            notice(
                f"WARN: gagal menyegarkan tab IDX "
                f"({type(error).__name__}: {error}); membuka tab baru",
            )
            self._tab_id = None
            self._open()
        self._wait_cleared()

    def close(self) -> None:
        """Tear the tab down. Best effort: it may already be gone."""
        tab_id = self._tab_id
        if tab_id is None:
            return
        try:
            self._client.close_tab(tab_id)
        except FirefoxBridgeClientError:
            pass
        self._tab_id = None

    def _open(self) -> None:
        wait_before_step("membuka tab IDX (warmup cloudflare)")
        try:
            info = self._client.open_tab(IDX_URL, active=False)
        except FirefoxBridgeClientError as error:
            notice(
                f"WARN: tidak dapat membuka tab IDX "
                f"({type(error).__name__}: {error}); unduhan tetap dicoba",
            )
            return
        self._tab_id = info.get("id") if isinstance(info, dict) else info

    def _navigate(self) -> None:
        try:
            self._client.navigate(self._tab_id, IDX_URL)
        except FirefoxBridgeClientError:
            # The tab is stale or gone: drop it and let `_open` make a fresh one.
            self._tab_id = None
            self._open()

    def _wait_cleared(self) -> None:
        if self._tab_id is None:
            return
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                page_text = self._text()
            except FirefoxBridgeClientError:
                return
            if is_clear_page(page_text):
                progress("STEP warmup: sesi IDX siap (takleo Cloudflare)", tab_id=self._tab_id)
                return
            time.sleep(POLL_INTERVAL_SECONDS)
        notice(
            f"WARN: tab IDX belum pasti melewati tantangan setelah "
            f"{READY_TIMEOUT_SECONDS:.0f}s; melanjutkan, unduhan pertama akan membuktikan"
        )

    def _text(self) -> str:
        result = self._client.text(self._tab_id, max_chars=4000)
        if isinstance(result, dict):
            return str(result.get("text") or "")
        return str(result or "")

    def probe_archive_reason(self, stock: str, year: int) -> str:
        """Navigate the held tab at one archive URL once and classify the response.

        Attached to a dead-window failure so a per-stock timeout can be
        reported as ``404 Not Found``, ``Cloudflare challenge`` or similar,
        instead of an opaque "Unduhan tidak pernah menulis apa-apa". The tab is
        restored to IDX afterwards so the run stays warm for the next stock.

        Best effort throughout: any failure resolves to a generic marker and is
        caught by the caller, so probing can never abort a run. Strictly
        diagnostic -- a URL that genuinely serves a zip is reported, not
        salvaged, keeping the "no fallback" contract.
        """
        if self._tab_id is None:
            return "tidak ada tab warm-up untuk probe"
        href = instance_url(stock, year)
        try:
            wait_before_step(f"probe alasan gagal {stock} {year}")
            self._client.navigate(self._tab_id, href)
        except FirefoxBridgeClientError as error:
            return f"probe navigasi gagal ({type(error).__name__}: {error})"
        text = ""
        deadline = time.monotonic() + PROBE_WAIT_SECONDS
        while time.monotonic() < deadline:
            try:
                text = self._text()
            except FirefoxBridgeClientError:
                break
            if text.strip():
                break
            time.sleep(POLL_INTERVAL_SECONDS)
        reason = classify_instance_page(text or "")
        try:
            self._client.navigate(self._tab_id, IDX_URL)
        except FirefoxBridgeClientError:
            pass
        return reason


__all__ = [
    "IDX_URL",
    "IdxSession",
    "is_clear_page",
    "classify_instance_page",
]
