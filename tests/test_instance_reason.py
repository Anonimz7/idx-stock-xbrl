"""Uji klasifikasi alasan kegagalan unduhan instance.zip.

Mencakup klasifikator pure ``classify_instance_page`` dan *probe*
``IdxSession.probe_archive_reason`` yang men-annotasi sebuah
``DownloadTimeout`` jendela-mati dengan alasan (404 vs Cloudflare vs lain).
"""

from __future__ import annotations

from typing import Any

import pytest
from firefox_bridge.client import FirefoxBridgeClientError
from firefox_bridge.instance.session import (
    IDX_URL,
    IdxSession,
    classify_instance_page,
)


class _FakeClient:
    """Mencatat panggilan dan mengembalikan teks halaman yang dijadwalkan."""

    def __init__(self, text_sequence: list[str] | str = "") -> None:
        self.texts = (
            list(text_sequence) if isinstance(text_sequence, list) else [text_sequence]
        )
        self.navigate_calls: list[tuple[Any, str]] = []
        self.text_calls: list[Any] = []

    def _pop(self) -> str:
        if not self.texts:
            return ""
        return self.texts.pop(0)

    def navigate(self, tab_id: Any, url: str) -> Any:
        self.navigate_calls.append((tab_id, url))
        return {"id": tab_id}

    def text(self, tab_id: Any, max_chars: int | None = None) -> Any:
        self.text_calls.append(tab_id)
        return {"text": self._pop()}


class _NavigateErrors:
    """Klien yang selalu gagal saat navigate()."""

    def navigate(self, tab_id: Any, url: str) -> Any:
        raise FirefoxBridgeClientError("bridge down")


@pytest.fixture(autouse=True)
def _fast_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bikin probe tak bersandar waktu nyata."""
    monkeypatch.setattr("firefox_bridge.instance.session.PROBE_WAIT_SECONDS", 0.1)
    monkeypatch.setattr("firefox_bridge.instance.session.POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(
        "firefox_bridge.instance.session.wait_before_step",
        lambda *args, **kwargs: None,
    )


def test_classify_404_markers() -> None:
    assert classify_instance_page("404 Not Found") == "404 Not Found"
    assert classify_instance_page("Halaman tidak ditemukan") == "404 Not Found"
    assert classify_instance_page("The resource cannot be found") == "404 Not Found"
    assert classify_instance_page("File tidak tersedia") == "404 Not Found"


def test_classify_cloudflare_challenge() -> None:
    assert classify_instance_page("Just a moment, please wait") == "Cloudflare challenge"
    assert (
        classify_instance_page("Checking your browser before continuing...")
        == "Cloudflare challenge"
    )


def test_classify_short_non_404_is_other() -> None:
    assert classify_instance_page("access denied") == "Cloudflare/other error"
    assert classify_instance_page("") == "Cloudflare/other error"
    assert classify_instance_page("   ") == "Cloudflare/other error"


def test_classify_full_idx_homepage_means_file_served() -> None:
    # Halaman panjang tanpa marker 404/challenge terbaca sebagai IDX, artinya
    # URL tidak menolak -- archive memang ter-served meski downloads.download
    # tidak menulis apa-apa ke staging.
    longpage = "PT Bursa Efek Indonesia - portal pasar modal" + " " + "x" * 200
    assert (
        classify_instance_page(longpage)
        == "200 (file exists; downloads.download failed)"
    )


def test_probe_returns_no_tab_marker_when_tab_is_none() -> None:
    session = IdxSession(_FakeClient())
    assert session.probe_archive_reason("ARKA", 2025) == "tidak ada tab warm-up untuk probe"


def test_probe_navigate_error_is_reported() -> None:
    session = IdxSession(_NavigateErrors())
    session._tab_id = "tab-1"
    reason = session.probe_archive_reason("ARKA", 2025)
    assert reason.startswith("probe navigasi gagal (FirefoxBridgeClientError")


def test_probe_reads_text_and_restores_tab() -> None:
    client = _FakeClient(["404 Not Found"])
    session = IdxSession(client)
    session._tab_id = "tab-1"

    reason = session.probe_archive_reason("ARKA", 2025)

    assert reason == "404 Not Found"
    # Dialihkan dulu ke URL archive...
    assert client.navigate_calls[0][1].endswith("/Audit/ARKA/instance.zip")
    # ...kemudian kembalikan ke homepage IDX agar sesi tetap hangat.
    assert client.navigate_calls[-1][1] == IDX_URL
    assert client.text_calls  # _text() dipanggil


def test_probe_empty_text_classified_as_other_and_still_restores() -> None:
    client = _FakeClient("")
    session = IdxSession(client)
    session._tab_id = "tab-1"

    reason = session.probe_archive_reason("ARKA", 2025)

    assert reason == "Cloudflare/other error"
    assert client.navigate_calls[-1][1] == IDX_URL
