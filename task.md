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
| History JSON | Tervalidasi | `download_history_<year>.json` |
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
| PKG-001 | selesai | `python -m build` menghasilkan sdist + wheel; wheel dipasang ke venv kosong dan ketiga console script berjalan. `firefox_bridge.__version__` kini dibaca dari metadata, bukan literal |
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
Status tiap kriteria, diverifikasi bukan Planning:

| Kriteria | Status | Bukti / yang kurang |
| --- | --- | --- |
| Mesin Windows baru menjalankan `firefox-bridge-download` dari source/venv | teruji | Wheel dipasang ke venv kosong, 14 modul diimpor, 3 console script jalan, `history verify` 52 ok. Tidak ada mesin Windows fisik yang bersih, jadi ini proksi yang kuat, bukan bukti harfiah |
| 1 NCKL + minimal 10 stock lain | **terlampaui** | NCKL + 12; 13 saham, 52 report, 11 MB |
| T1–T4 untuk tahun yang sama | teruji | 52 report mencakup T1–T4 tiap emiten |
| **Run kedua mengunduh 0 file valid** | **BELUM** | Run 13 saham adalah run pertama. Tidak ada run kedua yang pernah dijalankan, jadi jalur skip pada data nyata belum diukur. Lihat §14 |
| Hash diverifikasi dan duplicate ditandai | teruji | `history verify`: 52 ok, 0 bermasalah, 0 orphan, 0 duplikat |
| **Kill process di tengah run, lalu resume** | **BELUM** | Prasyaratnya sudah ada (`STEP 0` scan staging, `discard_staged()`), tapi prosesnya belum pernah dibunuh di tengah unduhan nyata. Lihat §14 |
| Extension disconnect menghasilkan exit code dan pesan jelas | teruji | Exit 3 dari health gate `STEP 0.5` |
| Token tidak ditemukan di log/history/report | teruji | `tests/test_secret_redaction.py` + scan statis seluruh riwayat git |
| `pytest`, coverage, Ruff, type checker, `web-ext lint`, package build | teruji | 431 pytest, ruff bersih, mypy 39 file, web-ext 0/0/0, build succeed, coverage 88% |
| README menjelaskan instalasi, konfigurasi, resume, troubleshooting | teruji | Termasuk bagian config file (CLI-003) |

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
| CORE-004 | selesai | `downloader/errors.py`: `ExtensionDisconnected` (fatal), `StaleReference`, `DownloadTimeout`, `IntegrityError`; CLI menerjemahkan kegagalan transport ke taksonomi dan mencantumkan nama tipenya di pesan |
| CORE-005 | selesai | `downloader/retry.py`: backoff eksponensial 1s/2s/4s (cap 30s), 3 percobaan; hanya `StaleReference`/`DownloadTimeout`/`IntegrityError`; `discard_staged()` jadi pagar idempotensi sebelum tiap retry; `move` dan tulis JSON **tidak** ikut di-retry |
| CORE-006 | selesai | `pacing.py` terpusat; `parse_delay()` jadi satu aturan dan dipakai bersama oleh `--delay` dan config file, jadi batas 1 detik berlaku di kedua jalur. Default 3 detik pindah ke `runconfig.DEFAULTS` |
| CORE-007 | selesai | `downloader/health.py`: `ensure_extension_ready()` dijalankan sebagai STEP 0.5 sebelum workflow browser; kegagalan keluar sebagai exit 3 tanpa membuka tab |
| DATA-001 | berjalan | Skema v1 tetap; migration belum ada |
| DATA-002 | berjalan | Atomic write sudah ada; lock file belum |
| DATA-003 | selesai | `downloader/staging.py`: pindai `saham/staging/` di awal run (STEP 0), laporkan, lalu bersihkan file basi dan folder kosong; unduhan berjalan dipertahankan |
| DATA-004 | selesai | `DownloadResult` sekarang membawa `status` (downloaded/skipped), `year`, `quarter`, `sha256`, `bytes`, `attempts`; hash dibaca kembali dari history, tidak di-hash ulang |
| DATA-005 | selesai | Hash dicek sebelum skip dan mismatch memicu unduhan ulang |
| DATA-006 | selesai | `duplicate_of` diisi dan duplikat dicetak |
| DATA-007 | selesai | `downloader/audit.py` + `--history verify\|rebuild`: audit dua arah, termasuk mendeteksi **orphan** (arsip ada, JSON tidak); rebuild mempertahankan URL yang sudah diketahui |
| SEC-001 | berjalan | Token di luar project; rotasi dan permission belum |
| SEC-002 | berjalan | Bind loopback sudah ada; enforcement belum |
| SEC-003 | selesai | `normalize_stock_code` menolak non-alfanumerik, `..`, separator, nama device Windows, dan kode >10 karakter; dipasang di CLI (exit 2 sebelum browser dibuka) **dan** di `paths.py` sebagai choke point |
| SEC-004 | selesai | `validate_report_url` mewajibkan skema http/https, host persis `www.idx.co.id`/`idx.co.id`, tahun, segmen kuartal (`/TWn/` atau `/Audit/` untuk TW4), dan segmen saham yang cocok; dipasang di `is_report_link` **dan** tepat sebelum download |
| SEC-005 | selesai | `validate_archive` memeriksa ukuran, signature ZIP, end-of-central-directory, keterbacaan, jumlah entri, dan rasio kompresi; dijalankan di staging sebelum file dipindah |
| SEC-006 | selesai | `tests/test_secret_redaction.py`: unduhan penuh tidak menulis token ke log/JSON/console, plus cek statis bahwa token tidak pernah masuk emitter |
| CLI-001 | selesai | `firefox-bridge-download` terpasang dari `pyproject.toml` |
| CLI-002 | selesai | Exit code 0/1/2/3. Kode 3 (bridge/extension gagal) sudah diproduksi health gate `STEP 0.5` dan diuji di 4 file test |
| CLI-003 | selesai | `firefox_bridge/runconfig.py`: file TOML/JSON, ditemukan dari `--config` > `$FIREFOX_BRIDGE_CONFIG` > `firefox-bridge.toml` di working directory. Precedence CLI > file > default, dijalankan sekali di awal `run()`; argumen menimpa file, bukan digabung. Key tak dikenal, tipe salah, dan `delay` < 1 detik ditolak sebelum browser disentuh, semua pesan menyebut file dan key. Baris "config: <path> (kunci)" dicetak ke terminal, bukan hanya ke log |
| CLI-004 | selesai | `--stocks` dan `--stocks-file` (SQL dump / CSV, baris delisted dilewati), keduanya bisa digabung dan keduanya bisa datang dari config file |
| CLI-005 | berjalan | Skip otomatis ada; flag `--resume` belum eksplisit |
| CLI-006 | selesai | `downloader/planning.py` + `--dry-run`: keputusan skip/fetch dipindah ke `plan_for()` yang dipakai bersama oleh kedua jalur, jadi dry-run tidak bisa menyimpang dari kenyataan |
| CLI-007 | selesai | `--report PATH` menulis JSON run report berversi; ditulis atomik, di semua exit path termasuk saat run gagal, dan tidak pernah tercampur ke output progress |
| CLI-008 | berjalan | Noninteraktif sudah; belum ada flag eksplisit |
| QA-001 | selesai | 10 file test: pacing, selector, parser, flow, history, orchestrator, CLI, regresi IDX, kontrak extension, bridge/client/config |
| QA-002 | berjalan | `idx/` 89-100% dan `downloader/` 85-100% sudah melewati target 85%; total paket **88%** setelah config file masuk, tapi `mcp_server.py` dan `server.py` masih 0% |
| QA-006 | selesai | E2E live NCKL 2025 dari `productions` sudah dijalankan, dan setelah itu 13 saham / 52 report terverifikasi bersih |
| QA-003 | selesai | `ruff check .` lulus bersih, 0 error |
| QA-004 | selesai | `mypy` lulus, 39 source file tanpa isu |
| QA-005 | selesai | `web-ext lint` 0 error, 0 warning, 0 notice; `node --check` lulus untuk 3 file JS |
| QA-007 | berjalan | Kontrak extension dan cold start sudah dijaga test; fault injection jaringan (timeout HTTP, socket putus di tengah unduhan) belum |
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
| Hash | keempatnya cocok dengan `download_history_<year>.json` |
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
mandiri bahwa hash di `download_history_<year>.json` bukan hanya konsisten dengan
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

