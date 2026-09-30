#!/usr/bin/env python3
"""Pengatur istirahat unduhan IDX 2025.

Pola: rehat 1 jam setiap kelipatan 25% dari sisa kerja.
  Mulai 1.166 ZIP, sisa 2.686 -> milestone di 1.838, 2.510, 3.182 ZIP (3x rehat).

Cara pakai:  python3 rest_manager.py check
Dipanggil oleh cron idx-download-rest tiap 15 menit. Keluaran:
  - "REHAT 1 JAM ..." / "ISTIRAHAT SELESAI ..." / "BUTUH MANUAL ..." -> laporkan ke user
  - selain itu -> diam (nothing to do)

Pause = SIGSTOP ke proses CLI (posisi emiten terjaga, tidak perlu buka ulang
halaman profil). Resume = pastikan stack bridge+Firefox sehat (restart kalau
mati), lalu SIGCONT ke CLI.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

PROJ = os.path.expanduser("~/workspace/idx-stock-xbrl")
VENV_PY = os.path.join(PROJ, ".venv", "bin", "python")
BASE = os.path.join(PROJ, "downloads-2025")
STATE_FILE = os.path.join(BASE, "rest_state.json")
PID_FILE = os.path.join(BASE, "run.pid")

START_ZIPS = 1166
TARGET_ZIPS = 3852
MILESTONES = [1838, 2510, 3182]  # 1166 + 672*k, k=1..3
REST_SECONDS = 3600


# --- util -----------------------------------------------------------------

def count_zips() -> int:
    n = 0
    saham = os.path.join(BASE, "saham")
    for _root, _dirs, files in os.walk(saham):
        n += sum(1 for f in files if f.endswith(".zip"))
    return n


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"milestones_done": [], "pause_until": None, "needs_manual": False}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def cli_pid() -> int | None:
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().decode(errors="replace")
    except OSError:
        return None
    if "firefox_bridge.cli" not in cmdline:
        return None
    return pid


def pct(zips: int) -> str:
    return f"{zips / TARGET_ZIPS * 100:.1f}%"


# --- stack -----------------------------------------------------------------

def _sh(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def stop_stack() -> None:
    # bracket trick: pola regex tidak match dirinya sendiri
    _sh(["pkill", "-f", "[f]irefox_bridge.server"])
    _sh(["pkill", "-f", "[f]irefox/firefox"])
    time.sleep(2)


def bridge_up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def bridge_token() -> str:
    r = _sh([VENV_PY, "-m", "firefox_bridge.server", "token"], timeout=30)
    return r.stdout.strip()


def extension_tabs(token: str) -> int:
    req = urllib.request.Request(
        "http://127.0.0.1:8765/api/v1/tabs",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return len(json.loads(r.read()))
    except Exception:
        return 0


def start_stack() -> tuple[bool, str]:
    """Hidupkan ulang bridge + Firefox + extension. Return (ok, pesan)."""
    stop_stack()
    blog = open("/tmp/bridge.log", "ab")
    subprocess.Popen(
        [VENV_PY, "-m", "firefox_bridge.server"],
        cwd=PROJ, stdout=blog, stderr=subprocess.STDOUT, start_new_session=True,
    )
    ok = False
    for _ in range(30):
        if bridge_up():
            ok = True
            break
        time.sleep(2)
    if not ok:
        return False, "bridge tidak mau nyala"
    token = bridge_token()
    if not token:
        return False, "gagal ambil token bridge"

    shutil.rmtree("/tmp/ext-resume", ignore_errors=True)
    shutil.copytree(os.path.join(PROJ, "extension"), "/tmp/ext-resume")
    p = "/tmp/ext-resume/background.js"
    s = open(p).read()
    s = s.replace('token: ""', f'token: "{token}"', 1)
    s = s.replace("enabled: false", "enabled: true", 1)
    open(p, "w").write(s)

    flog = open("/tmp/firefox.log", "ab")
    subprocess.Popen(
        [os.path.expanduser("~/workspace/firefox/firefox"), "--headless",
         "--profile", os.path.expanduser("~/workspace/ff-profile-test"),
         "--marionette", "--marionette-port", "2828", "about:blank"],
        stdout=flog, stderr=subprocess.STDOUT, start_new_session=True,
    )
    time.sleep(10)
    r = _sh([sys.executable, os.path.expanduser("~/workspace/marionette_install.py")],
            timeout=90)
    if "ADDON INSTALLED OK" not in r.stdout:
        return False, f"install extension gagal: {r.stdout[-200:]} {r.stderr[-200:]}"
    time.sleep(6)
    if extension_tabs(token) < 1:
        return False, "extension tidak konek ke bridge"
    return True, "stack nyala, extension konek"


# --- check ------------------------------------------------------------------

def main() -> int:
    zips = count_zips()
    state = load_state()
    now = time.time()

    if state.get("needs_manual"):
        print(f"BUTUH MANUAL: {state.get('manual_reason', 'perlu penanganan')}. "
              f"ZIP: {zips}/{TARGET_ZIPS} ({pct(zips)}).")
        return 0

    # --- sedang istirahat: waktunya lanjut? ---
    pause_until = state.get("pause_until")
    if pause_until:
        if now < pause_until:
            mins = int((pause_until - now) / 60)
            print(f"MASIH ISTIRAHAT: {mins} menit lagi. "
                  f"ZIP: {zips}/{TARGET_ZIPS} ({pct(zips)}).")
            return 0
        pid = cli_pid()
        if pid is None:
            state["pause_until"] = None
            state["needs_manual"] = True
            state["manual_reason"] = ("istirahat selesai tapi proses downloader "
                                      "tidak hidup")
            save_state(state)
            print(f"BUTUH MANUAL: istirahat selesai tapi downloader tidak hidup. "
                  f"ZIP: {zips}/{TARGET_ZIPS} ({pct(zips)}).")
            return 0
        token = bridge_token()
        if not bridge_up() or extension_tabs(token) < 1:
            ok, msg = start_stack()
            if not ok:
                state["pause_until"] = None
                state["needs_manual"] = True
                state["manual_reason"] = f"restart stack gagal saat resume: {msg}"
                save_state(state)
                print(f"BUTUH MANUAL: restart stack gagal ({msg}). "
                      f"ZIP: {zips}/{TARGET_ZIPS} ({pct(zips)}).")
                return 0
            print(f"RESUME: stack sempat mati saat istirahat, dihidupkan ulang. ", end="")
        os.kill(pid, signal.SIGCONT)
        state["pause_until"] = None
        save_state(state)
        print(f"ISTIRAHAT SELESAI: downloader (PID {pid}) dilanjutkan. "
              f"ZIP: {zips}/{TARGET_ZIPS} ({pct(zips)}).")
        return 0

    # --- tidak istirahat: cek milestone ---
    done = set(state.get("milestones_done", []))
    for m in MILESTONES:
        if m not in done and zips >= m:
            pid = cli_pid()
            done.add(m)
            state["milestones_done"] = sorted(done)
            if pid is not None:
                os.kill(pid, signal.SIGSTOP)
                state["pause_until"] = now + REST_SECONDS
                save_state(state)
                print(f"REHAT 1 JAM: milestone {m} ZIP tercapai "
                      f"({zips}/{TARGET_ZIPS} = {pct(zips)}). Downloader di-pause, "
                      f"lanjut otomatis 1 jam lagi.")
            else:
                save_state(state)
                print(f"MILESTONE {m} tercapai tapi downloader tidak hidup; "
                      f"tidak ada yang di-pause. ZIP: {zips}/{TARGET_ZIPS}.")
            return 0

    nxt = next((m for m in MILESTONES if m not in done), None)
    if nxt is None:
        print(f"SELESAI: semua milestone rehat terlewati. "
              f"ZIP: {zips}/{TARGET_ZIPS} ({pct(zips)}).")
    else:
        print(f"BERJALAN: {zips}/{TARGET_ZIPS} ({pct(zips)}). "
              f"Rehat berikutnya di {nxt} ZIP.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "check":
        print("pakai: rest_manager.py check")
        sys.exit(2)
    sys.exit(main())
