# Task.md — Firefox Bridge + IDX Downloader, Production Readiness

Tanggal: 26 September 2026  
Status: M1 berjalan — core dipecah dan dikunci regression test  
Target: Windows lokal, Firefox, Python 3.11+  
Fase pertama: Engineer core, tanpa UI

> Legenda status: `selesai` sudah tervalidasi, `berjalan` dikerjakan pada sesi ini,
> `belum` masih terbuka.

## 1. Tujuan

Menjadikan sistem yang sudah tervalidasi secara fungsional menjadi fondasi produksi yang:

- dapat dijalankan berulang tanpa mengunduh ulang arsip valid;
- aman dipulihkan setelah crash, disconnect, atau file corrupt;
- memiliki kontrak CLI, konfigurasi, logging, dan exit code yang stabil;
- dapat diuji otomatis tanpa bergantung pada IDX dan Firefox secara langsung;
- tetap kompatibel dengan distribusi source/venv serta distribusi EXE/installer.

## 2. Baseline yang Sudah Tervalidasi

| Area | Status | Bukti |
| --- | --- | --- |
| Alur IDX: profil → Laporan Keuangan → tahun | Tervalidasi | Browser snapshot dan UI IDX |
| Deteksi seluruh link TW1–TW3 + Audit | Tervalidasi | 4 link untuk NCKL 2025 |
| Delay minimal 1 detik pada setiap langkah | Tervalidasi | Output `WAIT` |
| Reuse satu tab profil | Tervalidasi | Satu tab digunakan untuk beberapa stock |
| Download melalui Firefox staging | Tervalidasi | Cookies/session IDX tetap terpakai |
| Pemindahan file oleh Python | Tervalidasi | `saham/<STOCK>/<YEAR>/` |
| History JSON | Tervalidasi | `download_history.json` |
| Skip berdasarkan history | Tervalidasi | 4 link dilewati tanpa download ulang |
| SHA-256 dan duplicate check | Tervalidasi | 4 hash unik |
| Penulisan JSON atomik | Sederhana | Implementasi sudah ada |
| Unit test | Adequate | 111 test pada workspace `productions` |

Baseline ini tidak boleh diregresi selama refactor.

## 3. Konsep Distribusi Gabungan

Source/venv dan EXE/installer dapat digabung sebagai dua jalur distribusi dari satu core:

1. **Source/venv** — untuk developer, CI, server, dan pengguna teknis.
2. **CLI terkemas** — EXE mandiri untuk pengguna akhir Windows.
3. **Installer** — shortcut, service bridge, extension, dan konfigurasi awal.
4. **Core yang sama** — IDX adapter, history engine, hashing, security, dan orchestration tidak diimplementasikan ulang.

Fase pertama hanya menyiapkan kontrak distribusi; pembuatan installer tidak boleh menghambat peningkatan kualitas core.

## 4. Arsitektur Target

```text
firefox_bridge/
  config.py                 # AppConfig terpusat
  errors.py                 # typed errors
  logging_config.py         # structured logging + redaction
  client.py                 # typed REST client
  bridge.py                 # transport/extension state
  app.py                    # REST service
  mcp_server.py             # MCP adapter
  pacing.py                 # aturan jeda minimal 1 detik
  cli.py                    # argparse entry point
  idx/
    models.py               # ReportLink, ReportTarget
    selectors.py            # year searchbox, option, tombol Laporan
    link_parser.py          # URL TW1–TW3/Audit
    browser_flow.py         # state machine langkah IDX
  downloader/
    models.py               # DownloadResult, DownloadRecord, RunSummary
    paths.py                # lokasi final, staging, history
    hashing.py              # SHA-256 primitives
    history.py              # JSON schema, atomic write, migrasi
    integrity.py            # verifikasi hash, duplicate_of
    filesystem.py           # tunggu selesai download, move
    orchestrator.py         # alur per-stock
    reporting.py            # ringkasan run
  tools/
    bulk_downloader.py      # shim kompatibilitas
extension/                  # MV3 Firefox extension
tests/
  conftest.py
  fixtures/                 # snapshot IDX tertangkap
  test_pacing.py
  test_idx_selectors.py
  test_idx_link_parser.py
  test_idx_flow.py
  test_downloader_history.py
  test_downloader_orchestrator.py
  test_cli.py
  test_regression_idx_page.py
```

Prinsip:

- Core tidak mencetak langsung ke terminal.
- Browser flow harus dapat diuji memakai fake client.
- History dan filesystem tidak bergantung pada FastAPI.
- CLI hanya menjadi adapter aplikasi.
- Config dipvalidasi satu kali di boundary.

## 5. Prioritas

- **P0** — wajib sebelum release candidate.
- **P1** — wajib sebelum distribusi beta.
- **P2** — setelah core stabil.

## 6. Daftar Tugas

### P0 — Stabilisasi Core

| ID | Tugas | Deliverable | Acceptance Criteria |
| --- | --- | --- | --- |
| CORE-001 | Kunci perilaku saat ini | Regression fixtures untuk IDX flow | Alur NCKL 2025 tetap menghasilkan 4 report |
| CORE-002 | Pecah `bulk_downloader.py` | Modul domain terpisah | Tidak ada browser logic, hashing, atau JSON dalam satu file |
| CORE-003 | Typed domain model | dataclass/Pydantic models | URL, periode, file, dan status tidak lagi berupa `dict` longgar |
| CORE-004 | Error taxonomy | `ExtensionDisconnected`, `StaleReference`, `IntegrityError`, `DownloadTimeout` | CLI tidak lagi menangkap semua error sebagai pesan umum |
| CORE-005 | Retry/backoff terukur | Retry hanya untuk error transient | Tidak mengulang action destruktif tanpa idempotency check |
| CORE-006 | Delay sebagai config | `step_delay >= 1.0` | Nilai <1 ditolak; semua langkah menggunakan config |
| CORE-007 | Extension health gate | Precheck sebelum workflow | Workflow berhenti lebih awal jika ekstensi terputus |

### P0 — History dan Integritas

| ID | Tugas | Deliverable | Acceptance Criteria |
| --- | --- | --- | --- |
| DATA-001 | History schema v2 | Schema dengan `url`, `file`, `size`, `sha256`, `status`, timestamps | Versi schema tercatat dan dapat dimigrasi |
| DATA-002 | Atomic + locked history | Lock file dan temp-file replace | Dua proses tidak merusak JSON |
| DATA-003 | Crash recovery | Startup scan staging dan final folder | File selesai tetapi JSON belum ditulis akan direkonsiliasi |
| DATA-004 | Resume semantics | Status `pending`, `downloading`, `completed`, `failed` | Restart melanjutkan report yang belum selesai |
| DATA-005 | Integrity verification | SHA-256 sebelum skip dan setelah move | Hash mismatch memicu recovery, bukan skip palsu |
| DATA-006 | Duplicate policy | `duplicate_of` dan warning yang jelas | File identik tetap disimpan, tetapi ditandai |
| DATA-007 | History repair command | `history verify/rebuild` | JSON dapat dibangun ulang dari file final |

### P0 — Security dan Reliability

| ID | Tugas | Deliverable | Acceptance Criteria |
| --- | --- | --- | --- |
| SEC-001 | Token hardening | Token file permission + rotation command | Token tidak pernah masuk log, JSON, atau report |
| SEC-002 | Loopback enforcement | Validasi host bind dan extension origin | Non-loopback binding ditolak |
| SEC-003 | Path safety | Validasi stock code dan filename | `../`, path separator, dan karakter ilegal ditolak |
| SEC-004 | IDX URL allowlist | Validasi host dan pola path | Redirect/URL asing tidak didownload |
| SEC-005 | Archive validation | Signature ZIP dan ukuran maksimum | File non-ZIP atau rusak tidak masuk folder final |
| SEC-006 | Secret redaction tests | Automated tests | Token dan Authorization header tidak pernah tercetak |

