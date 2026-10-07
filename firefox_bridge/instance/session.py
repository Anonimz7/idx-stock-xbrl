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
from collections.abc import Callable
from typing import Any

from firefox_bridge.captcha import (
    CAPTCHA_REASON,
    MAX_PROMPT_ROUNDS,
    CaptchaRequired,
    is_captcha_page,
)
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

# The probe answer that ends the matter: the archive URL itself said the file
# is not there. Every other classification is a condition rather than a
# verdict, and a condition can clear on the next attempt.
NOT_FOUND_REASON = "404 Not Found"

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
    """Return True when ``page_text`` looks like IDX rather than a challenge.

    The interactive checkbox counts as not-clear for the same reason the
    automatic one does -- neither is content -- but only the automatic one is
    safe to wait out. Stopping on the other one is the caller's job.
    """
    if is_captcha_page(page_text):
        return False
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
    # Checked before the 404 wording and before the length test: the checkbox
    # page answers 200, has no 404 text, and is long enough to pass for the
    # homepage. Reading it as "file exists" is what cost six stocks their
    # place in the history -- the 200 belongs to the challenge, not the zip.
    if is_captcha_page(page_text):
        return CAPTCHA_REASON
    if any(marker in lowered for marker in CHALLENGE_MARKERS):
        return "Cloudflare challenge"
    if any(marker in lowered for marker in NOT_FOUND_MARKERS):
        return NOT_FOUND_REASON
    # Not a challenge and not a recognized 404, but too short to be the real
    # IDX homepage: some other interstitial (e.g. a 403 body) rather than a
    # clean success.
    if not is_clear_page(page_text):
        return "Cloudflare/other error"
    # A full IDX homepage means the URL did not 404 and did not challenge --
    # the archive itself served, yet downloads.download still wrote nothing.
    # Reported only: not salvaged, keeping the "no fallback" contract.
    return "200 (file exists; downloads.download failed)"


def is_definitive_reason(reason: str) -> bool:
    """True when a probe answer is final, so no retry could change it.

    Only :data:`NOT_FOUND_REASON` qualifies. The archive URL gave a verdict
    about itself, and asking it again -- with a Cloudflare refresh in between --
    spends the same wall-clock time to reach the same one. The rest are all
    conditions: a challenge clears, a 403 body belongs to a challenge in
    progress, a "file exists" means the URL served and only the transfer
    failed, and an unreadable probe is a gap in the diagnosis rather than a
    finding.

    This is what keeps a stock with no 2025 audited report -- roughly 3% of the
    active list -- from paying for three attempts, two session refreshes and
    two backoffs just to be told the same thing three times.
    """
    return reason == NOT_FOUND_REASON


