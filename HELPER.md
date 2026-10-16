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

## Browser skill (v7) - KASH checks websites on your laptop, read-only

KASH opens a visible Chrome/Edge window on your laptop, signs in with a login
YOU saved on that laptop, and reads the page. She never clicks Submit, Delete,
Pay, Send etc. - only the login button and read-only navigation links.
Your password never goes through Telegram, Render or GitHub.

One-time setup (Windows and Linux):
1. Put **both** `kash_helper.py` and `kash_browser.py` in the same folder.
2. `pip install playwright` (uses your installed Chrome or Edge, no big download).
3. Save the login, typing the password in this terminal (hidden):
   `python kash_helper.py --save-login msu.digiicampus.com`
4. Restart the helper. On Telegram say: **check my DigiCampus assignments**.

If a captcha/MFA shows in the window, solve it there (she waits 2 minutes).
The browser keeps its own profile in `~/.kash/browser-profile`, so you stay
logged in and captchas should be rare after the first time.

Where the password lives: Windows - encrypted to your Windows account (DPAPI).
Linux - a file only your user can read (obscured, NOT encrypted at rest).
`--list-logins` shows saved sites, `--forget-login SITE` deletes one.
Limits: laptop on + helper running; first run reads the dashboard and the
first assignment-like pages as raw text, so expect to tune it after one real
run; no submitting or changes - that stays out of scope until you ask.

## Wake word mode (optional, off by default)

Say "Hey KASH" (or "hey cash", "hi kash") plus your request; she answers out loud and a copy lands in Telegram.

Start it only when you want it: `python kash_helper.py --wake` (add `--wake-minutes 60` to change the auto-stop, default 120; `--no-voice` for text-only replies).

Setup once: put `kash_wake.py` next to `kash_helper.py`, then `pip install vosk sounddevice` (Linux also: `sudo apt install libportaudio2 espeak-ng`). First run offers a free ~36 MB offline speech model download (asks y/N).

Privacy and limits:
- Wake detection and speech-to-text run on your laptop (Vosk, offline). Mic audio is never saved and never uploaded. Only the text of your request goes to KASH.
- The mic is open only while the window is running, and the window says so. Stop with Ctrl+C, closing the window, or "Hey KASH, stop listening". It auto-stops after the time limit.
- Approvals never work by voice (a TV or a friend could say "yes"). Anything risky is asked in Telegram and you answer there. Same ask-first rules as typed messages.
- It is not as accurate as a phone assistant: small offline model, Indian English works best, Hinglish and noisy rooms get misheard, and it can mistake similar words. Max 40 voice requests per hour.
- Replies are read with your computer's own voice (Windows/macOS built in, Linux espeak-ng), not KASH's Telegram voice. Free: no extra Gemini quota except for the answer itself.
- Needs the laptop awake and the Render server up (free tier can take ~30 s to wake after idle).