### DATA-003 — Scan staging di awal run (LULUS)

Dipilih bukan karena urutannya di daftar, tapi karena dua hal. Pertama, ini prasyarat
agar **retry (CORE-005) aman**: retry yang berjalan di atas file parsial yang
tidak diketahui asalnya justru memperburuk — itulah cara data korup tercatat.
Kedua, tanpa ini kriteria "kill di tengah run lalu resume" **tidak bisa diuji**,
karena run berikutnya berinteraksi dengan puing-puing run sebelumnya.

#### Bahaya yang diukur, bukan suspected

Run yang dibunuh di tengah unduhan meninggalkan file parsial di staging pada
path yang persis sama dengan tempat run berikutnya akan mengunduh — tanpa
sibling `.crdownload`. Diperiksa langsung:

```
staged (partial) : 4100 byte, tanpa .crdownload
is_download_complete(staged) = True
wait_for_completed_download  -> 4100 byte dalam 1.0 detik
HASIL: file partial yang tertinggal dianggap SELESAI
```

Logika `wait_for_completed_download` hanya mensyaratkan ukuran bertahan sama
dua kali sampel — dan file yang tidak sedang ditulis **selalu** begitu. Yang
menyelamatkan dari akibat terburuk adalah SEC-005: `validate_archive` menolak ZIP
terpotong, jadi file korup tidak pernah tercatat. Tapi run tetap gagal dengan
error yang membingungkan, dan file basinya tidak pernah hilang.

#### Invarian yang menutupnya

**Tidak ada yang ada di staging yang layak disimpan.** Arsip yang sudah selesai
di sana belum pernah ditulis ke history, jadi membuangnya hanya installations satu
unduhan ulang — dan menghilangkan kemungkinan file itu tertukar dengan file yang
sedang diunduh.

Pengecualian tunggal: unduhan yang benar-benar sedang berjalan. Firefox menyimpan
sibling `.crdownload`/`.part` selama seluruh transfer, dan itulah sinyal untuk
menyentirekan folder itu.

#### Satu bug yang Guidelines test saya sendiri

Versi pertama scan ikut **menghapus file `.crdownload` milik Firefox**. Saat
iterasi sampai ke file temp itu, ia memeriksa apakah ada `.crdownload` dari
`.crdownload` — yang tidak ada — lalu menganggapnya basi dan menghapusnya.
Artinya unduhan milik proses lain bisa dibatalkan oleh run berikutnya.

Test `test_an_in_flight_download_is_never_touched` menangkapnya: file target
tidak hilang, tapi assertion pada file temp-nya gagal.

Perbaikannya: file temp dikenali oleh sufiksnya dan **tidak** diproses sebagai
kandidat untuk dibuang. Dia dilewati selama target-nya masih ada, dan baru dibuang sebagai
yatim kalau target sudah hilang — itu yang meninggalkan run terputus setelah
arsipnya sempat terindah.

#### Bukti di lingkungan nyata

File parsial dan unduhan aktif dibuat sengaja di staging sungguhan, lalu run
dijalankan:

```
STEP 0: staging: 1 file basi dibuang, 1 unduhan berjalan dipertahankan
  - dibuang: NCKL_inlineXBRL_T1_2025.zip
  - dipertahankan (sedang diunduh): NCKL_inlineXBRL_T2_2025.zip
```

Folder `2025` ikut bertahan karena masih dipakai unduhan berjalan. Run
lanjutan selesai `Successful: 4, Failed: 0`, exit 0, hash T4 tetap
`27aead46…fccd`, keempat file utuh.

Folder staging yang saya buat untuk uji sudah dibuang, jadi Celah 2 QA-007
(folder kosong yang menumpuk) ikut tertutup.

#### Tes yang menjaga

| Pengaman | Yang dijaga |
| --- | --- |
| `test_the_measured_race_is_closed_by_scanning_first` | Race yang terukur ditutup: sebelum scan file terbaca selesai, sesudah scan tidak |
| `test_an_in_flight_download_is_never_touched` | Target **dan** file `.crdownload` utuh |
| `test_both_firefox_temp_suffixes_mark_in_flight` | `.crdownload` dan `.part` keduanya dikenali |
| `test_an_in_flight_download_keeps_its_directories` | Folder yang masih dipakai tidak diprune |
| `test_a_finished_staged_archive_is_also_discarded` | Arsip selesai di staging pun dibuang |
| `test_empty_directories_are_pruned_but_the_root_stays` | 6 folder terhapus, root staging tetap ada |
| `test_dry_run_reports_without_touching_anything` | `dry_run` tidak menyentuh filesystem |
| `test_a_second_scan_of_a_cleaned_folder_finds_nothing` | Scan kedua bersih |

