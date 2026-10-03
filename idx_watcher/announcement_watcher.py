"""Watcher harian pengumuman IDX: deteksi rilis laporan keuangan baru dan unduh otomatis.

Cara kerja:
1. Buka halaman Pengumuman IDX lewat bridge (tanpa bypass; kalau kena blokir,
   keluar diam-diam dan serahkan ke block_watch.py).
2. Baca entri pengumuman dari tanggal `since` sampai hari ini (halaman diurut
   dari yang terbaru; jalan mundur sampai tanggal lebih tua dari `since`).
3. Filter judul yang mengandung kata kunci laporan keuangan.
4. Untuk tiap emiten yang cocok: unduh semua kuartal terdeteksi tahun berjalan
   lewat download_all_detected (file yang sudah valid otomatis di-skip via
   history+hash, jadi tidak ada duplikat).
5. Pengumuman "Koreksi": unduh ulang kuartal yang dikoreksi (file lama
   dibackup dulu).
6. Simpan watermark tanggal scan + daftar pengumuman yang sudah diproses di
   watcher_state.json agar tiap pengumuman hanya diproses sekali.

Jadwal yang disarankan: cron harian (mis. 07:00 WIB).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firefox_bridge.client import FirefoxBridgeClient, FirefoxBridgeClientError
from firefox_bridge.downloader.history import history_entry, load_download_history
from firefox_bridge.downloader.orchestrator import (
    download_all_detected,
    download_stock,
)
from firefox_bridge.downloader.paths import download_root, final_report_path
from firefox_bridge.pacing import sleep_between_stocks

PENGUMUMAN_URL = "https://www2.idx.co.id/id/berita/pengumuman/"

# Judul pengumuman yang dianggap rilis laporan keuangan.
REPORT_KEYWORDS = (
    "laporan keuangan",
    "financial statement",
    "financial report",
)

BLOCK_SIGNATURES = (
    "you have been blocked",
    "security verification",
    "performing security verification",
    "just a moment",
)

MONTHS_ID = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "Mei": 5, "Jun": 6,
    "Jul": 7, "Agu": 8, "Sep": 9, "Okt": 10, "Nov": 11, "Des": 12,
}

DATE_RE = re.compile(
    r"(\d{2})\s+(Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Sep|Okt|Nov|Des)\s+(\d{4})"
)
TIME_RE = re.compile(r"\b(\d{2}:\d{2}:\d{2})\b")
TICKER_RE = re.compile(r"\[\s*([A-Z0-9\-]{3,6})\s*\]")
ATTACH_RE = re.compile(r"-\s*(.+?_(\d{8})_lamp\d+\.\w+)")
ANN_ID_RE = re.compile(r"_(\d{8})_lamp\d+\.")

# Koreksi -> kuartal. Urutan penting: pola spesifik dulu.
QUARTER_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"triwulan\s*(?:ke[-\s]*)?([123])\b", re.I), 0),  # grup 1 = angka
    (re.compile(r"triwulan\s*(iii|ii|i)\b", re.I), 0),  # angka romawi
    (re.compile(r"\bTW\s*([123])\b", re.I), 0),
    (re.compile(r"triwulan\s+(satu|dua|tiga)\b", re.I), 0),
    (re.compile(r"kuartal\s*([123])\b", re.I), 0),
    (re.compile(r"semester\s*(?:ke[-\s]*)?(1|i)\b", re.I), 2),
    (re.compile(r"\b6\s*bulan\b|\benam\s*bulan\b", re.I), 2),
    (re.compile(r"\b9\s*bulan\b|\bsembilan\s*bulan\b", re.I), 3),
    (re.compile(r"semester\s*(?:ke[-\s]*)?(2|ii)\b", re.I), 4),
    (re.compile(r"tahunan|tahun\s*buku|\bauditan\b", re.I), 4),
]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_id_date(text: str) -> date | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    day, mon, year = m.group(1), m.group(2), m.group(3)
    return date(int(year), MONTHS_ID[mon], int(day))


def parse_announcements(page_text: str) -> list[dict]:
    """Pecah teks halaman pengumuman menjadi entri {title, ticker, date, time, ann_id}.

    Urutan baris per entri di halaman: TANGGAL, JAM, JUDUL, [TICKER],
    lampiran-lampiran. Tolerant terhadap judul diawali '# ' (render markdown)
    atau baris teks biasa; ticker kadang menempel di baris judul.
    """
    entries: list[dict] = []

    def blank() -> dict:
        return {"title": None, "ticker": None, "date": None, "time": None,
                "ann_id": None, "attachments": []}

    cur = blank()

    def flush() -> None:
        if cur["title"] and cur["ticker"] and cur["date"]:
            entries.append(dict(cur))

    for raw in page_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if DATE_RE.search(line):
            # Tanggal memulai entri baru; simpan entri sebelumnya bila lengkap.
            flush()
            cur = blank()
            cur["date"] = parse_id_date(line)
            continue
        tm = TIME_RE.search(line)
        if tm:
            cur["time"] = tm.group(1)
            continue
        tkm = TICKER_RE.search(line)
        if tkm:
            if cur["ticker"] is None:
                cur["ticker"] = tkm.group(1).strip()
            line = TICKER_RE.sub("", line).strip()
            if not line:
                continue
        am = ATTACH_RE.search(line)
        if am:
            cur["attachments"].append(am.group(1))
            im = ANN_ID_RE.search(am.group(1))
            if im and not cur["ann_id"]:
                cur["ann_id"] = im.group(1)
            continue
        # Kandidat judul: baris teks substansial pertama setelah tanggal/jam.
        if cur["title"] is None and len(line) > 12 and not DATE_RE.search(line):
            cleaned = line[2:].strip() if line.startswith("# ") else line
            if len(cleaned) > 12 and not re.match(
                r"^(Beranda|Home|Pengumuman|Cari|Filter|Tampilkan|Menampilkan)\b",
                cleaned, re.I,
            ):
                cur["title"] = cleaned
    flush()
    return entries


def is_report_announcement(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in REPORT_KEYWORDS)


def is_correction(title: str) -> bool:
    return "koreksi" in title.lower()


def quarter_from_title(title: str) -> int | None:
    t = title.lower()
    for pat, fixed in QUARTER_PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        if fixed:
            return fixed
        g = m.group(1).lower()
        if g.isdigit():
            return int(g)
        return {"satu": 1, "dua": 2, "tiga": 3,
                "i": 1, "ii": 2, "iii": 3}[g]
    return None


def entry_key(e: dict) -> str:
    d = e["date"].isoformat() if e["date"] else "nodate"
    return f"{d}|{e['ticker']}|{e['title']}|{e.get('ann_id') or ''}"


def page_blocked(text: str) -> bool:
    low = text.lower()
    return any(s in low for s in BLOCK_SIGNATURES)


def fetch_page_text(client: FirefoxBridgeClient, tab_id: str, url: str,
                    timeout: float = 45.0) -> str:
    client.navigate(tab_id, url)
    try:
        text = client.wait_until(
            tab_id,
            lambda t: len(t.strip()) > 200 or page_blocked(t),
            timeout=timeout,
            poll_interval=2.0,
            description=f"teks {url}",
        )
    except Exception as exc:
        raise RuntimeError(f"gagal memuat {url}: {exc}") from exc
    return text


def find_next_page(client: FirefoxBridgeClient, tab_id: str) -> str | None:
    """Cari tombol halaman berikutnya di snapshot; kembalikan ref atau None."""
    try:
        snap = client.snapshot(tab_id)
    except FirefoxBridgeClientError:
        return None
    items = snap if isinstance(snap, list) else snap.get("elements", [])
    if not isinstance(items, list):
        return None
    for el in items:
        if not isinstance(el, dict):
            continue
        name = str(el.get("name") or el.get("text") or "").strip().lower()
        role = str(el.get("role") or "").lower()
        if role in ("button", "link") and name in ("berikutnya", "next", ">", "»"):
            return str(el.get("ref") or el.get("id") or "")
    return None


def collect_announcements(client: FirefoxBridgeClient, since: date,
                          max_pages: int = 5) -> list[dict]:
    """Kumpulkan entri pengumuman dari tanggal `since` sampai hari ini."""
    with client.managed_tab(PENGUMUMAN_URL) as info:
        tab_id = info.get("id") if isinstance(info, dict) else info
        all_entries: list[dict] = []
        for page in range(max_pages):
            text = fetch_page_text(client, tab_id, PENGUMUMAN_URL)
            if page_blocked(text):
                raise RuntimeError("BLOCKED")
            entries = [e for e in parse_announcements(text)
                       if e["date"] and e["date"] >= since]
            # Urutan halaman: terbaru dulu. Berhenti kalau halaman ini sudah
            # memuat entri lebih tua dari `since` (artinya halaman berikut
            # hanya berisi yang lebih tua lagi).
            page_dates = [e["date"] for e in parse_announcements(text) if e["date"]]
            all_entries.extend(entries)
            log(f"halaman {page + 1}: {len(entries)} entri >= {since.isoformat()}")
            if not page_dates or min(page_dates) < since:
                break
            nxt = find_next_page(client, tab_id)
            if not nxt:
                break
            try:
                client.click(tab_id, nxt)
            except FirefoxBridgeClientError:
                break
            time.sleep(3)
        # Buang duplikat antar-halaman, urutkan dari yang terlama.
        seen, uniq = set(), []
        for e in all_entries:
            k = entry_key(e)
            if k not in seen:
                seen.add(k)
                uniq.append(e)
        uniq.sort(key=lambda e: (e["date"], e.get("time") or ""))
        return uniq


def backup_existing(stock: str, year: int, quarter: int,
                    download_dir: Path | None) -> Path | None:
    final = final_report_path(stock, year, quarter, download_dir)
    if not final.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d")
    backup = final.with_name(f"{final.stem}.koreksi-{stamp}{final.suffix}")
    final.rename(backup)
    log(f"backup koreksi: {final.name} -> {backup.name}")
    return backup


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Watcher harian pengumuman IDX.")
    ap.add_argument("--year", type=int, default=date.today().year)
    ap.add_argument("--download-dir", default="downloads-2026")
    ap.add_argument("--delay", type=float, default=20)
    ap.add_argument("--delay-max", type=float, default=40)
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--since", default=None,
                    help="YYYY-MM-DD; default: 2 hari lalu bila belum ada state")
    ap.add_argument("--dry-run", action="store_true",
                    help="hanya pindai pengumuman, jangan unduh")
    args = ap.parse_args(argv)

    root = download_root(Path(args.download_dir))
    state_path = root / "watcher_state.json"
    block_path = root / "block_state.json"

    if block_path.is_file():
        log("JEDA BLOKIR: block_state.json ada; watcher diam, block-watch yang menangani.")
        return 0

    state: dict = {"last_scan": None, "processed": []}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text())
        except (json.JSONDecodeError, OSError):
            log("state korup, mulai dari awal")

    today = date.today()
    if args.since:
        since = date.fromisoformat(args.since)
    elif state.get("last_scan"):
        since = date.fromisoformat(state["last_scan"]) - timedelta(days=1)
    else:
        since = today - timedelta(days=2)
    log(f"pindai pengumuman {since.isoformat()} s.d. {today.isoformat()}")

    try:
        client = FirefoxBridgeClient()
    except FirefoxBridgeClientError as exc:
        log(f"STACK_TIDAK_SIAP: bridge tidak terjangkau ({exc}); coba lagi nanti.")
        return 2

    try:
        entries = collect_announcements(client, since, args.max_pages)
    except RuntimeError as exc:
        if str(exc) == "BLOCKED":
            log("BLOCKED saat memuat pengumuman; diam, block-watch yang menangani.")
            return 0
        raise
    except FirefoxBridgeClientError as exc:
        log(f"STACK_TIDAK_SIAP: {exc}")
        return 2

    log(f"total entri dalam rentang: {len(entries)}")
    processed: set[str] = set(state.get("processed") or [])
    matched = [e for e in entries
               if is_report_announcement(e["title"]) and entry_key(e) not in processed]
    log(f"kandidat laporan keuangan baru: {len(matched)}")

    downloaded, skipped, corrected, failed = 0, 0, 0, []
    download_dir = Path(args.download_dir)

    for i, e in enumerate(matched):
        stock, title = e["ticker"], e["title"]
        key = entry_key(e)
        log(f"[{i + 1}/{len(matched)}] {stock} {e['date']}: {title[:80]}")
        if args.dry_run:
            processed.add(key)
            continue
        try:
            if i > 0:
                sleep_between_stocks(args.delay, args.delay_max)
            if is_correction(title):
                q = quarter_from_title(title)
                quarters = [q] if q else [1, 2, 3, 4]
                log(f"koreksi terdeteksi, kuartal target: {quarters}")
                for qq in quarters:
                    backup = backup_existing(stock, args.year, qq, download_dir)
                    try:
                        res = download_stock(client, stock, args.year, qq, download_dir)
                    except Exception:
                        if backup and backup.is_file():
                            backup.rename(final_report_path(stock, args.year, qq, download_dir))
                            log("unduh koreksi gagal; backup dikembalikan")
                        raise
                    corrected += 1
                    log(f"koreksi {stock} TW{qq}: {res.status}")
            else:
                results = download_all_detected(client, stock, args.year, download_dir)
                for r in results:
                    if r.status == "downloaded":
                        downloaded += 1
                    else:
                        skipped += 1
                log(f"{stock}: {len(results)} kuartal diproses")
            processed.add(key)
        except FirefoxBridgeClientError as exc:
            log(f"STACK_TIDAK_SIAP di tengah jalan: {exc}")
            break
        except Exception as exc:  # noqa: BLE001 - lanjut ke emiten berikut
            log(f"GAGAL {stock}: {type(exc).__name__}: {exc}")
            failed.append(stock)

    # Simpan state: watermark = hari ini, processed dibatasi 3000 terakhir.
    state["last_scan"] = today.isoformat()
    state["processed"] = sorted(processed)[-3000:]
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    log(f"ringkasan: unduh={downloaded} skip={skipped} koreksi={corrected} "
        f"gagal={len(failed)}")
    if failed:
        log("gagal: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
