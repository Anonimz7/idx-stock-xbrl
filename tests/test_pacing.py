"""Pacing rules must stay observable and never drop below one second."""

from __future__ import annotations

import argparse

import pytest
from firefox_bridge import pacing
from firefox_bridge.pacing import (
    minimum_one_second,
    nonnegative_minutes,
    parse_minutes,
    wait_before_step,
)


def test_every_explicit_step_delay_is_at_least_one_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(pacing.time, "sleep", sleeps.append)

    wait_before_step("test")

    assert sleeps == [pacing.STEP_DELAY_SECONDS]
    assert pacing.STEP_DELAY_SECONDS >= 1.0
    assert pacing.MINIMUM_STEP_DELAY_SECONDS >= 1.0


def test_wait_before_step_announces_the_step(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pacing.time, "sleep", lambda _seconds: None)

    wait_before_step("jeda sebelum klik Laporan Keuangan")

    output = capsys.readouterr().out
    assert "WAIT" in output
    assert "Laporan Keuangan" in output


@pytest.mark.parametrize("value", ["0", "0.5", "0.999"])
def test_minimum_one_second_rejects_fast_delays(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        minimum_one_second(value)


def test_minimum_one_second_rejects_non_numeric_delay() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        minimum_one_second("cepat")


def test_minimum_one_second_accepts_valid_delays() -> None:
    assert minimum_one_second("1") == 1.0
    assert minimum_one_second("3.5") == 3.5


def test_parse_minutes_accepts_zero_and_positive() -> None:
    assert parse_minutes("0") == 0.0
    assert parse_minutes("30") == 30.0
    assert parse_minutes("2.5") == 2.5
    assert parse_minutes(45) == 45.0


def test_parse_minutes_rejects_negative_and_non_numeric() -> None:
    for value in ("-1", "abc", "", "nan", "inf"):
        with pytest.raises(ValueError):
            parse_minutes(value)


def test_nonnegative_minutes_argparse_type_rejects_negative() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        nonnegative_minutes("-5")
    assert nonnegative_minutes("30") == 30.0