### P0 — CLI Profesional

| ID | Tugas | Deliverable | Acceptance Criteria |
| --- | --- | --- | --- |
| CLI-001 | Console entry point | `firefox-bridge-download` | Tidak perlu `python -m` |
| CLI-002 | Exit code stabil | 0 sukses, 1 kegagalan sebagian, 2 input salah, 3 bridge/extension gagal | Script dapat membedakan outcome |
| CLI-003 | Config file | TOML/JSON config | CLI argument dapat mengoverride config |
| CLI-004 | Stock input | CSV dan file | Mendukung `--stocks` dan `--stocks-file` |
| CLI-005 | Resume flags | `--resume`, `--retry-failed`, `--force` | Resume tidak mengunduh ulang file valid |
| CLI-006 | Dry run | Deteksi tanpa download | Menampilkan link dan status history tanpa mengubah data |
| CLI-007 | Machine report | JSON run report | Cocok untuk automation dan monitoring |
| CLI-008 | Noninteractive mode | Tanpa prompt dalam batch | Aman untuk Task Scheduler/CI |

### P0 — Testing dan Quality Gate

| ID | Tugas | Deliverable | Acceptance Criteria |
| --- | --- | --- | --- |
| QA-001 | Test pyramid | Unit, integration, contract, E2E terbatas | Selector, history, filesystem, dan recovery terisolasi |
| QA-002 | Coverage gate | `pytest-cov` | Minimal 85% pada core idx/downloader |
| QA-003 | Linting | Ruff | Tidak ada error |
| QA-004 | Static typing | Mypy/pyright strict pada core | Tidak ada error pada modul produksi |
| QA-005 | Extension lint | `web-ext lint` | 0 error dan 0 warning |
| QA-006 | End-to-end smoke | Satu stock, empat report | Run NCKL 2025 pada Windows bersih |
| QA-007 | Fault injection | Disconnect, timeout, corrupt JSON, hash mismatch | Tidak ada false success |

### P1 — Observability dan Dokumentasi

| ID | Tugas | Deliverable | Acceptance Criteria |
| --- | --- | --- | --- |
| OBS-001 | Structured logging | JSON lines dengan `run_id`, `stock`, `year`, `step` | Log dapat difilter tanpa print statement |
| OBS-002 | Progress reporting | Progress TTY + plain mode | Tidak bercampur dengan JSON report |
| OBS-003 | Diagnostics command | `doctor` | Menjampilkan versi, config, token presence, port, extension state tanpa membocorkan secret |
| DOC-001 | README profesional | Install, run, resume, troubleshooting | Jalur baru dapat diikuti dari mesin bersih |
| DOC-002 | Architecture decision records | ADR untuk history, staging, transport | Keputusan penting terdokumentasi |
| DOC-003 | Changelog dan versioning | SemVer + release notes | Perubahan breaking dapat dilacak |

### P1 — Distribusi

| ID | Tugas | Deliverable | Acceptance Criteria |
| --- | --- | --- | --- |
| PKG-001 | Source/venv distribution | Wheel + editable install | CLI dan bridge terinstal bersih |
| PKG-002 | Build EXE | PyInstaller/Nuitka artifact | Jalan pada Windows target tanpa Python global |
| PKG-003 | Installer | Start bridge, extension, config wizard | Install/uninstall tidak merusak data pengguna |
| PKG-004 | Extension packaging | Signed/persistent XPI | Tidak perlu temporary add-on pada release final |
| PKG-005 | Release pipeline | Build, test, scan, checksum, artifact | Release dapat direproduksi |

### P1 — Sumber Daftar Saham dari Database

Daftar saham pada akhirnya dibaca dari MariaDB lokal, bukan dari argumen CLI.
Koneksi yang sudah dipakai HeidiSQL: TCP/IP `127.0.0.1:3306`, user `orca`.
Password **tidak pernah** disimpan di source, `task.md`, log, atau history JSON.

| ID | Tugas | Deliverable | Acceptance Criteria |
| --- | --- | --- | --- |
| SRC-001 | Kontrak skema tabel | Tabel `saham` dengan kolom minimal kode, nama, aktif, prioritas | Query dibaca dikunci di test agar tidak berubah diam-diam |
| SRC-002 | Konfigurasi koneksi | `IDX_DB_HOST`, `IDX_DB_PORT`, `IDX_DB_USER`, `IDX_DB_PASSWORD`, `IDX_DB_NAME` | Password hanya dari environment atau file `.env` yang di-gitignore |
| SRC-003 | Provider daftar saham | Protocol `StockListProvider` dengan implementasi CSV, CLI, dan MariaDB | Orkestrator tidak tahu asal daftar saham |
| SRC-004 | Driver dan pooling | `PyMySQL` atau `mariadb` + pool + connect/read timeout | Run panjang tidak exhausting koneksi |
| SRC-005 | Filter dan urutan | `aktif = 1`, urutan prioritas, deduplikasi kode | Kode tidak valid ditolak sebelum tab browser dibuka |
| SRC-006 | Cache daftar saham | Snapshot lokal dengan TTL | Run ulang tidak query database setiap kali |
| SRC-007 | Fallback saat DB mati | Cache, lalu CSV, lalu error jelas | Kegagalan tidak diam tanpa penjelasan |
| SRC-008 | CLI | `--stocks-source {cli,file,db}` dan `--stocks-limit` | `--stocks` lama tetap kompatibel |
| SRC-009 | Test tanpa database | Fake provider + integration test di balik marker | `pytest` tetap lulus tanpa MariaDB running |
| SRC-010 | Redaksi secret | Filter log dan larangan print password | Password tidak muncul di output mana pun |
| SRC-011 | Batch dari DB | Ambil N kode per batch dengan progres | Ribuan saham diproses tanpa memory membengkak |

Catatan keamanan: password database yang dibagikan di chat sebaiknya dirotasi
dan tidak dipakai ulang di lingkungan lain.

## 7. Milestone

| Milestone | Cakupan | Keluar |
| --- | --- | --- |
| M1 — Baseline | CORE-001, struktur test, config awal | Perilaku tervalidasi terkunci |
| M2 — Reliable | History v2, recovery, integrity, error handling | Crash/disconnect tidak merusak data |
| M3 — Professional CLI | CLI-001 sampai CLI-008, logging, report | Batch run siap digunakan |
| M4 — Stock List Database | SRC-001 sampai SRC-011 | Daftar saham dibaca dari MariaDB |
| M5 — Release Candidate | QA, docs, source distribution | RC Windows internal |
| M6 — Distribution | EXE, installer, persistent extension | Beta terbatas |
| M7 — Public Release | Signing, release pipeline, support docs | Release stabil |

## 8. Kriteria Done Fase pertama

- Mesin Windows baru dapat menjalankan `firefox-bridge-download` dari source/venv.
- 1 NCKL + minimal 10 stock lain selesai untuk satu tahun.
- T1–T4 untuk tahun yang sama ditemukan.
- Run kedua mengunduh 0 file valid.
- Hash diverifikasi dan duplicate ditandai.
- Kill process di tengah run, lalu resume, tidak menghasilkan file corrupt atau JSON rusak.
- Extension disconnect menghasilkan exit code dan pesan yang jelas.
- Token tidak ditemukan pada log, history, atau run report.
- `pytest`, coverage, Ruff, type checker, `web-ext lint`, dan package build lulus.
- README menjelaskan instalasi, konfigurasi, resume, dan troubleshooting.

## 9. Di Luar Scope Fase Pertama

