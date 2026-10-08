"""Orchestration: skip rules, staging, and the full detected-report loop."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from firefox_bridge.downloader import orchestrator
from firefox_bridge.downloader.errors import DownloaderError, StaleReference
from firefox_bridge.downloader.models import DownloadResult
from firefox_bridge.downloader.orchestrator import (
    download_all_detected,
    download_detected_link,
    download_stock,
)
from firefox_bridge.downloader.paths import (
    download_history_path,
    final_report_path,
    staging_relative_filename,
)

from conftest import make_snapshot


def _report_zip() -> bytes:
    """Return a minimal but genuinely readable ZIP archive.

    The orchestrator now rejects a completed download that is not a real
    archive, so a fixture of magic bytes would fail for the right reason while
    measuring the wrong thing. The timestamp is pinned so the bytes -- and
    therefore the SHA-256 written into the history -- are identical on every
    run, which is what lets these tests compare hashes at all.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("instance_1/Navigator.txt", date_time=(2025, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, b"xbrl report payload for a quarter")
    return buffer.getvalue()


CONTENT = _report_zip()
HREF = (
    "https://www.idx.co.id/Laporan%20Keuangan%20Tahun%202025/"
    "TW1/NCKL/inlineXBRL.zip"
)


class RecordingClient:
    """Minimal client that stages a report when a download is requested."""

    def __init__(self, root: Path, links: list[dict[str, Any]]) -> None:
        self.root = root
        self.links = links
        self.downloads: list[str] = []

    def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
        return make_snapshot(self.links)

    def download(
        self,
        *,
        ref: str,
        filename: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.downloads.append(ref)
        staged = self.root / filename
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(CONTENT)
        return {
            "downloaded": True,
            "ref": ref,
            "filename": str(staged),
        }


def test_download_detected_link_moves_file_and_json_skips_next_run(
    no_sleep: None,
    tmp_path: Path,
) -> None:
    link = {"ref": "e1", "href": HREF}
    client = RecordingClient(tmp_path, [link])

    first = download_detected_link(
        client,  # type: ignore[arg-type]
        "15",
        "NCKL",
        2025,
        link,
        tmp_path,
    )
    second = download_detected_link(
        client,  # type: ignore[arg-type]
        "15",
        "NCKL",
        2025,
        link,
        tmp_path,
    )

    final_path = tmp_path / "saham" / "2025" / "NCKL" / "NCKL_inlineXBRL_T1_2025.zip"
    history = json.loads(download_history_path(tmp_path, year=2025).read_text(encoding="utf-8"))
    entry = history["downloads"]["NCKL"]["2025"]["1"]

    assert first.filename == str(final_path)
    assert second.filename == str(final_path)
    assert final_path.read_bytes() == CONTENT
    assert not (tmp_path / staging_relative_filename("NCKL", 2025, 1)).exists()
    assert client.downloads == ["e1"]
    assert entry["url"] == HREF
    assert entry["file"] == "saham/2025/NCKL/NCKL_inlineXBRL_T1_2025.zip"
    assert entry["size"] == len(CONTENT)
    assert entry["sha256"] == hashlib.sha256(CONTENT).hexdigest()
    assert entry["duplicate_of"] is None
    assert entry["integrity_status"] == "verified"


def test_download_detected_link_rejects_an_unknown_period(
    no_sleep: None,
    tmp_path: Path,
) -> None:
    link = {"ref": "e1", "href": "https://idx.test/NCKL/inlineXBRL.zip"}

    with pytest.raises(ValueError):
        download_detected_link(
            None,  # type: ignore[arg-type]
            "15",
            "NCKL",
            2025,
            link,
            tmp_path,
        )


def test_download_detected_link_requires_a_fresh_ref(
    no_sleep: None,
    tmp_path: Path,
) -> None:
    link = {"ref": "e1", "href": HREF}

    class EmptyClient:
        def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
            return make_snapshot([])

    # Asserting the specific type, not `RuntimeError`: the whole point of the
    # error taxonomy is that a caller can tell "the page moved under us" apart
    # from every other failure without parsing a message.
    with pytest.raises(StaleReference, match="tidak tersedia"):
        download_detected_link(
            EmptyClient(),  # type: ignore[arg-type]
            "15",
            "NCKL",
            2025,
            link,
            tmp_path,
        )


def test_download_detected_link_rejects_a_failed_download(
    no_sleep: None,
    tmp_path: Path,
) -> None:
    link = {"ref": "e1", "href": HREF}

    class FailingClient:
        def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
            return make_snapshot([link])

        def download(self, **_kwargs: Any) -> dict[str, Any]:
            return {"downloaded": False, "error": "blocked"}

    with pytest.raises(DownloaderError, match="gagal dimulai"):
        download_detected_link(
            FailingClient(),  # type: ignore[arg-type]
            "15",
            "NCKL",
            2025,
            link,
            tmp_path,
        )


def test_download_detected_link_backfills_history_for_an_untracked_file(
    no_sleep: None,
    tmp_path: Path,
) -> None:
    link = {"ref": "e1", "href": HREF}
    existing = final_report_path("NCKL", 2025, 1, tmp_path)
    existing.write_bytes(CONTENT)
    client = RecordingClient(tmp_path, [link])

    download_detected_link(
        client,  # type: ignore[arg-type]
        "15",
        "NCKL",
        2025,
        link,
        tmp_path,
    )

    history = json.loads(download_history_path(tmp_path, year=2025).read_text(encoding="utf-8"))

    assert history["downloads"]["NCKL"]["2025"]["1"]["url"] == HREF
    assert client.downloads == []


def test_download_detected_link_redownloads_a_corrupted_file(
    no_sleep: None,
    tmp_path: Path,
) -> None:
    link = {"ref": "e1", "href": HREF}
    client = RecordingClient(tmp_path, [link])
    download_detected_link(
        client,  # type: ignore[arg-type]
        "15",
        "NCKL",
        2025,
        link,
        tmp_path,
    )
    final_path = final_report_path("NCKL", 2025, 1, tmp_path)
    # A real archive cut in half, which is what an interrupted download actually
    # leaves behind. A different size and a different hash, so the integrity
    # check is what sends this back for a re-download.
    final_path.write_bytes(CONTENT[: len(CONTENT) // 2])

    download_detected_link(
        client,  # type: ignore[arg-type]
        "15",
        "NCKL",
        2025,
        link,
        tmp_path,
    )

    history = json.loads(download_history_path(tmp_path, year=2025).read_text(encoding="utf-8"))

    assert client.downloads == ["e1", "e1"]
    assert final_path.read_bytes() == CONTENT
    assert (
        history["downloads"]["NCKL"]["2025"]["1"]["sha256"]
        == hashlib.sha256(CONTENT).hexdigest()
    )


def test_download_detected_link_redownloads_a_missing_file(
    no_sleep: None,
    tmp_path: Path,
) -> None:
    link = {"ref": "e1", "href": HREF}
    client = RecordingClient(tmp_path, [link])
    download_detected_link(
        client,  # type: ignore[arg-type]
        "15",
        "NCKL",
        2025,
        link,
        tmp_path,
    )
    final_report_path("NCKL", 2025, 1, tmp_path).unlink()

    download_detected_link(
        client,  # type: ignore[arg-type]
        "15",
        "NCKL",
        2025,
        link,
        tmp_path,
    )

    assert client.downloads == ["e1", "e1"]


def test_download_all_detected_processes_every_link(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    links = [
        {
            "ref": f"e{quarter}",
            "href": f"https://idx.test/{period}/NCKL/inlineXBRL.zip",
        }
        for quarter, period in enumerate(("TW1", "TW2", "TW3", "Audit"), start=1)
    ]
    processed: list[tuple[str, int]] = []

    monkeypatch.setattr(
        orchestrator,
        "prepare_stock_year",
        lambda *_args, **_kwargs: "15",
    )
    monkeypatch.setattr(
        orchestrator,
        "wait_for_detected_links",
        lambda *_args, **_kwargs: (links, make_snapshot([])),
    )
    monkeypatch.setattr(orchestrator, "audit_stock_year_hashes", lambda *_a, **_k: {})

    def record_download(
        _client: Any,
        _tab_id: str,
        stock: str,
        year: int,
        link: dict[str, Any],
        _download_dir: Any,
    ) -> DownloadResult:
        quarter = orchestrator.quarter_from_report_href(link["href"])
        processed.append((stock, quarter or 0))
        return DownloadResult(stock, link["href"], str(tmp_path))

    monkeypatch.setattr(orchestrator, "download_detected_link", record_download)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda _seconds: None)

    results = download_all_detected(
        None,  # type: ignore[arg-type]
        "NCKL",
        2025,
        tmp_path,
    )

    assert len(results) == 4
    assert processed == [
        ("NCKL", 1),
        ("NCKL", 2),
        ("NCKL", 3),
        ("NCKL", 4),
    ]


def test_download_all_detected_downloads_four_reports_once(
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    tmp_path: Path,
) -> None:
    base = "https://www.idx.co.id/Laporan%20Keuangan%20Tahun%202025"
    periods = {1: "TW1", 2: "TW2", 3: "TW3", 4: "Audit"}
    links = [
        {"ref": f"e{quarter}", "href": f"{base}/{period}/NCKL/inlineXBRL.zip"}
        for quarter, period in periods.items()
    ]
    client = RecordingClient(tmp_path, links)

    monkeypatch.setattr(orchestrator, "prepare_stock_year", lambda *_a, **_k: "15")
    monkeypatch.setattr(
        orchestrator,
        "wait_for_detected_links",
        lambda *_a, **_k: (links, make_snapshot(links)),
    )

    first = download_all_detected(
        client,  # type: ignore[arg-type]
        "NCKL",
        2025,
        tmp_path,
    )
    second = download_all_detected(
        client,  # type: ignore[arg-type]
        "NCKL",
        2025,
        tmp_path,
    )

    assert len(first) == 4
    assert len(second) == 4
    assert client.downloads == ["e1", "e2", "e3", "e4"]
    history = json.loads(download_history_path(tmp_path, year=2025).read_text(encoding="utf-8"))
    assert set(history["downloads"]["NCKL"]["2025"]) == {"1", "2", "3", "4"}


def test_download_stock_prepares_year_before_link_search(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class StopBeforeLinkSearch(Exception):
        pass

    class FakeClient:
        def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
            return make_snapshot([])

    monkeypatch.setattr(
        orchestrator,
        "prepare_stock_year",
        lambda *_args, **_kwargs: events.append("prepare") or "15",
    )

    def stop_before_link_search(*_args: Any, **_kwargs: Any) -> None:
        events.append("link")
        raise StopBeforeLinkSearch

    monkeypatch.setattr(
        orchestrator,
        "find_inline_xbrl_link",
        stop_before_link_search,
    )

    with pytest.raises(StopBeforeLinkSearch):
        download_stock(
            FakeClient(),  # type: ignore[arg-type]
            "NCKL",
            2025,
            2,
            download_dir=tmp_path,
        )

    assert events == ["prepare", "link"]


def test_download_stock_retries_the_link_lookup_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    link = {"ref": "e1", "href": HREF}
    snapshots = iter([make_snapshot([]), make_snapshot([link])])

    class FakeClient:
        def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
            return next(snapshots)

    monkeypatch.setattr(orchestrator, "prepare_stock_year", lambda *_a, **_k: "15")
    monkeypatch.setattr(orchestrator.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        orchestrator,
        "download_detected_link",
        lambda *_a, **_k: DownloadResult("NCKL", HREF, str(tmp_path)),
    )
    monkeypatch.setattr(orchestrator, "audit_stock_year_hashes", lambda *_a, **_k: {})

    result = download_stock(
        FakeClient(),  # type: ignore[arg-type]
        "NCKL",
        2025,
        1,
        download_dir=tmp_path,
    )

    assert result.href == HREF
