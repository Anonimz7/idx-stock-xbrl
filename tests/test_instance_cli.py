"""The instance CLI's own contract: arguments in, exit code out.

No browser and no bridge are involved. Everything here runs before a download is
attempted, which is exactly why it deserves its own file: a defect on this path
takes down a run of nearly a thousand tickers, and only after the first stock has
already been processed -- late enough that the time lost is the reason for
running them in one pass.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from firefox_bridge import runconfig
from firefox_bridge.captcha import CaptchaRequired
from firefox_bridge.downloader.errors import DownloadTimeout
from firefox_bridge.downloader.models import (
    STATUS_DOWNLOADED,
    STATUS_SKIPPED,
    DownloadResult,
)
from firefox_bridge.instance import cli as cli_module
from firefox_bridge.instance.cli import (
    EXIT_CAPTCHA,
    EXIT_FAILURES,
    EXIT_SUCCESS,
    _failure_reason,
    _stock_delay,
    build_parser,
    run,
)


def _parse(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(["--stocks", "NCKL", "--year", "2025", *argv])


def test_an_unset_delay_falls_back_to_the_shared_default() -> None:
    """A missing ``--delay`` must not reach ``time.sleep(None)``.

    The page-flow CLI gets this value from ``runconfig.resolve``, which fills
    every unset option from the config file or the defaults. This program takes
    no config file, so it has to read the same default itself -- and the failure
    it avoids is a crash after the first stock, with the rest of the list never
    attempted.
    """
    args = _parse()

    assert args.delay is None
    assert _stock_delay(args) == runconfig.DEFAULT_DELAY_SECONDS


def test_an_explicit_delay_wins_over_the_default() -> None:
    assert _stock_delay(_parse("--delay", "5")) == 5


# --- which diagnosis gets reported ----------------------------------------


class _Prober:
    """A warm-up tab that answers when asked, and counts how often it was."""

    def __init__(self, answer: str = "Cloudflare challenge") -> None:
        self.answer = answer
        self.calls = 0

    def probe_archive_reason(self, stock: str, year: int) -> str:  # noqa: ARG002
        self.calls += 1
        return self.answer


def test_a_reason_the_retry_loop_already_found_is_not_probed_again() -> None:
    """The 404 that ended the retries is the 404 that gets reported.

    Asking twice would buy a second navigation and could disagree with the very
    answer the run stopped on -- the recorded reason and the decision that was
    made would then be two different stories about one failure.
    """
    session = _Prober()
    failure = DownloadTimeout("tidak pernah menulis apa pun")
    failure.archive_reason = "404 Not Found"

    reason = _failure_reason(session, "ADES", 2025, failure)

    assert reason == "404 Not Found"
    assert session.calls == 0, "a diagnosis already in hand was bought twice"


def test_a_failure_nobody_diagnosed_is_probed_here() -> None:
    """No session during the retry, or an error that never reached the probe."""
    session = _Prober(answer="Cloudflare challenge")

    reason = _failure_reason(session, "ADES", 2025, DownloadTimeout("stuck"))

    assert reason == "Cloudflare challenge"
    assert session.calls == 1


def test_a_probe_that_fails_keeps_the_failure_line_readable() -> None:
    """Diagnosis is best effort: it reports what went wrong, and nothing more."""
    session = _Prober()

    def broken(stock: str, year: int) -> str:
        raise RuntimeError("bridge mati")

    session.probe_archive_reason = broken  # type: ignore[method-assign]

    reason = _failure_reason(session, "ADES", 2025, DownloadTimeout("stuck"))

    assert reason.startswith("probe gagal (RuntimeError")


def test_dry_run_creates_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--dry-run`` is the mode that promises to change nothing; it must.

    Pointed at an empty root so the assertion is about this run rather than about
    whatever a developer already has in their download folder.
    """
    root = tmp_path / "instance"
    monkeypatch.setenv("FIREFOX_BRIDGE_INSTANCE_DIR", str(root))
    monkeypatch.delenv("FIREFOX_BRIDGE_DOWNLOAD_DIR", raising=False)

    exit_code = run(["--stocks", "NCKL,BBCA", "--year", "2025", "--dry-run"])

    assert exit_code == 0
    assert not root.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_year_is_required() -> None:
    """Omitted options are caught before the browser is ever touched."""
    with pytest.raises(SystemExit) as caught:
        run(["--stocks", "NCKL"])

    assert caught.value.code == 2


def test_stocks_are_required() -> None:
    with pytest.raises(SystemExit) as caught:
        run(["--year", "2025"])

    assert caught.value.code == 2