- Desktop/web UI.
- Multi-platform.
- Cloud service.
- Bypass CAPTCHA, Cloudflare, atau access control IDX.
- Paralelisme beberapa tab browser.
- Pengubahan struktur URL IDX tanpa adapter khusus.
- Integrasi database masuk fase kedua (M4), bukan fase pertama.

## 10. Risiko Utama

| Risiko | Mitigasi |
| --- | --- |
| IDX mengubah DOM | Contract tests + selector semantic + error jelas |
| Extension sering disconnect | Health gate, retry, resume, no false success |
| JSON rusak saat crash | Atomic write, lock, recovery, backup |
| File corrupt tetapi histori ada | SHA-256 verification |
| Installer menghapus data | Uninstall hanya menghapus app data, bukan report |
| Log membocorkan token | Redaction central + tests |
| CLI terlalu sulit | Config file, dry-run, exit codes, report JSON |
| MariaDB tidak tersedia saat run | Cache lokal, fallback CSV, error yang jelas |
| Password database bocor | Hanya dari `.env`/environment, redaksi log, file di-gitignore |
| Skema tabel berubah diam-diam | Query dikunci di contract test |
| Driver MySQL tidak jalan di Windows EXE | Pure-Python driver, diverifikasi saat packaging |

## 11. Keputusan yang Sudah Disepakati

- Target produksi: Windows lokal.
- Fase pertama: engineer core tanpa UI.
- Source/venv dan CLI dapat menjadi jalur distribusi pertama.
- EXE/installer disiapkan setelah core stabil.
- Tidak mengubah alur IDX yang sudah berhasil sebelum ada regression coverage.
- `browser-bridge` tetap menjadi referensi yang tidak diubah; semua refactor
  dilakukan di `productions` sampai validasi end-to-end lulus.
- Daftar saham pada akhirnya berasal dari MariaDB lokal di `127.0.0.1:3306`
  (user `orca`), diakses lewat driver yang sama dengan HeidiSQL.
- Kredensial database hanya lewat environment atau `.env` yang di-gitignore.
  Password tidak ditulis di source, dokumen, log, maupun laporan run.

## 12. Alternatif Tanpa Extension (keputusan arsitektur)

Pertanyaan: apakah bridge bisa dijalankan sebagai Service Worker agar pengguna
tidak perlu memasang extension? Jawabannya tidak, karena tiga batas platform.

| Kebutuhan bridge | WebExtension | Service Worker |
| --- | --- | --- |
| `tabs.list`, `tabs.update`, `tabs.create` | ada | tidak ada |
| `scripting.executeScript` ke DOM halaman IDX | ada | tidak ada |
| `downloads.download` menyimpan cookie sesi | ada | tidak ada |
| Mengontrol tab di origin lain (`www.idx.co.id`) | ada lewat host permission | tidak, scope satu origin |

Service Worker hanya mengendalikan halaman dari origin yang sama dengan
origin SW-nya. Kita tidak meng-host apa pun di `www.idx.co.id`, jadi SW tidak
pernah menyentuh DOM IDX. Satu-satunya primitive yang dimiliki SW adalah
`fetch`/`caches`/`clients` — tidak ada injeksi skrip, tidak ada browser API.

Catatan: 14 Service Worker yang terlihat di `about:debugging` milik situs lain
(Gmail, YouTube, Facebook, X, Telegram, dan lain-lain), bukan milik bridge ini.
Bridge muncul di tab **Extensions**, bukan Service Workers.

Opsi tanpa extension yang realistis, bila nanti dibutuhkan:

| Opsi | Pengorbanan |
| --- | --- |
| geckodriver + WebDriver dengan profil Firefox | Butuh geckodriver, profile lock bentrok dengan Firefox yang sedang jalan, parity fitur snapshot/click perlu ditulis ulang |
| Firefox Remote Agent (CDP) | API CDP di Firefox terbatas, tidak cocok untuk produksi |
| Playwright headful | Cookie profil terbaca, tapi kehilangan browser nyata dan menambah dependensi besar |

Keputusan: tetap pakai WebExtension. Rasa "harus pasang extension" diselesaikan
di lapisan distribusi, bukan dengan mengganti arsitektur: add-on persisten
(-signed XPI, PKG-004) yang dipasang sekali oleh installer (PKG-003), bukan
temporary add-on yang dimuat ulang tiap restart.

## 13. Status Kemajuan

### Sesi 26 September 2026 — M1 Baseline

| ID | Status | Bukti |
| --- | --- | --- |
| CORE-001 | selesai | `tests/fixtures/idx_nckl_2025_snapshot.json` + `test_regression_idx_page.py` mengunci 4 report, year searchbox, dan tombol Laporan |
| CORE-002 | selesai | `bulk_downloader.py` 935 baris dipecah menjadi `idx/` (4 modul) dan `downloader/` (8 modul); shim lama tetap jalan |
| CORE-003 | berjalan | `ReportLink`, `ReportTarget`, `DownloadResult`, `DownloadRecord`, `RunSummary` sudah typed; `dict` longgar masih dipakai di batas snapshot |
| CORE-004 | belum | Semua error masih `RuntimeError`/`TimeoutError` umum |
| CORE-005 | belum | Belum ada retry/backoff |
| CORE-006 | berjalan | `pacing.py` terpusat; `--delay` masih hardcoded default 3 detik di CLI |
| CORE-007 | belum | Precheck connection belum ada di awal run |
| DATA-001 | berjalan | Skema v1 tetap; migration belum ada |
| DATA-002 | berjalan | Atomic write sudah ada; lock file belum |
| DATA-003 | belum | Startup scan staging belum ada |
| DATA-004 | belum | Status report belum ada di JSON |
| DATA-005 | selesai | Hash dicek sebelum skip dan mismatch memicu unduhan ulang |
| DATA-006 | selesai | `duplicate_of` diisi dan duplikat dicetak |
| DATA-007 | belum | Perintah `history verify/rebuild` belum ada |
| SEC-001 | berjalan | Token di luar project; rotasi dan permission belum |
| SEC-002 | berjalan | Bind loopback sudah ada; enforcement belum |
| SEC-003 | selesai | `normalize_stock_code` menolak non-alfanumerik, `..`, separator, nama device Windows, dan kode >10 karakter; dipasang di CLI (exit 2 sebelum browser dibuka) **dan** di `paths.py` sebagai choke point |
| SEC-004 | selesai | `validate_report_url` mewajibkan skema http/https, host persis `www.idx.co.id`/`idx.co.id`, tahun, segmen kuartal (`/TWn/` atau `/Audit/` untuk TW4), dan segmen saham yang cocok; dipasang di `is_report_link` **dan** tepat sebelum download |
| SEC-005 | selesai | `validate_archive` memeriksa ukuran, signature ZIP, end-of-central-directory, keterbacaan, jumlah entri, dan rasio kompresi; dijalankan di staging sebelum file dipindah |
| SEC-006 | selesai | `tests/test_secret_redaction.py`: unduhan penuh tidak menulis token ke log/JSON/console, plus cek statis bahwa token tidak pernah masuk emitter |
| CLI-001 | selesai | `firefox-bridge-download` terpasang dari `pyproject.toml` |
| CLI-002 | berjalan | Exit code 0/1/2 ada; kode 3 untuk bridge gagal belum |
| CLI-003 | belum | Config file belum |
| CLI-004 | berjalan | `--stocks` ada; `--stocks-file` belum |
| CLI-005 | berjalan | Skip otomatis ada; flag `--resume` belum eksplisit |
| CLI-006 | belum | `--dry-run` belum |
| CLI-007 | belum | JSON run report belum |
| CLI-008 | berjalan | Noninteraktif sudah; belum ada flag eksplisit |
| QA-001 | selesai | 10 file test: pacing, selector, parser, flow, history, orchestrator, CLI, regresi IDX, kontrak extension, bridge/client/config |
| QA-002 | berjalan | `idx/` 89–100% dan `downloader/` 85–100% sudah melewati target 85%; total paket naik ke 78% karena `mcp_server.py` dan `server.py` belum diuji |
| QA-003 | selesai | `ruff check .` lulus bersih, 0 error |
| QA-004 | selesai | `mypy` lulus, 28 source file tanpa isu |
| QA-005 | selesai | `web-ext lint` 0 error, 0 warning, 0 notice pada versi 0.1.6; `node --check` lulus untuk 3 file JS |
| QA-006 | tertunda | Perlu E2E live NCKL 2025 dari `productions` |
| QA-007 | berjalan | Kontrak extension sudah dijaga test; fault injection jaringan belum |
| SRC-001 | belum | Skema tabel `saham` belum ditentukan |
| SRC-002 | belum | Konfigurasi koneksi belum ada |
| SRC-003 | belum | `StockListProvider` belum ada |
| SRC-004 | belum | Driver dan pooling belum |
| SRC-005 | belum | Filter dan urutan belum |
| SRC-006 | belum | Cache daftar saham belum |
| SRC-007 | belum | Fallback belum |
| SRC-008 | belum | `--stocks-source` belum |
| SRC-009 | belum | Test provider belum |
| SRC-010 | belum | Redaksi secret database belum |
| SRC-011 | belum | Batch dari database belum |

