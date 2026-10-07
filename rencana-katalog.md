# Rencana Implementasi: Unduhan Berbasis Katalog IDX

Tanggal: 7 Oktober 2026
Status: **rencana** — belum ada baris kode yang mengerjakan langkah 3–5
Fokus sesaat: **selesaikan run 785 yang tertunda** (lihat §1)
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

### Run 785 — masih berjalan; JANGAN sentuh bridge sebelum selesai

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
`download_history.json` menjamin tidak ada yang diunduh dua kali.

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

Commit: `dac41f7` (stop-on-404) → `c3c6998` (watcher) → `d4f52f1` (CSV).

**Penting:** katalog dan downloader **belum tersambung**. Menjalankan watcher
saat ini menghasilkan data referensi; tidak ada satu pun keputusan unduhan
yang berubah karenanya.

### Cara pakai watcher yang sudah ada

```bash
python -m idx_watcher.instance_catalog --years 2025          # satu tahun
python -m idx_watcher.instance_catalog                        # semua tahun yang ditawarkan halaman
python -m idx_watcher.instance_catalog --years 2025 --dry-run # tanpa menulis
```

Keluaran: `db/instance_catalog.json`, kunci `TAHUN|EMITEN`:

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
| `pageSize` | belum diuji batas atasnya | |

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

- `File_Modified` **presisi detik**, bukan menit
- lampiran `instance.zip` bisa disaring dari `Attachments` dengan `File_Name`,
  lalu `File_Path` diprefix `https://www.idx.co.id` → URL final

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

---

## 3. Rencana empat langkah

```
0. Selesaikan run 785              ← fokus saat ini, 35 menit
1. Cocokkan 40 kode 404 vs katalog ← diagnostik termurah, menjawab "benarkah tidak ada?"
2. Endpoint `evaluate` di bridge   ← MENGHENTIKAN RUN; kerjakan setelah 0
3. Mode `--source api` di watcher  ← katalog jadi murah
4. Downloader membaca katalog      ← metode baru aktif
```

**Jangan mengerjakan langkah 2 sebelum langkah 0 selesai.** Menambah endpoint
`evaluate` mengubah ekstensi, dan ekstensi yang diubah wajib di-*reload* di
`about:debugging`. Saat reload, content script semua tab ikut mati dan
`_tab_id` yang dipegang downloader jadi tidak berlaku — run akan rusak di
tengah-tahap.

### Langkah 0 — selesaikan run 785

Jalankan perintah resume di §1. Selesai ketika log menunjukkan selesai dan
`785/785` tercatat. Kegagalan `404 Not Found` yang tersisa itu wajar —
sekitar ±40-an dari 785.

### Langkah 1 — cocokkan 40 kode 404 terhadap katalog [TERVERIFIKASI langkahnya]

Tujuan: menjawab pertanyaan yang selama ini tidak bisa dijawab —
***apakah benar-benar tidak ada laporan, atau pola URL kita yang salah?***

1. `python -m idx_watcher.instance_catalog --years 2025`
2. Ambil semua kode berstatus `404 Not Found` hari ini dari
   `C:\Users\ORCA\Downloads\instance\saham\download_history.json` (field
   `failed_at` + `reason == "404 Not Found"`).
3. Cocokkan: `kunci = f"2025|{KODE}"`.

Keputusan:

| Hasil | Makna | Tindakan |
| --- | --- | --- |
| Tidak ada satu pun dari 40 yang ada di katalog | Pola konstruksi bersih, 404 memang artinya tidak ada laporan | Lanjut ke langkah 3–4 dengan yakin |
| Ada yang **ada di katalog** tapi tetap 404 | **Pola URL kita salah** untuk emiten itu | Selidiki `File_Path` mereka; ini temuan severity tinggi, bukan bug kecil |

Ongkos: ±2 menit, satu tahun, tanpa unduhan.

### Langkah 2 — endpoint `evaluate` di bridge [RENCANA]

Satu-satunya penghalang agar API bisa dipakai (§2.2).

**Server** — rute baru di `firefox_bridge/app.py` (FastAPI, sudah ada
`FastAPI(...)` di baris 64):

```
POST /api/v1/tabs/{tab_id}/evaluate
body: { "expression": str, "await": bool = true, "timeout": float = 30 }
→ { "ok": bool, "value": any, "error": str | null }
```

**Ekstensi** (`extension/`): handler baru yang mengeksekusi `expression`
di dunia halaman (**MAIN world**, bukan isolated world), agar `fetch`
memakai origin dan cookie halaman termasuk clearance Cloudflare. Hasilnya
dikembalikan lewat kanal messaging yang sudah ada.

**Client** (`firefox_bridge/client.py`): metode `evaluate(tab_id, expression,
*, await_=True, timeout=...)`.

**Uji pertama** (urutannya penting, gagal berurutan = salah asumsi):

1. `evaluate` `"document.title"` → judul halaman
2. `evaluate` `"location.href"` → URL halaman
3. `evaluate` pada tab yang sudah di halaman listing:
   ```js
   fetch('/primary/ListedCompany/GetFinancialReport?indexFrom=1&pageSize=5000&year=2025&reportType=rdf&EmitenType=s&periode=audit&kodeEmiten=&SortColumn=KodeEmiten&SortOrder=asc')
     .then(r => r.text())
   ```
   → harus berupa JSON berisi `ResultCount` dan `Results`.

