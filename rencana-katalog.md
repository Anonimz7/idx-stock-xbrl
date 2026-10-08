# Rencana Implementasi: Unduhan Berbasis Katalog IDX

Tanggal: 7 Oktober 2026 — **diperbarui 8 Oktober 2026**
Status: **langkah 0 dan 1 selesai**; langkah 2–4 masih rencana
Fokus: **putusan API sebagai sumber kebenaran** (§2.4), lalu langkah 3–4
Dokumen ini ditulis agar bisa dieksekusi ulang **tanpa konteks percakapan**.

> Legenda: **[TERVERIFIKASI]** = sudah dibuktikan lewat percobaan nyata,
> **[RENCANA]** = belum dikerjakan, **[PERLU KEPUTUSAN]** = menunggu pemilik.

---

## 0. Inti gagasan

Selama ini downloader bekerja dengan **inferensi negatif**:

```
URL disusun sendiri  →  404  →  "berarti laporan itu tidak ada"
```

Di situ ada lubang sunyi: **404 karena URL salah** dan **404 karena laporan
memang tidak ada** tidak bisa dibedakan. Keduanya menghasilkan catatan yang
identik dari luar, jadi kesalahan pola konstruksi bisa terlihat seperti
keberhasilan.

Gagasannya menukar inferensi negatif menjadi **bukti positif**:

```
ada di katalog  →  unduh URL persis yang diberikan File_Path
tidak ada       →  memang tidak ada, disimpulkan dari daftar,
                   bukan dari respons
```

Konsekuensi paling penting: begitu URL berasal dari katalog, **404 di atasnya
selalu anomali nyata** — layak diulang dan diselidiki — bukan sesuatu yang
harus ditebak maknanya.

### Jangan melebih-lebihkan manfaatnya [TERVERIFIKASI]

Dari run 554 percobaan: 514 sukses, 40 berstatus 404. Tiap 404 memakan
sekitar 8–9 detik (jeda + probe ±6 detik), jadi **±340 detik tersia-sia**.
Memindai katalog satu tahun butuh **±71 halaman ≈ 120 detik**.

Hemat bersih ≈ **4 menit dari 84 menit, kira-kira 5%.** Nyata tapi kecil.
Nilai sebenarnya adalah kualitas pengetahuan (§0 paragraf pertama), bukan
bandwidth. Kalau suatu saat ada yang mengklaim penghematan besar, angka di
atas adalah koreksinya.

---

## 1. Keadaan saat dokumen ini ditulis

### Run 785 — **SELESAI** (riwayat di bawah dipertahankan sebagai catatan cara kerja)

> **Status 8 Oktober 2026:** run **selesai** — 785/785 tercatat, **723 berkas**
> unduh + **62** gagal `404 Not Found`, 0 folder 2025 kosong, 0 file tersisa di
> staging, CAPTCHA tidak pernah muncul. Waktu ±7 menit setelah perbaikan jeda
> (§4 butir 13), dari sebelumnya ±45 menit.
>
> Karena itu peringatan **"JANGAN sentuh bridge sebelum selesai" tidak berlaku
> lagi** — bridge bebas dimatikan/dihidupkan (sudah terbukti aman, extension
> 0.1.8 menyambung sendiri). Yang **tetap dilarang** hanyalah **mengubah kode
> extension**: itu menuntut reload manual dan barulah sesi putus. Kueri PID dan
> cara jeda di bawah tetap berlaku untuk run berikutnya — **perintahnya tidak**:
> `--stocks-file` sudah digantikan `--catalog` (Langkah 4), jadi baris Perintah
> di bawah hanya riwayat.

| Item | Nilai |
| --- | --- |
| Perintah | `python -m firefox_bridge.instance.cli --stocks-file db\list_saham_remaining.csv --year 2025 --delay 2 --delay-max 5` |
| Dijalankan dari | `productions\` |
| Kode berjalan | **`98b9199`** — deteksi CAPTCHA + popup sudah terpasang |
| Status | **BERJALAN (ulang).** PID asli 9884 **mati** waktu server di-restart ±17:47 WIB; diganti shell background pada 17:49 WIB |
| Jeda | Dua kali: 16:04:35→16:31:12 (26,6 mnt) dan 17:05:29 sampai prosesnya mati |
| Jeda terakhir | 17:05:29 WIB, tepat setelah `VISI` dipindah (posisi 748/785) |
| Laju sejati | **9,1 dtk/saham** — lihat peringatan soal jeda di §4 butir 13 |
| Staging | `C:\Users\ORCA\Downloads\saham\staging` — 0 file saat bersih. **Bukan** `Downloads\instance\saham\staging` (folder itu tidak ada) |
| Log run | `C:\Users\ORCA\.local\share\opencode\shell\26cc3c667eb0b7ff2d59bec6f81d51cd5b73f44c\sh_115fb32df001O8zTCCF0Nuxu9v.out` |
| Log bridge | `…\sh_115f80a13001CryzVS9SOo6NMu.out` (port 8765) |

**PID-nya tidak diketahui** karena sekarang berjalan sebagai shell background.
Cari bila perlu menghentikan sementara:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*firefox_bridge.instance.cli*" } |
  Select-Object ProcessId, CommandLine
```

