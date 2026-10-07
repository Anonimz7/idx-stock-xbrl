"""The IDX session tab that lets a Cloudflare wait resolve in-browser.

The behaviour under test is a policy, not an I/O path: "open/navigate a tab,
wait for the challenge to clear, best-effort". The browser is faked so the
policy can be checked without Firefox running.
"""

from __future__ import annotations

from typing import Any

import pytest
from firefox_bridge.captcha import MAX_PROMPT_ROUNDS, CaptchaRequired
from firefox_bridge.client import FirefoxBridgeClientError
from firefox_bridge.instance.session import (
    IDX_URL,
    IdxSession,
    is_clear_page,
)


class _FakeClient:
    """Records calls and lets a test drive what ``text()/navigate()`` return."""

    def __init__(self, text_sequence: list[str] | str = "") -> None:
        self.texts = list(text_sequence) if isinstance(text_sequence, list) else [text_sequence]
        self.calls: list[tuple[str, Any]] = []

    def _pop(self) -> str:
        if not self.texts:
            return ""
        return self.texts.pop(0)

    def open_tab(self, url: str, active: bool = False) -> Any:
        self.calls.append(("open_tab", url))
        return {"id": "tab-1", "url": url}

    def navigate(self, tab_id: Any, url: str) -> Any:
        self.calls.append(("navigate", tab_id, url))
        return {"id": tab_id}

    def text(self, tab_id: Any, max_chars: int | None = None) -> Any:
        self.calls.append(("text", tab_id))
        return {"text": self._pop()}

    def close_tab(self, tab_id: Any) -> Any:
        self.calls.append(("close_tab", tab_id))
        return {}


class _BrokenClient:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure
        self.opened = False

    def open_tab(self, url: str, active: bool = False) -> Any:
        self.opened = True
        raise self.failure

    def close_tab(self, tab_id: Any) -> Any:
        raise self.failure


def test_is_clear_page_rejects_a_challenge_page() -> None:
    assert not is_clear_page("Just a moment... checking your browser before continuing")
    assert not is_clear_page("Verifying you are human")


def test_is_clear_page_accepts_real_content() -> None:
    assert is_clear_page("MASUK DAFTAR EN ID DATA PASAR PRODUK & LAYANAN PERUSAHAAN TERCATAT IDX")
    assert is_clear_page("PT Bursa Efek Indonesia - portal pasar modal dan keuangan syariah")
    assert not is_clear_page("")      # empty is not clear
    assert not is_clear_page("   ")   # blank is not clear
    assert not is_clear_page("Just a moment...")  # challenge is never clear