Gate: 235 pytest (dari 222), ruff, mypy 31 file.

### CORE-004 + CORE-007 — Taksonomi error dan health gate (LULUS)

Keduanya dikerjakan bersama karena keduanya menjawab satu pertanyaan: **exit code
dan pesan yang jelas saat extension disconnect** — kriteria fase pertama yang
belum pernah terpenuhi.

#### Exit code 3 akhirnya bisa terjadi

CLI-002 sudah menjanjikan `3` untuk "bridge/extension gagal" sejak daftar tugas
ditulis. **Tidak ada yang bisa menghasilkannya**: CLI menangkap semua error
sebagai `Exception` umum, mencetak satu pesan, dan mengembalikan `1`. Run yang
kehilangan extension dan run yang kehilangan ref basi pada satu ref basi terlihat identik, dan run
terus mengulang kegagalan yang sama untuk setiap saham yang tersisa.

#### Yang membedakan bukan seberapa parah, tapi apakah saham berikutnya bisa jalan

Inilah yang jadi sumbu desainnya, dan ituatribut `fatal`:

| Tipe | `fatal` | Alasan |
| --- | --- | --- |
| `ExtensionDisconnected` | **ya** | Tidak ada yang bisa bekerja setelahnya juga |
| `StaleReference` | tidak | IDX me-render ulang; wajar, dan saham berikutnya dapat halaman baru |
| `DownloadTimeout` | tidak | Unduhan satu yang lambat, bukan lingkungan yang mati |
| `IntegrityError` | tidak | Satu arsip buruk, bukan semua |

Mematikan run karena satu ref basi akan berarti satu halaman flaky mengakhiri
batch ratusan saham. Itu kesalahan yang mahal dan tidak terlihat sampaiTerlambat.

#### Health gate: sebelum satu langkah browser pun

Membuka profil IDX_initializer took sekitar dua belas langkah ber-paced dan
setengah menit. Kalau extension tidak terhubung, setiap langkah itu gagal dengan
cara yang sama, dan pengguna dibiarkan membaca timeout daripada satu fakta yang
penting: nyalakan bridge, lalu tekan Connect.

`ensure_extension_ready()` classifies dua kondisi yang berbeda namun bagi
pengguna ini hal yang sama persis: bridge tidak berjalan (`ConnectError`) dan
bridge hidup tapi extension tidak menjawab (`connected: false`). Keduanya menjadi
`ExtensionDisconnected`, keduanya keluar sebagai exit 3, keduanya keluar
sebelum menyentuh browser.

#### Satu perbaikan yang datang dari test

`ensure_extension_ready()` awalnya memanggil `client.status()` **dua kali** untuk
satu fakta — sekali untuk classifies, sekali untuk mengambil versi. Itu dua
round-trip, dan ada jendela di mana extension bisa terhubung di antara keduanya,
sehingga hasil pemeriksaan dan versi yang dilaporkan menggambarkan dua momen
berbeda. Test `test_a_connected_extension_passes_the_gate` menangkapnya lewat
`IndexError` pada fake client. Sekarang satu pembacaan.

#### Bukti di lingkungan nyata

Bridge dimatikan, lalu CLI dijalankan:

```
PRAJAMAL: Bridge tidak berjalan di http://127.0.0.1:8765 (ConnectError)
  -> nyalakan bridge, lalu tekan Connect di popup extension
EXIT: 3   (durasi 2,4 detik)
```

Lalu bridge dinyalakan lagi, jalur sehat tetap utuh:

```
EXIT: 0
STEP 0.5: extension 0.1.8 (siap)
STEP 0: staging bersih
Successful: 4 | Failed: 0
hash T4: 27aead46…fccd
```

Cabang "bridge hidup, extension mati" diuji di level unit
(`test_a_bridge_with_no_extension_is_reported_not_raised`) karena mematikan
extension di Firefox tidak bisa dilakukan dari sini; kedua cabangberbisnis di
ke `ExtensionDisconnected` yang sama dan exit code yang sama, dan itu yang diuji.

#### Tes yang menjaga

| Pengaman | Yang dijaga |
| --- | --- |
| `test_only_losing_the_extension_is_fatal` | Hanya kehilangan extension yang `fatal` |
| `test_a_refused_connection_becomes_extension_disconnected` | `ConnectError` → fatal |
| `test_http_503_becomes_extension_disconnected` | 503 → fatal |
| `test_other_http_errors_are_not_treated_as_a_disconnect` | 400 **tidak** menghentikan run |
| `test_a_disconnected_extension_exits_3_before_touching_the_browser` | Exit 3, pesan menyebut Connect, tidak ada `STEP 1` |
| `test_a_fatal_failure_mid_run_exits_3_and_stops_early` | 3 saham diminta, hanya 1 dicoba |
| `test_a_non_fatal_failure_exits_1_and_continues` | Satu saham gagal, 2 lain tetap jalan |
| `test_the_failure_line_names_the_error_type` | Nama tipe ada di pesan console |
| `test_exit_codes_are_distinct` | 0/1/2/3 empat nilai berbeda |

Gate: 253 pytest (dari 235), ruff, mypy 33 file.

### CORE-005 — Retry hanya untuk yang transien (LULUS)

Paling berisiko dari semua task P0, karena menyentuh tindakan destruktif. Acceptance
criteria-nya sudah menyyaratkan dua hal, dan yang kedua justru yang berbahaya.

#### Aturan 1: hanya kegagalan transien

| Tipe | Di-retry? | Alasan |
| --- | --- | --- |
| `StaleReference` | ya | IDX me-render ulang; percobaan berikutnya bisa beda |
| `DownloadTimeout` | ya | Unduhan lambat sering kali jadi cepat |
| `IntegrityError` | ya | Bisa jadi unduhan terpotong, dan file basi dibuang dulu |
| `DownloaderError` (base) | **tidak** | Tak terklasifikasi: gagal sekali dan keras, bukan tiga kali senyap |
| `ExtensionDisconnected` | **tidak** | Sudah `fatal`; CLI menghentikan run |
| `ValidationError` | **tidak** | Kode saham atau URL salah akan jadi salah lagi |

`DownloaderError` dasar sengaja dikeluarkan. Tanpa itu, kesalahan baru yang belum
diklasifikasi akan diulang tiga kali sambil terdengar seperti masalah sementara.

#### Aturan 2: retry tidak boleh mewarisi puing sebelumnya

