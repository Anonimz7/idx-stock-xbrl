#!/usr/bin/env python
"""Generate daftar saham tersisa — semua kecuali yang sudah ada catatannya.

Dipanggil sebelum resume penuh, agar tidak membuang waktu pada emiten yang
sudah selesai maupun yang sudah diketahui tidak punya laporan audit di tahun
bersangkutan (reason: "404 Not Found" — diverifikasi manual, bukan halangan
Cloudflare). Keduanya tetap dihitung "sudah ditangani": sebuah 404 bisa saja
berubah bila IDX mengunggah laporan terlambat, jadi ia tidak dihapus dari
riwayat — hanya dilewati pada satu putaran ini, dan putaran berikutnya
mengulanginya dengan satu percobaan saja.
"""
import json
from pathlib import Path

from firefox_bridge.stocksource import read_stock_list

DB = Path("db/list_saham.sql")
HISTORY = Path.home() / "Downloads" / "instance" / "saham" / "download_history.json"


def main() -> None:
    stocks = read_stock_list(DB)
    all_codes = {s.upper() for s in stocks.codes}

    history = json.loads(HISTORY.read_text(encoding="utf-8"))
    already_handled: set[str] = set()

    for stock, years in history.get("downloads", {}).items():
        for _year, quarters in years.items():
            for _quarter, entry in quarters.items():
                if isinstance(entry, dict):
                    already_handled.add(stock.upper())

    remaining = sorted(all_codes - already_handled)

    # `read_stock_list` menolak daftar tanpa kolom delisted -- tidak ada
    # gunanya menebak mana yang harus dibuang. Semua kode di sini sudah melewati
    # filter itu (sumbernya sendiri yang menyisihkan yang delisted), jadi
    # benderanya ditulis 0: nilai yang benar, sekaligus nilai yang bisa dibaca
    # pemanggil `--stocks-file`. Tanpa kolom ini daftar gagal dibaca, dan run
    # berhenti sebelum saham pertama pun dimulai.
    out_path = Path("db/list_saham_remaining.csv")
    with out_path.open("w", encoding="utf-8") as f:
        f.write("ticker,label_delisted\n")
        for code in remaining:
            f.write(f"{code},0\n")

    verified = {
        s for s, d in history.get("downloads", {}).items()
        for _y, q in d.items()
        for _q, e in q.items()
        if isinstance(e, dict) and e.get("integrity_status") == "verified"
    }
    failed = already_handled - verified

    print(f"[generate_remaining] saham tercatat di history JSON: {len(already_handled)}")
    print(f"  - verified : {len(verified)}")
    print(f"  - 404      : {len(failed)}  (dilewati putaran ini; alasan: 404 Not Found)")
    print(f"[generate_remaining] sisa untuk didownload: {len(remaining)}  -> {out_path}")


if __name__ == "__main__":
    main()
