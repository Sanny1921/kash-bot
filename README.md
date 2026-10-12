# kash-bot

KASH v1: a private Telegram companion with a Gemini brain and persistent memory. Python 3.8+, no dependencies (only `pymongo` when using MongoDB).

## Run locally
    export TELEGRAM_BOT_TOKEN=...   # from @BotFather
    export GEMINI_API_KEY=...       # from aistudio.google.com
    python3 bot.py

Optional: `OWNER_TELEGRAM_ID` (lock the bot to you), `GEMINI_MODEL`, `KASH_DB`.
Without `MONGODB_URI`, memory is a local SQLite file (`kash.db`). Never commit `.env`.

## Commands
- `/memories` list what KASH remembers
- `/forget` wipe memory

## Deploy free: Render + MongoDB Atlas + cron-job.org

**1. Memory (MongoDB Atlas, free M0, 512 MB, no card).** Render's free disk is wiped on restart, so memory lives in Atlas.
- Sign up at mongodb.com/cloud/atlas, create a project, then Create a cluster, choose the Free tier (M0), any nearest region.
- Database Access: add a database user (username + password, read/write).
- Network Access: add IP `0.0.0.0/0` (Render free has no fixed IP).
- Connect > Drivers > copy the `mongodb+srv://...` string and put your password in it. That is `MONGODB_URI`.
- Atlas pauses a free cluster after 30 days with zero connections; the bot connects constantly, so it stays active.

**2. Render.** New > Blueprint (uses `render.yaml`) > pick this repo. Or New > Web Service: runtime Python, build `pip install -r requirements.txt`, start `python3 bot.py`, instance type Free.
Set env vars: `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `OWNER_TELEGRAM_ID`, `MONGODB_URI`.
Deploy; logs should say `KASH is up. memory: mongodb`.

**3. Keep it awake (cron-job.org).** Free web services sleep after 15 min without inbound HTTP. Create a cron job at cron-job.org with URL `https://<your-service>.onrender.com/health`, every 10 minutes. The bot answers `ok`.

Note: Render free gives about 750 instance-hours per month per workspace.