def test_ensure_opens_a_tab_and_waits_for_clear_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("firefox_bridge.instance.session.READY_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr("firefox_bridge.instance.session.POLL_INTERVAL_SECONDS", 0.0)
    client = _FakeClient(["Just a moment...", "still checking", "MASUK DAFTAR EN ID DATA PASAR"])
    session = IdxSession(client)

    session.ensure()

    assert session.tab_id == "tab-1"
    assert client.calls[0][0] == "open_tab"
    assert client.calls[0][1] == IDX_URL


def test_ensure_does_not_raise_when_opening_fails(monkeypatch) -> None:
    monkeypatch.setattr("firefox_bridge.instance.session.READY_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr("firefox_bridge.instance.session.POLL_INTERVAL_SECONDS", 0.0)
    client = _BrokenClient(FirefoxBridgeClientError("bridge down"))
    session = IdxSession(client)

    session.ensure()  # best-effort: must not raise

    assert session.tab_id is None


def test_refresh_navigates_and_only_returns_once_cleared(
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    monkeypatch.setattr("firefox_bridge.instance.session.READY_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr("firefox_bridge.instance.session.POLL_INTERVAL_SECONDS", 0.0)
    # Two challenge reads, then clearance -- enough for the loop to stop early.
    client = _FakeClient([
        "Just a moment...",
        "still checking your browser",
        "PT Bursa Efek Indonesia - portal pasar modal dan keuangan syariah",
    ])
    session = IdxSession(client)
    session._tab_id = "tab-1"

    session.refresh()

    navigates = [c for c in client.calls if c[0] == "navigate"]
    texts = [i for i, c in enumerate(client.calls) if c[0] == "text"]
    assert len(navigates) == 1
    assert navigates[0][2] == IDX_URL
    assert len(texts) == 3  # two challenges, then cleared


def test_close_is_best_effort_even_when_the_bridge_is_gone() -> None:
    session = IdxSession(_BrokenClient(FirefoxBridgeClientError("gone")))
    session._tab_id = "tab-1"

    session.close()  # must not raise

    assert session.tab_id is None


def test_close_is_a_noop_when_no_tab_was_opened() -> None:
    session = IdxSession(_FakeClient())

    session.close()

    assert session.tab_id is None


# --- the interactive checkbox stops the run -------------------------------
#
# The widget answers 200, carries no 404 wording, and is long enough to pass
# for content -- which is why a run that trusted one reading called it an
# archive that existed. Stopping is the whole contract here: nothing may
# continue against a box the program may not click for the operator.

CHECKBOX = '<input type="checkbox" aria-label="Verify you are human">'
CLEARED = "MASUK DAFTAR EN ID DATA PASAR PRODUK & LAYANAN PERUSAHAAN TERCATAT IDX"


class _SnapshotClient(_FakeClient):
    """A browser holding both readings, kept apart on purpose.

    ``text()`` returns ``innerText``, where the widget's label never appears;
    ``snapshot()`` returns the accessibility names, where it does. One source
    alone is exactly what let the checkbox pass for an archive.
    """

    def __init__(self, texts: list[str], names: list[list[str]]) -> None:
        super().__init__(texts)
        self._names = list(names)
        self.snapshot_calls = 0
        self.activated = 0

    def snapshot(self, tab_id: Any, max_elements: int | None = None) -> Any:
        self.snapshot_calls += 1
        batch = self._names.pop(0) if self._names else []
        return {"elements": [{"name": name} for name in batch]}

    def activate_tab(self, tab_id: Any) -> Any:
        self.activated += 1
        return {}


def _accepting(asked: list[str | None]) -> Any:
    """A prompt that says yes and records where it was shown."""

    def _prompt(url: str | None) -> bool:
        asked.append(url)
        return True

    return _prompt


def test_is_clear_page_rejects_the_interactive_checkbox() -> None:
    """Regresi: widget ini lolos kedua cek -- cukup panjang, tanpa penanda.

    Dulu kedua hal itulah yang membuat sesi dilaporkan "siap" dan probe
    menjawab "200 (file exists)" untuk halaman yang bukan arsip sama sekali.
    """
    assert not is_clear_page(CHECKBOX)
    assert not is_clear_page("Performing security verification " + CHECKBOX)


def test_a_label_that_lives_only_in_an_attribute_is_read_from_the_snapshot() -> None:
    """Tanpa snapshot, kata "siap" diucapkan untuk halaman yang belum siap."""
    client = _SnapshotClient(
        texts=["Performing security verification " + "x" * 60, CLEARED],
        names=[["Verify you are human"], []],
    )
    session = IdxSession(client, prompt=_accepting([]))
    session._tab_id = "tab-1"

    session.ensure()

    assert client.snapshot_calls >= 1, "snapshot tidak pernah ditanya"
    assert client.activated == 1, "tab harus dibawa ke depan agar kotak terlihat"
    assert session.tab_id == "tab-1"


def test_a_solved_captcha_lets_the_run_carry_on() -> None:
    """Inti permintaannya: berhenti, tampilkan popup, lalu lanjut."""
    asked: list[str | None] = []
    client = _FakeClient([CHECKBOX, CLEARED])
    session = IdxSession(client, prompt=_accepting(asked))
    session._tab_id = "tab-1"

    session.ensure()  # tidak melempar

    assert len(asked) == 1, "satu centang seharusnya cukup"
    assert session.tab_id == "tab-1"


def test_a_captcha_without_a_prompt_stops_the_run() -> None:
    """Tak ada layar untuk ditanya, maka berhenti -- bukan lanjut diam-diam."""
    client = _FakeClient([CHECKBOX])
    session = IdxSession(client)
    session._tab_id = "tab-1"

    with pytest.raises(CaptchaRequired):
        session.ensure()


def test_an_operator_declining_stops_the_run() -> None:
    asked: list[str | None] = []

    def _decline(url: str | None) -> bool:
        asked.append(url)
        return False

    client = _FakeClient([CHECKBOX])
    session = IdxSession(client, prompt=_decline)
    session._tab_id = "tab-1"

    with pytest.raises(CaptchaRequired) as caught:
        session.ensure()

    assert len(asked) == 1
    assert "CAPTCHA" in str(caught.value)


def test_an_unsolved_captcha_gives_up_after_a_bounded_number_of_rounds() -> None:
    """Popup yang tak terjawab tidak boleh berputar selamanya.

    Terikat, tapi terhitung: berapa kali popup muncul dan berapa kali pula
    kegagalan itu berhenti, keduanya harus sama-sama bisa diprediksi.
    """
    asked: list[str | None] = []
    client = _FakeClient([CHECKBOX] * (MAX_PROMPT_ROUNDS + 1))
    session = IdxSession(client, prompt=_accepting(asked))
    session._tab_id = "tab-1"

    with pytest.raises(CaptchaRequired):
        session.ensure()

    assert len(asked) == MAX_PROMPT_ROUNDS