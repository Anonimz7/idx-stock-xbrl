"""Browser flow for one stock and one reporting year.

The sequence is fixed and observable:

1. reuse a single company-profile tab and load the requested stock;
2. open the **Laporan Keuangan** panel;
3. select the reporting year through the year searchbox;
4. detect every inlineXBRL report link for that year.

Each step is announced and paced so the asynchronous IDX front end has time to
render before the next action.
"""

from __future__ import annotations

import time
from typing import Any

from firefox_bridge.client import FirefoxBridgeClient
from firefox_bridge.pacing import wait_before_step

from ..progress import progress
from .link_parser import find_inline_xbrl_links
from .selectors import (
    find_dropdown_option_ref,
    find_laporan_keuangan_ref,
    find_year_dropdown_ref,
    selected_year,
)

PROFILE_URL = (
    "https://www.idx.co.id/id/perusahaan-tercatat/"
    "profil-perusahaan-tercatat/{stock}"
)
PROFILE_PATH_PREFIX = "/id/perusahaan-tercatat/profil-perusahaan-tercatat/"
STUCK_LOADING_SECONDS = 5.0
SNAPSHOT_ELEMENTS = 2000
SNAPSHOT_ELEMENTS_COMPACT = 800
PROFILE_SETTLE_SECONDS = 3.0


def find_reusable_profile_tab(client: FirefoxBridgeClient) -> str | None:
    """Return one existing IDX company-profile tab, regardless of stock code.

    Reusing a single tab keeps memory flat across long batch runs; opening one
    tab per stock exhausts the browser profile.
    """
    for tab in client.tabs():
        if PROFILE_PATH_PREFIX in (tab.get("url") or ""):
            return str(tab["id"])
    return None


def find_tab_for_url(client: FirefoxBridgeClient, url_fragment: str) -> str | None:
    """Return the id of the tab whose url contains the fragment, or None."""
    target = next(
        (
            tab
            for tab in client.tabs()
            if url_fragment in (tab.get("url") or "")
        ),
        None,
    )
    return str(target["id"]) if target else None


def reload_tab(client: FirefoxBridgeClient, tab_id: str) -> None:
    """Force a reload by navigating to the tab's current URL."""
    tab = next(
        (item for item in client.tabs() if str(item.get("id")) == tab_id),
        None,
    )
    url = tab.get("url") if tab else None
    if not url:
        raise RuntimeError(f"URL tab {tab_id} tidak ditemukan untuk reload")
    client.navigate(tab_id, url)


def wait_for_load(
    client: FirefoxBridgeClient,
    url_fragment: str,
    timeout: float = 45.0,
) -> str:
    """Poll tabs until the matching tab finished loading and return its id.

    A tab that stays ``loading`` for five seconds is force-reloaded once,
    because the IDX profile page occasionally sticks under the bridge's
    authenticated HTTP polling transport.
    """
    deadline = time.monotonic() + timeout
    time.sleep(2)  # Allow navigation to start.
    loading_started_at: float | None = None
    while time.monotonic() < deadline:
        tab_id = find_tab_for_url(client, url_fragment)
        if tab_id:
            tab = next(
                (
                    item
                    for item in client.tabs()
                    if str(item.get("id")) == tab_id
                ),
                None,
            )
            if tab:
                status = tab.get("status")
                if status == "complete":
                    return tab_id
                if status == "loading":
                    if loading_started_at is None:
                        loading_started_at = time.monotonic()
                    elif time.monotonic() - loading_started_at >= STUCK_LOADING_SECONDS:
                        reload_tab(client, tab_id)
                        loading_started_at = None
                        time.sleep(2)
                else:
                    loading_started_at = None
        time.sleep(1)
    raise TimeoutError(f"Tab for {url_fragment} did not finish loading")


def ensure_open(
    client: FirefoxBridgeClient,
    stock: str,
    timeout: float = 30.0,
) -> str:
    """Reuse one company-profile tab and wait for the requested stock."""
    profile_path = f"/profil-perusahaan-tercatat/{stock}"
    url = PROFILE_URL.format(stock=stock)
    existing = find_reusable_profile_tab(client)
    if existing is not None:
        client.navigate(existing, url)
    else:
        client.open_tab(url, active=True)
    return wait_for_load(client, profile_path, timeout)