Ini bukan teori. Sudah diukur lebih dulu: file yang tidak sedang ditulis memenuhi
"ukuran bertahan sama dua kali sampel", jadi `wait_for_completed_download`
melaporkannya selesai. Meaningunanya **retry di atas file parsial membuat
percobaan kedua berlomba dengan file yang ukurannya cukup untuk menipu
pemeriksaan itu lagi.**

Setiap retry karena itu memanggil `discard_staged()` dulu: menghapus file staged
**beserta sibling `.crdownload` dan `.part`**. Tanpa itu, retry bukan cara menghindari
arsip korup, tapi cara mendapatkannya.

#### Yang tidak di-retry sama sekali: `move` dan tulis JSON

Wilayah retry hanya dari `_fetch_archive` — baca ulang halaman, minta download,
tunggu, validasi. `move_completed_download` dan `record_download_history` berada
di luar dan berjalan **tepat sekali**. Mengulang langkah destruktif tanpa
memeriksa apakah sudah terjadi itulah cara satu laporan tercatat dua kali.
Ada test khusus untuk ini: dua kegagalan lalu sukses, dan history harus berisi
tepat satu entri.

#### Path yang dipakai Firefox, bukan yang diminta

Firefox boleh menjawab dengan nama file berbeda dari yang diminta
(`... (1).zip`). Kalau `discard` membersihkan path hasil **tebakan**, file
aslinya tertinggal — persis file yang akan disalahartikan sebagai unduhan
selesai. Jadi `_fetch_archive` mencatat path yang benar-benar ditulis, dan
`discard` memakai daftar itu.

#### Dua bug yang ditemukan test sendiri

**Test fake tidak konsisten sehingga suite menggantung 6 menit.** Fake saya
melaporkan satu path tapi menulis path lain, lalu menunggu timeout asli 180
detik. Satu kasus = 3 menit, cukup panjang untuk test rusak bersembunyi
beberapa run. Fake diperbaiki agar melapor dan menulis path yang sama, dan
ditambah fixture `isolated_timeout` yang memaksa timeout 1 detik di test —
supaya kesalahan serupa menggagalkan cepat, bukan menggantung.

**CATATAN PENTING: pytest menghapus isi folder Downloads Anda**

Ini ditemukan saat memperbarui CLI, dan **bukan kosmetik**.

Semua path helper menerima download root opsional dan jatuh ke
`%USERPROFILE%\Downloads` kalau tidak diberi. Test CLI yang memanggil `run()`
tanpa `--download-dir` karena itu resolve ke folder **sungguhan**, dan
`scan_staging` lalu menghapus file basi serta memangkas folder di sana.

Diukur, bukan dibaca kode: sebuah folder penanda di staging sungguhan **tidak
bertahan** setelah `pytest` penuh — 276 test, semuanya hijau, penanda hilang.
Kelas bug yang sama dengan polusi `logs/bridge.log` yang sudah diperbaiki, tapi
lebih berbahaya karena yang hilang adalah data pengguna, bukan baris log.

Perbaikannya: fixture `isolated_download_dir` di `conftest.py` mengarahkan
seluruh suite ke root sementara lewat `FIREFOX_BRIDGE_DOWNLOAD_DIR`, membuat
fallback ke folder asli tak terjangkau dari test. Diverifikasi: penanda sekarang
**bertahan** melewati `pytest` penuh.

Ada test yang mengunci fixture ini aktif, supaya menghapusnya gagal di test
bukan diam-diam di folder orang.

#### Tes yang menjaga

| Pengaman | Yang dijaga |
| --- | --- |
| `test_permanent_failures_are_never_retried` | 3 tipe permanen tidak di-retry |
| `test_the_base_error_is_excluded_on_purpose` | `DownloaderError` dasar tidak masuk daftar |
| `test_backoff_grows_and_is_capped` | 1s/2s/4s, capped 30s |
| `test_discard_removes_the_staged_file_and_its_temp_siblings` | File + `.crdownload` + `.part` terhapus |
| `test_a_permanent_failure_is_not_retried` | Percobaan hanya 1 |
| `test_attempts_are_finite_and_the_last_error_propagates` | Maksimal 3, error terakhir naik |
| `test_discard_runs_before_every_retry` | Pagar idempotensi dipanggil |
| `test_the_report_is_moved_and_recorded_exactly_once` | 3 percobaan → history tetap 1 entri |
| `test_retries_exhausted_leaves_no_staged_file_behind` | Tidak ada laporan tercatat |
| `test_the_path_firefox_actually_used_is_the_one_cleaned` | Path asli, bukan hasil tebakan, yang dibersihkan |
| `test_a_cli_run_without_download_dir_stays_inside_the_suite` | Root suite bukan folder Downloads |

Gate: 276 pytest (dari 253), ruff, mypy 34 file.

### DATA-007 — `history verify` dan `history rebuild` (LULUS)

Dipilih karena inilah yang membuat kriteria "10 saham" bisa dipercaya: setelah 40
arsip terunduh, orang perlu cara memverifikasi semuanya tanpa mengunduh ulang.

```
firefox-bridge-download --history verify
firefox-bridge-download --history rebuild
```

Keduanya offline: tanpa browser, tanpa bridge, tanpa pacing. Itu disengaja —
`verify` adalah alat yang justru Anda pakai **karena** tidak memercayai
keadaan saat ini, jadi ia tidak boleh bergantung pada keadaan itu.

#### Audit dua arah, dan arah yang mudah terlewat

History adalah kesepakatan dua pihak: JSON mengatakan file ada, dan file benar-benar
ada dengan hash cocok. Salah satu bisa menyimpang. Tapi yang mudah terlewat adalah
arah kedua — **arsip yang sudah dipindah tapi catatannya belum tertulis**, yaitu
persis yang meninggalkan run yang dibunuh di antara `move` dan tulis JSON.

Mencari hanya masalah yang sudah diketahui history akan melewatkan file-file itu, dan run
berikutnya mengunduh semuanya lagi tanpa alasan. Jadi `verify` melaporkan
**orphan** juga.

| Status | Arti |
| --- | --- |
| `OK` | File ada, hash cocok, arsip terbaca |
| `MISSING` | JSON mencatat, file tidak ada di disk |
| `MISMATCH` | File ada, hash tidak cocok dengan JSON |
| `CORRUPT` | Hash cocok, tapi isinya bukan arsip yang bisa dibaca |
| `ORPHAN` | File ada di disk, tidak tercatat di JSON |

