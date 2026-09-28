"""`--stocks-file` end to end, including the file the user actually has.

The empty HeidiSQL export is the important case: it is a real file that looks
like a schema dump, and the reader has to say *why* it cannot be used rather
than returning an empty list that would quietly download nothing and exit 0.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from firefox_bridge.cli import EXIT_BRIDGE_UNAVAILABLE, EXIT_INVALID_INPUT, EXIT_SUCCESS, run

DUMP = """-- Host: 127.0.0.1
/*!40101 SET NAMES utf8 */;
INSERT INTO `datasaham` (`kode`, `nama`, `delisted`) VALUES
('NCKL','NICKEL INDONESIA',0),
('BBCA','BANK RAKYAT INDONESIA',0),
('BNI','BANK NEGARA INDONESIA',1),
('BLBI','BANK LIQUIDITAS',1);
"""

EMPTY_DUMP = "-- Data exporting was unselected.\n/*!40101 SET NAMES utf8 */;\n"


class FakeClient:
    base_url = "http://127.0.0.1:8765"
    requested: list[str] = []

    def status(self) -> dict[str, Any]:
        return {"connected": True, "extension": {"version": "0.1.8"}}

    def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
        return {"elements": []}


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record which stocks the run asked for, and never touch a real tab."""
    import firefox_bridge.cli as cli

    seen: list[str] = []
    monkeypatch.setattr(cli, "FirefoxBridgeClient", lambda *_a, **_k: FakeClient())
    monkeypatch.setattr("time.sleep", lambda _s: None)

    def fake_all(_client: Any, stock: str, *_args: Any, **_kwargs: Any) -> list[Any]:
        seen.append(stock)
        return []

    monkeypatch.setattr(cli, "download_all_detected", fake_all)
    return seen


def _sql(tmp_path: Path, body: str = DUMP) -> Path:
    path = tmp_path / "datasaham.sql"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_sql_file_supplies_the_stock_codes(_no_browser: list[str], tmp_path: Path) -> None:
    code = run([
        "--stocks-file", str(_sql(tmp_path)), "--year", "2025", "--all-detected",
        "--download-dir", str(tmp_path / "dl"),
    ])

    assert code == EXIT_SUCCESS
    assert _no_browser == ["NCKL", "BBCA"]


def test_delisted_rows_never_reach_the_browser(
    _no_browser: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A delisted code still has a profile page, so without this the batch
    quietly spends a paced workflow on a company that no longer trades."""
    run(["--stocks-file", str(_sql(tmp_path)), "--year", "2025", "--all-detected",
         "--download-dir", str(tmp_path / "dl")])

    assert "BNI" not in _no_browser
    assert "BLBI" not in _no_browser
    assert "2 delisted dilewati" in capsys.readouterr().out


def test_an_empty_export_is_rejected_with_a_reason(
    _no_browser: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Returning an empty list here would download nothing and exit 0, which
    looks like success."""
    code = run(["--stocks-file", str(_sql(tmp_path, EMPTY_DUMP)), "--year", "2025",
                "--all-detected", "--download-dir", str(tmp_path / "dl")])

    assert code == EXIT_INVALID_INPUT
    assert _no_browser == []
    assert "Ekspor harus menyertakan data" in capsys.readouterr().err


def test_a_file_is_merged_with_inline_codes(
    _no_browser: list[str], tmp_path: Path
) -> None:
    run(["--stocks", "TLKM", "--stocks-file", str(_sql(tmp_path)), "--year", "2025",
         "--all-detected", "--download-dir", str(tmp_path / "dl")])

    assert _no_browser == ["TLKM", "NCKL", "BBCA"]


def test_a_csv_file_is_accepted(_no_browser: list[str], tmp_path: Path) -> None:
    path = tmp_path / "saham.csv"
    path.write_text("kode,nama,delisted\nAGRO,AGRO,0\nBNI,BANK NEGARA,1\n", encoding="utf-8")

    run(["--stocks-file", str(path), "--year", "2025", "--all-detected",
         "--download-dir", str(tmp_path / "dl")])

    assert _no_browser == ["AGRO"]


def test_a_missing_file_is_rejected_not_ignored(
    _no_browser: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(["--stocks-file", str(tmp_path / "tidak-ada.sql"), "--year", "2025",
                "--all-detected", "--download-dir", str(tmp_path / "dl")])

    assert code == EXIT_INVALID_INPUT
    assert _no_browser == []
    assert "tidak ditemukan" in capsys.readouterr().err


def test_neither_stocks_nor_file_is_still_rejected(
    _no_browser: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(["--year", "2025"]) == EXIT_INVALID_INPUT
    assert "--stocks-file" in capsys.readouterr().err


def test_the_health_gate_still_runs_before_any_stock(
    _no_browser: list[str], tmp_path: Path
) -> None:
    """A 40-stock batch must not start opening tabs when nothing is connected."""
    import firefox_bridge.cli as cli

    class Disconnected:
        base_url = "http://127.0.0.1:8765"

        def status(self) -> dict[str, Any]:
            return {"connected": False}

    cli.FirefoxBridgeClient = lambda *_a, **_k: Disconnected()  # type: ignore[assignment]

    code = run(["--stocks-file", str(_sql(tmp_path)), "--year", "2025", "--all-detected",
                "--download-dir", str(tmp_path / "dl")])

    assert code == EXIT_BRIDGE_UNAVAILABLE
    assert _no_browser == []