def test_a_captcha_stops_the_run_with_its_own_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keluar dengan 4, bukan 1 (unduhan gagal) dan bukan 3 (bridge mati).

    Angka yang berbeda itu adalah kontraknya: "perbaiki setup Anda" dan
    "pergi mencentang kotak" tidak boleh terbaca sama oleh pemanggil, dan
    keduanya tidak boleh terbaca sebagai keberhasilan.
    """
    # Keduanya diarahkan ke tempat sementara. Staging TIDAK ikut
    # FIREFOX_BRIDGE_INSTANCE_DIR -- ia mengikuti download_root() -- jadi
    # tanpa variabel kedua ini scan_staging akan menghapus file parsial di
    # folder unduhan sungguhan hanya karena sebuah tes dijalankan.
    monkeypatch.setenv("FIREFOX_BRIDGE_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.delenv("FIREFOX_BRIDGE_INSTANCE_DIR", raising=False)
    monkeypatch.setattr(cli_module, "ensure_extension_ready", lambda _client: "v-test")

    closed = False

    class _BlockedSession:
        def __init__(self, client: object, prompt: object = None) -> None:
            assert prompt is not None, "run yang asli harus membawa popup CAPTCHA"

        def ensure(self) -> None:
            raise CaptchaRequired(
                "halaman menampilkan CAPTCHA (https://www.idx.co.id/x/instance.zip)"
            )

        def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(cli_module, "IdxSession", _BlockedSession)

    exit_code = run(["--stocks", "NCKL", "--year", "2025"])

    assert exit_code == EXIT_CAPTCHA
    assert not closed, "tab ditutup, jadi kotaknya hilang dari layar operator"


# --- who pays the between-stock pause --------------------------------------


class _QuietSession:
    """A warm-up tab that opens and closes without asking anything of IDX."""

    def __init__(self, client: object, prompt: object = None) -> None:
        assert prompt is not None, "IdxSession asli dipanggil dengan popup CAPTCHA"
        self.client = client

    def ensure(self) -> None:
        return None

    def close(self) -> None:
        return None

    def probe_archive_reason(self, stock: str, year: int) -> str:
        return "404 Not Found"


def _paced_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcomes: dict[str, str]
) -> list[tuple[float, float | None]]:
    """Run the CLI over ``outcomes`` (code -> "skip" | "download" | "fail").

    Returns every pause the loop took. The count, not the duration, is what a
    test can rely on: a timing assertion would be flaky and would not say which
    stock was charged.
    """
    # Both roots are redirected, for the same reason as the CAPTCHA test above:
    # staging follows FIREFOX_BRIDGE_DOWNLOAD_DIR while the program's own root
    # follows FIREFOX_BRIDGE_INSTANCE_DIR, and a test touching only one of them
    # would delete a real partial file on the developer's machine.
    monkeypatch.setenv("FIREFOX_BRIDGE_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FIREFOX_BRIDGE_INSTANCE_DIR", str(tmp_path / "instance"))
    monkeypatch.setattr(cli_module, "ensure_extension_ready", lambda _client: "v-test")
    monkeypatch.setattr(cli_module, "IdxSession", _QuietSession)

    def _fake_download(
        client: object,
        stock: str,
        year: int,
        download_dir: object = None,
        session: object = None,
    ) -> DownloadResult:
        if outcomes[stock] == "fail":
            raise DownloadTimeout("Unduhan tidak pernah menulis apa pun")
        status = STATUS_SKIPPED if outcomes[stock] == "skip" else STATUS_DOWNLOADED
        return DownloadResult(
            stock=stock,
            href=f"https://example.invalid/{stock}.zip",
            filename=f"{stock}_instance_T4_{year}.zip",
            status=status,
            year=year,
            quarter=4,
        )

    pauses: list[tuple[float, float | None]] = []

    def _record_pause(delay_min: float, delay_max: float | None = None) -> None:
        pauses.append((delay_min, delay_max))

    monkeypatch.setattr(cli_module, "download_instance", _fake_download)
    monkeypatch.setattr(cli_module, "sleep_between_stocks", _record_pause)

    exit_code = run(
        [
            "--stocks",
            ",".join(outcomes),
            "--year",
            "2025",
            "--delay",
            "2",
            "--delay-max",
            "5",
        ]
    )
    expected = EXIT_FAILURES if "fail" in outcomes.values() else EXIT_SUCCESS
    assert exit_code == expected, "tes gagal di titik yang tidak sedang diuji"
    return pauses


def test_a_local_skip_takes_no_pause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A skip read a file and returned before any request existed to pace.

    The pause exists so IDX does not see a burst of requests. A stock that sent
    nothing cannot contribute to one, yet it was charged the same 3.5 seconds --
    across ~715 already-downloaded reports that is what turned a resume into a
    45-minute walk in which nothing at all reached IDX.
    """
    pauses = _paced_run(
        monkeypatch, tmp_path, {"NCKL": "skip", "BBRI": "skip", "TLKM": "skip"}
    )

    assert pauses == [], "loop berjalan tanpa jaringan tetap tetap dijeda"


def test_only_the_stock_that_reached_idx_is_paced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Skip-download-skip charges exactly once, at the download.

    A mixed list is what makes this a real assertion. A single-stock test would
    also pass if the flag were merely reset once somewhere, or if the pause
    were taken at the top of the loop instead of after the request it is meant
    to separate.
    """
    pauses = _paced_run(
        monkeypatch,
        tmp_path,
        {"NCKL": "skip", "BBRI": "download", "TLKM": "skip"},
    )

    assert len(pauses) == 1, "unduhan nyata harus tetap diapit jeda"


def test_a_failed_attempt_takes_the_pause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failure followed a request, so it is paced like a success.

    The flag defaults to True for exactly this reason: the branch that raises
    has no result to read a status from, and a pause lost here would be a pause
    lost precisely when the session is least certain of itself.
    """
    pauses = _paced_run(monkeypatch, tmp_path, {"NCKL": "fail"})

    assert len(pauses) == 1, "percobaan yang gagal tetap menyentuh IDX"
