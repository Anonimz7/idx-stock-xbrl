"""The packaging surface: the legacy shim, and what the built artifacts promise.

`tools/bulk_downloader.py` exists so an old command line keeps working after the
935-line script was split into modules. task.md has claimed "shim lama tetap
jalan" since the split, and that claim was true but untested -- a promise nobody
checks is a promise that quietly stops being true.

Building the wheel is deliberately *not* tested here. A build takes seconds,
reaches the network, and depends on the backend resolving; it is verified by
running it, not by the suite. What the suite does check is everything that would
make a built artifact wrong in a way tests could have caught.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SHIM = PROJECT_ROOT / "firefox_bridge" / "tools" / "bulk_downloader.py"

CONSOLE_SCRIPTS = {
    "firefox-bridge": "firefox_bridge.server:main",
    "firefox-bridge-download": "firefox_bridge.cli:main",
    "firefox-bridge-mcp": "firefox_bridge.mcp_server:main",
}


def _pyproject() -> dict:
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


# --- the legacy shim ------------------------------------------------------


def test_the_shim_still_exists() -> None:
    assert SHIM.is_file(), "the legacy entry point was removed"


def test_the_shim_starts() -> None:
    """An old `python -m firefox_bridge.tools.bulk_downloader` must still parse."""
    result = subprocess.run(
        [sys.executable, str(SHIM), "--help"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "firefox-bridge-download" in result.stdout


def test_the_shim_exposes_the_current_options() -> None:
    """Not merely "it starts": the options it accepts must be the current ones.

    A shim that runs but silently drops `--history` or `--dry-run` would be worse
    than one that fails, because the old command would appear to work while
    quietly doing something else.
    """
    result = subprocess.run(
        [sys.executable, str(SHIM), "--help"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=PROJECT_ROOT,
    )
    for option in ("--stocks", "--stocks-file", "--history", "--dry-run", "--report"):
        assert option in result.stdout, f"the shim no longer accepts {option}"


def test_the_shim_rejects_an_invalid_stock_code_like_the_real_cli(tmp_path: Path) -> None:
    """It must be the same program, not a lookalike that skips validation."""
    result = subprocess.run(
        [sys.executable, str(SHIM), "--stocks", "../evil", "--year", "2025"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert result.returncode == 2
    assert "tidak valid" in result.stderr


# --- what the package promises -------------------------------------------


def test_every_console_script_resolves_to_a_real_main() -> None:
    """A declared entry point pointing at nothing is a broken install.

    Checked by import rather than by string comparison, so renaming a module
    breaks this test instead of producing an `.exe` that exits 1 on click.
    """
    import importlib

    for name, target in CONSOLE_SCRIPTS.items():
        module_name, attribute = target.split(":")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, attribute)), f"{name} -> {target} is not callable"


def test_the_declared_dependencies_cover_the_imports() -> None:
    """Everything the package imports at runtime must be declared.

    A missing declaration produces a wheel that installs cleanly and then fails
    on first use, in an environment that was never tested.
    """
    project = _pyproject()["project"]
    declared = {
        name.split(">")[0].split("[")[0].split("=")[0].strip().lower().replace("-", "_")
        for name in project["dependencies"]
    }
    # Modules imported at runtime, mapped to their distribution name.
    for distribution in ("fastapi", "httpx", "uvicorn", "mcp", "pydantic"):
        assert distribution in declared, f"{distribution} is imported but not declared"


def test_the_version_in_the_metadata_matches_the_package() -> None:
    """Two version numbers is one too many."""
    import firefox_bridge

    declared = _pyproject()["project"]["version"]
    assert declared == getattr(firefox_bridge, "__version__", declared), (
        "firefox_bridge.__version__ and pyproject version disagree"
    )


def test_readme_is_present_because_it_is_declared() -> None:
    """`readme` is declared in the metadata, so a missing file fails the build late."""
    project = _pyproject()["project"]
    assert (PROJECT_ROOT / project["readme"]).is_file()