Orphan **tidak** membuat laporan `unhealthy`: arsip yang belum tercatat adalah
sesuatu untuk diadopsi, bukan arsip yang rusak. `verify` keluar `1` hanya kalau ada
masalah sungguhan.

#### `rebuild` adalah perbaikan, bukan reset

Ini cacat desain yang saya temukan sendiri saat menguji di data nyata. Versi
pertama membangun ulang JSON **dari nol**, sehingga URL yang sudah diketahui di
semua entri ikut hilang. Itu reset yang memakai nama alat perbaikan — dan URL
adalah satu-satunya hal yang tidak bisa direkonstruksi dari nama file.

Perbaikannya: `rebuild_history` menerima `existing` dan mempertahankan URL serta
`completed_at` yang sudah tercatat. Verified pada data nyata dengan entri TW2
dihapus dari JSON:

```
TW1: recovered=True   url=https://www.idx.co.id/Portals/0/StaticData/L...
TW2: recovered=False  url=None
TW3: recovered=True   url=https://www.idx.co.id/Portals/0/StaticData/L...
TW4: recovered=True   url=https://www.idx.co.id/Portals/0/StaticData/L...
```

TW2 yang benar-benar hilang dari JSON tetap `None` dengan `url_recovered: false` —
lubang yang jujur, bukan tebakan yang terlihat meyakinkan. Run berikutnya akan
mengisinya. Sha256 TW2 yang pulih cocok dengan hash aslinya.

`rebuild` juga menolak menulis JSON sama sekali kalau ada arsip yang tidak bisa
dibaca, supaya file korup tidak ikut terekam.

#### Kompatibilitas

`--stocks` tidak lagi `required`, karena `--history` tidak butuh kode saham.
Pemeriksaan dipindah ke `run()`: tanpa `--history` dan tanpa `--stocks`, keluar
`2` dengan pesan yang menyebut keduanya. Kontrak lama
`firefox-bridge-download --stocks NCKL ...` tetap berjalan tanpa perubahan.

#### Tes yang menjaga

| Pengaman | Yang dijaga |
| --- | --- |
| `test_a_deleted_file_is_reported_missing` | File hilang terdeteksi |
| `test_a_modified_file_is_reported_as_a_mismatch` | Hash beda terdeteksi, kedua hash ditampilkan |
| `test_a_file_matching_its_hash_but_not_a_zip_is_corrupt` | Hash benar pun arsip bisa korup |
| `test_an_orphan_is_reported` | Arsip tanpa catatan terdeteksi |
| `test_an_orphan_does_not_make_the_report_unhealthy` | Orphan bukan "sakit" |
| `test_verify_writes_nothing` | `verify` benar-benar read-only |
| `test_a_file_named_like_nothing_recognisable_is_ignored` | ZIP asing tidak dicatat di bawah kode tebakan |
| `test_rebuild_keeps_urls_the_old_history_already_knew` | Rebuild bukan reset |
| `test_rebuild_fills_the_gap_for_an_entry_the_history_never_knew` | Lubang jujur, bukan tebakan |
| `test_a_rebuilt_history_verifies_cleanly` | Hasil rebuild yang `verify` setujui |
| `test_history_verify_does_not_need_a_bridge` | Offline, tidak butuh extension |
| `test_the_cli_still_requires_stocks_without_history` | Kontrak lama utuh |

Gate: 299 pytest (dari 276), ruff, mypy 35 file.

### CLI-007 + DATA-004 — JSON run report (LULUS)

Dikerjakan berpasangan karena sebenarnya satu pekerjaan: laporan JSON adalah tempat
status report tinggal. Dipilih sebelum run 10 saham, karena 40 file butuh catatan
yang bisa dipantau — bukan hanya output terminal yang harus di-scroll.

```
firefox-bridge-download --stocks ... --report logs\run.json
```

Tidak ada default. Flag-nya opsional, dan tanpa flag tidak ada file yang ditulis
di mana pun — menebak lokasi laporan lebih buruk daripada-none.

#### Dua pembaca, dua format

Output console ditulis untuk orang yang menonton. Laporan ditulis untuk sesuatu
yang **tidak ada** ketika run berlangsung: scheduler, dashboard, atau alat lain
yang memutuskan perlu bertindak atau tidak. Itu alasan keduanya dipisah — mencampur
keduanya membuat keduanya tidak terbaca, dan console bukan format log.

`test_the_report_never_appears_in_the_progress_output` mengunci itu. Schema
diversikan sejak awal: laporan dibaca program, dan program yang harus menebak
apakah sebuah field masih berarti apa seperti bulan lalu adalah program yang
pelan-pelan diam-diam rusak.

#### Yang paling penting: laporan tetap ditulis saat run gagal

Dilewati lewat satu helper `_finish()` yang dipanggil di **semua** exit path,
termasuk gate yang gagal sebelum browser disentuh. Laporan yang hilang tepat
ketika run bermasalah adalah justru laporan yang paling dibutuhkan.

```
$ run dengan extension mati
$ --report run.json
  report.environment.extension_version == ""
  report.counts.failed == 0
  report.exit_code == 3
```

#### `counts` memisahkan yang diunduh dari yang dilewati

"4 ok" menyembunyikan satu-satunya pertanyaan yang penting: ada benar-benar
terunduh sesuatu? Laporan memisahkan `downloaded` dari `skipped`, dan menambah
`bytes` total. Terbukti pada data nyata setelah T3 dihapus:

```
counts : {'processed': 4, 'downloaded': 1, 'skipped': 3, 'failed': 0, 'bytes': 993883}
NCKL 2025 TW1  skipped      243904 byte  attempts=1  6bfaad2ed340...
NCKL 2025 TW2  skipped      244307 byte  attempts=1  2821f7bd7e30...
NCKL 2025 TW3  downloaded   252792 byte  attempts=1  a3c8a83992c5...
NCKL 2025 TW4  skipped      252880 byte  attempts=1  27aead4699e9...
```

TW3 terunduh ulang dengan hash `a3c8a83992c5…` — identik dengan run-run
sebelumnya, jadi reproducibility byte-for-byte terkonfirmasi sekali lagi dari
arah yang berbeda.

#### Hash dibaca kembali, bukan dihitung ulang

`record_download_history` sudah meng-hash file tersebut. Meng-hash lagi hanya
untuk mengisi field laporan akan menggandakan IO setiap unduhan tanpa untung,
jadi `_make_result()` membaca entri yang baru ditulis.

#### Penulisan atomik, dan kegagalan tidak prosecute run

