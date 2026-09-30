"""Observable pacing rules for the IDX browser workflow.

Every step must announce itself and pause at least one second before acting.
The delay is intentionally not configurable below one second because the IDX
front end mis-handles an unnaturally fast click sequence and renders its
dropdown options asynchronously.
"""

from __future__ import annotations

import argparse
import random
import time

from .progress import progress

MINIMUM_STEP_DELAY_SECONDS = 1.0
STEP_DELAY_SECONDS = MINIMUM_STEP_DELAY_SECONDS


def wait_before_step(message: str, **fields: object) -> None:
    """Announce the next step, then let the page settle before acting."""
    progress(f"WAIT: {message} ({STEP_DELAY_SECONDS:.0f} detik)", step=message, **fields)
    time.sleep(STEP_DELAY_SECONDS)


def parse_delay(value: object) -> float:
    """Return a usable delay in seconds, or raise ``ValueError``.

    Shared by the ``--delay`` argument and the config file, on purpose. The
    one-second minimum exists because the IDX front end mis-handles a fast click
    sequence, so it has to hold no matter where the number came from -- a rule
    enforced on only one of the two paths is a rule that will eventually be
    bypassed through the other one.

    ``str(value)`` rather than a bare ``float(value)`` so a boolean is rejected
    by the same numeric parse rather than quietly becoming 1.0.
    """
    try:
        seconds = float(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError("delay harus berupa angka") from error
    if seconds < MINIMUM_STEP_DELAY_SECONDS:
        raise ValueError(f"delay minimal {MINIMUM_STEP_DELAY_SECONDS:.0f} detik")
    return seconds


def minimum_one_second(value: str) -> float:
    """argparse type that rejects any delay below the safe minimum."""
    try:
        return parse_delay(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def parse_minutes(value: object) -> float:
    """Return a usable duration in minutes (>= 0), or raise ``ValueError``.

    Shared by the ``--run-minutes``/``--rest-minutes`` arguments and the config
    file, on purpose: a rule enforced on only one of the two paths is a rule
    that will eventually be bypassed through the other one.
    """
    try:
        minutes = float(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"durasi menit tidak valid: {value!r}") from None
    if minutes != minutes or minutes == float("inf"):  # NaN / inf
        raise ValueError(f"durasi menit tidak valid: {value!r}")
    if minutes < 0:
        raise ValueError("durasi menit tidak boleh negatif")
    return minutes


def nonnegative_minutes(value: str) -> float:
    """argparse type for --run-minutes/--rest-minutes (0 = nonaktif)."""
    try:
        return parse_minutes(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def resolve_stock_delay(delay_min: float, delay_max: float | None = None) -> float:
    """Return the actual pause for one inter-stock delay.

    Fixed at ``delay_min`` when ``delay_max`` is None; otherwise uniform random
    in [delay_min, delay_max]. A ``delay_max`` below ``delay_min`` is clamped so
    the range can never invert.
    """
    if delay_max is None:
        return delay_min
    return random.uniform(delay_min, max(delay_min, delay_max))


def sleep_between_stocks(delay_min: float, delay_max: float | None = None) -> None:
    """Pause between stocks, honoring the randomized range when configured."""
    time.sleep(resolve_stock_delay(delay_min, delay_max))
