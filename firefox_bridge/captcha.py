"""Stop the run when IDX shows a CAPTCHA only a human may click.

A failed download is diagnosed by reading the page Firefox rendered.
Cloudflare's interactive challenge answers HTTP 200 with a checkbox labelled
"Verify you are human", and that page carries none of the wording the
automatic challenge uses -- so the reader took it for a healthy page: the
warm-up declared the session ready, and the probe concluded the archive
existed. Six stocks were recorded that way on 2026-10-07, all of them with
the reason ``200 (file exists; downloads.download failed)``.

Clicking the box is a human's job, and guessing at that answer is exactly
what caused the damage -- so detection stops the run and asks instead. It
never clicks for them, which would be bypassing an access control.

Stopping has to survive this codebase's deliberate broad ``except Exception``
handlers: they exist so a transient problem never aborts a run, and there are
several of them between the page and the CLI. :class:`CaptchaRequired`
therefore derives from :class:`BaseException`, carrying the meaning
``SystemExit`` carries -- a stop signal, not a failure to be absorbed.

Two readings of the page are needed, and the difference between them is the
whole bug: the ``text`` endpoint returns ``innerText``, which has visible
words but no attributes, while the widget names itself with ``aria-label``.
The snapshot walks accessibility names and does see that label, so a page is
believed only after both agree it has no checkbox.
"""

from __future__ import annotations

import ctypes

from .progress import notice

# The widget's own label, matched against lowercased text. Only this phrase
# is used: a script tag or a container class can appear on a perfectly
# healthy page, an element that *names itself* "Verify you are human" cannot.
CAPTCHA_MARKERS: tuple[str, ...] = (
    "verify you are human",
)

# What a probe reports instead of "200 (file exists...)": the 200 belongs to
# the challenge page, not to the archive.
CAPTCHA_REASON = "CAPTCHA: Verify you are human"

# Bounded so an unanswered popup cannot pin the run -- this many rounds of
# "still there" ends it rather than asking a window nobody is looking at.
MAX_PROMPT_ROUNDS = 6

# MB_OKCANCEL (operator may decline) | MB_ICONEXCLAMATION (a stop, not an
# inquiry) | MB_SETFOREGROUND | MB_TOPMOST (above Firefox, where it is seen).
POPUP_FLAGS = 0x00000001 | 0x00000030 | 0x00010000 | 0x00040000
POPUP_TITLE = "IDX CAPTCHA - unduhan dihentikan"
POPUP_OK = 1

IDX_FALLBACK_URL = "https://www.idx.co.id/id"


class CaptchaRequired(BaseException):
    """IDX is showing an interactive CAPTCHA and nobody has cleared it.

    Ends the run. :class:`BaseException` rather than :class:`Exception` on
    purpose: every layer in between catches ``Exception`` so one bad stock
    cannot kill a run, and this is the single thing that must not be kept
    alive -- a run still clicking against an unsolved checkbox is worse than
    one that stopped and said why.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def is_captcha_page(page_text: str) -> bool:
    """True when the interactive checkbox is present in ``page_text``."""
    lowered = (page_text or "").lower()
    return any(marker in lowered for marker in CAPTCHA_MARKERS)


def popup_text(page_url: str | None) -> str:
    """The operator's instructions: what stopped, and the three steps out."""
    target = page_url or IDX_FALLBACK_URL
    return (
        "IDX menuntut CAPTCHA. Unduhan DIHENTIKAN.\n"
        "\n"
        "Tab Firefox sudah dibawa ke depan dan sedang menampilkan:\n"
        f"  {target}\n"
        "\n"
        '1. Centang "Verify you are human" di tab itu.\n'
        "2. Klik OK -- unduhan dilanjutkan dari saham terakhir.\n"
        "\n"
        "Klik Batal untuk keluar. Jalankan ulang perintah yang sama untuk\n"
        "melanjutkan; download_history_<tahun>.json jadi dasar resume."
    )


def prompt_to_solve(page_url: str | None) -> bool:
    """Modal popup asking the operator to click the checkbox.

    True means OK -- "clicked", after which the caller re-reads the page and
    only a reading without the marker counts as cleared. False means Batal,
    or that no popup could be shown at all; either way the caller ends the
    run, because there is nothing else it may do with a human's answer.
    """
    try:
        result = ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
            None,
            popup_text(page_url),
            POPUP_TITLE,
            POPUP_FLAGS,
        )
    except Exception:  # noqa: BLE001 - no desktop, not Windows, headless session
        notice(
            "CAPTCHA: popup tidak dapat ditampilkan; run dihentikan "
            f"({page_url or IDX_FALLBACK_URL})"
        )
        return False
    return int(result) == POPUP_OK


__all__ = [
    "CAPTCHA_MARKERS",
    "CAPTCHA_REASON",
    "IDX_FALLBACK_URL",
    "MAX_PROMPT_ROUNDS",
    "CaptchaRequired",
    "is_captcha_page",
    "popup_text",
    "prompt_to_solve",
]