Progres berubah terus, jadi jangan menghafal angkanya — **ukur**, dari log:

```powershell
$i = Get-Item "<path Log run di tabel>"
"{0} byte, {1}" -f $i.Length, $i.LastWriteTime
```

Cara memastikan benar-benar berhenti: **ukuran log tidak berubah dalam 10
detik.** Pemeriksaan stempel waktu file saja tidak cukup; CPU hanya bisa
dicek bila PID-nya sudah diketahui.

Pembuktian jeda tidak berasal dari pemeriksaan staging (jalur staging yang
saya periksa pertama ternyata salah dan selalu "0", jadi tak efektif),
melainkan dari tiga hal ini sekaligus: baris log `Downloaded instance <kode>`,
file zip-nya ada utuh, dan `STEP JSON OK` sebelumnya. Ulangi dengan ketiganya.

**Lanjutkan** — mekanismenya tetap `NtResumeProcess`, hanya PID-nya yang
berubah (pakai hasil kueri di atas, `<PID>`):

```powershell
Add-Type -Namespace Win32 -Name R -MemberDefinition '[DllImport("ntdll.dll")] public static extern int NtResumeProcess(IntPtr h);'
[Win32.R]::NtResumeProcess((Get-Process -Id <PID>).Handle)
```

**Bila prosesnya sudah mati**, jalankan ulang perintah di tabel —
`download_history_<year>.json` menjamin tidak ada yang diunduh dua kali.

Bridge boleh dihidupkan ulang **tanpa** memutus run: extension 0.1.8
menyambung sendiri lagi, terbukti dari `STEP 0.5: extension 0.1.8 (siap)`
setelah bridge restart. Yang tetap dilarang adalah **mengubah kode
extension** — itu menuntut reload manual dan barulah sesi run putus.

### Sudah jadi

| Berkas | Isi |
| --- | --- |
| `idx_watcher/instance_catalog.py` | Pemindai halaman listing → katalog JSON, **tanpa unduhan** |
| `idx_watcher/README.md` | Cara pakai, format katalog, catatan pembatas |
| `idx_watcher/__init__.py` | Menjadikan `idx_watcher` paket (dibutuhkan mypy) |
| `tests/test_idx_watcher_instance_catalog.py` | 23 tes murni (parser, merge, paginasi) |

Commit: `dac41f7` (stop-on-404) → `c3c6998` (watcher) → `d4f52f1` (CSV) →
`236bc2f` (lewati 404) → `bb35936` (jeda hanya untuk yang menyentuh IDX) →
`da4b7c4` (katalog 890 entri dari API).

**Penting:** katalog dan downloader **belum tersambung**. Menjalankan watcher
saat ini menghasilkan data referensi; tidak ada satu pun keputusan unduhan
yang berubah karenanya.

### Cara pakai watcher yang sudah ada

```bash
python -m idx_watcher.instance_catalog --years 2025          # satu tahun
python -m idx_watcher.instance_catalog                        # semua tahun yang ditawarkan halaman
python -m idx_watcher.instance_catalog --years 2025 --dry-run # tanpa menulis
```

Keluaran: `db/instance_catalog_<year>.json` (satu berkas per tahun), kunci `TAHUN|EMITEN`:

```json
"2025|AADI": {
  "ticker": "AADI", "year": 2025, "period": "audit",
  "url": "https://www.idx.co.id/Portals/…/Laporan%20Keuangan%20Tahun%202025/Audit/AADI/instance.zip",
  "uploaded_at": "2026-03-06T15:57",
  "first_seen": "…", "last_seen": "…"
}
```

Aman diulang (idempoten). `uploaded_at` yang berubah pada run berikutnya =
emiten mengunggah ulang.

---

## 2. Temuan terverifikasi

### 2.1 API di balik halaman [TERVERIFIKASI]

```
GET https://www.idx.co.id/primary/ListedCompany/GetFinancialReport
    ?indexFrom=1
    &pageSize=12
    &year=2026
    &reportType=rdf
    &EmitenType=s
    &periode=tw1
    &kodeEmiten=
    &SortColumn=KodeEmiten
    &SortOrder=asc
→ 200 OK
```

Nilai parameter:

| Parameter | Nilai | Arti |
| --- | --- | --- |
| `reportType` | `rdf` / `rda` | `rdf` = Laporan Keuangan (punya `instance.zip`), `rda` = Laporan Tahunan |
| `EmitenType` | `s` / `o` | Saham / Obligasi |
| `periode` | `tw1` `tw2` `tw3` `audit` | `audit` = Tahunan |
| `tahun` | 2022–2026 | |
| `kodeEmiten` | kosong = semua, atau satu ticker | |
| `pageSize` | **`5000` sudah diuji** — mengembalikan seluruh 890 entri 2025/audit dalam **satu** permintaan, tanpa perlu `indexFrom` berikutnya | |

Bentuk respons (disalin dari respons sungguhan):

