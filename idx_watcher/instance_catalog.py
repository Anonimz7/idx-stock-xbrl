#!/usr/bin/env python
"""Katalog URL `instance.zip` laporan tahunan dari halaman resmi IDX.

Watcher ini hanya **MENGUMPULKAN**, tidak mengunduh. Ia membaca halaman
"Laporan Keuangan dan Tahunan" di idx.co.id, mencatat URL `instance.zip`
laporan tahunan tiap emiten beserta waktu IDX mengunggapnya, lalu menyimpannya
ke katalog JSON. Unduhan dilakukan pada waktu terpisah -- dari katalog ini.

Mengapa halaman ini, bukan halaman pengumuman (pemilik lama modul ini):

* **Periode dan tahun berupa field terstruktur** (`Tahun : 2025`,
  `Periode : Audit`), sehingga tidak ada lagi tebakan dari judul bebas --
  titik lemah yang membuat pembaca judul lama salah pada 7 dari 15 judul uji.
* **Ketidakhadiran sebuah emiten berarti "tidak ada laporan"**; tidak ada
  lagi inferensi "tidak ada" dari respons 404.
* **Tautan sudah final.** `File_Path` di halaman adalah URL penuh menuju
  `instance.zip`; tidak ada lagi konstruksi URL sendiri.
* **Waktu unggah tersedia per laporan**, presisi ke menit -- itulah nilai
  yang dicatat katalog ini untuk dipakai belakangan.

Catatan soal API: di balik halaman ini ada endpoint rapi
`GET /primary/ListedCompany/GetFinancialReport` yang mengembalikan JSON
lengkap (termasuk `File_Modified` presisi detik). Endpoint itu memang bisa
dipanggil, tetapi **tidak bisa dibaca lewat jembatan Firefox**: content
script ekstensi tidak berjalan pada dokumen `application/json`, dan
jembatan tidak menyediakan alat eksekusi JS. Karena seluruh proyek berjalan
melewati peramban (agar lolos Cloudflare), jalur yang dipakai di sini adalah
halaman HTML yang dirender. Konsekuensinya: waktu unghap dicatat presisi
menit, bukan detik.

Cara kerja pemindaian: filter di halaman berupa radio + tombol `Terapkan`
(URL tidak ikut berubah), jadi tiap tahun dipilih dulu filternya, lalu
halaman digerakkan lewat tombol "Go to next page". Ada 12 entri per halaman.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firefox_bridge.client import (
    FirefoxBridgeClient,
    FirefoxBridgeClientError,
)
from firefox_bridge.pacing import sleep_between_stocks

PAGE_URL = "https://www.idx.co.id/id/perusahaan-tercatat/laporan-keuangan-dan-tahunan/"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "db" / "instance_catalog.json"
PAGE_SIZE = 12
PERIOD = "audit"
REPORT_TYPE_LABEL = "Laporan Keuangan"
EMITEN_TYPE_LABEL = "Saham"
PERIOD_LABEL = "Tahunan"
APPLY_LABEL = "Terapkan"
NEXT_LABEL = "Go to next page"

# Bulan ditulis lengkap di halaman ("Februari", "Maret"), tetapi bentuk
# pendeknya ("Feb", "Mar") tetap diterima agar parser tidak goyah kalau IDX
# mengubah format. Alternation diurutkan dari yang terpanjang supaya
# "februari" tidak tertangkap lebih dulu oleh "feb".
MONTHS = {
    "jan": 1, "januari": 1,
    "feb": 2, "februari": 2,
    "mar": 3, "maret": 3,
    "apr": 4, "april": 4,
    "mei": 5,
    "jun": 6, "juni": 6,
    "jul": 7, "juli": 7,
    "agu": 8, "agustus": 8,
    "sep": 9, "september": 9,
    "okt": 10, "oktober": 10,
    "nov": 11, "november": 11,
    "des": 12, "desember": 12,
}
_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))

# .../Laporan Keuangan Tahun 2025/Audit/AADI/instance.zip
HREF_RE = re.compile(
    r"Laporan Keuangan Tahun (\d{4})/Audit/([A-Z0-9]+)/instance\.zip",
    re.IGNORECASE,
)

# AADI 06 Maret 2026 | 15:57
#
# Pengecekan huruf besar-kecil dibatasi pada grup bulan saja: nama bulan
# ditulis "Februari"/"Maret" sedangkan tabel MONTHS memakai huruf kecil.
# Pengenal emiten sengaja tetap bersifat huruf besar -- halaman menulis
# kode emiten huruf besar, dan aba-aba `re.I` di situ akan membuat pola
# menangkap kata biasa seperti "nama 06 Maret 2026 | 15:57".
STAMP_RE = re.compile(
    rf"\b([A-Z0-9]{{3,6}})\s+(\d{{1,2}})\s+(?i:({_MONTH_ALT}))\s+(\d{{4}})\s*\|\s*(\d{{2}}:\d{{2}})\b"
)

YEAR_LABEL_RE = re.compile(r"^\d{4}$")
BLOCK_MARKERS = ("Verifikasi Anda sedang menyelesaikan", "Verifying you are human")


class CatalogError(RuntimeError):
    """Kegagalan yang membuat hasil pemindaian tidak layak ditulis."""


def _log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def _text_of(raw: object) -> str:
    if isinstance(raw, dict):
        return str(raw.get("text") or "")
    return str(raw or "")


def read_text(client: FirefoxBridgeClient, tab_id: str) -> str:
    """Baca teks halaman sampai benar-benar utuh.

    Membaca terpotong berarti entri hilang diam-diam, jadi teks yang masih
    menyentuh batas `max_chars` dianggap belum selesai dan dinaikkan. Bila
    tetap tidak utuh, ini kegagalan -- bukan hasil yang boleh ditulis.
    """
    last_error = ""
    for cap in (30_000, 120_000, 400_000):
        try:
            text = _text_of(client.text(tab_id, max_chars=cap))
        except FirefoxBridgeClientError as exc:
            last_error = str(exc)
            continue
        if len(text) < cap:
            return text
    raise CatalogError(
        "teks halaman masih terpotong pada batas pembacaan terbesar; "
        f"entri bisa hilang ({last_error or 'tanpa pesan'})"
    )


def parse_stamps(text: str) -> dict[str, str]:
    """Peta `TICKER` -> `YYYY-MM-DDTHH:MM` saat laporan itu diunggah."""
    stamps: dict[str, str] = {}
    for match in STAMP_RE.finditer(text):
        ticker, day, month_name, year, hhmm = match.groups()
        month = MONTHS.get(month_name.lower())
        if month is None:
            continue
        stamps[ticker.upper()] = f"{year}-{month:02d}-{int(day):02d}T{hhmm}"
    return stamps


def parse_instance_hrefs(elements: list[dict]) -> dict[str, dict]:
    """Peta `2025|AADI` -> entri dasar yang diambil dari href lampiran."""
    found: dict[str, dict] = {}
    for element in elements:
        href = element.get("href")
        if not href:
            continue
        match = HREF_RE.search(unquote(str(href)))
        if not match:
            continue
        year, ticker = match.group(1), match.group(2).upper()
        found[f"{year}|{ticker}"] = {
            "ticker": ticker,
            "year": int(year),
            "period": PERIOD,
            "url": str(href),
        }
    return found


def find_element(elements: list[dict], *, role: str, name: str) -> dict | None:
    """Cari elemen snapshot berdasarkan peran dan label aksesibilitasnya."""
    for element in elements:
        if element.get("role") != role:
            continue
        if str(element.get("name") or "").strip() != name:
            continue
        return element
    return None


def next_page_button(elements: list[dict]) -> tuple[str | None, bool]:
    """(ref, sudah_mati) untuk tombol halaman berikutnya."""
    element = find_element(elements, role="button", name=NEXT_LABEL)
    if element is None:
        return None, False
    state = element.get("state") or {}
    return element.get("ref"), bool(state.get("disabled"))


def year_labels(elements: list[dict]) -> list[str]:
    """Semua tahun yang ditawarkan halaman, urut menurun."""
    years = sorted(
        {
            str(element.get("name") or "").strip()
            for element in elements
            if element.get("role") == "radio"
            and YEAR_LABEL_RE.fullmatch(str(element.get("name") or "").strip())
        },
        reverse=True,
    )
    return years


def snapshot_elements(client: FirefoxBridgeClient, tab_id: str) -> list[dict]:
    """Elemen snapshot, ditapis agar hanya berisi dict yang bisa dipakai."""
    snapshot = client.snapshot(tab_id, max_elements=200)
    raw = snapshot.get("elements") if isinstance(snapshot, dict) else snapshot
    return [element for element in (raw or []) if isinstance(element, dict)]


def wait_for(
    client: FirefoxBridgeClient,
    tab_id: str,
    predicate: Callable[[str], bool],
    *,
    tries: int,
    what: str,
) -> str:
    """Ulangi pembacaan sampai `predicate` terpenuhi; navigasi itu async."""
    text = ""
    for attempt in range(tries):
        text = read_text(client, tab_id)
        if predicate(text):
            return text
        if attempt < tries - 1:
            time.sleep(1)
    raise CatalogError(f"halaman tidak pernah menunjukkan yang diharapkan: {what}")


def apply_filters(client: FirefoxBridgeClient, tab_id: str, year: str) -> None:
    """Pilih jenis laporan, tahun, dan periode lalu tekan `Terapkan`."""
    elements = snapshot_elements(client, tab_id)
    selections = [
        (REPORT_TYPE_LABEL, "radio"),
        (EMITEN_TYPE_LABEL, "radio"),
        (year, "radio"),
        (PERIOD_LABEL, "radio"),
    ]
    for label, role in selections:
        element = find_element(elements, role=role, name=label)
        if element is None:
            raise CatalogError(f"filter {role} {label!r} tidak ada di halaman")
        state = element.get("state") or {}
        if state.get("checked"):
            continue
        client.click(tab_id, element["ref"])

    apply_ref = find_element(elements, role="button", name=APPLY_LABEL)
    if apply_ref is None:
        raise CatalogError(f"tombol {APPLY_LABEL!r} tidak ada di halaman")
    client.click(tab_id, apply_ref["ref"])

    wait_for(
        client,
        tab_id,
        lambda text: f"Tahun : {year}" in text and "Periode : Audit" in text,
        tries=12,
        what=f"hasil filter {year}/Audit",
    )
    _log(f"filter {year}/{PERIOD} aktif")


def merge_entries(
    catalog: dict,
    found: dict[str, dict],
    stamps: dict[str, str],
    now: str,
) -> tuple[int, int]:
    """Gabungkan temuan halaman ke katalog; kembalikan (baru, berubah)."""
    entries = catalog.setdefault("entries", {})
    added = changed = 0
    for key, item in found.items():
        candidate = dict(item)
        uploaded_at = stamps.get(item["ticker"])
        if uploaded_at:
            candidate["uploaded_at"] = uploaded_at
        previous = entries.get(key)
        if previous is None:
            candidate["first_seen"] = now
            candidate["last_seen"] = now
            entries[key] = candidate
            added += 1
            continue
        merged = dict(previous)
        merged["last_seen"] = now
        differs = False
        for field in ("url", "uploaded_at"):
            new_value = candidate.get(field)
            if new_value and new_value != previous.get(field):
                merged[field] = new_value
                differs = True
        entries[key] = merged
        if differs:
            changed += 1
    return added, changed


def scan_year(
    client: FirefoxBridgeClient,
    tab_id: str,
    year: str,
    catalog: dict,
    *,
    max_pages: int,
    delay: float,
    delay_max: float,
) -> tuple[int, int, int]:
    """Pindai satu tahun; kembalikan (entri, baru, berubah)."""
    apply_filters(client, tab_id, year)

    page = 0
    total = added = updated = 0
    previous_keys: frozenset[str] | None = None

    while True:
        page += 1
        if max_pages and page > max_pages:
            _log(f"{year}: berhenti di batas --max-pages {max_pages}")
            break

        elements = snapshot_elements(client, tab_id)
        stamps = parse_stamps(read_text(client, tab_id))
        found = parse_instance_hrefs(elements)
        if not found:
            raise CatalogError(f"{year} halaman {page}: tidak ada satu pun href instance.zip")

        keys = frozenset(found)
        if previous_keys is not None and keys == previous_keys:
            raise CatalogError(
                f"{year} halaman {page}: daftar tidak bergerak setelah tombol berikutnya "
                "-- paginasi macet, hasil tidak boleh dianggap lengkap"
            )

        page_added, page_changed = merge_entries(catalog, found, stamps, datetime.now().astimezone().isoformat(timespec="seconds"))
        total += len(found)
        added += page_added
        updated += page_changed
        _log(
            f"{year} halaman {page}: {len(found)} entri instance.zip "
            f"(baru {page_added}, berubah {page_changed})"
        )
        previous_keys = keys

        ref, done = next_page_button(elements)
        if ref is None or done:
            _log(
                f"{year}: selesai di halaman {page}"
                + (" (tombol berikutnya sudah mati)" if done else " (tombol berikutnya tidak ada)")
            )
            break
        sleep_between_stocks(delay, delay_max)
        client.click(tab_id, ref)
        _wait_page_change(client, tab_id, previous_keys, year=year, page=page)

    return total, added, updated


def _wait_page_change(
    client: FirefoxBridgeClient,
    tab_id: str,
    previous_keys: frozenset[str],
    *,
    year: str,
    page: int,
) -> list[dict]:
    """Tunggu halaman berikutnya benar-benar terganti; kalau tidak, berhenti."""
    elements: list[dict] = []
    for attempt in range(10):
        elements = snapshot_elements(client, tab_id)
        if parse_instance_hrefs(elements) and frozenset(parse_instance_hrefs(elements)) != previous_keys:
            return elements
        if attempt < 9:
            time.sleep(1)
    raise CatalogError(
        f"{year} halaman {page + 1}: halaman berikutnya tidak pernah tampil "
        "-- pemindaian dihentikan agar tidak menulis katalog yang tidak lengkap"
    )


def load_catalog(path: Path) -> dict:
    if not path.exists():
        return {"source": PAGE_URL, "entries": {}}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise CatalogError(f"{path} bukan objek JSON")
    data.setdefault("source", PAGE_URL)
    data.setdefault("entries", {})
    return data


def save_catalog(path: Path, catalog: dict) -> None:
    """Tulis atomik supaya katalog tidak pernah tersimpan setengah jadi."""
    catalog["source"] = PAGE_URL
    catalog["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(catalog, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temp.replace(path)


def run(args: argparse.Namespace) -> int:
    output = Path(args.output)
    catalog = load_catalog(output)
    existing = len(catalog.get("entries", {}))
    _log(f"membaca {existing} entri lama dari {output}")

    totals = added = updated = 0
    with (
        FirefoxBridgeClient(base_url=args.bridge_url, timeout=args.timeout) as client,
        client.managed_tab(PAGE_URL, active=False) as tab_info,
    ):
        tab_id = tab_info.get("id") if isinstance(tab_info, dict) else tab_info
        if not tab_id:
            raise CatalogError("jembatan tidak mengembalikan id tab")
        text = client.wait_until(
            tab_id,
            lambda t: any(marker in t for marker in BLOCK_MARKERS)
            or "Laporan Keuangan dan Tahunan" in t,
            timeout=args.timeout,
            poll_interval=2.0,
            description=PAGE_URL,
        )
        if any(marker in text for marker in BLOCK_MARKERS):
            _log("BLOCKED: Cloudflare menuntut verifikasi; hentikan agar tidak diulang terus")
            return 1

        elements = snapshot_elements(client, tab_id)
        offered = year_labels(elements)
        if not offered:
            raise CatalogError("daftar tahun tidak ditemukan di halaman")

        if args.years:
            requested = [chunk.strip() for chunk in args.years.split(",") if chunk.strip()]
            unknown = [year for year in requested if year not in offered]
            if unknown:
                raise CatalogError(
                    f"tahun {', '.join(unknown)} tidak ditawarkan halaman "
                    f"(tersedia: {', '.join(offered)})"
                )
            years = requested
        else:
            years = offered
        _log(f"memindai {len(years)} tahun: {', '.join(years)}")

        for year in years:
            count, new_count, changed_count = scan_year(
                client,
                tab_id,
                year,
                catalog,
                max_pages=args.max_pages,
                delay=args.delay,
                delay_max=args.delay_max,
            )
            totals += count
            added += new_count
            updated += changed_count

    if args.dry_run:
        _log(f"DRY RUN: {totals} entri terbaca, {added} baru, {updated} berubah; katalog tidak ditulis")
        return 0

    save_catalog(output, catalog)
    _log(
        f"selesai: {totals} entri terbaca, {added} baru, {updated} berubah, "
        f"{len(catalog['entries'])} total tersimpan di {output}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Katalog URL instance.zip laporan tahunan IDX (tanpa unduhan)."
    )
    parser.add_argument(
        "--years",
        help="tahun dipisah koma, mis. 2025,2026; default semua tahun yang ditawarkan halaman",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="path katalog JSON")
    parser.add_argument("--max-pages", type=int, default=0, help="batas halaman per tahun (0 = tanpa batas)")
    parser.add_argument("--delay", type=float, default=1.0, help="jeda minimum antar halaman (detik)")
    parser.add_argument("--delay-max", type=float, default=2.0, help="jeda maksimum antar halaman (detik)")
    parser.add_argument("--dry-run", action="store_true", help="pindai dan laporkan tanpa menulis katalog")
    parser.add_argument("--bridge-url", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args(argv)

    try:
        return run(args)
    except (CatalogError, FirefoxBridgeClientError) as exc:
        print(f"GAGAL: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