class IdxSession:
    """One kept-open IDX tab, opened on demand and re-pointed on refusal.

    ``prompt`` is what turns an unsolved CAPTCHA into a stop: when one is
    found, downloads halt and it is asked to show the checkbox to a human.
    Left unset -- tests, and any embedder with no screen -- there is nobody
    to ask, so the run stops without a popup rather than clicking for them.
    """

    def __init__(
        self,
        client: FirefoxBridgeClient,
        prompt: Callable[[str | None], bool] | None = None,
    ) -> None:
        self._client = client
        self._tab_id: Any = None
        self._prompt = prompt

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
            # The checkbox stops the run right here, before "siap" can be
            # declared: a session that is only half-clear is what the last
            # probe mistook for an archive that exists.
            if self._captcha_seen(page_text):
                page_text = self._resolve_captcha()
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

    def _current_url(self) -> str | None:
        """Best-effort URL of the held tab, or ``None`` when unreadable.

        ``navigate`` returns as soon as the request is posted, so the tab URL
        is the only cheap signal that the browser actually left the page it was
        showing; reading text before that just returns the previous document.
        """
        if self._tab_id is None:
            return None
        # Any failure -- a missing method on a stub client, a dead bridge, a
        # malformed payload -- resolves to "unknown", which lets the caller
        # fall back to reading text instead of aborting the probe.
        reader = getattr(self._client, "tabs", None)
        if not callable(reader):
            return None
        try:
            tabs = reader()
        except FirefoxBridgeClientError:
            return None
        items: Any = ()
        if isinstance(tabs, dict):
            items = tabs.get("result") or tabs.get("tabs") or ()
        elif isinstance(tabs, list):
            items = tabs
        wanted = str(self._tab_id)
        for item in items:
            if isinstance(item, dict) and str(item.get("id")) == wanted:
                return str(item.get("url") or "")
        return None

    def _snapshot_text(self) -> str:
        """Flatten the snapshot's accessibility names, or ``""`` if unreadable.

        The snapshot is asked because ``text`` cannot answer this question:
        that endpoint returns ``innerText``, and ``aria-label`` -- where the
        checkbox names itself -- is an attribute, invisible to visible text.
        Any trouble reading it resolves to "no names", which is the safe
        direction: the caller is left with the wording it already has rather
        than a spurious clearance.
        """
        if self._tab_id is None:
            return ""
        reader = getattr(self._client, "snapshot", None)
        if not callable(reader):
            return ""
        try:
            result = reader(self._tab_id, max_elements=200)
        except Exception:  # noqa: BLE001 - best effort, never aborts a run
            return ""
        if not isinstance(result, dict):
            return ""
        elements = result.get("elements")
        if not isinstance(elements, list):
            return ""
        names = [
            str(item.get("name") or "")
            for item in elements
            if isinstance(item, dict)
        ]
        return " ".join(name for name in names if name)

    def _captcha_seen(self, page_text: str) -> bool:
        """True when the interactive checkbox is on the held tab.

        Both readings are asked, for the reason in :meth:`_snapshot_text`.
        A page is believed only when neither shows the marker: `text` misses
        an ``aria-label`` the snapshot catches, and the snapshot is asked
        precisely so that a page with no marker in its visible words is not
        mistaken for content. One source alone is what let this pass for an
        archive that existed.
        """
        if is_captcha_page(page_text):
            return True
        return is_captcha_page(self._snapshot_text())

    def _activate(self) -> None:
        """Bring the held tab forward: the popup says the checkbox is there."""
        if self._tab_id is None:
            return
        try:
            self._client.activate_tab(self._tab_id)
        except Exception:  # noqa: BLE001 - the popup still shows without it
            pass

    def _resolve_captcha(self) -> str:
        """Stop, ask a human to click the checkbox, and hand back the page.

        Returns the re-read page once neither reading shows the marker, so
        the caller carries on as though nothing had happened. Raises
        :class:`CaptchaRequired` when there is no prompt to show, when the
        operator declines, or when it is still there after
        ``MAX_PROMPT_ROUNDS``: the run does not continue against a checkbox
        it may not click for them, and it does not loop either.

        Re-reads rather than trusting what the caller was holding: a page
        that has just been clicked is not the page that was handed over, and
        declaring it solved on stale evidence is the same mistake in
        miniature.
        """
        url = self._current_url()
        target = url or IDX_URL
        self._activate()
        if self._prompt is None:
            raise CaptchaRequired(f"halaman menampilkan CAPTCHA ({target})")
        notice(
            f"CAPTCHA: {CAPTCHA_REASON} di {target}; unduhan dihentikan, "
            "menunggu centang diselesaikan",
        )
        for _ in range(MAX_PROMPT_ROUNDS):
            if not self._prompt(url):
                raise CaptchaRequired(
                    f"CAPTCHA tidak diselesaikan oleh operator ({target})",
                )
            try:
                current = self._text()
            except FirefoxBridgeClientError as error:
                raise CaptchaRequired(
                    f"gagal membaca ulang halaman CAPTCHA "
                    f"({type(error).__name__}: {error})",
                ) from error
            if not self._captcha_seen(current):
                progress("CAPTCHA terselesaikan; run dilanjutkan", tab_id=self._tab_id)
                return current
        raise CaptchaRequired(
            f"CAPTCHA masih ada setelah {MAX_PROMPT_ROUNDS} kali ({target})",
        )

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
            before_url = self._current_url()
            self._client.navigate(self._tab_id, href)
        except FirefoxBridgeClientError as error:
            return f"probe navigasi gagal ({type(error).__name__}: {error})"
        # ``navigate`` only posts the request: the tab still shows the warm-up
        # IDX page until the browser commits the new one. Reading text straight
        # away returned that stale homepage, which classified every failure as
        # "200 (file exists)" even when the archive URL 404s. Wait for the URL
        # to move first; a served zip instead downloads and leaves the URL put,
        # so timing out here is itself the signal that the file was reachable.
        if before_url is not None:
            deadline = time.monotonic() + PROBE_WAIT_SECONDS
            while time.monotonic() < deadline:
                if self._current_url() != before_url:
                    break
                time.sleep(POLL_INTERVAL_SECONDS)
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
        # Asked before classifying, not after: a checkbox page answers 200
        # with no 404 wording and enough text to pass for the homepage, so it
        # would be filed as "200 (file exists)" -- an archive that exists
        # only as far as the challenge is concerned. The tab is left exactly
        # where it is when this raises, which is what makes the box visible
        # to whoever the popup is asking; when it returns, the value is the
        # page as it stands after the click.
        if self._captcha_seen(text):
            text = self._resolve_captcha()
        reason = classify_instance_page(text or "")
        try:
            self._client.navigate(self._tab_id, IDX_URL)
        except FirefoxBridgeClientError:
            pass
        return reason


__all__ = [
    "IDX_URL",
    "IdxSession",
    "NOT_FOUND_REASON",
    "classify_instance_page",
    "is_clear_page",
    "is_definitive_reason",
]
