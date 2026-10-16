# KASH local helper (v6) - let KASH work on your own machine

The helper is a small Python program that runs on your laptop/phone and
connects OUTWARD to KASH over HTTPS. Nothing on your machine is exposed to
the internet - no open ports, no port forwarding, works on any Wi-Fi.

**What KASH can do once paired** (inside one folder, default `~/KASH`):
- On her own: list files, read files, create new files, append to files.
- Only after your Telegram "yes": delete files/folders, run commands, open apps.
- She knows which machine each device is (name + OS) and whether it is online.

**Needs:** Python 3.8+ only. No pip installs, no admin rights.

## Windows

1. Install Python from https://www.python.org/downloads/ - during setup,
   tick **"Add python.exe to PATH"**.
2. Download `kash_helper.py` from this repo
   (Code > Download ZIP, or open the file and save it).
3. On Telegram, tell KASH: **pair my laptop** - she sends a 6-digit code.
4. Double-click `kash_helper.py` (or in a terminal: `python kash_helper.py`).
5. Paste the code when asked. Done - leave the window open.

Useful: `python kash_helper.py --status`, `--unpair`.

To start it automatically with Windows: press Win+R, type `shell:startup`,
and put a shortcut to `kash_helper.py` in that folder.

## Linux

1. `sudo apt install python3` (already there on most systems).
2. Download `kash_helper.py` from this repo.
3. On Telegram, tell KASH: **pair my laptop** - she sends a 6-digit code.
4. `python3 kash_helper.py`, paste the code. Leave it running.

Autostart (optional): add `@reboot python3 /full/path/kash_helper.py`
with `crontab -e`.

## Android phone (Termux)

Same helper works on Android through Termux:

1. Install **Termux from F-Droid** (termux.dev - the Play Store build is outdated).
2. In Termux: `pkg install python`
3. Optional, to let KASH reach shared storage files: `termux-setup-storage`
4. Download the helper: `curl -LO https://raw.githubusercontent.com/Sanny1921/kash-bot/main/kash_helper.py`
5. Tell KASH **pair my phone**, then `python kash_helper.py` and paste the code.

Phone caveats: Android may kill Termux in the background - disable battery
optimization for Termux and run `termux-wake-lock` for reliability. "Open
app" uses termux-open when available. iPhone is not supported (no way to run
a background helper on iOS).

## How pairing stays safe

- The 6-digit code works once and expires in 10 minutes.
- The helper exchanges it for a device token over HTTPS; the token is saved
  only on your machine (`~/.kash/config.json`) and never appears in chat.
- Everything happens inside one folder (`~/KASH`). The helper refuses any
  path outside it.
- Tell KASH `/unpair` (or "unpair my laptop") to kill a device's token
  anytime. The helper just stops working.
