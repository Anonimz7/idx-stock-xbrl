# idx_watcher — katalog URL `instance.zip`

Modul ini memantau halaman resmi IDX **Laporan Keuangan dan Tahunan** dan
mengumpulkan **URL `instance.zip` laporan tahunan** beserta **waktu IDX
mengunggahnya**. Hasilnya disimpan ke katalog JSON.

Watcher ini **tidak mengunduh apa pun**. Unduhan dilakukan pada waktu
terpisah, dari katalog itu.

## Alamat

```
https://www.idx.co.id/id/perusahaan-tercatat/laporan-keuangan-dan-tahunan/
```

## Cara pakai

```bash
# dari productions/, dengan bridge Firefox sudah berjalan
.venv\Scripts\python.exe -m idx_watcher.instance_catalog                 # semua tahun
.venv\Scripts\python.exe -m idx_watcher.instance_catalog --years 2025    # satu tahun
.venv\Scripts\python.exe -m idx_watcher.instance_catalog --years 2025,2026 --dry-run
```

| Opsi | Arti |
| --- | --- |
| `--years 2025,2026` | tahun yang dipindai; default semua tahun yang ditawarkan halaman |
| `--output PATH` | katalog tujuan (default `db/instance_catalog.json`) |
| `--max-pages N` | batas halaman per tahun, pengaman kalau paginasi nakal |
| `--delay` / `--delay-max` | jeda acak antar halaman (detik) |
| `--dry-run` | pindai dan laporkan tanpa menulis apa pun |

Pemindaian membuka **tabnya sendiri** lewat `managed_tab`, jadi aman dijalankan
bersamaan dengan sesi unduh instance yang memakai tab lain.

## Format katalog

```json
{
  "source": "https://www.idx.co.id/id/perusahaan-tercatat/laporan-keuangan-dan-tahunan/",
  "updated_at": "2026-10-07T10:15:00+07:00",
  "entries": {
    "2025|AADI": {
      "ticker": "AADI",
      "year": 2025,
      "period": "audit",
      "url": "https://www.idx.co.id/Portals/…/Laporan%20Keuangan%20Tahun%202025/Audit/AADI/instance.zip",
      "uploaded_at": "2026-03-06T15:57",
      "first_seen": "2026-10-07T10:15:00+07:00",
      "last_seen": "2026-10-07T10:15:00+07:00"
    }
  }
}
```

* Kunci entri `TAHUN|EMITEN`.
* `uploaded_at` — saat unggah menurut halaman, presisi **menit** (lihat catatan
  di bawah).
* `first_seen` / `last_seen` — saat entri ini pertama dan terakhir dilihat
  watcher; `last_seen` yang berubah tanpa disertai perubahan `url` berarti
  laporan masih berada di daftar.
* Tulisannya atomik: file `.tmp` lalu ditukar, jadi katalog tidak pernah
  tersimpan setengah jadi.

## Mengapa halaman ini, bukan halaman pengumuman

Pemilik lama modul ini (`announcement_watcher.py`) membaca halaman pengumuman
dan menebak periode dari judul bebas. Modul pengujianannya sendiri menunjukkan
`quarter_from_title` salah pada **7 dari 15** judul, dan "laporan tidak ada"
disebut lewat inferensi 404. Halaman ini menutup seluruh celah itu:

| Halaman pengumuman (lama) | Halaman laporan (baru) |
| --- | --- |
| Periode ditebak dari judul | `Tahun : 2025`, `Periode : Audit` — field terstruktur |
| "Tidak ada laporan" disimpulkan dari 404 | Ketidakhadiran di daftar = tidak ada laporan |
| URL instance disusun sendiri | `File_Path` sudah berupa URL final |
| Watermark dari tanggal pengumuman | Waktu unggah per laporan |

## Catatan pembatas

* **Periode.** Label radio tertulis `Tahunan`, tetapi nilai datanya `Audit`
  (`Periode : Audit`, dan `…/Audit/<EMITEN>/instance.zip`). Parser memakai
  nilai data, bukan labelnya.
* **API di balik halaman.** Ada `GET /primary/ListedCompany/GetFinancialReport`
  yang mengembalikan JSON lengkap, termasuk `File_Modified` presisi detik.
  Endpoint itu **tidak bisa dibaca lewat jembatan Firefox**: content script
  ekstensi tidak berjalan pada dokumen `application/json`, dan jembatan tidak
  punya alat eksekusi JS. Karena seluruh proyek melewati peramban agar lolos
  Cloudflare, watcher memakai halaman HTML yang dirender — sebab itu waktu
  unggah presisi menit, bukan detik.
* **Batas halaman.** 12 entri per halaman; tahun penuh kira-kira 71 halaman.
  Bila halaman tidak bergerak setelah tombol berikutnya di-klik, pemindaian
  **berhenti dengan galat** alih-alih menulis katalog yang diam-diam tidak
  lengkap.
* **Tahun default adalah semua.** Untuk pemantauan harian, batasi dengan
  `--years <tahun berjalan>` supaya tidak memindai ulang arsip.

## Pengujian

```bash
.venv\Scripts\python.exe -m pytest tests/test_idx_watcher_instance_catalog.py
```

Tesnya murni (parser, merge, paginasi) — tidak membutuhkan peramban maupun
bridge.
