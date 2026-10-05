"""The IDX session tab that lets a Cloudflare wait resolve in-browser.

The behaviour under test is a policy, not an I/O path: "open/navigate a tab,
wait for the challenge to clear, best-effort". The browser is faked so the
policy can be checked without Firefox running.
"""

from __future__ import annotations

from typing import Any

import pytest
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