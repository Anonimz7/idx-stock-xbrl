"""Observable pacing rules for the IDX browser workflow.

Every step must announce itself and pause at least one second before acting.
The delay is intentionally not configurable below one second because the IDX
front end mis-handles an unnaturally fast click sequence and renders its
dropdown options asynchronously.
"""

from __future__ import annotations

import argparse
import time

from .progress import progress

MINIMUM_STEP_DELAY_SECONDS = 1.0
STEP_DELAY_SECONDS = MINIMUM_STEP_DELAY_SECONDS


def wait_before_step(message: str, **fields: object) -> None:
    """Announce the next step, then let the page settle before acting."""
    progress(f"WAIT: {message} ({STEP_DELAY_SECONDS:.0f} detik)", step=message, **fields)
    time.sleep(STEP_DELAY_SECONDS)


def minimum_one_second(value: str) -> float:
    """argparse type that rejects any delay below the safe minimum."""
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("delay harus berupa angka") from error
    if seconds < MINIMUM_STEP_DELAY_SECONDS:
        raise argparse.ArgumentTypeError("delay minimal 1 detik")
    return seconds