Catatan teknis sesi ini:

- Dua bug nyata ditemukan dan diperbaiki saat pemecahan modul: konstanta
  `TEMPARY_SUFFIXES` salah nama sehingga `is_download_complete` gagal, dan
  penanda perubahan duplikat tidak tersimpan ke JSON.
- Impor siklik antara `history` dan `integrity` diselesaikan dengan memindahkan
  SHA-256 ke `downloader/hashing.py` dan kunci JSON ke `downloader/models.py`.
- Suite berjalan 2,6 detik untuk 111 test.
- Tiga entry point terverifikasi: console script, `python -m firefox_bridge.cli`,
  dan `python -m firefox_bridge.tools.bulk_downloader`.
- Dua temuan lint pada kode lama diperbaiki: `request_id` yang tidak terpakai
  kini masuk ke log debug, dan validasi port di `config.py` jadi eksplisit.

### Sesi 26 September 2026 — Perbaikan Extension (0.1.5 → 0.1.6)

Empat defect nyata ditemukan sebelum pengujian end-to-end:

| Defect | Dampak | Perbaikan |
| --- | --- | --- |
| `MAX_SNAPSHOT_ELEMENTS` = 300 di `background.js` dan `page_functions.js` | Klien meminta 2000 elemen, ekstensi diam-diam memangkas ke 300; link report IDX bisa hilang tanpa error | Dinaikkan ke 2000 di kedua file, dikunci `tests/test_extension_contract.py` |
| `setConnectionStatus("connecting")` tiap iterasi poll | State flapping, tombol Connect kedip-nyala tiap poll | Hanya.set status "connecting" saat `authenticated` masih false |
| `MAX_HTTP_RETRY_MS` = 10000 | Jeda loopback sampai 10 detik; selama itu bridge menjawab 503 untuk semua command | Diturunkan ke 3000 ms |
| Handler `bridge-download` yang tidak terpakai | Jalur fallback `window.open` dari background page, tidak pernah dipanggil | Handler dihapus |

Semua file extension dinormalkan ke line ending LF. `node --check` lulus untuk
ketiga file JS dan `web-ext lint` tetap 0 error pada versi 0.1.6.

### Regresi dari perbaikan sendiri (0.1.6, putaran 2)

Perbaikan batas snapshot di atas justru membuat background page gagal load:

- `background.scripts` di Firefox memuat seluruh file ke satu scope global.
  `const MAX_SNAPSHOT_ELEMENTS` di `page_functions.js` bentrok dengan nama yang
  sama di `background.js` → `SyntaxError: Identifier 'MAX_SNAPSHOT_ELEMENTS' has
  already been declared`.
- `scripting.executeScript({ func })` hanya menserialisasi body fungsi, jadi
  konstanta module-level tidak tersedia di halaman IDX.

Gejala di popup: `Could not establish connection. Receiving end does not exist.`

Perbaikan: limit dipindah ke dalam `snapshotPage` sebagai `elementLimit` lokal.
Detector baru yang mencegah regresi sejenis:

| Pengaman | Cara kerja |
| --- | --- |
| `test_background_page_boots_and_registers_one_message_listener` | `tests/extension_harness.js` memuat kedua skrip di scope bersama seperti Firefox, lalu memastikan tepat 1 listener terdaftar |
| `test_background_scripts_share_one_scope_without_redeclared_names` | Menolak nama top-level yang sama di dua background script |
| `test_page_functions_declares_no_top_level_state` | Menolak `const/let/var/class` top-level di `page_functions.js` karena tidak ikut terinjeksi |

Harness sudah diverifikasi: dengan defect dikembalikan, ia gagal persis dengan
`SyntaxError: Identifier 'MAX_SNAPSHOT_ELEMENTS' has already been declared`.

### Event page "Stopped" (0.1.6 → 0.1.7)

Gejala yang dilaporkan: background script di `about:debugging` berubah menjadi
**Stopped**, dan harus diklik **Start** agar jalan lagi.

#### Pengukuran yang menolak hipotesis awal

Hipotesis awal: Firefox membuang event page setelah
`extensions.background.idle.timeout` (30 detik), sehingga loop poll ikut mati.

Diuji dengan menghitung `POST /extension/poll` di log server:

| t | polls | delta |
| --- | --- | --- |
| 0 s | 1490 | — |
| 20 s | 1510 | +20 |
| 40 s | 1530 | +20 |
| 60 s | 1550 | +20 |
| 80 s | 1570 | +20 |
| 100 s | 1590 | +20 |

Tepat 1 Hz kontinu, tanpa interaksi pengguna. **Hipotesis itu salah.** Di
Firefox Build.user ini, loop poll yang berjalan terus tidak memicu idle
discard.

#### Akar masalah yang sebenarnya

Temporary add-on yang di-reload kehilangan storage. Akibatnya `settings.enabled`
kembali `false`, `startBridge()` keluar sebelum menghubungi server, polling
berhenti permanen, dan `status()` menjadi `{"connected": false}`. Klik **Start**
di `about:debugging` membangun ulang konteks, dan `manuallyDisconnected` tidak
dipersist sehingga nilainya `false` lagi — makanya langsung pulih.

Ini masalah **lapisan distribusi**, bukan masalah bridge. Hilang begitu
extension dikirim sebagai signed XPI persisten yang dipasang installer
(PKG-003 dan PKG-004).

#### Klaim yang sempat salah dan sudah dikoreksi

Dulu disimpulkan WebSocket terbuka akan menahan event page hidup, lalu disimpulkan
pula `fetch` 1 Hz akan pasti mati dalam 30 detik. **Keduanya tidak didukung.**
Setelah membaca `toolkit/components/extensions/parent/ext-backgroundPage.js`
(mozilla-central, rev 9c0ad0c9), `terminateBackground()` hanya memperbolehkan
pengecualian berikut:

| Yang mencegah termination | Cara |
| --- | --- |
| DevTools terpasang | `hasDevToolsAttached` |
| Port native messaging terbuka | reason `nativeapp` |
| Promise async listener masih pending | reason `listeners` |
| `StreamFilter` aktif | reason `streamfilter` |
| **Panggilan WebExtension API dari konteks ini** | reason `parentapicall` |

WebSocket terbuka maupun `fetch` pending **tidak ada** di daftar itu. Namun
daftar itu tidak membuktikan bahwa idle discard pasti terjadi, dan pengukuran
membuktikan sebaliknya untuk `fetch` 1 Hz. Yang benar: tidak ada di antara keduanya
yang *dijamin* menahan halaman hidup.

