"""A run's options: their defaults, their validation, and the file that may supply them.

Before this module a downloader run could only be described by typing flags. A
scheduled task therefore had to carry the whole command line, and the flags it
most wanted to vary -- which stocks, which year -- were the same every time.

Precedence, highest first:

1. the command line
2. the config file
3. the built-in default

The environment is deliberately absent from that list. ``download_dir`` already
has an environment contract in ``downloader.paths``, and a second one here would
give the same variable two precedences depending on which code path read it.

Two decisions worth stating because they look like omissions:

``history`` is not configurable. It rewrites or rewrites-over the history JSON,
and a config file that did it silently on every unattended run would be a
nasty surprise. It is a one-off repair command, typed when you mean it.

``all_quarters`` is not configurable either. It is a legacy alias for
``all_detected``, and letting a file set both would put the run in a state with
two names for one decision.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .pacing import MINIMUM_STEP_DELAY_SECONDS, parse_delay

CONFIG_FILENAME = "firefox-bridge.toml"
CONFIG_ENV = "FIREFOX_BRIDGE_CONFIG"
SUPPORTED_SUFFIXES = (".toml", ".json")

DEFAULT_YEAR = 2025
DEFAULT_QUARTER = 4
DEFAULT_DELAY_SECONDS = 3.0

# A sanity bound, not a claim about what IDX publishes. Without one a typo like
# `year = 20255` is accepted and the run simply downloads nothing, which looks
# exactly like "already downloaded".
MIN_YEAR = 1990
MAX_YEAR = 2100


class ConfigError(ValueError):
    """A config file that cannot be used as written.

    Raised rather than warned about: a run that proceeded with half the config
    honoured would be harder to debug than one that refused to start.
    """


@dataclass(frozen=True, slots=True)
class Field:
    """One settable option and the shape its value must have."""

    dest: str
    kind: str


FIELDS: dict[str, Field] = {
    field.dest: field
    for field in (
        Field("stocks", "stocks"),
        Field("stocks_file", "text"),
        Field("year", "year"),
        Field("quarter", "quarter"),
        Field("all_detected", "bool"),
        Field("dry_run", "bool"),
        Field("delay", "delay"),
        Field("delay_max", "delay"),
        Field("session", "text"),
        Field("download_dir", "text"),
        Field("report", "text"),
    )
}

# What a run uses when nothing supplies it. Kept here rather than as argparse
# defaults so there is one list to read, and a defaulted flag is still visibly
# "unset" to the precedence logic below.
DEFAULTS: dict[str, Any] = {
    "year": DEFAULT_YEAR,
    "quarter": DEFAULT_QUARTER,
    "all_detected": False,
    "dry_run": False,
    "delay": DEFAULT_DELAY_SECONDS,
}

#: Options the command line may leave unset for the config file to fill in.
CONFIGURABLE = frozenset(FIELDS)


# --- validation, shared by the file and the command line --------------------


def parse_year(value: object) -> int:
    """Return a plausible reporting year, or raise ``ValueError``."""
    if isinstance(value, bool):
        raise ValueError("year harus berupa angka")
    try:
        year = int(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError("year harus berupa angka") from error
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise ValueError(f"year harus antara {MIN_YEAR} dan {MAX_YEAR}")
    return year


def parse_quarter(value: object) -> int:
    """Return a quarter in 1..4, or raise ``ValueError``."""
    if isinstance(value, bool):
        raise ValueError("quarter harus berupa angka 1-4")
    try:
        quarter = int(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError("quarter harus berupa angka 1-4") from error
    if not 1 <= quarter <= 4:
        raise ValueError("quarter harus antara 1 dan 4")
    return quarter


# --- reading the file ------------------------------------------------------


def discover_config_path(explicit: str | None = None, cwd: Path | None = None) -> Path | None:
    """Return the config file to use, or ``None`` when there is none to use.

    An explicitly named file is never allowed to be missing: the user asked for
    that file, and quietly running without it would give them a run they did not
    ask for. Discovery is best-effort, because running from a folder that happens
    to hold no config is the normal case, not an error.
    """
    if explicit:
        return Path(explicit).expanduser()
    from_env = os.environ.get(CONFIG_ENV)
    if from_env and from_env.strip():
        return Path(from_env).expanduser()
    candidate = (cwd or Path.cwd()) / CONFIG_FILENAME
    return candidate if candidate.is_file() else None


def read_config(path: Path) -> dict[str, Any]:
    """Parse the config file at ``path`` into a mapping.

    The format follows the suffix instead of being sniffed, so a file named
    ``config.json`` is never parsed as TOML on a hopeful basis.
    """
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ConfigError(
            f"format config tidak dikenal: {suffix or '(tanpa ekstensi)'}. "
            f"yang didukung: {', '.join(SUPPORTED_SUFFIXES)}"
        )
    try:
        # `utf-8-sig`, not `utf-8`. On Windows a config file is very likely to
        # have been written by PowerShell, Notepad, or Visual Studio, all of
        # which add a byte-order mark. `tomllib` rejects a BOM outright, and the
        # error it gives -- "Invalid statement (at line 1, column 1)" -- points
        # at the first character of a line that is perfectly valid, which sends
        # the reader looking for a syntax error that is not there. `utf-8-sig`
        # strips the mark when there is one and behaves as plain UTF-8 when
        # there is not.
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ConfigError(f"{path}: tidak bisa dibaca: {error}") from error
    try:
        data = json.loads(text) if suffix == ".json" else tomllib.loads(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
        # The path belongs in the message: "invalid value at line 1" is a
        # complaint about a file the reader may not even be holding.
        raise ConfigError(f"{path}: tidak valid ({suffix}): {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(
            f"{path}: config harus berisi tabel key-value di level atas, "
            f"bukan {type(data).__name__}"
        )
    return data


def _coerce(kind: str, value: Any) -> Any:
    """Return ``value`` in the shape ``kind`` requires, or raise ``ValueError``."""
    if kind == "bool":
        # Strict, and stricter than the others on purpose: TOML has a real
        # boolean, so a quoted "false" is a mistake -- and treating that string
        # as true would silently invert a dry run into a real download.
        if not isinstance(value, bool):
            raise ValueError("harus true atau false (tanpa tanda kutip)")
        return value
    if kind == "delay":
        return parse_delay(value)
    if kind == "year":
        return parse_year(value)
    if kind == "quarter":
        return parse_quarter(value)
    if kind == "stocks":
        if isinstance(value, str):
            return value
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            items = list(value)
            if not all(isinstance(item, str) for item in items):
                raise ValueError("daftar harus berisi teks saja")
            return ",".join(item.strip() for item in items if item.strip())
        raise ValueError("harus teks 'NCKL,BBCA' atau daftar teks")
    if kind == "text":
        if not isinstance(value, str):
            raise ValueError(f"harus teks, bukan {type(value).__name__}")
        if not value.strip():
            raise ValueError("tidak boleh kosong")
        return value
    # Unreachable from outside: FIELDS is built here from literals, so a new kind
    # arrives together with its branch. Asserted rather than defaulted so a kind
    # added to FIELDS without a rule fails loudly instead of passing through.
    raise AssertionError(f"jenis config tidak dikenal: {kind}")  # pragma: no cover


def resolve_config(data: Mapping[str, Any], path: Path) -> dict[str, Any]:
    """Validate every key and return the values keyed by argparse destination.

    Every message names ``path``, because "invalid year" without saying which
    file was invalid is a message about a file the reader may not be looking at.

    An unknown key is an error that lists the keys that were available. Silently
    ignoring `yeer = 2024` would leave a run using the default year while the
    file on disk confidently says otherwise.
    """
    unknown = sorted(set(data) - set(FIELDS))
    if unknown:
        raise ConfigError(
            f"{path}: key tidak dikenal: {', '.join(unknown)}\n"
            f"    yang tersedia: {', '.join(sorted(FIELDS))}"
        )
    resolved: dict[str, Any] = {}
    for key, raw in data.items():
        field = FIELDS[key]
        try:
            resolved[field.dest] = _coerce(field.kind, raw)
        except ValueError as error:
            raise ConfigError(f"{path}: {key} = {raw!r} tidak valid: {error}") from error
    return resolved


def load_config(explicit: str | None = None, cwd: Path | None = None) -> tuple[
    dict[str, Any], Path | None
]:
    """Read, validate and return the config for this run, with its path if any."""
    path = discover_config_path(explicit, cwd)
    if path is None:
        return {}, None
    if not path.is_file():
        # Only reachable for an explicit path or the env var; discovery already
        # checked that its candidate exists.
        raise ConfigError(f"config tidak ditemukan: {path}")
    return resolve_config(read_config(path), path), path


# --- applying it -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Resolution:
    """The value of every option, and where each one came from.

    Recorded so a run report can answer "which year did this use, and why"
    without anybody having to reconstruct the precedence by hand.
    """

    values: dict[str, Any]
    path: Path | None
    from_file: tuple[str, ...]
    from_cli: tuple[str, ...]
    from_default: tuple[str, ...]


def resolve(namespace: Any, explicit: str | None = None, cwd: Path | None = None) -> Resolution:
    """Fill the unset options on ``namespace`` and describe where each came from.

    ``namespace`` is an ``argparse.Namespace`` whose configurable options are
    all ``None`` when not given. That is what makes "the user typed it" and
    "the default is 2025" two different things.
    """
    from_file_values, path = load_config(explicit, cwd)
    values: dict[str, Any] = {}
    from_cli: list[str] = []
    from_file: list[str] = []
    from_default: list[str] = []

    for name in CONFIGURABLE:
        given = getattr(namespace, name, None)
        if given is not None:
            values[name] = given
            from_cli.append(name)
            continue
        if name in from_file_values:
            values[name] = from_file_values[name]
            from_file.append(name)
            continue
        values[name] = DEFAULTS.get(name)
        from_default.append(name)

    for name, value in values.items():
        setattr(namespace, name, value)
    # The two alias flags are turned back into real booleans. They are not
    # settings, so the file cannot supply them, and leaving them as the `None`
    # that means "not given" would hand every later reader a third state to
    # remember alongside True and False.
    namespace.all_quarters = bool(namespace.all_quarters)
    return Resolution(
        values=values,
        path=path,
        from_file=tuple(sorted(from_file)),
        from_cli=tuple(sorted(from_cli)),
        from_default=tuple(sorted(from_default)),
    )


def describe(resolution: Resolution) -> str:
    """Return one line saying which file was used and what it supplied."""
    if resolution.path is None:
        return "config: tidak ada file, semua opsi dari argumen atau default"
    keys = ", ".join(resolution.from_file) if resolution.from_file else "tidak ada kunci"
    return f"config: {resolution.path} ({keys})"


__all__ = [
    "CONFIG_ENV",
    "CONFIG_FILENAME",
    "CONFIGURABLE",
    "DEFAULTS",
    "DEFAULT_DELAY_SECONDS",
    "DEFAULT_QUARTER",
    "DEFAULT_YEAR",
    "ConfigError",
    "Field",
    "MAX_YEAR",
    "MIN_YEAR",
    "MINIMUM_STEP_DELAY_SECONDS",
    "Resolution",
    "describe",
    "discover_config_path",
    "load_config",
    "parse_quarter",
    "parse_year",
    "read_config",
    "resolve",
    "resolve_config",
]