```json
{
  "Search": {"ReportType":"rdf","KodeEmiten":null,"Year":"2026",
             "SortColumn":"KodeEmiten","SortOrder":"asc",
             "EmitenType":"s","Periode":"tw1","indexfrom":1,"pagesize":12},
  "ResultCount": 851,
  "Results": [
    {
      "KodeEmiten": "AADI",
      "File_Modified": "2026-04-30T16:51:25.623",
      "Report_Period": "TW1",
      "Report_Year": "2026",
      "NamaEmiten": "PT Adaro Andalan Indonesia Tbk",
      "Attachments": [
        {
          "Emiten_Code": "AADI",
          "File_ID": "<uuid>",
          "File_Modified": "2026-04-30T16:51:25.623",
          "File_Name": "FinancialStatement-2026-I-AADI.pdf",
          "File_Path": "/Portals/0/StaticData/ListedCompanies/Corporate_Actions/New_Info_JSX/Jenis_Informasi/01_Laporan_Keuangan/02_Soft_Copy_Laporan_Keuangan//Laporan Keuangan Tahun 2026/TW1/AADI/FinancialStatement-2026-I-AADI.pdf",
          "File_Size": 873494,
          "File_Type": ".pdf",
          "Report_Period": "TW1",
          "Report_Type": "rdf",
          "Report_Year": "2026"
        }
      ]
    }
  ]
}
```

Dua keunggulan atas halaman yang dirender:

- `File_Modified` **presisi milidetik** (`2026-03-06T15:57:00.003`), bukan
  menit seperti cap waktu halaman (`06 Maret 2026 | 15:57`) — momennya sama,
  hanya API yang lebih teliti.
- lampiran `instance.zip` bisa disaring dari `Attachments` dengan `File_Name`,
  lalu `File_Path` diprefix `https://www.idx.co.id` → URL final

**Tetapi yang paling penting: API lengkap, halaman tidak** — lihat §2.4.

### 2.2 API itu TIDAK bisa dibaca lewat bridge [TERVERIFIKASI]

Percobaan yang sudah dilakukan:

1. Halaman listing **bisa** dibaca dari Firefox lewat bridge
   (`page_text` mengembalikan teks lengkap + href).
2. URL API dinavigasikan di tab yang sama → tab `status: complete`, URL benar.
3. `browser_page_text` **gagal** pada `max_chars` 200000, 60000, dan 20000.
4. `browser_snapshot` **gagal** juga.

Kesimpulan: content script ekstensi **tidak berjalan pada dokumen
`application/json`** — Firefox menampilkannya sebagai dokumen khusus.
firefox-bridge juga **tidak punya alat eksekusi JS** (11 tool-nya hanya:
`browser_status`, `browser_tabs`, `browser_open_tab`, `browser_navigate`,
`browser_snapshot`, `browser_page_text`, `browser_click`, `browser_fill`,
`browser_download`, `browser_activate_tab`, `browser_close_tab`).

**Akibatnya ongkos katalog ditentukan di sini.** Tanpa `evaluate`: 71 halaman
±120 detik. Dengan `evaluate`: satu permintaan, di bawah 1 detik.

Catatan: API ini ditemukan lewat rekam jaringan (`browser.network.list/get`)
pada **browser Chromium terpisah**, bukan Firefox — karena server `browser`
punya tool rekam jaringan sedangkan `firefox-bridge` tidak. Chromium berhasil
mendapat 200; jangan dijadikan bukti bahwa Firefox bisa.

### 2.3 Halaman listing [TERVERIFIKASI]

```
https://www.idx.co.id/id/perusahaan-tercatat/laporan-keuangan-dan-tahunan/
```

- Filter berupa **radio + tombol `Terapkan`**, dan **URL tidak ikut berubah**
  (klien-sisi). Nilai radio = nama aksesibilitasnya:
  `Laporan Keuangan`, `Laporan Tahunan`, `Saham`, `Obligasi`,
  `2026`…`2022`, `Triwulan 1`, `Triwulan 2`, `Triwulan 3`, `Tahunan`.
- **Jebakan label:** radio bertuliskan **`Tahunan`**, tetapi datanya
  **`Periode : Audit`**, dan path-nya `…/Audit/<EMITEN>/instance.zip`.
  Parser memakai nilai data, bukan label.
- Paginasi: `<select>` nomor halaman (1–71) ditambah tombol
  `Go to first page`, `Go to previous page`, `Go to next page`,
  `Go to last page`. **12 entri per halaman**, ±71 halaman per tahun.
  `Go to next page` punya `state.disabled` yang bisa dibaca.
- href sungguhan — **persis** pola yang disusun downloader sekarang:

  ```
  https://www.idx.co.id/Portals/0/StaticData/ListedCompanies/Corporate_Actions/
  New_Info_JSX/Jenis_Informasi/01_Laporan_Keuangan/02_Soft_Copy_Laporan_Keuangan//
  Laporan%20Keuangan%20Tahun%202025/Audit/AADI/instance.zip
  ```
- Bentuk teks per entri:

  ```
  AADI 06 Maret 2026 | 15:57 Nama : PT Adaro Andalan Indonesia Tbk
  Tahun : 2025 Periode : Audit … instance.zip
  ```

  Nama bulan **ditulis lengkap** (`Februari`, `Maret`), bukan `Feb`/`Mar`.
  Cap waktu itulah **saat unggah**, presisi menit.

