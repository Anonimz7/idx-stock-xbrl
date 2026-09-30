"""Named download sessions: validation, persistence, and the prompt."""

from __future__ import annotations

from pathlib import Path

import pytest
from firefox_bridge.downloader.session import (
    list_sessions,
    load_session,
    mark_stock_done,
    new_session,
    prompt_session_name,
    save_session,
    validate_session_name,
)


def test_validate_session_name_accepts_safe_names() -> None:
    assert validate_session_name("sesi-2025_utama") == "sesi-2025_utama"
    assert validate_session_name("  abc123  ") == "abc123"


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "a/b", "../x", "sesi baru", "a" * 65, ".hidden", "-dash"],
)
def test_validate_session_name_rejects_unsafe_names(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_session_name(bad)


def test_session_roundtrip_marks_stocks_in_order(tmp_path: Path) -> None:
    session = new_session("sesi-uji", 2025)
    mark_stock_done(session, "aali")
    mark_stock_done(session, "bbca")
    save_session(tmp_path, session)

    reloaded = load_session(tmp_path, "sesi-uji")
    assert reloaded is not None
    assert reloaded["stocks_done"] == ["AALI", "BBCA"]
    assert reloaded["last_stock"] == "BBCA"
    assert reloaded["year"] == 2025


def test_load_session_returns_none_for_a_new_name(tmp_path: Path) -> None:
    assert load_session(tmp_path, "belum-ada") is None


def test_mark_stock_done_never_duplicates(tmp_path: Path) -> None:
    session = new_session("sesi-uji", 2025)
    mark_stock_done(session, "AALI")
    mark_stock_done(session, "aali")
    assert session["stocks_done"] == ["AALI"]


def test_list_sessions_summarizes_known_sessions(tmp_path: Path) -> None:
    first = new_session("pertama", 2025)
    mark_stock_done(first, "AALI")
    save_session(tmp_path, first)
    save_session(tmp_path, new_session("kedua", 2024))

    summaries = {item["name"]: item for item in list_sessions(tmp_path)}
    assert summaries["pertama"]["done"] == 1
    assert summaries["pertama"]["last_stock"] == "AALI"
    assert summaries["kedua"]["done"] == 0


def test_prompt_accepts_existing_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    save_session(tmp_path, new_session("lama", 2025))
    monkeypatch.setattr("builtins.input", lambda _prompt: "lama")

    assert prompt_session_name(tmp_path) == "lama"
    assert "lama" in capsys.readouterr().out


def test_prompt_reprompts_on_invalid_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    answers = iter(["tidak valid!", "baru-ok"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert prompt_session_name(tmp_path) == "baru-ok"