Monitor yang mem-poll file ini tidak boleh pernah menangkapnya setengah tertulis —
JSON terpotong akan terbaca sebagai run yang gagal, yang merupakan kebohongan
tentang run. Dan laporan yang gagal ditulis tetap diberi peringatan di console
serta **tidak** menggagalkan run: unduhan yang sudah selesai tidak boleh hilang
karena sebuah file laporan tidak bisa dibuat.

#### Tes yang menjaga

| Pengaman | Yang dijaga |
| --- | --- |
| `test_the_report_is_versioned` | `schema_version` ada |
| `test_the_report_describes_what_was_requested` | Laporan menyebut saham yang diminta |
| `test_counts_separate_downloads_from_skips` | "4 ok" tidak menutupi "0 unduhan" |
| `test_each_result_carries_its_own_hash_and_attempts` | Hash/ukuran/percobaan per hasil |
| `test_a_failure_carries_its_type_so_a_consumer_need_not_parse_a_message` | Tipe error di laporan |
| `test_writing_is_atomic` | Tidak ada `.tmp` tertinggal |
| `test_no_report_file_means_no_file` | Tanpa flag, tanpa file |
| `test_the_report_never_appears_in_the_progress_output` | Kedua format terpisah |
| `test_a_report_is_written_even_when_the_extension_is_missing` | Laporan tetap ada saat gagal |
| `test_an_unwritable_report_does_not_fail_the_run` | Unduhan tidak hilang gara-gara laporan |

Gate: 313 pytest (dari 299), ruff, mypy 36 file.

### CLI-006 — `--dry-run` (LULUS)

Satu-satunya mode yang seluruh nilainya terletak pada apakah ia bisa dipercaya.
Kalau ia berbeda dari jalur nyata **ke arah yang menenangkan**, ia lebih buruk
daripada tidak ada — karena itu arah yang tidak pernah diperiksa orang.

#### Dua hal yang membuatnya bisa dipercaya

**Keputusan dipindah ke satu fungsi.** `plan_for()` menjawab "lewati atau
unduh?" untuk satu laporan, dan **jalur nyata serta dry-run memanggil fungsi yang
sama**. Dry-run yang mengimplementasikan ulang aturan itu bisa berbeda dari
kenyataan, dan tidak ada yang akan fastening memperhatikannya.

Fungsi ini juga memanggil `validate_report_url`, jadi dry-run **menolak** URL
off-host persis seperti jalurnya — tidak bisa melaporkan "aman" untuk sesuatu yang
run akan tolak.

**Dry-run berhenti sebelum scan staging.** Scan itu *menghapus* file. Melaporkan
dengan jujur sambil diam-diam membersihkan puing bukan dry-run, dan itu jenis
kejutan yang baru terlihat ketika unduhan parsial yang someone's cared about
hilang. Ada test yang membuat `scan_staging` meledak jika dipanggil.

#### Bug yang ditemukan test: laporan dry-run selalu kosong

`_run_dry_run` membuat `summary` dan `failures` sendiri, tapi `finish` menutup
yang milik pemanggil — sehingga laporan JSON selalu melaporkan `processed: 0`
untuk dry-run yang memeriksa beberapa laporan. Itu laporan yang berbohong, dan
sekarang keduanya memakai satu summary.

Ada detail lain yang mudah terlewat: dry-run yangSkip tapi history-nya belum
lengkap **akan menulis ulang JSON** saat run nyata. `history_needs_update`
menandainya secara terbuka, karena "tidak ada yang berubah" akan jadi dusta.

#### Yang tetap dipakai: browser

Dry-run tetap membuka profil dan membaca link, karena "apa yang *akan* diunduh"
adalah sifat halaman, bukan sifat history saja. Yang berbeda: tidak ada file yang
ditulis, tidak ada history yang disentuh, tidak ada unduhan yang dimulai.

Gate: 377 pytest (dari 362), ruff, mypy 38 file.

### QA-008 — Run 10 saham sungguhan (36 file, LULUS)

Ini kriteria fase pertama yang paling berat, dan pertama kali
bukan test — test hanya bisamiau whoever. 10 saham, tahun 2025, semua kuartal.

```
NCKL, BBCA, BBRI, BMRI, TLKM, ASII, UNVR, ICBP, ANTM, ADRO
```

| Pemeriksaan | Hasil |
| --- | --- |
| Link terdeteksi | 4 per saham, 40 total |
| Hasil | 36 report, 8.4 MB |
| `history verify` | **36 ok, 0 bermasalah, 0 tanpa catatan** |
| Laporan JSON | `processed 36, downloaded 32, skipped 4, failed 1` |
| Exit code | 1 (karena 1 saham gagal — bukan 0, dan bukan berhenti) |
| Jumlah tab browser | **1** (`tab_id=53`) — satu profil dipakai ulang, sesuai aturan |
| Urutan langkah | profil → Laporan Keuangan → tahun; tidak ada input tahun ke `Search Company Code` |
| Jalur TW4 | `/Audit/` di semua saham ✓ |

`skipped 4` adalah NCKL: sudah ada dari run sebelumnya, jadi **jalur skip dan
jalur unduh keduanya teruji dalam satu run yang sama**.

#### Kegagalan UNVR, dan apa yang sebenarnya terjadi

```
FAILED UNVR 2025 [DownloaderError]: Kontrol tahun tidak muncul dalam 5 detik
setelah membuka 'Laporan Keuangan' untuk UNVR
```

Run **tidak berhenti** — ICBP, ANTM, ADRO tetap diproses. Itu jalur non-fatal
bekerja. Tapi penyebabnya tidak boleh diasumsikan, jadi halaman UNVR dibuka
manual dan diklik sendiri:

```
e31 heading Laporan Keuangan Tahun 2026. Periode TW2
e35 searchbox 2026 Loading...
```

Kontrol year's **ada**. Halaman UNVR berat (94 elemen) dan butuh lebih dari 5
detik untuk merender panel, dengan field IDX sendiri masih membaca
"Loading...".

Jadi ini **bukan** bug selector dan bukan bug retry: `timeout` kontrol tahun
hanya 5 detik, sementara setiap tunggu lain di rantai yang sama dapat 12–45
detik (tab 45s, profil 30s, deteksi link 12s). Angka 5 itu outlier, bukan
batas yang pernah dipikirkan. Dinaikkan ke **20 detik** lewat konstanta bernama
`YEAR_CONTROL_TIMEOUT_SECONDS`, lalu diuji ulang: UNVR lewat 4 dari 4, exit 0.