### 2.4 Halaman daftar TIDAK lengkap — pakai API [TERVERIFIKASI]

> **Putusan: anggap API sebagai satu-satunya sumber kebenaran.**

Halaman daftar **menjatuhkan baris yang sebenarnya ada.** Terbukti pada
8 Oktober 2026 untuk tahun 2025/audit:

| Sumber | Jumlah | Selisih |
| --- | --- | --- |
| API `GetFinancialReport` (`pageSize=5000`) | **890** | acuan |
| Pemindaian halaman (74 halaman penuh) | 888 | **−2** |
| Berkas nyata di `Downloads\instance\saham` | **890** | 0 |

- API − halaman = `['ZONE', 'ZYRX']`; halaman − API = kosong.
- Keduanya **ada**, diverifikasi manual di halaman profil masing-masing:
  `…/profil-perusahaan-tercatat/ZONE` menampilkan *Laporan Keuangan Tahun 2025,
  Periode Audit*, lengkap dengan `instance.zip`.
- `label_delisted` keduanya `0`, jadi bukan karena sudah delisted.

**Ini bukan bug paginasi di sisi kita.** Pemindaian memang setia pada yang
disajikan halaman: 74 halaman, semuanya penuh 12 baris, `state.disabled` tombol
`Go to next page` `true` di halaman terakhir, footer menulis `dari 74` (=888),
dan kedua kode tidak muncul di satu pun dari 888 baris itu. Halamannya memang
menyajikan 888; API menyajikan 890.

**Konsekuensi:** `--source page` tidak boleh dipakai sebagai sumber kelengkapan.
Ia tetap berguna untuk membaca halaman, tetapi angka kelengkapannya tidak bisa
diandalkan.

---

## 3. Rencana empat langkah

```
0. Selesaikan run 785              ← SELESAI 8 Okt: 785/785, 723 unduh + 62 gagal 404
1. Cocokkan kode 404 vs katalog    ← SELESAI 8 Okt: 0 dari 62 ada di API
2. Endpoint `evaluate` di bridge   ← GUGUR 8 Okt (tak wajib, lihat Langkah 3)
3. Mode `--source api` di watcher  ← SELESAI 8 Okt: 890 entri, setara katalog halaman
4. Downloader membaca katalog      ← SELESAI 8 Okt: --catalog menggantikan --stocks-file
```

Empat dari lima baris di atas sudah selesai, satu gugur — **tidak ada lagi
yang mengantre.**

**Jangan mengerjakan langkah 2 sebelum langkah 0 selesai.** Menambah endpoint
`evaluate` mengubah ekstensi, dan ekstensi yang diubah wajib di-*reload* di
`about:debugging`. Saat reload, content script semua tab ikut mati dan
`_tab_id` yang dipegang downloader jadi tidak berlaku — run akan rusak di
tengah-tahap.

### Langkah 0 — selesaikan run 785

Jalankan perintah resume di §1. Selesai ketika log menunjukkan selesai dan
`785/785` tercatat. Kegagalan `404 Not Found` yang tersisa itu wajar —
sekitar ±40-an dari 785.

### Langkah 1 — cocokkan kode 404 terhadap katalog [SELESAI 8 Okt 2026]

**Hasil: 62 kode 404, nol di antaranya ada di katalog → pola konstruksi bersih.**

| Pemeriksaan | Angka |
| --- | --- |
| Kode berstatus `404 Not Found` (2025, di CSV 785) | **62** |
| yang ternyata ada di API | **0** |
| yang benar tidak ada di API | **62** |
| `File_Path` API vs URL yang kita susun, seluruh katalog | **890 identik, 0 berbeda** |

Uji terakhir itu yang paling kuat: seluruh 890 baris `File_Path` dari API
**identik string** dengan URL yang tersimpan di `download_history_<year>.json`.
Jadi bukan hanya "unduhannya berhasil", tetapi URL-nya memang sama dengan
yang IDX sendiri catat.

Ongkos: ±3 menit scan halaman + ±2 menit pencocokan, tanpa satu pun unduhan.

Metode langkahnya (dipakai, lalu digantikan API — lihat §2.4):

1. `python -m idx_watcher.instance_catalog --years 2025`
2. Ambil semua kode berstatus `404 Not Found` dari
   `C:\Users\ORCA\Downloads\instance\saham\download_history_<year>.json` (field
   `failed_at` + `reason == "404 Not Found"`).
3. Cocokkan: `kunci = f"2025|{KODE}"`.

Keputusan:

| Hasil | Makna | Tindakan |
| --- | --- | --- |
| Tidak ada satu pun kode 404 yang ada di katalog | Pola konstruksi bersih, 404 memang artinya tidak ada laporan | Lanjut ke langkah 3–4 dengan yakin ← **kasus ini, 62/62** |
| Ada yang **ada di katalog** tapi tetap 404 | **Pola URL kita salah** untuk emiten itu | Selidiki `File_Path` mereka; ini temuan severity tinggi, bukan bug kecil |

### Langkah 2 — endpoint `evaluate` di bridge **[GUGUR — 8 Okt 2026]**

Dibatalkan karena **tidak lagi diperlukan**, bukan karena gagal.

Dasarnya: uji langsung membuktikan Python **tidak bisa** menembus API tanpa
peramban —