**Keamanan [PERLU KEPUTUSAN]:** endpoint ini menambah kapabilitas —
mengeksekusi JS arbitrer di tab mana pun. Saat ini bridge sudah dibatasi
localhost + token, tapi penambahan ini layak dibahas dan didokumentasikan
sebelum dikerjakan, termasuk apakah `expression` perlu dibatasi.

**Juga [PERLU KEPUTUSAN]:** setelah ekstensi berubah, versinya naik dari
0.1.8, dan seluruh langkah verifikasi di `task.md` §13 soal ekstensi
layak diulang minimal sekali.

### Langkah 3 — mode `--source api` pada watcher [RENCANA]

`idx_watcher/instance_catalog.py` sudah punya jalur halaman yang terbukti
jalan. Tambahkan:

```
--source page   (default, jalur lama, tetap dipertahankan)
--source api    (baru, butuh langkah 2)
```

Mode `api`: satu `evaluate` per (tahun, periode), `pageSize` besar, lalu
iterasi `Results`; untuk tiap entri pilih lampiran `File_Name == "instance.zip"`
dan ambil `url = "https://www.idx.co.id" + File_Path`,
`uploaded_at = File_Modified` (potong ke menit agar format katalog tetap sama).

Mode `api` **bukan pengganti** mode `page`: API bisa berubah sewaktu-waktu
tanpa pemberitahuan, sedangkan halaman adalah jalur yang sudah terbukti.
Mode `page` dipertahankan sebagai fallback dan untuk verifikasi silang.

### Langkah 4 — downloader membaca katalog [RENCANA]

Saat ini `firefox_bridge/instance/cli.py` menerima `--stocks-file`, lalu URL
disusun sendiri. Tambahkan:

```
--catalog db/instance_catalog.json
```

Perilaku bila `--catalog` ada:

1. Ambil entri `f"{tahun}|{KODE}"` untuk tahun yang diminta → pekerjaan =
   daftar entri tersebut (bukan `--stocks-file`).
2. Lewati yang sudah terverifikasi di `download_history.json` (aturan ini
   tidak berubah — tetap dasar anti-duplikat).
3. Unduh `entry["url"]` **apa adanya** — tidak ada konstruksi URL.
4. **404 di atas URL katalog = anomali nyata.** `is_definitive_reason`
   tidak lagi cukup memperlakukannya sebagai "berhenti, tidak ada laporan";
   ia harus tercatat sebagai kegagalan yang layak diulang/diselediki.
5. Laporan akhir run wajib menampilkan dua angka ini:
   - entri katalog yang tidak berhasil diunduh (dan alasannya)
   - 404 yang terjadi **di atas URL katalog** (keadaan mustahil = bug)

**Tentukan dulu [PERLU KEPUTUSAN]**: apakah `--catalog` menggantikan
`--stocks-file` atau menjadi alternatif keduanya (salah satu wajib diisi)?
Keputusan ini memengaruhi kontrak CLI yang sudah dianggap stabil di
`task.md` §6 "CLI Profesional".

---

## 4. Jebakan yang sudah diketahui

Semua ini pernah memakan waktu dalam sesi ini. Jangan diulangi.

1. **Timestamp di `download_history.json` adalah UTC** (`+00:00`), sedangkan
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

---

## 5. Gate dan baseline

Semua angka di bawah diukur 7 Oktober 2026, setelah commit `d4f52f1`.

```bash
.venv\Scripts\python.exe -m pytest -q                 # seluruh suite, hijau
.venv\Scripts\python.exe -m pytest --collect-only -q   # ringkasan per file, untuk menghitung jumlah tes
.venv\Scripts\python.exe -m ruff check idx_watcher tests/test_idx_watcher_instance_catalog.py
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
| pytest | hijau | **588 tes** (23 di antaranya milik `idx_watcher`) |
| ruff file baru | **0** | wajib tetap 0 |
| ruff penuh | **14** | pre-existing. Turun dari baseline 17 karena 3 error ikut terhapus bersama `announcement_watcher.py` (sudah diverifikasi: file lama persis 3 error) |
| mypy gate | **6** | pre-existing di `firefox_bridge/cli.py`. Tidak boleh naik |
| mypy `.` | ±89 | bukan gate, ada di `tests/` |

---

## 6. Definisi selesai

- [ ] Run 785 mencapai 785/785 tanpa kerusakan; `history verify` bersih
- [ ] 40 kode 404 sudah dicek terhadap katalog, hasilnya didokumentasikan
      (§3 langkah 1) — ini yang menjawab apakah pola konstruksi bersih
- [ ] Endpoint `evaluate` teruji `document.title` → `location.href` → `fetch` API
- [ ] `--source api` dan `--source page` menghasilkan katalog yang **sama**
      untuk tahun yang sama (uji silang — ini yang membuktikan keduanya benar)
- [ ] Mode `--catalog` menolak mengunduh di luar katalog, dan 404 di atas
      URL katalog dilaporkan sebagai anomali, bukan "tidak ada laporan"
- [ ] Gate §5 tidak bergerak ke arah yang salah
- [ ] Ekstensi 0.1.9 diverifikasi ulang bila ikut berubah

## 7. Di luar rencana ini

- Bug `quarter_from_title` pada watcher lama — **tidak relevan lagi**, kodenya
  sudah dihapus bersama `announcement_watcher.py` pada commit `c3c6998`.
- `rest_manager.py` — rehat otomatisnya tidak berfungsi di Windows (§4 butir 3).
  Diperbaiki hanya kalau diminta.
- Mengunduh `inlineXBRL.zip` — tidak diminta; watcher hanya mencatat
  `instance.zip` laporan tahunan.