#### Perbaikan (0.1.7)

| Perubahan | Alasan yang benar |
| --- | --- |
| `startKeepalive()` memanggil `browser.runtime.getPlatformInfo()` tiap 20 detik | Asuransi terhadap idle discard yang didokumentasikan, dan menutup kasus yang tercatat melewati loop poll seperti bangun dari sleep (bug 1834683) serta kehabisan memori. Bukan perbaikan atas bug yang teramati |
| WebSocket jadi transport utama | Perintah di-push: tanpa polling 1 Hz, tanpa jurang staleness 5 detik, tanpa pembuangan antrean |
| HTTP long-poll jadi fallback, dan diam selama `wsAuthenticated` | Socket terblokir tidak membuat bridge tak terpakai, tapi tidak juga membayar dua transport sekaligus |
| `publicState()` melaporkan `transport`, popup menampilkannya | Pengguna perlu tahu jalur mana yang aktif, bukan sekadar "connected" |
| `stopBridge()` menutup socket secara eksplisit | `connect` dan `disconnect` jadi deterministik, tidak menggantung di close socket |

Detector baru:

| Pengaman | Yang dijaga |
| --- | --- |
| `test_event_page_keepalive_runs_faster_than_firefox_idle_timeout` | Periode keepalive < 30 detik |
| `test_event_page_keepalive_calls_a_webextension_api` | Keepalive memanggil `browser.runtime.*` dan **tidak** memakai `fetch`, `new WebSocket`, atau `setTimeout` — ketiganya tidak dijamin menahan event page |
| `test_websocket_is_the_primary_transport_with_http_fallback` | Klien WebSocket ada, dan loop HTTP berhenti begitu socket terautentikasi |
| `test_reported_extension_version_matches_the_manifest` | `EXTENSION_VERSION` di `background.js` sama dengan `version` di `manifest.json` |
| `extension_harness.js` | Sekarang juga melaporkan interval yang terdaftar dan URL kedua transport; pytest menolak jika keepalive tidak terpasang |

Keempat mutasi berikut diuji dan semuanya tertangkap test yang tepat:
keepalive lebih lambat dari idle timeout, pemanggilan keepalive dihapus,
keepalive diganti `fetch`, dan HTTP fallback tidak menghormati socket hidup.

### QA-006 — E2E NCKL 2025 (LULUS)

 Dijalankan dari `productions` dengan
`firefox-bridge-download.exe --stocks NCKL --year 2025 --all-detected --delay 1`.

#### Skenario 1: semua file sudah ada

| Pemeriksaan | Hasil |
| --- | --- |
| Urutan langkah | profil → Laporan Keuangan → tahun 2025, tidak ada input tahun ke `Search Company Code` |
| Link terdeteksi | 4 (TW1, TW2, TW3, TW4) |
| Jalur TW4 | `/Audit/NCKL/`, bukan `/TW4/` ✓ |
| Tindakan | 4 `STEP SKIP` (hash valid), 0 unduhan |
| Hash | keempatnya cocok dengan `download_history.json` |
| Duplikat | tidak ada |
| Staging | tidak ada file tersisa |
| Exit code | 0 |

#### Skenario 2: file TW4 dihapus, history tetap ada

Ini menguji aturan "JSON otoritatif hanya bila file akhir ada".

| Pemeriksaan | Hasil |
| --- | --- |
| Deteksi | `WARN: JSON mencatat TW4, tetapi file hilang; mengunduh ulang` ✓ |
| Staging | `saham/staging/NCKL/2025/...` (bukan sufiks `.staging` di nama file) |
| Perpindahan | dipindahkan ke `saham/NCKL/2025/`, lalu JSON ditulis ulang |
| Hash hasil unduhan | `27aead46…fccd`, **identik** dengan file asli → byte-for-byte reproducible dari IDX |
| Entri JSON | tetap 4 kuartal, `integrity_status: verified`, `duplicate_of: null`, tanpa entri ganda |
| Timestamp T1–T3 | tidak berubah; hanya T4 yang diperbarui |
| Staging setelah selesai | bersih |
| Exit code | 0 |

Kedua skenario di atas sempat dijalankan di jalur HTTP. Jalur WebSocket
diuji ulang terpisah setelah akar masalahnya ditemukan — lihat bagian
berikutnya.

### WebSocket tidak pernah sampai ke server (0.1.7 → 0.1.8)

Gejala: extension 0.1.7 melaporkan `websocket: false`,
`websocket_error: "WebSocket connection failed"`, `websocket_state:
"awaiting socket"`, dan `websocket_attempts` terus bertambah — artinya loop
socket hidup dan berputar. Tetapi **log server tidak pernah memuat satu pun
percobaan**. Server HTTP polos, yang mencatat setiap request, tidak melihat
apa pun.

#### Server ditanya, bukan ditebak

Bridge tidak bisa menginspeksi background page, dan socket yang gagal
tidak meninggalkan jejak sama sekali di sisi server. Jadi diagnosis
dipindahkan ke sisi yang bisa dilihat sendiri: extension sekarang
mengirim identitas dan diagnosisnya pada setiap poll HTTP, dan
`/api/v1/status` melaporkan apa adanya.

| Key | Isi |
| --- | --- |
| `version` | versi asli dari `EXTENSION_VERSION`, bukan string hardcoded |
| `http_diagnostics.websocket` | apakah socket dianggap hidup |
| `http_diagnostics.websocket_attempts` | percobaan ke-berapa |
| `http_diagnostics.websocket_state` | `awaiting socket` / `authenticated` / `retrying` |
| `http_diagnostics.websocket_exit` | alasan loop keluar, jika keluar |
| `http_diagnostics.websocket_url` | URL yang benar-benar dibentuk |

#### Temuan sampingan: log membanjir 1 Hz

`Extension is polling over HTTP, not WebSocket` pernah menyala **tiap
detik**: 44 baris dalam 43 detik, hampir seluruhnya identik. Log seperti itu
tidak lagi membawa informasi, hanya menutupi kejadian yang penting.
Log sekarang hanya keluar saat diagnosis benar-benar berubah
(`_http_diagnostics_logged`), dan test mengunci sifat itu.

#### Akar masalah: `ws://` dinaikkan ke `wss://`

Diagnosis server sama sekali. Kesimpulannya: default CSP MV3 menganggap
permintaan `ws://` tidak eksplisit, sehingga Firefox menaikkannya ke
`wss://`. Server ini HTTP polos di `127.0.0.1:8765`, tidak pernah SSL,
jadi percobaan itu hilang sebelum sampai.

Dua bukti yang saling menguatkan:

1. `fetch()` ke `http://127.0.0.1:8765` pada port yang sama **berhasil** —
   jadi port, host, dan host permission bukan penyebabnya.
2. Setelah `connect-src` dipasang, socket langsung masuk:
   `WebSocket /extension [accepted]` → `connection open`.

Catatan penting dari sumber Mozilla Discourse: `connect-src` harus
menyertakan **port**, dan port itu harus wildcard `*` — pola host dengan
port eksplisit (*silent grant*) diberikan tetapi tidak cocok dengan
apa pun (Firefox bug 1362809). Itu sebabnya entry-nya ditulis
`ws://127.0.0.1:*`, bukan `ws://127.0.0.1:8765`.

#### Perbaikan

`manifest.json` 0.1.8 menambahkan `content_security_policy.extension_pages`
dengan `connect-src` yang dipin ke loopback:

```
'connect-src 'self' ws://127.0.0.1:* ws://localhost:* wss://127.0.0.1:* wss://localhost:*
```

Ini bukan sekadar melonggarkan — posisinya lebih ketat dari default, yang
mengizinkan `connect-src *`.