```
httpx.get(GetFinancialReport, headers=<browser>)  →  HTTP 403 text/html
httpx.get(GetFinancialReport, tanpa header)       →  HTTP 403 text/html
```

403 itu berarti pengambilan **harus** dari dalam peramban yang sudah lolos
Cloudflare. Ada tiga jalur yang memungkinkan; pemilik memilih yang ketiga:
**unduh `GetFinancialReport.json` secara manual**, lalu program hanya
**membaca berkas itu**.

```
1. evaluate di firefox-bridge   -> perlu ekstensi berubah + reload   [ditolak]
2. evaluate di browser Chromium -> perlu sesi IDX terpisah           [ditolak]
3. baca file JSON hasil unduhan -> nol kode koneksi                  [DIPILIH]
```

Konsekuensinya, dua `[PERLU KEPUTUSAN]` yang sebelumnya mengantre di
belakang langkah ini ikut gugur, keduanya sudah dihapus dari teks:

- **Keamanan `evaluate`** — moot. Tidak ada endpoint yang dieksekusi,
  `firefox_bridge/app.py` tidak disentuh.
- **Versi ekstensi 0.1.8 → naik** — moot. `extension/` tidak disentuh,
  jadi langkah verifikasi `task.md` §13 tidak perlu diulang.

Semua `[PERLU KEPUTUSAN]` kini sudah terjawab. Dua di atas gugur bersama
Langkah 2; satu lagi di Langkah 4 dijawab pemilik pada 8 Okt 2026 dengan
**menggantikan sepenuhnya** — `--stocks` / `--stocks-file` dicabut dari
program instance, `--catalog` menjadi satu-satunya sumber daftar.

Satu koreksi atas yang tertulis di sini: Langkah 4 tidak menyentuh
`task.md` §6. Setelah diperiksa, `task.md` tidak pernah menyebut `instance`
— CLI-004 menerangkan `firefox-bridge-download` (yang memang menerima
`--stocks` / `--stocks-file` lewat config file), dan program itu tidak
diubah. Kontrak yang berubah tidak tercatat di task.md, hanya di README.

Sisa desain endpoint dipertahankan di riwayat git bila suatu saat
dibutuhkan lagi; tidak ada pekerjaan yang hilang, hanya ditunda.

> Perlu diingat: pilihan ini menjadikan **kualitas file JSON menjadi
> tanggung jawab manusia**. Kalau file itu basi, katalog ikut basi — tidak
> ada yang memverifikasi kesegarannya otomatis. Lihat jebakan §4 butir 18.

### Langkah 3 — mode `--source api` pada watcher **[SELESAI 8 Okt 2026]**

Selesai tanpa menyentuh bridge, ekstensi, maupun server — jalurnya jadi
murni berkas, karena langkah 2 gugur.

`idx_watcher/instance_catalog.py` kini punya dua sumber:

```
--source page   (default, jalur lama, tetap dipertahankan)
--source api    (baru, wajib disertai --api-file)
--api-file PATH (file JSON hasil unduhan manual dari GetFinancialReport)
```

Mode `api` mengimpor `Results` → untuk tiap entri memilih lampiran
`File_Name == "instance.zip"`, lalu:

- `url` = `https://www.idx.co.id` + `File_Path`, spasi diganti `%20`
  supaya bentuknya identik dengan URL halaman;
- `uploaded_at` = `File_Modified` **dipotong ke menit** — API memberi
  presisi milidetik, halaman menit, dan satu kolom tidak boleh
  bercampur dua format;
- `period` = `Report_Period` huruf kecil, bukan label yang ditulis tetap;
- kunci = `f"{tahun}|{KODE}"`, sama dengan katalog halaman.

Penggabungan memakai `merge_entries` yang **sudah lama diuji** — mode `api`
tidak menulis ulang logika merge, hanya memasok bahan.

Mode `api` **bukan pengganti** mode `page`: API bisa berubah sewaktu-waktu
tanpa pemberitahuan, sedangkan halaman adalah jalur yang sudah terbukti.
Mode `page` dipertahankan sebagai fallback dan untuk verifikasi silang.

**Sifat merge = tambah-saja, tidak pernah hapus** (konsisten dengan mode
halaman). Karena itu entri yang hilang dari API tetap diam di katalog.
Ini disengaja; kalau suatu saat perlu penyusutan, itu keputusan terpisah.

**Bukti jalan** (8 Okt 2026, terhadap `GetFinancialReport.json` asli 890
baris dan katalog yang sudah berisi 890 entri):

```
$ python -m idx_watcher.instance_catalog --source api \
      --api-file C:\Users\ORCA\Downloads\GetFinancialReport.json --dry-run
[15:25:22] membaca 890 entri lama dari ...\db\instance_catalog_*.json
[15:25:22] API: 890 entri dari GetFinancialReport.json (0 baru, 0 berubah)
[15:25:22] DRY RUN: 890 entri terbaca, 0 baru, 0 berubah; katalog tidak ditulis
EXIT = 0
```

890/890 terbaca dan **nol berubah** — artinya parser API menghasilkan
entri yang persis sama dengan yang sudah ada di katalog. Itu uji
kesetaraan, bukan sekadar "tidak error". 19 tes baru mengunci kontraknya.

