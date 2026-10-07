# Firefox Bridge + IDX Report Downloader

A local bridge that connects a normal Firefox profile to Python, plus a downloader
that collects IDX `inlineXBRL.zip` financial reports for many stock codes.

Firefox is driven through an authenticated loopback API, so the browser session
and its IDX cookies are reused exactly as a human would. No Playwright, no
headless browser, no scraping outside the real page.

## Components

- `extension/`: Firefox MV3 WebExtension.
- `firefox_bridge/`: bridge service, REST API, Python client, MCP adapter.
- `firefox_bridge/idx/`: IDX page semantics, selectors, and browser flow.
- `firefox_bridge/downloader/`: paths, history, integrity, orchestration.
- `firefox_bridge/cli.py`: `firefox-bridge-download` command.
- `tests/`: unit, integration, and regression tests with a captured IDX fixture.
- `task.md`: production readiness plan and task status.

## Requirements

- Python 3.11 or newer (developed on 3.13).
- Firefox 142 or newer.
- Node.js only for optional extension linting.

## Install

```powershell
cd C:\Users\ORCA\Downloads\project02\productions
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Run the bridge

The bridge must be running in its own terminal:

```powershell
.\.venv\Scripts\python.exe -m firefox_bridge.server
```

It binds to `127.0.0.1:8765`. Verify it locally:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

Two logs come out of this. The terminal shows human-readable lines, and every
line is also appended as JSON to `logs\bridge.log` inside this folder, so
request traces and extension traffic can be filtered after the fact:

```powershell
Get-Content logs\bridge.log -Tail 20
Select-String -Path logs\bridge.log -Pattern '"transport":"websocket"'
```

Set `FIREFOX_BRIDGE_LOG_DIR` to write it elsewhere, and `FIREFOX_BRIDGE_LOG` to
change the level (`DEBUG`, `INFO`, `WARNING`). The token is never logged. If the
log file cannot be opened the bridge still starts and says so on the console
rather than failing silently.

The access token is stored outside the project in
`%LOCALAPPDATA%\firefox-bridge\token`. Print it once with:

```powershell
.\.venv\Scripts\python.exe -m firefox_bridge.server token
```

The token is never written to logs, history files, or run reports.

## Load the Firefox extension

1. Open `about:debugging#/runtime/this-firefox` in Firefox.
2. Select `Load Temporary Add-on`.
3. Select `productions/extension/manifest.json`.
4. Open the Firefox Bridge toolbar popup.
5. Enter `ws://127.0.0.1:8765/extension`.
6. Paste the token.
7. Select `Connect`.

The extension connects over an authenticated WebSocket and falls back to HTTP
long-polling if the socket cannot be established. The popup shows which transport
is active, so `Transport: HTTP polling (fallback)` means the WebSocket is not
usable right now.

An MV3 background page is not guaranteed to persist: Firefox can discard it after
`extensions.background.idle.timeout` (30s by default), and a discarded page runs
no timers, so nothing revives it. The extension calls
`browser.runtime.getPlatformInfo()` every 20s because that is the only keepalive
the Firefox source honours. Measured on this machine the poll loop has survived
100s straight without it, so this is insurance, not the cause of past
disconnects.

The usual reason a temporary add-on goes quiet is that it was reloaded: a
temporary add-on loses its storage, so `enabled` returns to `false` and the
bridge stops polling until you press **Connect** again. This is a packaging
problem, not a bridge problem, and it disappears once the extension ships as a
persistent signed XPI installed by the installer.

The `downloads` permission is required so IDX cookies are preserved when Firefox
saves a report.

## Download reports

Every detected report of the chosen year, for one or more stock codes:

```powershell
.\.venv\Scripts\firefox-bridge-download.exe --stocks NCKL --year 2025 --all-detected --delay 1
```

A single quarter instead of the whole year:

```powershell
.\.venv\Scripts\firefox-bridge-download.exe --stocks NCKL --year 2025 --quarter 1
```

Useful options:

| Option | Meaning |
| --- | --- |
| `--stocks` | Comma separated codes, for example `NCKL,BBCA,BBRI`. |
| `--year` | Reporting year. |
| `--quarter` | One quarter, 1 to 4. Ignored with `--all-detected`. |
| `--all-detected` | Process every report link the page shows for the year. |
| `--all-quarters` | Legacy alias for `--all-detected`. |
| `--delay` | Seconds between stocks. Values below 1 are rejected. |
| `--download-dir` | Download root. Default `%FIREFOX_BRIDGE_DOWNLOAD_DIR` or `~/Downloads`. |
| `--config` | TOML or JSON file supplying any option below. See [Config file](#config-file). |

The equivalent module form keeps working for existing scripts:

```powershell
.\.venv\Scripts\python.exe -m firefox_bridge.tools.bulk_downloader --stocks NCKL --year 2025 --all-detected
```

## Download audited annual `instance.zip` directly

Where the flow above *finds* report links by driving the IDX year panel, the
`instance` program *builds* the URL for the audited annual `instance.zip`
(`/Audit/{TICKER}/instance.zip`, i.e. quarter 4) and downloads it with no page
interaction. It is a separate program writing under its own root, so its
`download_history.json` can never collide with the page-driven one — both are
keyed by stock + year + quarter, which is exactly the collision the separation
prevents.

```powershell
.\.venv\Scripts\firefox-bridge-instance.exe --stocks-file db/list_saham.sql --year 2025 --delay 2 --delay-max 5
```

Dry run against the real stock list (writes nothing, needs no bridge):

```powershell
.\.venv\Scripts\python.exe -m firefox_bridge.instance.cli --stocks-file db/list_saham.sql --year 2025 --dry-run
```

Useful options:

| Option | Meaning |
| --- | --- |
| `--stocks-file` | Read codes from a `.sql` dump; delisted rows are excluded. |
| `--stocks` | Comma separated codes; merged with `--stocks-file` when both are given. |
| `--year` | Reporting year. |
| `--delay` / `--delay-max` | Seconds between stocks; with `--delay-max`, uniform random in `[--delay, --delay-max]`. Minimum 1. |
| `--download-dir` | Download root. Default `$FIREFOX_BRIDGE_INSTANCE_DIR` or `<download root>/instance`. |
| `--dry-run` | Report planned downloads and skips, change nothing, no bridge needed. |

### Cloudflare

Direct downloads to the `instance.zip` URL are bare requests: no page origin and
no `Referer`, which Cloudflare answers with a `403` challenge page. Firefox then
creates the file and immediately withdraws it (it appears at `t=0.01s` and is
gone by `t=0.26s`), so the wait loop sees a path that is never written. To avoid
that, this program keeps one tab at `https://www.idx.co.id/`, waits for the
challenge to resolve, and re-points that tab at IDX before retrying a download
that came back with nothing — a challenge always has a page to resolve in
instead of failing the request. The tab is held open for the whole run and
closed at the end.

Not every challenge resolves by waiting. The interactive one renders a
checkbox labelled **"Verify you are human"** and answers `200`, so a probe
reading only visible text finds neither 404 wording nor any of the
automatic-challenge markers — and concludes the archive exists. Six stocks
were recorded as `200 (file exists; downloads.download failed)` that way,
when what had answered was the challenge page.

On finding that checkbox the run **stops**: the held tab is brought to the
front, a modal popup names the URL and asks for the click, and downloads
resume from the same stock once the marker is gone. Declining — or a run
where no popup can be shown — exits with code **4** and leaves the tab open,
because it is showing the very box to click. Nothing clicks it automatically;
doing so would be bypassing an access control.

Detection reads the page twice on purpose. The `text` endpoint returns
`innerText`, which never carries an element's `aria-label`, and that
attribute is where the widget names itself — so the snapshot's accessibility
names are consulted too, and a page is believed only when neither reading
shows the marker.

Exit codes: `0` success · `1` failures · `2` invalid input · `3` bridge
unavailable · `4` CAPTCHA waiting for a human.

A URL that genuinely has no audited archive for the year is reported per stock
and never aborts the run; the whole run can be re-run later and already-fetched
stocks skip instantly via the shared history check.

## Config file

Options that a scheduled run keeps repeating can live in a TOML or JSON file
instead of on the command line. Start from `firefox-bridge.example.toml`.

```powershell
# a firefox-bridge.toml in the working directory is found automatically
.\.venv\Scripts\firefox-bridge-download.exe --dry-run

# or point at one explicitly
.\.venv\Scripts\firefox-bridge-download.exe --config C:\schedules\weekly.toml
```

Precedence, highest first: **command line, then the file, then the built-in
default.** Every argument still wins, so one command can override the file for a
single run without editing it.

| Where the file comes from | Order |
| --- | --- |
| `--config PATH` | 1 |
| `$FIREFOX_BRIDGE_CONFIG` | 2 |
| `firefox-bridge.toml` in the working directory | 3 |
| built-in defaults | 4 |

A file named explicitly has to exist; one merely discovered may be absent.

The run prints which file it used and which options that file actually
supplied, so a file cannot quietly change what a run does. An unknown key, a
value of the wrong type, and a `delay` under one second are all refused before
the browser is touched, each naming the file and the key.

Two Windows notes, both learned the hard way:

- Save as UTF-8 **with or without** a byte-order mark. A BOM is stripped, because
  Notepad, PowerShell and Visual Studio all add one and `tomllib` rejects it
  with an error that blames line 1.
- Write Windows paths in single quotes: `download_dir = 'C:\Users\anda\Downloads'`.
  In double quotes a backslash starts an escape, and the error points at a
  column rather than at the backslash.

`history` and `all_quarters` cannot be set in a file. `history = "rebuild"`
would rewrite the history JSON on every unattended run, and an alias that
duplicates `all_detected` would put the run in a state with two names for one
decision.

## The flow

Each stock runs the same observable sequence, reusing a single profile tab:

1. open the company profile page;
2. click **Laporan Keuangan**;
3. select the reporting year through the year searchbox;
4. detect every `inlineXBRL.zip` link for that year;
5. download through Firefox into a staging folder;
6. move the finished archive to its final folder;
7. record the result in the history JSON.

TW1, TW2 and TW3 come from their own IDX paths; the fourth report lives under
the `Audit` path and is treated as quarter 4. The year selector is matched only
by year semantics, never by a generic combobox, so a year can never be typed
into the company-code box.

## Output layout

```text
<download-dir>/
  saham/
    download_history.json
    staging/
      <STOCK>/<YEAR>/<STOCK>_inlineXBRL_T<n>_<YEAR>.zip
    <STOCK>/
      <YEAR>/<STOCK>_inlineXBRL_T<n>_<YEAR>.zip
```

Firefox writes into `saham/staging/...` because it rejects a path segment that
starts with a dot. Python waits for the transfer to finish, moves the archive,
and only then updates the JSON.

## History and resume

`download_history.json` stores one entry per report:

```json
{
  "version": 1,
  "downloads": {
    "NCKL": {
      "2025": {
        "1": {
          "url": "https://www.idx.co.id/.../TW1/NCKL/inlineXBRL.zip",
          "file": "saham/NCKL/2025/NCKL_inlineXBRL_T1_2025.zip",
          "size": 243904,
          "sha256": "6bfaad2e...",
          "duplicate_of": null,
          "integrity_status": "verified",
          "completed_at": "2026-09-25T11:27:12.274007+00:00"
        }
      }
    }
  }
}
```

Skip rules:

- file present and SHA-256 matches the history: skipped;
- file present without a history entry: backfilled, then skipped;
- file present but the hash differs: downloaded again and replaced;
- history entry without a file: downloaded again;
- two quarters with the same hash: both kept, later ones flagged in
  `duplicate_of`.

A second run over the same stock and year therefore downloads nothing.

## Quality gates

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest --cov=firefox_bridge --cov-report=term-missing
```

`tests/fixtures/idx_nckl_2025_snapshot.json` is a captured IDX page used as the
regression base. Refs in that fixture are illustrative: the real flow always
re-reads the page because IDX invalidates element refs on every render.

## Stock list source

Today the stock list comes from `--stocks`. A MariaDB-backed provider is planned
for a later milestone; see `task.md` section `SRC-001` to `SRC-011`.

Connection settings will be read from the environment, never from source code:

| Variable | Meaning |
| --- | --- |
| `IDX_DB_HOST` | MariaDB host, for example `127.0.0.1`. |
| `IDX_DB_PORT` | MariaDB port, for example `3306`. |
| `IDX_DB_USER` | Database user. |
| `IDX_DB_PASSWORD` | Database password. Keep it in a local `.env` only. |
| `IDX_DB_NAME` | Database name. |

Copy `.env.example` to `.env` for local settings. `.env` is gitignored; never
commit a password.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `connected: false` | The extension is not connected. Reload the temporary add-on and reconnect in the popup. |
| `Tombol 'Laporan Keuangan' tidak ditemukan` | The panel markup changed. Update the fixture and the selector. |
| `option tahun ... tidak muncul` | The dropdown did not render its options. Retry with a larger `--delay`. |
| `HASH MISMATCH` | The archive changed on disk. The report is downloaded again automatically. |
| `filename must not contain illegal characters` | A staging path segment started with a dot. Keep the `staging` name. |
| `Tab ... did not finish loading` | The profile page stuck while loading. The flow reloads once, then retries. |

## Roadmap

Production readiness tasks, milestones, and acceptance criteria live in
`task.md`.