def open_laporan_keuangan(
    client: FirefoxBridgeClient,
    tab_id: str,
    stock: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Open the Laporan Keuangan panel and return its year control.

    When the panel is already expanded the year control is returned directly,
    which makes a repeated run on the same tab cheap.
    """
    progress(f"STEP 2: Mencari tab 'Laporan Keuangan' untuk {stock}")
    snapshot = client.snapshot(tab_id=tab_id, max_elements=SNAPSHOT_ELEMENTS)
    year_control = find_year_dropdown_ref(snapshot)
    if year_control is not None:
        progress(
            f"STEP 2 OK: panel sudah terbuka; kontrol tahun={year_control['ref']}",
        )
        return year_control

    laporan_ref = find_laporan_keuangan_ref(snapshot)
    if laporan_ref is None:
        raise RuntimeError(f"Tombol 'Laporan Keuangan' tidak ditemukan untuk {stock}")

    wait_before_step("jeda sebelum klik Laporan Keuangan")
    progress(f"STEP 2.1: Klik 'Laporan Keuangan', ref={laporan_ref}")
    click_result = client.click(tab_id=tab_id, ref=laporan_ref)
    if not isinstance(click_result, dict) or not click_result.get("clicked"):
        raise RuntimeError(f"Tombol 'Laporan Keuangan' gagal diklik: {click_result}")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(1)
        snapshot = client.snapshot(tab_id=tab_id, max_elements=SNAPSHOT_ELEMENTS)
        year_control = find_year_dropdown_ref(snapshot)
        if year_control is not None:
            progress(
                f"STEP 2 OK: panel terbuka; kontrol tahun={year_control['ref']}",
            )
            return year_control

    raise RuntimeError(
        f"Kontrol tahun tidak muncul dalam {timeout:.0f} detik setelah membuka "
        f"'Laporan Keuangan' untuk {stock}"
    )


def select_year_dropdown(
    client: FirefoxBridgeClient,
    tab_id: str,
    year: int,
    initial_dropdown: dict[str, Any] | None = None,
) -> bool:
    """Type into the year input, then click the single exact rendered option.

    A synchronous option click cannot work: v-select only renders its option
    list after the first request returns, so the flow re-snapshots and then
    clicks the ref it actually observed.
    """
    target_year = str(year)
    dropdown = initial_dropdown
    if dropdown is None:
        snapshot = client.snapshot(
            tab_id=tab_id,
            max_elements=SNAPSHOT_ELEMENTS_COMPACT,
        )
        dropdown = find_year_dropdown_ref(snapshot)
    if dropdown is None:
        progress("STEP 3 GAGAL: input tahun tidak ditemukan")
        return False

    if selected_year(dropdown) == year:
        progress(f"STEP 3 OK: tahun sudah {target_year}")
        return True

    wait_before_step("jeda sebelum input tahun")
    progress(
        f"STEP 3.1: Input {target_year} pada kontrol tahun ref={dropdown['ref']}",
    )
    open_result = client.select_dropdown(
        tab_id=tab_id,
        ref=dropdown["ref"],
        value=target_year,
    )
    if not isinstance(open_result, dict) or open_result.get("error"):
        progress(f"STEP 3 GAGAL: dropdown tahun tidak bisa dibuka: {open_result}")
        return False

    for _ in range(5):
        time.sleep(1)
        snapshot = client.snapshot(
            tab_id=tab_id,
            max_elements=SNAPSHOT_ELEMENTS_COMPACT,
        )
        current_dropdown = find_year_dropdown_ref(snapshot)
        option = find_dropdown_option_ref(snapshot, target_year)
        if current_dropdown is None or option is None:
            continue
        if str(current_dropdown.get("value") or "").strip() != target_year:
            continue

        wait_before_step("jeda sebelum klik option tahun")
        progress(
            f"STEP 3.2: Option {target_year} ditemukan; klik option tepat",
        )
        click_result = client.click(tab_id=tab_id, ref=option["ref"])
        if not isinstance(click_result, dict) or not click_result.get("clicked"):
            progress(f"STEP 3 GAGAL: option tahun tidak bisa diklik: {click_result}")
            return False

        for _ in range(10):
            time.sleep(1)
            verification = client.snapshot(
                tab_id=tab_id,
                max_elements=SNAPSHOT_ELEMENTS_COMPACT,
            )
            if selected_year(find_year_dropdown_ref(verification)) == year:
                progress(
                    f"STEP 3 OK: kontrol tahun terverifikasi sebagai {target_year}",
                )
                return True

        progress(
            f"STEP 3 GAGAL: option {target_year} diklik, tetapi nilai tahun tidak terverifikasi",
        )
        return False

    progress(f"STEP 3 GAGAL: option tahun {target_year} tidak muncul")
    return False


def prepare_stock_year(
    client: FirefoxBridgeClient,
    stock: str,
    year: int,
) -> str:
    """Open one profile tab, open Laporan Keuangan, and select one year."""
    progress(f"STEP 1: Membuka profil {stock}")
    tab_id = ensure_open(client, stock)
    progress(f"STEP 1 OK: profil terbuka, tab_id={tab_id}")
    time.sleep(PROFILE_SETTLE_SECONDS)

    year_control = open_laporan_keuangan(client, tab_id, stock)
    progress(f"STEP 3: Memilih tahun {year}")
    if not select_year_dropdown(
        client,
        tab_id,
        year,
        initial_dropdown=year_control,
    ):
        raise RuntimeError(f"Tahun {year} tidak berhasil dipilih untuk {stock}")
    return tab_id


def wait_for_detected_links(
    client: FirefoxBridgeClient,
    tab_id: str,
    year: int,
    timeout: float = 12.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Wait until at least one report link for the year is rendered."""
    deadline = time.monotonic() + timeout
    snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = client.snapshot(tab_id=tab_id, max_elements=SNAPSHOT_ELEMENTS)
        links = find_inline_xbrl_links(snapshot, year)
        if links:
            return links, snapshot
        time.sleep(1)
    raise RuntimeError(f"Link inlineXBRL.zip tahun {year} tidak ditemukan")