**Gerbang setelah perubahan ini (8 Okt 2026):** pytest **631 tes / 36
file, exit 0** (naik dari 612); ruff **14** dan mypy gate **6** — persis
baseline, nol dari file yang disentuh; `mypy idx_watcher` **Success**.

### Langkah 4 — downloader membaca katalog [SELESAI 8 Okt 2026]

`firefox_bridge/instance/cli.py` tidak lagi menerima `--stocks` /
`--stocks-file`. Daftar datang dari `--catalog`, default
`db/instance_catalog_<year>.json` (diselesaikan dari `--year`) — path yang sama
dengan yang ditulis `idx_watcher`, sehingga perintah tanpa argumen pun
menemukannya.

Butir per butir terhadap rencana awal:

1. **Entri tahun yang diminta menjadi pekerjaannya** — lewat modul baru
   `firefox_bridge/instance/catalog.py`, `load_entries()` mengembalikan
   `(kode, url)` terurut. Terurut karena katalog berbentuk dict, jadi
   urutannya ikut urutan merge yang berubah tiap scan; jalur yang stabil
   membuat run lanjutan terbaca sama dengan run yang ia lanjutkan.
2. **Aturan anti-duplikat tidak berubah** — file + hash + entri history.
3. **`entry["url"]` dipakai apa adanya.** `download_instance()` kini
   menerima `href` sebagai parameter **wajib** posisi keempat dan tidak
   lagi pernah membangun URL. Vektor tebaknya ditutup di tandatangan, bukan
   di komentar: parameter berarti default, dan pemanggil yang lupa akan
   diam-diam kembali menebak sambil tetap lolos tes.
4. **404 = anomali.** Blok `is_definitive_reason` yang dulu memensiunkan
   saham dihapus. Alasannya sudah tidak ada: aturan itu dibuat untuk "3%
   daftar tidak punya laporan", dan populasi itulah yang dihapus katalog
   (890/890 ada). Dampaknya dua. Pertama, 404 ditanya ulang pada run
   berikutnya. Kedua — yang tidak kalah penting — **laporan anomali tetap
   hidup**: skip dikembalikan sebagai `STATUS_SKIPPED` yang tak pernah
   masuk daftar kegagalan, jadi aturan lama membuat run kedua dan
   seterusnya *diam* tentang satu-satunya hal yang memang harus ia
   laporkan.
5. **Dua angka di akhir run** — `KATALOG: N entri diminta, X beres, Y
   gagal`, ditambah `ANOMALI: n entri katalog dijawab 404 Not Found` bila
   ada. Nol pun tetap dicetak: laporan yang hanya bicara saat ada masalah
   adalah laporan yang berhenti dibaca.

**Keputusan pemilik**: *menggantikan sepenuhnya*. `--stocks` /
`--stocks-file` dicabut dari program instance. `task.md` §6 ternyata tidak
perlu diubah — setelah diperiksa, `task.md` tidak pernah menyebut
`instance` sama sekali; CLI-004 menerangkan `firefox-bridge-download`,
yang tidak disentuh (lihat koreksi di §3 atas).

**Bukti**: `--dry-run` terhadap katalog sungguhan menghasilkan
`890 entri tahun 2025` → `0 akan diunduh, 890 dilewati`, tanpa peringatan
drift (890/890 URL katalog cocok dengan pola terbitan `instance_url`).
643 tes lulus; ruff 0 pada file yang disentuh; mypy 6 (baseline).

---

## 4. Jebakan yang sudah diketahui

Semua ini pernah memakan waktu dalam sesi ini. Jangan diulangi.

1. **Timestamp di `download_history_<year>.json` adalah UTC** (`+00:00`), sedangkan
   jam lokal WIB (UTC+7). `08:45:10+00:00` = 15:45 WIB. Ini sempat membuat
   run tampak "jalan 8 jam" padahal 1 jam; dan membuat ETA salah dibaca.
   Selalu konversi sebelum membandingkan dengan log atau `Get-Date`.
2. **`client.managed_tab(...)` menghasilkan info dict, bukan id.**
   Benar: `with client.managed_tab(url, active=False) as info: tab_id = info.get("id")`.
   Salah: `... as tab_id`.
3. **`signal.SIGSTOP` TIDAK ADA di Windows.** `rest_manager.py` memakai
   `os.kill(pid, signal.SIGSTOP)` untuk rehat otomatis — itu akan
   `AttributeError` di Windows. Pengganti yang terbukti:
   `NtSuspendProcess` / `NtResumeProcess` dari `ntdll` lewat P/Invoke.
4. **`python -c "..."` dengan here-string PowerShell untuk `git commit -m`
   pecah** menjadi banyak pathspec. Pakai `git commit -F <file>`.
5. **`Get-ChildItem -Directory ... -File`** — filter itu saling eksklusif,
   hasilnya kosong.
6. **`Select-Object -First N` yang memotong pipeline** membuat
   `$LASTEXITCODE = -1`. Itu artefak, bukan kegagalan.