#### Bukti bahwa jalur WebSocket benar-benar dipakai

Setelah socket terakhir terbuka, dihitung pada jendela baris log setelahnya:

| Metrik | Nilai |
| --- | --- |
| `POST /extension/poll` | 0 |
| `POST /extension/response` | 0 |
| Panggilan `/api/v1/*` | 13 |

13 perintah bridge, nol lalu lintas HTTP fallback. E2E NCKL 2025 diulang
pada 0.1.8 di jalur WebSocket: 4 link, 4 `STEP SKIP`, 0 unduhan, hash
cocok, tanpa duplikat, TW4 lewat `/Audit/`, exit 0.

#### Reconnect otomatis

Server dimatikan lalu dinyalakan ulang tanpa ada campur tangan di Firefox.
Socket masuk pada detik yang sama server start (16:28:29), dan `status()`
kembali `connected: true` tanpa perlu klik **Connect** lagi.

#### Cacat yang ditemukan sendiri saat memperbaiki ini

`status()` sempat menempelkan `http_diagnostics` ke respons apa pun
yang berisi extension. Begitu socket naik, blok itu berisi laporan dari
**sebelum** socket naik — `websocket: false`, `attempts: 12` — dan
disajikan seolah-olah deskripsi keadaan sekarang. Ini persis kelas
kesalahan yang sedang diperbaiki di tempat lain: fault yang sudah
beres tampak masih hidup. Sekarang blok itu hanya disertakan selama
poll HTTP masih berjalan.

#### Tes yang menjaga

| Pengaman | Yang dijaga |
| --- | --- |
| `test_http_poll_reports_extension_identity_and_diagnostics` | `/api/v1/status` melaporkan identitas dan diagnosis nyata |
| `test_http_poll_carries_extension_identity_and_transport_diagnostics` | Extension benar-benar mengirim field diagnosis itu |
| `test_stale_http_diagnostics_are_dropped_once_the_socket_authenticates` | Laporan basi tidak ikut ditampilkan setelah socket terautentikasi |
| `test_transport_diagnostic_is_logged_once_per_change` | Log tidak membanjir tiap detik |
| `test_extension_pages_csp_allows_the_loopback_socket` | `connect-src` ada, dipin ke loopback, dan menyertakan port |
| `test_reported_extension_version_matches_the_manifest` | 0.1.8 di `background.js` == `version` di `manifest.json` |

Satu artefak test yang sempat menyesatkan: `client.post("/extension/poll")`
memblokir portal `TestClient` selama durasi poll, dan itu lebih lama dari
timeout autentikasi bridge sendiri, sehingga socket gagal auth dan test
menyalahkan produk. Di produksi poll tidak memblokir apa pun. Test
diperbaiki dengan mengautentikasi lebih dulu.

### Lokasi log (OBS-001 dikoreksi)

Log terstruktur ternyata tidak pernah ada di `productions`. `_temp_dir()`
menaruhnya di `%LOCALAPPDATA%\Temp\opencode\firefox-bridge\bridge.log` —
yaitu folder temp milik **opencode**, bukan milik produk ini. Saat pengguna
mengecek folder proyek, log tidak ada; yang terlihat hanya tangkapan stdout
milik shell opencode. Dua hal yang berbeda, dan yang kedua menutupi yang
pertama.

| | |
| --- | --- |
| Lokasi lama | `%LOCALAPPDATA%\Temp\opencode\firefox-bridge\bridge.log` |
| Lokasi baru | `productions\logs\bridge.log` |
| Override | env `FIREFOX_BRIDGE_LOG_DIR` (level: `FIREFOX_BRIDGE_LOG`) |

`%TEMP%` adalah tempat yang biasanya dibersihkan otomatis Windows atau
Storage Sense. Log 465 baris yang berisi seluruh bukti diagnosis WebSocket
bisa hilang tanpa jejak — dan log yang hilang terbaca sebagai "tidak ada
masalah", bukan "tidak ada yang terekam".

Rancangan awal tetap accommodates EXE: `productions\logs` saat source,
`%LOCALAPPDATA%\firefox-bridge\` saat `sys.frozen`. Keputusan owner: **tidak
ada rencana EXE**, sehingga tidak ada mode distribusi kedua yang butuh
menulis ke tempat lain. Cabang `sys.frozen` dihapus — kode untuk skenario
yang tidak akan pernah terjadi hanya menambah beban perawatan tanpa menutup
risiko apa pun.

Satu perbaikan robustness yang menyertai: kegagalan membuka log tidak boleh
menggagalkan start bridge. Handler file yang gagal sekarang memberi tahu di
console lalu melanjutkan console-only, karena log file adalah kenyamanan —
bukan produknya. Kalau tidak, direktori yang tidak bisa ditulis akan
mematikan layanan tepat saat Anda paling membutuhkan log itu.

Riwayat 465 baris tidak dihapus: dipindahkan ke lokasi baru, lalu ditappend
oleh server. Diverifikasi menjadi 467 baris setelah start, dengan dua baris
baru `server.start` dan `WebSocket connection attempting authentication`.

#### Tes yang menjaga

| Pengaman | Yang dijaga |
| --- | --- |
| `test_log_lands_in_the_project_folder_by_default` | Default ke `logs/bridge.log` di folder proyek |
| `test_log_dir_environment_variable_wins` | `FIREFOX_BRIDGE_LOG_DIR` menang atas default |
| `test_log_lines_are_json_and_carry_no_token` | Baris JSON valid, field ekstra ikut, token tidak masuk |
| `test_bridge_still_starts_when_the_log_cannot_be_written` | Log hilang → console tetap jalan, ada penjelasan |

### QA-007 — Cold start (LULUS, dan dua celah ditemukan)

Folder `saham\` dihapus pengguna seluruhnya, lalu `firefox-bridge-download.exe
--stocks NCKL --year 2025 --all-detected --delay 1` dijalankan dari nol.
Bukan regresi dari dua run sebelumnya yang semuanya `STEP SKIP` — semua jalur
pembuatan baru ikut teruji.

| Pemeriksaan | Hasil |
| --- | --- |
| Kondisi awal | `saham\` tidak ada sama sekali |
| Link terdeteksi | 4 (TW1, TW2, TW3, TW4) |
| Unduhan | 4 dari 4, staging → `saham\NCKL\2025\`, JSON ditulis tiap file |
| Jalur TW4 | `/Audit/NCKL/`, bukan `/TW4/` ✓ |
| Tampilan ZIP | `_T1_`…`_T4_` (underscore, bukan strip ganda) ✓ |
| Hash T4 | `27aead46…fccd` — **byte-for-byte identik** dengan file asli sebelum dihapus, untuk ketiga kalinya |
| Ukuran T1–T4 | 238.2 / 238.6 / 246.9 / 247 KB — sama persis dengan run sebelumnya |
| Duplikat | tidak ada |
| Exit code | 0 |

Jadi byte yang sama direproduksi ulang dari IDX pada cold start. Itu konfirmasi
mandiri bahwa hash di `download_history.json` bukan hanya konsisten dengan
file yang pernah ada, tetapi juga benar sebagai nilai yang dihitung ulang.

#### Celah 1: file log tidak merekam proses unduhan

Run WebSocket menambah **nol** baris ke `logs\bridge.log`. Sepanjang 465 baris
sejarah, `command` = 0 dan `method` = 0. Isinya hanya empat jenis pesan:

| Jumlah | Pesan |
| --- | --- |
| 253 | `Extension is polling over HTTP, not WebSocket` |
| 130 | `WebSocket connection attempting authentication` |
| 74 | `Command rejected: extension unavailable` |
| 18 | `Firefox bridge starting on http://127.0.0.1:8765` |