#### Lapisan yang harus dijaga

Error itu tetap `TimeoutError` bawaan, **bukan** `DownloadTimeout`. Percobaan
pertama mengimpor `downloader.errors` ke dalam `idx/browser_flow.py` dan
langsung menghasilkan circular import — karena `downloader/__init__` memanggil
orkestrator yang memanggil `idx/browser_flow`.

Lapisan halaman tidak boleh mengimpor lapisan file. Translasi ke taksonomi
dilakukan di batasnya, di `_as_downloader_error`, tempat semua translasi
batas lain sudah terjadi. Test menyassert `TimeoutError` secara spesifik supaya
jalan pintas import di masa depan langsung patah.

Tambahan 3 saham (BBNI, BBTN, PGAS) dijalankan terpisah: 12 report, 0 gagal,
exit 0. Rekapitulasi seluruh run:

| | |
| --- | --- |
| Saham | **13** (NCKL + 12 lain) |
| Report | **52 file, 11 MB** |
| `history verify` | **52 ok, 0 bermasalah, 0 tanpa catatan** |
| `integrity_status` | 52 `verified`, 0 selain itu |
| Duplikat | 0 |
| Saham gagal | 0 — UNVR berhasil setelah timeout diperbaiki |
| Sisa staging | kosong |

Kriteria "1 NCKL + minimal 10 stock lain" terlampaui: NCKL + 12.

Gate: 377 pytest, ruff, mypy 38 file.

### Package build (LULUS) + coverage diukur

Kriteria fase pertama terakhir: "`pytest`, coverage, Ruff, type checker,
`web-ext lint`, dan package build lulus".

```
firefox_bridge-0.2.0.tar.gz        191.3 KB
firefox_bridge-0.2.0-py3-none-any.whl  73.9 KB
```

Build berjalan dalam mode isolated, dan detail pentingnya adalah
`Building wheel from sdist` — wheel dibangun **dari** sdist, jadi sdist-nya
terbukti lengkap karena wheel tidak akan berhasil kalau ada berkas hilang.

#### Uji clean install, dan satu kesalahan yang saya buat

Wheel dipasang ke venv kosong, lalu 14 modul diimpor dan ketiga console script
dijalankan. **Uji pertama saya tidak benar**: cwd-nya `productions`, jadi
`import firefox_bridge` resolve ke source tree, bukan ke wheel — folder proyek
men-*shadow* paket terpasang. Uji "clean install" itu sebenarnya tidak menguji
apa pun yang sudah dibangun. Diulang dari direktori netral, import baru resolve
ke `site-packages`.

Dari instalasi wheel itu: `history verify` melaporkan 52 ok, dan shim lama
`firefox_bridge.tools.bulk_downloader` tetap jalan.

#### Dua cacat yang ditemukan proses ini

**Versi paket berbohong.** `firefox_bridge.__version__` = `0.1.0` sementara
metadata = `0.2.0`. Dua sumber kebenaran, dan tidak ada test yang gagal selama
berbulan-bulan karena tidak ada yang mengadu keduanya. Sekarang dibaca dari
`importlib.metadata`, jadi tidak ada lagi tempat untuk menulis angka kedua.

**`history verify` memberi all-clear palsu.** Diuji dengan
`--download-dir ...\saham` (salah satu level), hasilnya `0 ok, 0 bermasalah`
— persis seperti pustaka yang sudah diverifikasi bersih dan utuh. Sekarang
folder `saham/` yang tidak ada menghasilkan exit 2 dengan pesan yang menyebut
level mana yang harus diberi, dan pustaka yang ada tapi kosong diberi catatan
"tidak ada laporan untuk diverifikasi" di stdout.

#### Coverage: 86% (pertama kali diukur)

| Modul | Cover | Catatan |
| --- | --- | --- |
| `cli.py` | 94% | |
| `downloader/retry.py` | 98% | |
| `downloader/history.py` | 97% | |
| `downloader/integrity.py` | 96% | |
| `downloader/staging.py` | 95% | |
| `validation.py` | 95% | |
| `downloader/health.py` | 93% | |
| `downloader/audit.py` | 90% | |
| `idx/browser_flow.py` | 89% | |
| `downloader/orchestrator.py` | 84% | |
| `bridge.py` | 72% | jalur HTTP banyak cabang |
| `config.py` | 64% | |
| `client.py` | 67% | |
| **`mcp_server.py`** | **0%** | tidak pernah diuji |
| **`tools/bulk_downloader.py`** | **0%** | shim, sudah diuji sekarang |

Dua modul 0% yang tersisa dicatat, bukan disembunyikan. `mcp_server.py`
adalah adapter MCP yang **sudah dipakai** (tool `firefox-bridge` di sesi ini
berjalan darinya) tetapi belum punya test. Itu gap yang paling jelas, dan kandidat
pekerjaan berikutnya.

Shim yang 0% sekarang punya test: ia harus benar-benar menjalankan program yang
sama — opsi `--stocks-file`, `--history`, `--dry-run`, `--report` semuanya
harus ada, dan kode saham tidak valid harus keluar 2. Shim yang jalan tapi
membuang opsi akan lebih buruk daripada yang gagal, karena perintah lama terlihat
bekerja sambil melakukan sesuatu yang lain.

Gate: 388 pytest, ruff, mypy 38 file, web-ext 0/0/0, build succeed, shim OK,
coverage 86%.

### CLI-003: config file (P0 terakhir)

Dengan ini **tidak ada lagi item P0 berstatus `belum`**.

`firefox_bridge/runconfig.py`. Precedence: **command line > file > default**,
diselesaikan sekali di awal `run()` supaya setiap cabang berikutnya — history,
dry run, download — bekerja dari nilai yang sama.

#### Dua keputusan yang terlihat seperti kelalaian

`history` tidak bisa diisi dari file. `history = "rebuild"` akan menulis ulang
history JSON pada setiap run terjadwal yang tidak diawasi, dan itu perintah
perbaikan yang harus diketik ketika memang dimaksudkan.

`all_quarters` juga tidak. Ia alias lama dari `all_detected`; membolehkan file
mengisinya berarti satu keputusan punya dua nama di dalam satu file, dan tidak
ada yang menang kalau keduanya diisi.

#### Aturan tidak bisa dilanggar lewat file