7. **Tab run tidak boleh disentuh.** Run memegang satu tab sendiri
   (saat itu id 5). Untuk penyelidikan, buka tab terpisah dengan
   `browser_open_tab`; jangan `browser_navigate` pada tab run.
   Saat itu `browser_open_tab` sempat membingungkan: tab run terlihat
   berpindah lalu kembali sendiri — baca ulang sebelum menyimpulkan.
8. **`navigate` bersifat fire-and-forget.** `page_text` sesudahnya masih
   membaca dokumen lama. Polling sampai isinya berubah; jangan baca sekali.
9. **Ref snapshot berlaku per snapshot.** Setelah navigasi/klik, snapshot
   ulang untuk mendapat ref baru.
10. **`(?i:...)` bersifat non-capturing.** Untuk menangkap grup sekaligus
    case-insensitive, tulis `(?i:(...))`. Salah ini membuat pesan
    `not enough values to unpack`.
11. **`mypy` konfigurasi proyek hanya memeriksa `files = ["firefox_bridge"]`.**
    Gate-nya `mypy` tanpa argumen = **6 error pre-existing**. `mypy .`
    melaporkan ±89 error di `tests/` dan itu bukan gate.
12. **`idx_watcher` harus punya `__init__.py`.** Tanpanya, mypy melihat
    `instance_catalog` dan `idx_watcher.instance_catalog` sebagai dua nama
    modul untuk file yang sama dan berhenti memeriksa apa pun.
13. **Jeda buatan membuang perhitungan laju dan ETA.** Setelah run dijeda
    26,6 menit, laju yang dihitung dari selisih stempel pertama–terakhir
    melompat dari **9,1 → 11,9 dtk/saham** dan ETA meleset ±10 menit,
    padahal laju kerjanya tidak berubah sama sekali. Kalau run pernah
    dijeda, kurangi durasi jeda sebelum menghitung. Perintahnya:
    ```python
    PAUSE_START, PAUSE_END = sec('2026-10-07T09:04:35'), sec('2026-10-07T09:31:12')
    kerja = total - (PAUSE_END - PAUSE_START)
    laju  = kerja / jumlah_entri
    ```
14. **Lokasi staging adalah `C:\Users\ORCA\Downloads\saham\staging`.**
    `C:\Users\ORCA\Downloads\instance\saham\staging` **tidak ada**. Memeriksa
    jalur yang salah membuat pemeriksaan "0 file" selalu terpenuhi dan
    menjadi tidak bermakna — persis yang terjadi saat memverifikasi jeda.
15. **`text()` tidak pernah memuat `aria-label`.** Endpoint itu mengembalikan
    `innerText`, sehingga elemen yang menamai dirinya lewat atribut — seperti
    kotak Cloudflare `aria-label="Verify you are human"` — sama sekali tak
    terbaca di sana. Yang membacanya adalah `snapshot()`, yang mengisi `name`
    dari `nameFor`; terbukti 39 dari 40 elemen halaman IDX punya `name`.
    Karena itu deteksi CAPTCHA menanyakan kedua sumber, dan sebuah halaman
    baru dinyatakan bersih kalau keduanya sepakat. Menanyakan satu sumber
    saja adalah asal mula bug `200 (file exists)` yang membuat enam saham
    tercatat punya arsip padahal yang menjawab 200 adalah halaman challenge.
16. **Halaman daftar `laporan-keuangan-dan-tahunan/` TIDAK lengkap — jangan
    dijadikan acuan kelengkapannya.** Untuk 2025/audit halaman menyajikan 888
    baris, API menyajikan 890, selisihnya `ZONE` dan `ZYRX`. Keduanya benar-benar
    ada (diverifikasi di halaman profil perusahaan masing-masing). Paginasi kita
    sendiri sudah benar — 74 halaman penuh, tombol next benar-benar mati. Detail
    di §2.4. **Acuan: API.**
17. **Akar berkas ada dua, dan yang satu nyaris kosong.** `Downloads\saham`
    berisi **1 folder** (hanya `staging`); semua arsip ada di
    `Downloads\instance\saham` (**890 folder**). Memeriksa akar yang salah
    membuat kesimpulan "belum diunduh" membengkak jadi 167 emiten — persis
    yang terjadi saat mencocokkan katalog. Selalu pakai
    `firefox_bridge.instance.paths.instance_download_dir()`, dan
    `stock_year_complete(..., download_dir=root)` wajib diberi `root` secara
    eksplisit.
18. **Jalur API tidak bisa dijalankan Python sendiri — dan kini bergantung
    pada manusia.** Uji langsung `httpx.get(GetFinancialReport)` dengan header
    peramban maupun tanpa header sama sekali **keduanya 403** (halaman blokir
    Cloudflare), jadi pengambilan wajib lewat peramban. Jalur yang dipakai
    kini: file diunduh manual, program hanya membaca (§3 Langkah 3). Efek
    sampingnya **belum diuji**: tidak ada yang memeriksa apakah
    `GetFinancialReport.json` masih segar. File basi → katalog ikut basi,
    dan `--source api` tetap akan melapor "0 berubah" dengan nada sukses.
    Sebelum memakai hasilnya, cocokkan tanggal file terhadap
    `File_Modified` terbaru di dalamnya.

---

## 5. Gate dan baseline

