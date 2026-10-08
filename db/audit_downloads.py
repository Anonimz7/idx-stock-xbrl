"""Audit konsistensi unduhan instance.zip terhadap katalog IDX.

Urutan dan keanggotaan diambil dari ``load_entries()`` -- pembaca yang sama
dipakai downloader -- sehingga hasil audit identik dengan yang dilihat runner.

Sebelumnya sumbernya ``read_stock_list()`` dari dump SQL. Itu sudah salah
sejak downloader berpindah ke katalog: auditnya mengukur keanggotaan daftar
yang bahkan tidak lagi dipakai, jadi bisa melapor "lengkap" sementara
katalog berisi entri yang tak pernah disentuh, atau sebaliknya.

Tiga sumber dibandingkan:

1. katalog            -- arsip yang IDX terbitkan, dibekukan ``idx_watcher``
2. ``download_history.json``  -- catatan sukses/gagal per emiten per tahun
3. folder unduhan di disk     -- berkas yang benar-benar ada

Status dibaca apa adanya: entri tanpa field ``status`` berarti sukses (punya
``sha256``), ``status=failed`` adalah kegagalan. Skrip ini murni laporan.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firefox_bridge.instance.catalog import CatalogError, load_entries

SUCCESS_KEYS = ("sha256", "file")


@dataclass
class Row:
    pos: int
    ticker: str
    status: str  # verified | failed | unrecorded
    fail_count: int
    on_disk: bool
    issues: list[str] = field(default_factory=list)


def audit(catalog_path: Path, history_path: Path, dl_dir: Path, year: int):
    # Dua kali membaca berkas, sengaja. Meta (sumber API, waktu perbaruan)
    # diambil dari dokumen mentah, sedangkan daftar emiten harus lewat
    # load_entries -- meniru penyaringannya di sini berarti dua parser yang
    # bisa berbeda pendapat tentang apa itu "entri tahun ini".
    doc = json.loads(catalog_path.read_text(encoding="utf-8"))
    tickers = [code for code, _url in load_entries(catalog_path, year)]
    meta = {
        "source": str(doc.get("source") or "(tidak tercatat)"),
        "updated_at": str(doc.get("updated_at") or "(tidak tercatat)"),
        "entries": len(tickers),
    }

    history = json.loads(history_path.read_text(encoding="utf-8"))
    downloads: dict = history.get("downloads", {})
    rows: list[Row] = []

    for pos, ticker in enumerate(tickers, 1):
        q4 = downloads.get(ticker, {}).get(str(year), {}).get("4")
        if not isinstance(q4, dict) or not q4:
            status, fail_count = "unrecorded", 0
        elif q4.get("status") == "failed":
            status = "failed"
            fail_count = int(q4.get("fail_count") or 0)
        elif any(k in q4 for k in SUCCESS_KEYS):
            status = "verified"
            fail_count = int(q4.get("fail_count") or 0)
        else:
            status, fail_count = "unrecorded", 0
        rows.append(Row(pos, ticker, status, fail_count, (dl_dir / ticker).exists()))

    for r in rows:
        if r.status == "verified" and not r.on_disk:
            r.issues.append("verified di history tapi folder tidak ada di disk")
        elif r.status == "failed" and r.on_disk:
            r.issues.append("tercatat gagal tapi folder ada di disk")
        elif r.status == "unrecorded" and r.on_disk:
            r.issues.append("ada di disk tapi tidak tercatat di history")
        elif r.status == "unrecorded":
            r.issues.append("TERLEWAT: tidak ada catatan sama sekali")
        elif r.status == "failed":
            r.issues.append(f"gagal ({r.fail_count}x), belum terunduh")

    known = set(tickers)
    orphan = sorted(t for t in downloads if t not in known)
    return rows, meta, orphan


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    base = Path.home() / "Downloads" / "instance" / "saham"
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog", type=Path, default=here / "instance_catalog.json")
    p.add_argument("--history", type=Path, default=base / "download_history.json")
    p.add_argument("--download-dir", type=Path, default=base)
    p.add_argument("--year", type=int, default=2025)
    p.add_argument("--show-all", action="store_true")
    p.add_argument("--first", type=int, default=40, help="baris tabel yang ditampilkan")
    args = p.parse_args(argv)

    for label, path in (("--catalog", args.catalog), ("--history", args.history)):
        if not path.exists():
            print(f"ERROR: {label} tidak ditemukan: {path}", file=sys.stderr)
            return 2

    try:
        rows, meta, orphan = audit(args.catalog, args.history, args.download_dir, args.year)
    except CatalogError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    flagged = [r for r in rows if r.issues]
    gaps = [r for r in rows if r.status == "unrecorded"]
    failed = [r for r in rows if r.status == "failed"]
    disk_only = [r for r in rows if r.on_disk and r.status != "verified"]
    no_file = [r for r in rows if r.status == "verified" and not r.on_disk]

    # Potongan tak-terputus: urutan tercatat vs posisi katalog.
    recorded = [r for r in rows if r.status != "unrecorded"]

    print("=" * 78)
    print(f"AUDIT UNDUHAN instance.zip  -- tahun {args.year}")
    print(f"katalog      : {args.catalog}")
    print(f"  entri      : {meta['entries']}")
    print(f"  diperbarui : {meta['updated_at']}")
    print(f"  sumber     : {meta['source']}")
    print("=" * 78)
    print(f"Saham ikut unduhan         : {len(rows)}")
    print(f"  verified (ada sha256)    : {sum(1 for r in rows if r.status == 'verified')}")
    print(f"  failed                   : {len(failed)}")
    print(f"  tidak tercatat           : {len(gaps)}")
    print(f"Folder ada di disk         : {sum(1 for r in rows if r.on_disk)}")
    print(f"History di luar katalog    : {len(orphan)}"
          + (f"  -> {', '.join(orphan)}" if orphan else ""))
    print("-" * 78)
    print(f"TOTAL TIDAK KONSISTEN      : {len(flagged)}")
    print("=" * 78)

    if recorded:
        print()
        print("--- PROGRES: rentang posisi katalog yang sudah tercatat ---")
        positions = [r.pos for r in recorded]
        print(f"  pertama : #{positions[0]} {recorded[0].ticker}")
        print(f"  terakhir: #{positions[-1]} {recorded[-1].ticker}")

        # Pecah jadi rentang kontinu: [1..175], [887..887], dst.
        runs: list[list[Row]] = [[recorded[0]]]
        for prev, cur in zip(recorded, recorded[1:], strict=False):
            if cur.pos == prev.pos + 1:
                runs[-1].append(cur)
            else:
                runs.append([cur])
        print(f"  potongan tercatat: {len(runs)}")
        for run in runs:
            if len(run) == 1:
                print(f"    #{run[0].pos} {run[0].ticker}")
            else:
                print(f"    #{run[0].pos} {run[0].ticker} .. #{run[-1].pos} "
                      f"{run[-1].ticker}  ({len(run)} emiten)")

        # Lompatan = jeda antar potongan tercatat.
        holes: list[tuple[int, int, list[Row]]] = []
        for a, b in zip(runs, runs[1:], strict=False):
            lo, hi = a[-1].pos + 1, b[0].pos - 1
            inside = [r for r in rows if lo <= r.pos <= hi]
            holes.append((lo, hi, inside))
        if holes:
            print()
            print("--- LOMPATAN (posisi katalog yang dilewati di tengah jalan) ---")
            for lo, hi, inside in holes:
                n_unrec = sum(1 for r in inside if r.status == "unrecorded")
                n_fail = sum(1 for r in inside if r.status == "failed")
                head = ", ".join(r.ticker for r in inside[:10])
                more = f", ... (+{len(inside) - 10})" if len(inside) > 10 else ""
                print(f"    #{lo}..#{hi}  ({len(inside)} emiten; "
                      f"{n_unrec} belum, {n_fail} gagal)")
                print(f"      {head}{more}")

        # Sisa setelah potongan tercatat terakhir = belum dijalankan sama sekali.
        tail = [r for r in rows if r.pos > positions[-1]]
        if tail:
            print()
            print(f"--- BELUM DIJALANKAN (setelah #{positions[-1]}): "
                  f"{len(tail)} emiten ---")
            print(f"    #{tail[0].pos} {tail[0].ticker} .. "
                  f"#{tail[-1].pos} {tail[-1].ticker}")

    if no_file:
        print()
        print(f"--- VERIFIED TAPI BERKAS HILANG ({len(no_file)}) ---")
        print("  " + ", ".join(r.ticker for r in no_file))

    if disk_only:
        print()
        print(f"--- ADA DI DISK TAPI TIDAK VERIFIED ({len(disk_only)}) ---")
        print("  " + ", ".join(f"{r.ticker}[{r.status}]" for r in disk_only))

    if failed:
        print()
        print(f"--- GAGAL ({len(failed)}) ---")
        print("  " + ", ".join(f"{r.ticker}(x{r.fail_count}, #{r.pos})" for r in failed))

    if gaps:
        print()
        print(f"--- TERLEWAT / BELUM DIJALANKAN ({len(gaps)}) ---")
        for i in range(0, len(gaps), 14):
            print("  " + ", ".join(f"{r.ticker}({r.pos})" for r in gaps[i:i + 14]))

    if args.show_all and flagged:
        print()
        print(f"{'POS':>5} {'TICKER':<7} {'STATUS':<12} {'FAIL':>4} {'DISK':<5} MASALAH")
        print("-" * 78)
        for r in flagged[: args.first]:
            print(f"{r.pos:>5} {r.ticker:<7} {r.status:<12} {r.fail_count:>4} "
                  f"{'ADA' if r.on_disk else '-':<5} {'; '.join(r.issues)}")

    return 1 if flagged else 0


if __name__ == "__main__":
    raise SystemExit(main())
