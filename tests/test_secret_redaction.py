"""The token must never appear in anything a person or a log file can read.

This was checked by hand once, by scanning the git history and the log file. A
check nobody runs is a check that quietly stops being true, so it lives here now.

The token is the only secret the bridge holds. It authenticates the extension,
and anyone holding it can drive the browser on this machine, so it is worth
proving it stays out of the three places output accumulates: the log file, the
history JSON on disk, and the console.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from firefox_bridge.downloader.models import DownloadResult, RunSummary
from firefox_bridge.downloader.orchestrator import download_detected_link
from firefox_bridge.downloader.paths import download_history_path
from firefox_bridge.downloader.reporting import print_run_summary
from firefox_bridge.logging_config import configure_logging, reset_logging
from firefox_bridge.progress import detail, notice, problem, progress

from conftest import make_snapshot

# Distinctive enough that a substring match cannot be satisfied by accident.
SECRET = "SEKRIT-TOKEN-abc123DEF456ghi789JKL"
HREF = (
    "https://www.idx.co.id/Portals/0/x/Laporan%20Keuangan%20Tahun%202025"
    "/TW1/NCKL/inlineXBRL.zip"
)


@pytest.fixture(autouse=True)
def _log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("FIREFOX_BRIDGE_LOG_DIR", str(log_dir))
    reset_logging()
    yield log_dir / "bridge.log"
    reset_logging()


def _staged_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("instance_1/Navigator.txt", date_time=(2025, 1, 1, 0, 0, 0))
        archive.writestr(info, b"payload")
    return buffer.getvalue()


class Client:
    """Client that stages a real archive, so the whole path really runs."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
        return make_snapshot([{"ref": "e1", "href": HREF}])

    def download(self, *, ref: str, filename: str, **_kwargs: Any) -> dict[str, Any]:
        staged = self.root / filename
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(_staged_zip())
        return {"downloaded": True, "ref": ref, "filename": str(staged)}


def test_a_full_download_writes_no_token_to_log_history_or_console(
    _log_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End to end, which is the only form of this claim worth making.

    A real download runs with the token present in the environment, then the log
    file, the history JSON, and both console streams are searched. Everything the
    run produced is fair game for whoever reads it later.
    """
    monkeypatch.setenv("FIREFOX_BRIDGE_TOKEN", SECRET)
    configure_logging()

    download_detected_link(  # type: ignore[arg-type]
        Client(tmp_path), "1", "NCKL", 2025, {"ref": "e1", "href": HREF}, tmp_path
    )
    print_run_summary(
        RunSummary(
            results=[DownloadResult(stock="NCKL", href=HREF, filename="NCKL_T1.zip")]
        )
    )

    assert _log_dir.is_file(), "the run produced no log file to inspect"
    surfaces = {
        "logs/bridge.log": _log_dir.read_text(encoding="utf-8"),
        "download_history.json": download_history_path(tmp_path).read_text(encoding="utf-8"),
        "stdout": capsys.readouterr().out,
        "stderr": capsys.readouterr().err,
    }
    for name, text in surfaces.items():
        assert SECRET not in text, f"the token leaked into {name}"


def test_the_emitters_are_usable_for_anything_except_the_token(
    _log_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """All four emitters reach the log, and none of them invent a secret.

    A field may be *named* `token` without anything leaking: the bridge never
    hands the real token to the logger, so the name is just a name.
    """
    progress("progress line", stock="NCKL", year=2025, quarter=1)
    notice("notice line", stock="NCKL")
    problem("problem line", stock="NCKL")
    detail("detail line", token="a-value-not-the-secret")

    lines = _log_dir.read_text(encoding="utf-8").splitlines()
    levels = [json.loads(line)["level"] for line in lines]
    assert levels == ["INFO", "WARNING", "WARNING", "INFO"]

    records = [json.loads(line) for line in lines]
    assert records[3]["token"] == "a-value-not-the-secret"
    assert SECRET not in _log_dir.read_text(encoding="utf-8")


def test_history_entries_describe_files_and_carry_no_secret_shaped_field(
    tmp_path: Path,
) -> None:
    """The history file is read by other tools, so its shape is part of the contract.

    It says which URL produced which file, with what hash and size. Nothing about
    how the download was authorized belongs in it.
    """
    download_detected_link(  # type: ignore[arg-type]
        Client(tmp_path), "1", "NCKL", 2025, {"ref": "e1", "href": HREF}, tmp_path
    )
    text = download_history_path(tmp_path).read_text(encoding="utf-8").lower()

    for forbidden in ("token", "authorization", "bearer", "password", "secret"):
        assert forbidden not in text, f"history contains a {forbidden!r} field"
    assert "sha256" in text


def test_startup_logging_does_not_echo_the_token(
    _log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIREFOX_BRIDGE_TOKEN", SECRET)
    configure_logging()

    import logging

    from firefox_bridge.logging_config import LOGGER_NAME

    logging.getLogger(LOGGER_NAME).info("bridge started")

    assert SECRET not in _log_dir.read_text(encoding="utf-8")


def test_no_emitter_is_ever_given_the_token() -> None:
    """A static check, so the guarantee does not depend on a test remembering to run.

    Any call that hands the token to a logger or a progress emitter is a bug,
    because both end up in `logs/bridge.log`, which is a file that outlives the
    process and gets read by people.
    """
    package = Path(__file__).resolve().parent.parent / "firefox_bridge"
    emitters = ("logger.", "progress(", "problem(", "notice(", "detail(")
    offenders: list[str] = []
    for source in package.rglob("*.py"):
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
            if ".token" not in line:
                continue
            if any(call in line for call in emitters):
                offenders.append(f"{source.name}:{number} {line.strip()}")
    assert not offenders, "token handed to an output emitter:\n" + "\n".join(offenders)


def test_the_token_subcommand_prints_without_writing_to_the_log(
    _log_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Printing the token on request is a feature; writing it to the log is a leak.

    `firefox_bridge.server token` exists so the user can read the token. The line
    it prints is deliberate. What matters is that the value never reaches
    `logs/bridge.log`, which is a file that outlives the process.

    The log file is usually already present here, because importing the module
    calls `get_logger()` at import time. That import side effect is noted
    elsewhere; it is not what this test is about.
    """
    from firefox_bridge import server
    from firefox_bridge.config import Settings

    monkeypatch.setattr(
        server, "get_settings", lambda: Settings(token=SECRET, url="http://127.0.0.1:8765")
    )

    server.main(["token"])

    assert capsys.readouterr().out.strip() == SECRET
    if _log_dir.exists():
        assert SECRET not in _log_dir.read_text(encoding="utf-8")