`delay = 0.4` di file ditolak dengan pesan yang sama seperti `--delay 0.4`.
Batas 1 detik ada karena halaman IDX, bukan karena CLI; aturan yang hanya
dijalankan di salah satu jalur adalah aturan yang pada akhirnya akan dilewati
lewat jalur yang lain. `year` sekarang juga dibatasi 1990–2100, dan batasnya
dipakai bersama oleh file dan command line: `year = 20255` dulu diterima lalu
mengunduh nol file, yang terlihat sama persis dengan "sudah terunduh".

#### Dua cacat yang ditemukan, satu hanya lewat uji nyata

**BOM ditolak `tomllib`.** Config yang ditulis PowerShell, Notepad, atau Visual
Studio semuanya memakai byte-order mark di Windows, dan `tomllib` menolaknya
dengan `Invalid statement (at line 1, column 1)` — error yang menyalahkan kolom
pertama dari baris yang syntax-nya memang benar, sehingga pembaca mencari
kesalahan syntax yang tidak ada. **Ini tidak akan pernah ketahuan oleh test**,
karus test menulis file-nya sendiri dengan encoding yang benar. Baru ketahuan
saat menjalankan CLI sungguhan dengan file yang ditulis PowerShell. Sekarang
dibaca sebagai `utf-8-sig`.

**`detail()` tidak mencetak ke terminal.** Baris config awalnya hanya masuk ke
log file. Padahal file config bisa mengubah jalannya run tanpa ada yang
mengetik argumen, jadi hal yang paling tidak boleh terjadi adalah itu tidak
tampak. Sekarang ke stdout, dan hanya ketika memang ada file yang dipakai.

#### Yang diverifikasi

`--config` eksplisit, `$FIREFOX_BRIDGE_CONFIG`, dan penemuan otomatis diuji
semuanya dari CLI sungguhan, termasuk dari instalasi wheel di venv bersih.
`stocks` dari file tetap melewati validasi kode saham (choke point di
`paths.py` tidak peduli dari mana asalnya), dan test mengunci itu.

`firefox-bridge.example.toml` ikut di-commit dan **diuji bisa dimuat** — contoh
yang sudah tidak bisa di-parse lebih buruk daripada tidak ada, dan tidak pernah
dijalankan manual sehingga akan membusuk diam-diam.

Gate: 431 pytest, ruff, mypy 39 file, web-ext 0/0/0, build succeed, wheel teruji
dari venv bersih, `runconfig.py` 100% coverage, total paket 88% (dari 86%).


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

## 14. Lanjut Sesi Berikutnya

> **Prioritas per 7 Oktober 2026 — baca dulu [`rencana-katalog.md`](rencana-katalog.md)**
> sebelum melanjutkan apa pun. Di sana ada: keadaan run 785 yang **berjalan
> menuju 785/785** (PID aslinya mati waktu server di-restart, jadi run
> dijalankan ulang dengan kode `98b9199`; kueri pencarian PID, perintah
> jeda/resume dan perintah restart semuanya tercatat di sana), temuan API
> `GetFinancialReport` beserta bukti bahwa API itu tidak
> terbaca lewat bridge, rencana empat langkah menuju unduhan berbasis katalog,
> dan daftar 15 jebakan yang sudah pernah memakan waktu.
>
> Satu larangan yang penting: **langkah 2 rencana itu (endpoint `evaluate`)
> mengubah ekstensi, dan ekstensi yang diubah wajib di-reload — yang akan
> mematikan sesi downloader.** Kerjakan hanya setelah run 785 selesai.

Semua item P0 sudah `selesai` atau `berjalan`; tidak ada lagi berstatus `belum`
kecuali SRC-001 sampai SRC-011, yang memang fase 2 (MariaDB) dan belum disentuh
sengaja. Sesi berikutnya sebaiknya **menutup dua kriteria §8 yang masih BELUM**,
karena keduanya satu-satunya klaim "production-ready" yang belum pernah diukur,
dan keduanya bisa menguji jalur yang paling merusak data.

### 14.1 Run kedua harus mengunduh 0 file (pertama, karena murah dan informatif)

Ini sekaligus menguji skip, integritas hash, dan deduplikasi terhadap 52 file
nyata, bukan fixture. Yang diukur, bukan diasumsikan:

1. Pastikan bridge hidup dan extension Connect.
2. Jalankan run yang sama persis dengan run 13 saham.
3. Yang diharapkan: `52 skipped`, `0 downloaded`, exit 0.
4. Lalu `history verify` -> harus tetap 52 ok, 0 bermasalah, dan **tidak ada
   duplikat baru** yang muncul karena file yang sama terunduh dua kali.
5. Periksa ukuran file sebelum dan sesudah. Run kedua yang menulis ulang file
   akan mengubah ukurannya; itu bukti langsung bahwa skip tidak bekerja.

Kalau ternyata muncul unduhan, jangan langsung "diperbaiki" — catat dulu saham
dan kuartal mana yang lolos, karena itu berarti keputusan skip di `planning.py`
tidak sama dengan kenyataan, dan itu bug yang harus dibaca, bukan ditembak.

### 14.2 Kill di tengah run, lalu resume

Prasyaratnya sudah ada: `STEP 0` memindai staging dan membuang sisa basi, dan
`discard_staged()` jadi pagar idempotensi sebelum tiap retry. Yang belum pernah
dilakukan adalah membunuh prosesnya.

1. Pilih satu emiten yang masih punya report TW2/TW3 yang belum ada, supaya
   ada kerja nyata yang bisa terpotong.
2. Jalankan run, lalu `Stop-Process` pada `firefox-bridge-download` **saat satu
   unduhan berjalan**, bukan sebelum atau sesudah.
3. Pastikan ada file parsial di `saham/staging/` sebelum langkah berikutnya.
4. Jalankan run yang sama lagi.
5. Yang diharapkan: exit 0, file yang selesai terpotong **diunduh ulang**
   (bukan dianggap sah), file yang sudah utuh **tidak** diunduh ulang, dan
   `history verify` bersih.
6. Yang harus diperiksa: tidak ada file parsial yang lolos ke folder final, dan
   JSON history tidak rusak (bisa dibaca `history verify` tanpa error).

Kalau file parsial ternyata lolos ke folder final, itu temuan severity tinggi
dan harus masuk `validation.validate_archive`, bukan diselesaikan dengan
membuang file secara manual.

### 14.3 Setelah itu

- `mcp_server.py` masih 0% coverage padahal adapter MCP-nya aktif dipakai.
- Fault injection jaringan untuk QA-007 (timeout HTTP, socket terputus di tengah
  unduhan) belum ada.
- SRC-001 sampai SRC-011 (MariaDB) — fase 2, masuk hanya kalau memang diminta.