`JsonLineFormatter` sudah punya field `command`, `method`, dan `tab_id`, tapi
tidak ada satu pun yang mengisinya. Docstring modul menjanjikan log bisa
memeriksa "request traces", padahal yang terekam hanya siklus hidup dan
perubahan state transport. **Acceptance criteria OBS-001 — "log dapat
difilter tanpa print statement" — belum terpenuhi.** Jejak unduhan hanya ada di
terminal, tidak di file yang bisa difilter.

#### Celah 2: staging meninggalkan direktori kosong

`saham\staging\NCKL\2025\` tertinggal setelah file dipindahkan. Tidak ada file
sisa, jadi kriteria "tidak ada file tersisa" tetap terpenuhi, tapi foldernya
tidak dibersihkan. Harmless untuk satu saham; kalau SRC-011 memproses ribuan
saham, itu ribuan folder kosong yang menumpuk tanpa pernah dipakai lagi.

### OBS-001 — Jejak unduhan sekarang masuk log (LULUS)

Celah di atas diperbaiki. `firefox_bridge/progress.py` jadi satu-satunya jalan
keluar untuk narasi downloader: **satu panggilan, dua penerima**. Baris tetap
dicetak ke stdout persis seperti sebelumnya — jadi output terminal dan 144 test
yang memeriksa stdout tidak berubah — dan baris yang sama ikut terekam sebagai
record JSON dengan field yang bisa difilter.

```json
{"time":"2026-09-26T09:58:27+00:00","level":"INFO","logger":"firefox_bridge.progress",
 "message":"STEP DOWNLOAD OK: TW4 dipindahkan ke ... (252880 byte)",
 "stock":"NCKL","year":2025,"quarter":4,"bytes":252880}
```

Empat pemanggil, satu untuk tiap maksud:

| Pemanggil | stdout | Level di file |
| --- | --- | --- |
| `progress()` | stdout | `INFO` |
| `notice()` | stdout | `WARNING` — untuk WARN/MISMATCH yang tidak menghentikan run |
| `problem()` | stderr | `WARNING` — untuk kegagalan |
| `detail()` | tidak ada | `INFO` — fakta yang baru berguna belakangan |

`print(settings.token)` di `server.py` **sengaja tidak** disentuh dan ada test
yang menjaganya, karena token tidak boleh masuk log.

#### Empat cacat yang ditemukan saat mengerjakan ini

**1. `get_logger()` memakai penanda konfigurasi yang salah.** Sempat menulis
`if not logger.handlers: configure_logging()`. Itu salah karena "punya handler"
bukan berarti "sudah dikonfigurasi": pytest, atau aplikasi apa pun yang
memakai bridge, menempelkan handler-nya sendiri, pertanyaan itu menjawab ya,
dan file log **diam-diam dilewati tanpa error**. Log yang berhenti ada tanpa
sinyal adalah kegagalan paling buruk yang bisaikin log. Diganti penanda milik
sendiri (`_firefox_bridge_handler`), dan `configure_logging()` kini hanya
melepas handler miliknya sendiri, bukan mencabut milik orang lain.

**2. `logging` menelan record yang gagal di-JSON.** `WindowsPath` di field
menghasilkan `--- Logging error ---` di stderr, dan record-nya hilang dari file.
Tepat jenis kehilangan yang sedang diperbaiki. Field yang tidak serializable
sekarang diubah ke string: karakter yang hilang lebih murah daripada satu baris
yang hilang.

**3. Nama field yang kebetulan tabrakan dengan `LogRecord`.** `extra={"filename":
...}` melempar `KeyError` dari dalam pemanggilan logging — proses yang sedang
mencobaoeksi diri sendiri ikut mati. `safe_extra()` membuang nama seperti
`filename`, `module`, `name`, dan `msg`.

**4. Test suite mencemari log proyek.**|Regresi yang saya sebabkan sendiri.
Setelah downloader mulaiOccurs logging, `pytest` menambah 201 baris kode saham
palsu dan path `pytest-of-ORCA` ke `logs/bridge.log` — satu-satunya file yang
dibaca orang saat run sungguhan bermasalah. Diperbaiki dengan fixture
`isolated_log_dir` di `tests/conftest.py` yang mengarahkan seluruh suite ke log
sendiri. Diverifikasi: menjalankan `pytest` sekarang menambah **nol** baris ke
log proyek. 140 baris polusi lama dibersihkan.

#### Catatan: `run_id` belum ada

OBS-001 menyebut `run_id` untuk mengelompokkan satu invocasi CLI. Belum
dikerjakan karena butuh konteks run yang harus dioper dari `cli.py` ke seluruh
lapisan — bukan tempelan. `stock`/`year`/`quarter` sudah lebih dari cukup untuk
menyaring satu sesi.

#### Tes yang menjaga

| Pengaman | Yang dijaga |
| --- | --- |
| `test_progress_writes_to_stdout_and_to_the_file` | Satu panggilan sampai ke stdout **dan** file |
| `test_console_does_not_repeat_the_progress_line` | Baris tidak muncul dua kali (stdout bersih, stderr kosong) |
| `test_records_carry_the_fields_a_later_run_filters_on` | `stock`/`year`/`quarter` jadi key tingkat atas |
| `test_problem_uses_stderr_and_warns_in_the_file` | Kegagalan ke stderr dan `WARNING` |
| `test_notice_prints_to_stdout_but_warns_in_the_file` | Anomalan yang dapat dipulihkan tetap terlihat tapi tidak di INFO |
| `test_every_fielded_record_is_json_serialisable` | Field aneh jadi string, record tidak hilang |
| `test_reserved_field_names_are_dropped_rather_than_crashing` | `filename`/`module` tidak mematikan run |
| `test_a_foreign_handler_does_not_disable_the_file_log` | Handler milik orang lain tidak mematikan file log |
| `test_configure_logging_leaves_foreign_handlers_alone` | `configure_logging()` tidak mencabut handler orang lain |
| `test_get_progress_logger_reuses_the_bridge_handlers` | Progress logger tidak membuka file kedua |
| `test_run_summary_reaches_the_log` | Summary masuk log, kegagalan berlevel `WARNING` |
| `test_token_print_in_server_stays_a_plain_print` | `print(settings.token)` tidak pernah jadi record log |
| `isolated_log_dir` (conftest) | `pytest` tidak pernah menyentuh log proyek |

### SEC-003/004/005/006 — Jalur validasi ditutup (LULUS)

Empat task ini dikerjakan sebagai satu kesatuan karena bentuknya satu rantai:
setiap tahap mempercayai input dari tahap sebelumnya, dan sebelumnya setiap mata
rantainya kosong.

```
stock code dari CLI  ──►  SEC-003  ──►  saham/<CODE>/<YEAR>/
href dari halaman    ──►  SEC-004  ──►  URL yang di-fetch
byte dari download   ──►  SEC-005  ──►  arsip yang masuk folder final
token / Authorization──►  SEC-006  ──►  tidak pernah tercetak
```

#### Yang setiap cek Irakritikal

**SEC-003** menolak apa pun yang bukan alfanumerik ASCII sepanjang 1–10
karakter.otek Sengaja lebih sempit dari yang mungkin|IDX perlukan: kode asli
yang ditolak itu perbaikan satu baris, sedangkan path yang tertelusuri adalah
file yang ditulis di tempat tak terduga. Nama device Windows (`CON`, `NUL`,
`COM1`…) juga ditolak karena gagal di level OS jauh setelah folder chosen.
Dipasang di dua tempat: CLI (exit 2, sebelum tab browser dibuka) dan
`paths.py` — karena `paths` adalah choke point yang dilalui setiap download,
termasuk pemanggil programatik yang tidak lewat CLI.

**SEC-004** memeriksa **host**, bukan cuma nama file. `is_report_link` lama hanya
mencocokkan fragmen path, sehingga
`https://evil.example/…/Laporan%20Keuangan%20Tahun%202025/TW1/NCKL/inlineXBRL.zip`
memenuhinya — semua fragmennya cocok, hanya host-nya berbeda. Siapa pun yang
bisa menaruh anchor di halaman IDX akan memilih URL yang diambil program ini.
Host kini harus persis `www.idx.co.id` atau `idx.co.id`.

