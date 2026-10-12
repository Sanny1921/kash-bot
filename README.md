# kash-bot

KASH v1: a private Telegram companion with a Gemini brain and persistent memory. Python 3.8+, no dependencies.

## Run
    export TELEGRAM_BOT_TOKEN=...   # from @BotFather
    export GEMINI_API_KEY=...       # from aistudio.google.com
    python3 bot.py

Optional: `OWNER_TELEGRAM_ID` (lock the bot to you), `GEMINI_MODEL`, `KASH_DB`.

## Commands
- `/memories` list what KASH remembers
- `/forget` wipe memory

Memory is a SQLite file (`kash.db`). Keep it on a persistent disk when hosting. Never commit `.env`.
