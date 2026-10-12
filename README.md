# kash-bot

KASH v1: a private Telegram companion with a Gemini brain and persistent memory. Python 3.8+, no dependencies.

## Run locally
    export TELEGRAM_BOT_TOKEN=...   # from @BotFather
    export GEMINI_API_KEY=...       # from aistudio.google.com
    python3 bot.py

Optional: `OWNER_TELEGRAM_ID` (lock the bot to you), `GEMINI_MODEL`, `KASH_DB`.
Without `TURSO_DATABASE_URL`, memory is a local SQLite file (`kash.db`). Never commit `.env`.

## Commands
- `/memories` list what KASH remembers
- `/forget` wipe memory

## Deploy free: Render + Turso + cron-job.org

**1. Memory (Turso, free, no card, doesn't expire).** Render's free disk is wiped on restart, so memory lives in Turso.
- Sign up at turso.tech, create a database (any name, nearest region).
- Copy its URL (`libsql://name-org.turso.io`) and create a database token (Dashboard > your DB > Generate token, read and write, no expiry).

**2. Render.** New > Blueprint (uses `render.yaml`) > pick this repo. Or New > Web Service: runtime Python, build command `echo ok`, start command `python3 bot.py`, instance type Free.
Set env vars: `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `OWNER_TELEGRAM_ID`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`.
Deploy; logs should say `KASH is up. memory: turso`.

**3. Keep it awake (cron-job.org).** Free web services sleep after 15 min without inbound HTTP. Create a cron job at cron-job.org with URL `https://<your-service>.onrender.com/health`, every 10 minutes (or 14). The bot answers `ok`.

Note: Render free gives about 750 instance-hours per month, enough for one always-on service.
