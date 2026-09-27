from __future__ import annotations

from pathlib import Path

from firefox_bridge.config import Settings


def test_environment_token_takes_precedence(tmp_path: Path, monkeypatch) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("file-token", encoding="utf-8")
    monkeypatch.setenv("FIREFOX_BRIDGE_TOKEN", "environment-token")
    monkeypatch.setenv("FIREFOX_BRIDGE_TOKEN_FILE", str(token_file))

    settings = Settings.from_env()

    assert settings.token == "environment-token"
    assert token_file.read_text(encoding="utf-8") == "file-token"


def test_missing_token_file_is_generated(tmp_path: Path, monkeypatch) -> None:
    token_file = tmp_path / "nested" / "token"
    monkeypatch.delenv("FIREFOX_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("FIREFOX_BRIDGE_TOKEN_FILE", str(token_file))

    settings = Settings.from_env()
    second = Settings.from_env()

    assert settings.token
    assert settings.token == second.token
    assert token_file.read_text(encoding="utf-8").strip() == settings.token


def test_empty_token_file_is_replaced(tmp_path: Path, monkeypatch) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("\n", encoding="utf-8")
    monkeypatch.delenv("FIREFOX_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("FIREFOX_BRIDGE_TOKEN_FILE", str(token_file))

    settings = Settings.from_env()

    assert settings.token
    assert token_file.read_text(encoding="utf-8").strip() == settings.token