Dua detail yang membuat cek ini benar secara teknis: `unquote` diterapkan
sebelum pencocokan tahun/kuartal, karena IDX menulis spasi sebagai `%20`; dan
segmen saham harus cocok, karena IDX memuat laporan **semua** perusahaan, jadi
host yang benar saja tidak menjamin itu laporan emiten yang diminta.

Cek dipasang dua kali: saat pengenalan link, dan lagi tepat sebelum download —
karena di antara snapshot dan baris itu halaman bisa dirender ulang.

**SEC-005** berjalan di staging, **sebelum** file dipindahkan. Unduhan yang
terputus adalah kegagalan yang paling mungkin terjadi, dan menolaknya saat masih
di staging berarti membuangnya tidak incurs biaya apa pun. Signature, absence of
end-of-central-directory, keterbacaan, dan rasio kompresi diperiksa — rasio
terakhir menangkap zip bomb yang secara teknis ZIP yang valid.

**SEC-006** menjadikan pemindaian manual yang pernah saya lakukan jadi test
permanen: satu unduhan penuh dijalankan dengan token ada di environment, lalu
log, JSON history, stdout, dan stderr searching. Ditambah cek statis bahwa token
tidak pernah masuk emitter mana pun.

#### Dua bug yang ditemukan oleh test saya sendiri

**Nama device Windows lolos.** `candidate in _RESERVED_WINDOWS_NAMES`
membandingkan `"CON"` dengan set berisi `"con"` — huruf besar vs kecil, jadi
tidak pernah cocok. Dipperbaiki dengan `candidate.lower()`. Ini yang menarik: bug
yang hanya muncul di Windows, di tester yang kebetulan tidak memakai kode
berupa device name.

**Default falsy di helper test.** `entries or {…}` membuat `{}` (justru kasus
yang diuji) mengambil dict default yang berisi. Pola `X or Y` yang salah
membuat test melompatkan sesuatu yang tidak diuji sama sekali.

#### Catatan: `server.py` mengonfigurasi logging sebagai efek samping import

`server.py:15` memanggil `get_logger()` di level modul, jadi **mengimpor**
modul itu langsung membuka `logs/bridge.log`. Bukan kebocoran token — test
membuktikan nilai token tidak masuk file itu — tapi efek samping yang tidak
diharapkan pada modul entry point. Belum diperbaiki; dicatat.

#### Bukti bahwa guard tidak menolak data sungguhan

Cold start penuh dengan keempat guard aktif: 4 URL IDX asli diterima (termasuk
path `/Audit/` untuk TW4 dan `//Laporan` double-slash), 4 ZIP asli diterima,
hash identik dengan run-run sebelumnya, exit 0. Kalau validasi terlalu ketat,
run inilah yang akan menunjukkannya — bukan unit test.

Jalur negatif diuji dari CLI dan semuanya keluar **sebelum** browser dibuka:

```
--stocks '../evil'              -> exit 2  (hanya huruf dan angka ASCII)
--stocks 'NCKL/../../Windows'   -> exit 2  (terlalu panjang)
--stocks 'CON'                  -> exit 2  (nama device Windows)
--stocks 'NC KL'                -> exit 2  (spasi)
```

#### Tes yang menjaga

| Pengaman | Yang dijaga |
| --- | --- |
| `test_real_stock_codes_are_accepted` | 9 kode IDX nyata tidak ditolak |
| `test_codes_that_could_escape_the_download_root_are_rejected` | 13 varian traversal/illegal |
| `test_windows_device_names_are_rejected` | `CON`, `PRN`, `AUX`, `NUL`, `COM1`, `LPT9` |
| `test_paths_module_refuses_to_build_a_traversing_path` | `paths.py` menolak, bukan cuma CLI |
| `test_off_idx_urls_are_rejected` | 8 host/scheme palsu termasuk `idx.co.id.evil.example` dan `javascript:` |
| `test_url_for_another_stock_is_rejected` | Laporan emiten lain pada host yang benar ditolak |
| `test_tw4_must_come_from_the_audit_path` | URL `/TW4/` palsu ditolak |
| `test_link_parser_no_longer_recognizes_off_host_links` | Pengenalan link ikut menolak host asing |
| `test_a_truncated_archive_is_rejected` | Unduhan terputus ditolak |
| `test_a_zip_bomb_is_rejected` | Rasio kompresi mencurigakan ditolak |
| `test_a_full_download_writes_no_token_to_log_history_or_console` | 4 permukaan output searched |
| `test_no_emitter_is_ever_given_the_token` | Cek statis seluruh source |
| `test_the_token_subcommand_prints_without_writing_to_the_log` | Fitur baca-token tidak membuka log |

Gate: 222 pytest (dari 160), ruff, mypy 30 file, node --check, web-ext 0/0/0.

### Pembersihan root `project02` dan status `browser-bridge`

`productions` sudah menjadi satu-satunya rumah. Penghapusan dilakukan berdasar
ukuran, bukan asumsi: `productions` terbukti **superset** - `browser-bridge` tidak
punya satu pun modul yang tidak dimiliki `productions`, dan satu-satunya file
unik yang hilang bila dihapus adalah `tests/test_bulk_downloader.py`.

| Item | Aksi | Bukti |
| --- | --- | --- |
| `.playwright-mcp` | dihapus | 3 artefak basi 25 Sep; server playwright `disabled: true` |
| `Aggent` | dihapus | ketiga file SHA-256 identik dengan `.opencode\skills\agent-core\references` |
| `.opencode` | disimpan | skill `agent-core` yang aktif memuat darinya |
| `opencode.json` | disimpan | konfigurasi MCP hidup |
| `browser-bridge` | **ditunda** | masih ditunjuk `opencode.json` dan 1 proses masih jalan |

`Aggent` bisa dipastikan redundan karena `.opencode` mengikuti layout standar
`skills/<nama>/SKILL.md`, sedangkan `Aggent` tidak punya `skills/` maupun
`SKILL.md` — jadi tidak mungkin menjadi sumber skill. Perbandingan isi memakai
SHA-256, bukan perbandingan ukuran.

#### Kenapa `browser-bridge` tidak bisa langsung dihapus

Tidak karena isinya bermasalah, tapi karena `opencode.json` menunjuk `.venv` di
sana untuk menjalankan MCP server. Menghapus folder yang sedang dipakai proses
aktif berisiko meninggalkan file yang tidak terhapus dan tool mati.

Yang dilakukan sebagai persiapan:

1. Backup `opencode.json` → `opencode.json.bak`.
2. `command` dan `cwd` dialihkan ke `productions`.
3. **Command pengganti dibuktikan jalan** sebelum config disentuh: handshake MCP
   `initialize` dijawab, 11 tool terdaftar, tidak ada yang hilang dibanding
   katalog.
4. JSON divalidasi ulang, dan kedua path dipastikan ada.

Kalau ternyata ada yang tidak beres setelah restart, kembalikan dengan:

```powershell
Copy-Item C:\Users\ORCA\Downloads\project02\opencode.json.bak C:\Users\ORCA\Downloads\project02\opencode.json
```

Setelah restart dan tool `firefox-bridge` terbukti berfungsi, `browser-bridge`
(89 MB) baru aman dihapus. Bridge server di `127.0.0.1:8765` ikut mati karena
dihentikan pada sesi sebelumnya, jadi harus dinyalakan lagi.
