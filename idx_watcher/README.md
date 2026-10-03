# idx_watcher — Watcher harian pengumuman IDX

Memindai halaman [Pengumuman IDX](https://www2.idx.co.id/id/berita/pengumuman/)
setiap hari, mendeteksi rilis laporan keuangan baru, dan mengunduh
`inlineXBRL.zip`-nya otomatis memakai mesin unduh yang sama dengan bulk
downloader (`firefox_bridge`).

## Cara kerja

1. Buka halaman Pengumuman lewat bridge Firefox (tanpa bypass; kalau kena
   blokir Cloudflare, keluar diam-diam dan serahkan ke `block_watch.py`).
2. Baca entri pengumuman dari `since` sampai hari ini (halaman diurut dari
   yang terbaru; jalan mundur per halaman sampai entri lebih tua dari `since`).
3. Filter judul mengandung `laporan keuangan` / `financial statement` /
   `financial report`.
4. Tiap emiten yang cocok: unduh semua kuartal terdeteksi via
   `download_all_detected` — file yang sudah valid otomatis di-skip
   (history + hash), jadi tidak ada duplikat.
5. Pengumuman **Koreksi**: kuartal yang dikoreksi diunduh ulang (file lama
   dibackup `.koreksi-DATE.bak` dulu, dikembalikan bila unduhan gagal).
6. Watermark tanggal + daftar pengumuman yang sudah diproses disimpan di
   `downloads-2026/watcher_state.json`.

## Pakai

```bash
cd idx-stock-xbrl
.venv/bin/python idx_watcher/announcement_watcher.py --year 2026 --dry-run   # pindai saja
.venv/bin/python idx_watcher/announcement_watcher.py --year 2026              # pindai + unduh
```

Opsi: `--since YYYY-MM-DD`, `--download-dir`, `--delay/--delay-max` (jeda acak
antar emiten, default 20–40 dtk), `--max-pages`.

## Jadwal

Cron harian `idx-2026-announcement-watch` (07:00 WIB): hanya melapor bila ada
unduhan/koreksi/gagal; diam bila tidak ada laporan baru atau saat jeda blokir.
