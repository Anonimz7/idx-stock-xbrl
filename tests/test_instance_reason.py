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
    is_definitive_reason,
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


def test_hanya_404_dianggap_jawaban_final() -> None:
    """Satu-satunya alasan yang boleh menghentikan retry lebih awal.

    Alasan lain bukan vonis melainkan kondisi, dan kondisi bisa berubah di
    percobaan berikutnya -- itulah gunanya retry. 404 adalah keputusan URL itu
    sendiri tentang dirinya, jadi mengulang pertanyaan hanya membeli jawaban
    yang sama dengan harga yang sama.
    """
    assert is_definitive_reason("404 Not Found")
    assert not is_definitive_reason("200 (file exists; downloads.download failed)")
    assert not is_definitive_reason("Cloudflare challenge")
    assert not is_definitive_reason("Cloudflare/other error")
    assert not is_definitive_reason("probe navigasi gagal (FirefoxBridgeClientError: x)")
    assert not is_definitive_reason("tidak ada tab warm-up untuk probe")
    assert not is_definitive_reason("")


def test_klasifikator_dan_status_final_tidak_berbeda_pendapat() -> None:
    """Ejaan 404 apa pun dianggap final; tidak satu pun hasil lain yang begitu.

    Kalau dua fungsi ini pernah berbeda pendapat, retry berhenti pada alasan
    yang salah -- atau justru tidak berhenti sama sekali pada yang benar.
    """
    assert is_definitive_reason(classify_instance_page("404 Not Found"))
    assert is_definitive_reason(classify_instance_page("Halaman tidak ditemukan"))
    assert is_definitive_reason(classify_instance_page("File tidak tersedia"))
    for page in (
        "Just a moment, please wait",
        "access denied",
        "",
        "PT Bursa Efek Indonesia " + "x" * 200,
    ):
        assert not is_definitive_reason(classify_instance_page(page)), page


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


class _PendingNavClient:
    """Klien yang meniru browser: navigasi baru butuh beberapa siklus untuk commit.

    Sampai URL tab berpindah, ``text()`` masih mengembalikan dokumen lama
    (homepage IDX) -- persis kondisi yang membuat probe salah melaporkan
    ``200 (file exists)`` untuk URL yang sebenarnya 404.
    """

    def __init__(
        self,
        homepage: str,
        final_text: str,
        pending_calls: int,
        final_url: str = "https://www.idx.co.id/Portals/0/x/Audit/ARKA/instance.zip",
    ) -> None:
        self._homepage = homepage
        self._final_text = final_text
        self._pending = pending_calls
        self._final_url = final_url
        self._old_url = IDX_URL
        self.tab_calls = 0
        self.navigate_calls: list[tuple[Any, str]] = []

    def _committed(self) -> bool:
        return self.tab_calls > self._pending

    def tabs(self) -> Any:
        self.tab_calls += 1
        url = self._final_url if self._committed() else self._old_url
        return {"result": [{"id": "tab-1", "url": url}]}

    def navigate(self, tab_id: Any, url: str) -> Any:
        self.navigate_calls.append((tab_id, url))
        return {"id": tab_id}

    def text(self, tab_id: Any, max_chars: int | None = None) -> Any:
        return {"text": self._final_text if self._committed() else self._homepage}


def test_probe_waits_for_navigation_before_reading_stale_homepage() -> None:
    """Regresi: teks halaman lama tidak boleh terbaca sebelum navigasi commit.

    ``navigate()`` hanya mem-post request, jadi tepat setelah dipanggil tab
    masih menampilkan homepage IDX. Dulu teks itulah yang langsung dibaca,
    sehingga URL yang benar-benar 404 ikut terklasifikasi "200 (file exists)".
    """
    homepage = "PT Bursa Efek Indonesia - portal pasar modal " + "x" * 200
    client = _PendingNavClient(homepage, "404 - Not Found", pending_calls=3)
    session = IdxSession(client)
    session._tab_id = "tab-1"

    reason = session.probe_archive_reason("ARKA", 2025)

    # Navigasi harus sempat commit dulu, baru teks dibaca.
    assert client.tab_calls > 3
    assert reason == "404 Not Found"
    # Dan sesi tetap dipulihkan ke homepage.
    assert client.navigate_calls[-1][1] == IDX_URL


def test_probe_still_reports_200_when_navigation_never_commits() -> None:
    """URL yang tak pernah berpindah = zip terunduh dan tab diam di homepage.

    Di sana "200 (file exists; ...)" memang artinya benar: file terjangkau,
    hanya ``downloads.download`` yang tidak menulis apa-apa.
    """
    homepage = "PT Bursa Efek Indonesia - portal pasar modal " + "x" * 200
    client = _PendingNavClient(homepage, homepage, pending_calls=10**9)
    session = IdxSession(client)
    session._tab_id = "tab-1"

    reason = session.probe_archive_reason("ARKA", 2025)

    assert reason == "200 (file exists; downloads.download failed)"
    assert client.navigate_calls[-1][1] == IDX_URL