Semua angka di bawah **diukur ulang 8 Oktober 2026** (aslinya 7 Okt setelah
commit `d4f52f1`); angka tidak bergerak sejak saat itu selain jumlah tes.

```bash
.venv\Scripts\python.exe -m pytest -q                 # seluruh suite, hijau
.venv\Scripts\python.exe -m pytest --collect-only -q   # ringkasan per file, untuk menghitung jumlah tes
.venv\Scripts\python.exe -m ruff check idx_watcher tests/test_idx_watcher_instance_catalog.py
.venv\Scripts\python.exe -m ruff check firefox_bridge/instance tests/test_instance_*.py
.venv\Scripts\python.exe -m ruff check .              # 14 error pre-existing
.venv\Scripts\python.exe -m mypy                      # 6 error pre-existing (ini gate-nya)
.venv\Scripts\python.exe -m mypy idx_watcher tests/test_idx_watcher_instance_catalog.py
```

> Catatan pengukuran: `pytest -q` di konfigurasi ini **tidak mencetak baris
> ringkasan** — cukup periksa exit code (0 = hijau) atau hitung lewat
> `--collect-only -q`. Menyimpulkan jumlah tes dari keluaran `pytest -q` yang
> terpotong `Select-Object -Last` pernah menghasilkan angka salah.

| Gate | Nilai | Catatan |
| --- | --- | --- |
| pytest | hijau | **643 tes** di 37 file (42 di antaranya milik `idx_watcher`); 631 sebelum Langkah 4, 612 saat baseline 8 Okt pagi, 588 saat baseline 7 Okt |
| ruff file baru | **0** | wajib tetap 0 — kini juga `instance/catalog.py` dan `tests/test_instance_catalog.py` |
| ruff penuh | **14** | pre-existing (`cli.py`, `client.py`, `session.py`, `rest_manager.py`, 3 file tes). Turun dari baseline 17 karena 3 error ikut terhapus bersama `announcement_watcher.py` |
| mypy gate | **6** | pre-existing di `firefox_bridge/cli.py`. Tidak boleh naik |
| mypy `idx_watcher` | **0** | Success; menangkap `int(raw_year)` yang bertipe `Any \| None` saat Langkah 3 ditulis |
| mypy `.` | ±90 | bukan gate, ada di `tests/` |

---

## 6. Definisi selesai

- [x] Run 785 mencapai 785/785 tanpa kerusakan — **8 Okt: 723 unduh +
      62 gagal `404 Not Found`, 0 folder kosong, 0 file tersisa di staging**
- [x] Kode 404 sudah dicek terhadap katalog, hasilnya didokumentasikan
      (§3 langkah 1) — **62/62 tidak ada di API; 890/890 URL identik**
- [x] Mode `--catalog` menolak mengunduh di luar katalog, dan 404 di atas
      URL katalog dilaporkan sebagai anomali, bukan "tidak ada laporan" —
      **8 Okt: `--stocks` / `--stocks-file` dicabut, `href` jadi parameter
      wajib `download_instance()` sehingga jalur unduh tidak punya cara
      membangun URL; blok `is_definitive_reason` dihapus agar 404 ditanya
      ulang dan tetap terhitung di laporan; `--dry-run` katalog sungguhan
      890/890 dilewati tanpa drift**
- [x] `--source api` berjalan dan **setara** dengan katalog halaman —
      **8 Okt: `--source api --dry-run` melapor 890 dibaca / 0 baru /
      0 berubah** terhadap katalog berisi 890 entri. 19 tes mengunci
      parsernya, `mypy idx_watcher` Success.
- [x] `--source page` ≤ `--source api`, selisihnya bisa dijelaskan
      per entri (§2.4) — **sudah dibuktikan: 888 vs 890, sisanya `ZONE`
      dan `ZYRX`**. Keduanya kini ikut di katalog lewat `--source api`.
      Catatan: keduanya memang **tidak** akan identik, jadi ini bukan
      syarat bahwa angkanya cocok, hanya bahwa selisihnya terbaca.
- [ ] Endpoint `evaluate` — **GUGUR** (langkah 2 dibatalkan, pemilik memilih
      membaca file unduhan manual). Dicoret sebagai hal yang tidak perlu
      dikerjakan, bukan sebagai selesai.
- [x] Gate §5 tidak bergerak ke arah yang salah — **gerbang 8 Okt setelah
      Langkah 4: 643 tes, ruff 14, mypy 6, mypy idx_watcher 0; tidak
      bergerak.**
- [ ] Ekstensi 0.1.9 diverifikasi ulang — **tidak berlaku**, ekstensi tidak
      berubah (masih 0.1.8), langkah ini ikut gugur bersama langkah 2.

## 7. Di luar rencana ini

- Bug `quarter_from_title` pada watcher lama — **tidak relevan lagi**, kodenya
  sudah dihapus bersama `announcement_watcher.py` pada commit `c3c6998`.
- `rest_manager.py` — rehat otomatisnya tidak berfungsi di Windows (§4 butir 3).
  Diperbaiki hanya kalau diminta.
- Mengunduh `inlineXBRL.zip` — tidak diminta; watcher hanya mencatat
  `instance.zip` laporan tahunan.
