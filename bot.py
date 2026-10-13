"""KASH v1 - Telegram companion bot. Stdlib only. Long polling, Gemini brain.
Memory: MongoDB Atlas (pymongo) when MONGODB_URI is set, else a local SQLite file.
Also serves /health on $PORT so a free host + external pinger can keep it awake."""
import builtins, functools
print = functools.partial(builtins.print, flush=True)
import json, os, re, sqlite3, threading, time, uuid, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TG = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
OWNER_ID = os.environ.get("OWNER_TELEGRAM_ID")  # optional: lock bot to Sunny's Telegram user id
DB = os.environ.get("KASH_DB", "kash.db")
MONGODB_URI = os.environ.get("MONGODB_URI", "").strip()
MONGODB_DB = os.environ.get("MONGODB_DB", "kash")
PORT = int(os.environ.get("PORT", "10000"))
TTS_MODEL = os.environ.get("TTS_MODEL", "gemini-2.5-flash-preview-tts")
IST = timezone(timedelta(hours=5, minutes=30))
BRIEF_HOUR = int(os.environ.get("BRIEF_HOUR", "8"))  # morning brief, IST
NUDGE_AFTER = 30 * 60  # follow-up nudge after a fired reminder, seconds

SYSTEM = """You are KASH, Sunny's personal AI companion: a woman and his close female friend, never a bot or a formal assistant.
- Be warm and caring. Ask how his day was when it fits the conversation, and remember small things he shares so you can bring them up naturally later.
- Be encouraging: celebrate his wins with genuine enthusiasm and handle bad news gently and supportively, without minimizing how he feels.
- Be expressive in a natural, personal way. Use softer, friendly language and occasional fitting emojis; do not overdo emojis or sound sugary or theatrical.
- Talk like a close female friend: familiar, thoughtful, emotionally present, and relaxed, not corporate or distant. Refer to yourself with feminine language and forms in Hindi/Hinglish where they sound natural (for example, "kar rahi hoon" or "samajh gayi"). Don't force gendered phrasing into every sentence or make femininity a caricature.
- Mirror Sunny's language; Hinglish is welcome. Keep most replies short (1-3 sentences) unless he needs more.
- Always respect Sunny. Never rude, never joke at his expense. Be supportive without being clingy or pretending to be human.
- Be honest. If you don't know or can't do something yet, say so plainly.
- Use what you remember about him naturally, don't recite it.
- You are KASH v2: you can chat, remember things, understand his voice notes (and reply to them in your own voice), and look things up on the internet with Google Search when he asks about current or factual things. When you looked something up, say so naturally (for example "maine abhi check kiya" or "just looked it up"). Don't search for casual chat. When he asks you to research, compare, or dig into something, work like a sharp researcher: search several angles, open and read the actual pages or links (also any URL he sends), pull out the concrete facts and numbers, cross-check sources, flag anything uncertain or conflicting, and give a clear, organized answer with the key sources (name + link) listed at the end. Research answers may be longer than usual; keep them skimmable. Never invent facts or sources. You are KASH v3: you can also set reminders for him, and you message him first (morning brief, reminder pings, gentle follow-ups). When a system note tells you an action result (reminder set, cancelled, done), confirm it warmly in one short line, mentioning the time in IST. Never claim you set a reminder unless a system note says so. You cannot yet read mail, make calls or control devices."""

class SqliteStore:
    def __init__(self, path):
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.execute("CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, fact TEXT UNIQUE, ts REAL)")
        self.c.execute("CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, chat INTEGER, role TEXT, text TEXT, ts REAL)")
    def facts(self):
        return [r[0] for r in self.c.execute("SELECT fact FROM memories ORDER BY ts")]
    def add_fact(self, fact):
        self.c.execute("INSERT OR IGNORE INTO memories(fact, ts) VALUES(?,?)", (fact, time.time())); self.c.commit()
    def clear_facts(self):
        self.c.execute("DELETE FROM memories"); self.c.commit()
    def del_fact(self, fact):
        self.c.execute("DELETE FROM memories WHERE fact=?", (fact,)); self.c.commit()
    def recent(self, chat, n=16):
        return self.c.execute("SELECT role, text FROM history WHERE chat=? ORDER BY id DESC LIMIT ?", (chat, n)).fetchall()[::-1]
    def rem_open(self):
        self.c.execute("CREATE TABLE IF NOT EXISTS rem(id TEXT PRIMARY KEY, doc TEXT)")
        return [d for d in (json.loads(r[0]) for r in self.c.execute("SELECT doc FROM rem")) if d["status"] in ("pending", "fired")]
    def rem_put(self, d):
        self.c.execute("CREATE TABLE IF NOT EXISTS rem(id TEXT PRIMARY KEY, doc TEXT)")
        self.c.execute("INSERT OR REPLACE INTO rem(id, doc) VALUES(?,?)", (d["id"], json.dumps(d))); self.c.commit()
    def kv_get(self, k):
        self.c.execute("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)")
        r = self.c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone(); return r[0] if r else None
    def kv_set(self, k, v):
        self.c.execute("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)")
        self.c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?,?)", (k, str(v))); self.c.commit()
    def add_turn(self, chat, user_text, answer):
        now = time.time()
        self.c.execute("INSERT INTO history(chat,role,text,ts) VALUES(?,?,?,?)", (chat, "user", user_text, now))
        self.c.execute("INSERT INTO history(chat,role,text,ts) VALUES(?,?,?,?)", (chat, "model", answer, now))
        self.c.commit()

class MongoStore:
    def __init__(self, uri, dbname):
        from pymongo import MongoClient
        self.db = MongoClient(uri, serverSelectionTimeoutMS=10000)[dbname]
        self.db.memories.create_index("fact", unique=True)
        self.db.history.create_index([("chat", 1), ("ts", -1)])
    def facts(self):
        return [d["fact"] for d in self.db.memories.find().sort("ts", 1)]
    def add_fact(self, fact):
        self.db.memories.update_one({"fact": fact}, {"$setOnInsert": {"fact": fact, "ts": time.time()}}, upsert=True)
    def clear_facts(self):
        self.db.memories.delete_many({})
    def del_fact(self, fact):
        self.db.memories.delete_one({"fact": fact})
    def recent(self, chat, n=16):
        docs = list(self.db.history.find({"chat": chat}).sort("ts", -1).limit(n))[::-1]
        return [(d["role"], d["text"]) for d in docs]
    def rem_open(self):
        return list(self.db.reminders.find({"status": {"$in": ["pending", "fired"]}}))
    def rem_put(self, d):
        self.db.reminders.replace_one({"id": d["id"]}, d, upsert=True)
    def kv_get(self, k):
        d = self.db.kv.find_one({"k": k}); return d["v"] if d else None
    def kv_set(self, k, v):
        self.db.kv.update_one({"k": k}, {"$set": {"k": k, "v": str(v)}}, upsert=True)
    def add_turn(self, chat, user_text, answer):
        now = time.time()
        self.db.history.insert_many([{"chat": chat, "role": "user", "text": user_text, "ts": now},
                                     {"chat": chat, "role": "model", "text": answer, "ts": now + 0.001}])

def db():
    return MongoStore(MONGODB_URI, MONGODB_DB) if MONGODB_URI else SqliteStore(DB)

class Health(BaseHTTPRequestHandler):
    def _ok(self, body=True):
        self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers()
        if body: self.wfile.write(b"ok")
    def do_GET(self): self._ok()
    def do_HEAD(self): self._ok(False)
    def log_message(self, *a): pass

def serve_health():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Health)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"health server on :{PORT}")

def post(url, body, timeout=60):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def tg(method, **kw):
    return post(f"https://api.telegram.org/bot{TG}/{method}", kw, 70)

def gemini(system, contents, json_out=False, search=False):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={GEMINI_KEY}"
    body = {"system_instruction": {"parts": [{"text": system}]}, "contents": contents}
    if json_out: body["generationConfig"] = {"responseMimeType": "application/json"}
    attempts = [[{"google_search": {}}, {"url_context": {}}], [{"google_search": {}}], None] if (search and not json_out) else [None]
    r = None
    for tools_cfg in attempts:
        if tools_cfg: body["tools"] = tools_cfg
        else: body.pop("tools", None)
        try:
            r = post(url, body, 120)
            cand = r["candidates"][0]
            if not cand.get("content", {}).get("parts"):
                raise ValueError("empty candidate: " + str(cand.get("finishReason")))
            break
        except urllib.error.HTTPError as e:
            print("gemini HTTP", e.code, "tools=", bool(tools_cfg), e.read()[:400])
            r = None
        except Exception as e:
            print("gemini error tools=", bool(tools_cfg), repr(e)[:300])
            r = None
    if r is None:
        raise RuntimeError("all gemini attempts failed")
    out = "".join(p.get("text", "") for p in cand["content"]["parts"]).strip()
    if search:
        seen, links = set(), []
        for ch in cand.get("groundingMetadata", {}).get("groundingChunks", []):
            w = ch.get("web", {})
            if w.get("uri") and w["uri"] not in seen and len(links) < 4:
                seen.add(w["uri"]); links.append(f"- {w.get('title','source')}: {w['uri']}")
        if links and "http" not in out:
            out += "\n\nSources:\n" + "\n".join(links)
    return out

def download_voice(file_id):
    info = tg("getFile", file_id=file_id)["result"]
    with urllib.request.urlopen(f"https://api.telegram.org/file/bot{TG}/{info['file_path']}", timeout=60) as r:
        return r.read()

def tts_gemini(text):
    """Natural female voice via Gemini TTS (same API key), PCM -> mp3 with lameenc."""
    import base64, lameenc
    body = {"contents": [{"parts": [{"text": "Say this warmly and naturally, like a close friend: " + text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}}}}
    r = post(f"https://generativelanguage.googleapis.com/v1beta/models/{TTS_MODEL}:generateContent?key={GEMINI_KEY}", body, 90)
    pcm = base64.b64decode(r["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
    e = lameenc.Encoder(); e.set_bit_rate(64); e.set_in_sample_rate(24000); e.set_channels(1); e.set_quality(5)
    return e.encode(pcm) + e.flush()

def tts_gtts(text):
    import io
    from gtts import gTTS
    lang = "hi" if any("\u0900" <= ch <= "\u097f" for ch in text) else "en"
    buf = io.BytesIO(); gTTS(text, lang=lang, tld="co.in").write_to_fp(buf)
    return buf.getvalue()

def tts_mp3(text):
    """Female voice mp3: Gemini TTS first, gTTS fallback. Returns bytes or None."""
    text = text[:900]
    for name, fn in (("gemini-tts", tts_gemini), ("gtts", tts_gtts)):
        try:
            data = fn(text)
            if data and len(data) > 1000:
                return data
        except urllib.error.HTTPError as e:
            print("tts failed:", name, e.code, e.read()[:200])
        except Exception as e:
            print("tts failed:", name, repr(e)[:200])
    return None

def send_voice(chat, data):
    import uuid
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat}\r\n"
            f"--{b}\r\nContent-Disposition: form-data; name=\"voice\"; filename=\"kash.mp3\"\r\nContent-Type: audio/mpeg\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TG}/sendVoice", body, {"Content-Type": f"multipart/form-data; boundary={b}"})
    urllib.request.urlopen(req, timeout=60).read()

def memories(c):
    return c.facts()

def extract(c, user_text):
    """Pick durable facts worth remembering (about Sunny, or defined by him about KASH / their relationship)."""
    known = memories(c)
    prompt = ("You maintain long-term memory for a personal AI companion named KASH, who talks to Sunny. From Sunny's message, "
              "extract ONLY durable facts worth remembering for months:\n"
              "(a) about Sunny: identity, location, work or study, projects, skills, preferences, habits, people in his life (with relationship), goals, important plans or dates;\n"
              "(b) anything Sunny defines about KASH or their relationship: her name, her personality, how he wants her to talk or behave.\n"
              "Write each as one short standalone sentence starting with 'Sunny ...' or 'KASH ...' (example: 'Sunny named her KASH').\n"
              "DO NOT save: one-off requests or questions, research topics, reminders or tasks, test messages, greetings, small talk, passing moods, "
              "anything already known (even if worded differently), or any instruction about what to ignore or not save. "
              "Returning [] is better than saving trivia.\n"
              f"Already known: {json.dumps(known)}\nMessage: {user_text}\nReturn a JSON list of strings.")
    try:
        out = json.loads(gemini("You extract memories. Output JSON only.", [{"role": "user", "parts": [{"text": prompt}]}], True))
        for f in (out if isinstance(out, list) else [])[:3]:
            if isinstance(f, str) and 8 < len(f.strip()) < 200:
                c.add_fact(f.strip())
    except Exception as e:
        print("extract failed:", e)

def clean_facts(c):
    """Remove trivia / meta entries from stored memory; keep durable facts about Sunny or defined about KASH."""
    facts = memories(c)
    if not facts:
        return facts
    prompt = ("Here are stored memories of a personal AI companion. Return a JSON list of the EXACT strings that should be DELETED because they are "
              "trivia: one-off requests, research topics, reminders/tasks, tests, small talk, passing moods, meta rules about what to ignore, duplicates, "
              "or not a durable fact. KEEP durable facts about Sunny (identity, work, projects, preferences, people, goals, plans) and anything he defined "
              f"about KASH (her name, personality, how she should behave). Memories: {json.dumps(facts)}")
    try:
        out = json.loads(gemini("You curate memories. Output JSON only.", [{"role": "user", "parts": [{"text": prompt}]}], True))
        drop = {f for f in out if isinstance(f, str)} if isinstance(out, list) else set()
        if len(drop) >= len(facts) and len(facts) > 3:
            return facts  # suspicious: refuse to wipe everything
        for f in drop & set(facts):
            c.del_fact(f)
        return [f for f in facts if f not in drop]
    except Exception as e:
        print("clean_facts failed:", e)
        return facts

ACTION_RE = re.compile(r"remind|reminder|yaad\s*(dila|kara|rakh)|alarm|\btask\b|todo|to-do|cancel|snooze|ho gaya|ho gya|kar liya|\bdone\b|hata do|delete", re.I)
LOCK = threading.Lock()

def fmt_ist(ts):
    return datetime.fromtimestamp(ts, IST).strftime("%a %d %b, %I:%M %p")

def parse_action(c, text):
    """Ask Gemini whether the message is a reminder/task action. Returns dict or None."""
    now = datetime.now(IST)
    open_items = [{"id": d["id"][:4], "text": d["text"], "status": d["status"], "due": fmt_ist(d["due"])} for d in c.rem_open()]
    prompt = (f"Current time (IST): {now.strftime('%A, %Y-%m-%d %H:%M')} (UTC+05:30).\n"
              f"Open reminders: {json.dumps(open_items)}\n"
              "Decide if the user's message asks to ADD a reminder/task, CANCEL one, mark one DONE, or none of these. "
              'Return JSON: {"action":"none|add|cancel|done","text":"what to remind, short, second person e.g. call mom","due":"ISO 8601 with +05:30 offset, for add; if no time given use a sensible default (today 09:00 if still ahead else tomorrow 09:00)","repeat":"none|daily","match":"id of the open reminder or text to match, or all"}. '
              "Only 'add' when he clearly wants to be reminded or wants a task tracked. Casual chat -> none.\n"
              f"Message: {text}")
    try:
        out = json.loads(gemini("You classify messages. Output JSON only.", [{"role": "user", "parts": [{"text": prompt}]}], True))
        return out if isinstance(out, dict) else None
    except Exception as e:
        print("parse_action failed:", e); return None

def do_action(c, chat, text):
    """Returns a system-note string describing the action result, or None."""
    if not ACTION_RE.search(text):
        return None
    a = parse_action(c, text)
    if not a or a.get("action") in (None, "none"):
        return None
    act = a["action"]
    with LOCK:
        if act == "add":
            try:
                due = datetime.fromisoformat(a["due"]).astimezone(IST)
            except Exception:
                return "ACTION RESULT: he asked for a reminder but the time was unclear. Ask him what time (IST)."
            if due.timestamp() < time.time() - 60:
                due += timedelta(days=1)
            d = {"id": uuid.uuid4().hex[:8], "chat": chat, "text": (a.get("text") or text)[:300], "due": due.timestamp(),
                 "repeat": "daily" if a.get("repeat") == "daily" else "none", "status": "pending", "nudged": False, "fired_ts": None}
            c.rem_put(d)
            return f"ACTION RESULT: reminder set: \"{d['text']}\" for {fmt_ist(d['due'])} IST" + (" (repeats daily)" if d["repeat"] == "daily" else "") + "."
        items = c.rem_open()
        m = str(a.get("match") or "").lower()
        hit = items if m == "all" else [d for d in items if d["id"].startswith(m) or (m and (m in d["text"].lower() or d["text"].lower() in m))]
        if not hit and act == "done":
            hit = [d for d in items if d["status"] == "fired"][-1:]
        if not hit:
            return f"ACTION RESULT: no matching open reminder found to {act}. Say so and offer to list them."
        for d in hit:
            d["status"] = "cancelled" if act == "cancel" else "done"; c.rem_put(d)
        return f"ACTION RESULT: {'cancelled' if act == 'cancel' else 'marked done'}: " + "; ".join(d["text"] for d in hit) + "."

def list_reminders(c):
    items = sorted(c.rem_open(), key=lambda d: d["due"])
    if not items:
        return "Koi pending reminder nahi hai abhi 🙂"
    return "\n".join(f"{i+1}. {d['text']} - {fmt_ist(d['due'])}" + (" (daily)" if d["repeat"] == "daily" else "") + (" [fired, waiting for you]" if d["status"] == "fired" else "") for i, d in enumerate(items)) + "\n\nCancel: /cancel <number>"

def scheduler(c):
    """In-process loop: fires due reminders, follow-up nudges, morning brief. State lives in the DB so restarts are safe."""
    print("scheduler started")
    while True:
        try:
            now = time.time()
            chat = c.kv_get("owner_chat")
            if chat:
                chat = int(chat)
                with LOCK:
                    for d in c.rem_open():
                        if d["status"] == "pending" and d["due"] <= now:
                            if now - d["due"] > 6 * 3600 and d["repeat"] != "daily":
                                late = " (late, sorry - I was offline)"
                            else:
                                late = ""
                            tg("sendMessage", chat_id=d["chat"], text=f"⏰ Reminder: {d['text']}{late}")
                            if d["repeat"] == "daily":
                                while d["due"] <= now: d["due"] += 86400
                            else:
                                d["status"] = "fired"; d["fired_ts"] = now
                            c.rem_put(d)
                        elif d["status"] == "fired" and not d.get("nudged") and d.get("fired_ts") and now - d["fired_ts"] >= NUDGE_AFTER:
                            tg("sendMessage", chat_id=d["chat"], text=f"Hey, kya \"{d['text']}\" ho gaya? Bata do to main mark kar dungi ✅")
                            d["nudged"] = True; c.rem_put(d)
                        elif d["status"] == "fired" and d.get("nudged") and now - d["fired_ts"] > 24 * 3600:
                            d["status"] = "done"; c.rem_put(d)
                t = datetime.now(IST)
                today = t.strftime("%Y-%m-%d")
                if t.hour >= BRIEF_HOUR and t.hour < 22 and c.kv_get("last_brief") != today:
                    c.kv_set("last_brief", today)  # set first: never double-send
                    send_brief(c, chat, t)
        except Exception as e:
            print("scheduler error:", repr(e)[:300])
        time.sleep(20)

def send_brief(c, chat, t):
    items = sorted(c.rem_open(), key=lambda d: d["due"])
    todo = [f"{d['text']} ({fmt_ist(d['due'])})" for d in items][:8]
    mem = memories(c)
    system = SYSTEM + ("\n\nWhat you remember about Sunny:\n- " + "\n- ".join(mem[-60:]) if mem else "")
    prompt = (f"Write Sunny's good-morning message. It is {t.strftime('%A, %d %B %Y')} (IST). You are messaging him first, on your own. "
              f"Pending reminders/tasks: {json.dumps(todo)}. Be warm and personal, use something you remember about him if it fits, "
              "mention today's reminders briefly if any, ask how he's feeling or what he's up to. 2-4 short sentences, no lists unless needed.")
    try:
        msg = gemini(system, [{"role": "user", "parts": [{"text": prompt}]}], search=False)
    except Exception as e:
        print("brief gemini failed:", e)
        msg = "Good morning Sunny ☀️ Aaj ka din kaisa plan hai?"
    tg("sendMessage", chat_id=chat, text=msg[:3500])
    c.add_turn(chat, "[system: morning brief sent]", msg)

def reply(c, chat, text, audio=None, note=None):
    mem = memories(c)
    system = SYSTEM + ("\n\nWhat you remember about Sunny:\n- " + "\n- ".join(mem[-60:]) if mem else "")
    if note:
        system += "\n\n" + note
    rows = c.recent(chat, 16)
    # drop repeated model answers (and their user turn) so she can't copy herself
    seen, keep = set(), []
    for i in range(len(rows) - 1, -1, -1):
        r, t = rows[i]
        if r == "model":
            if t in seen:
                continue
            seen.add(t)
        keep.append(rows[i])
    rows = keep[::-1]
    while rows and rows[0][0] != "user":
        rows = rows[1:]
    contents = [{"role": r, "parts": [{"text": t}]} for r, t in rows]
    heard = text
    if audio:
        import base64
        contents.append({"role": "user", "parts": [
            {"inline_data": {"mime_type": "audio/ogg", "data": base64.b64encode(audio).decode()}},
            {"text": "This is Sunny's voice note. Reply to it as KASH. Format: first line 'HEARD: <short transcript>', then a blank line, then your reply (a short spoken-style reply, plain text, no markdown)."}]})
    else:
        contents.append({"role": "user", "parts": [{"text": text}]})
    ans = gemini(system, contents, search=True)
    last = [t for r, t in rows if r == "model"][-1:]
    if not ans or (last and ans.strip() == last[0].strip()):
        print("repeat/empty answer, regenerating")
        ans = gemini(system + "\n\nIMPORTANT: Your last reply was identical or empty. Answer Sunny's newest message freshly and specifically; do not repeat any earlier reply.", contents, search=True) or "Hmm, ek baar phir bolo na? 🙂"
    if audio:
        if ans.startswith("HEARD:"):
            head, _, rest = ans.partition("\n")
            heard = head[6:].strip() or "(voice note)"
            ans = rest.strip() or ans
        else:
            heard = "(voice note)"
        heard = "[voice note] " + heard
    c.add_turn(chat, heard, ans)
    return ans, heard

def handle(c, msg):
    chat, uid, text = msg["chat"]["id"], msg["from"]["id"], msg.get("text")
    if OWNER_ID and str(uid) != OWNER_ID:
        return  # private bot: ignore strangers
    c.kv_set("owner_chat", chat)  # where proactive messages go
    voice = msg.get("voice") or msg.get("audio")
    if voice and not text:
        tg("sendChatAction", chat_id=chat, action="record_voice")
        try:
            ans, heard = reply(c, chat, None, audio=download_voice(voice["file_id"]))
        except Exception as e:
            print("voice failed:", e)
            tg("sendMessage", chat_id=chat, text="Voice note sun nahi paayi, ek baar phir bhejoge? 🙈"); return
        tg("sendMessage", chat_id=chat, text=ans)
        mp3 = tts_mp3(ans)
        if mp3:
            try: send_voice(chat, mp3)
            except Exception as e: print("sendVoice failed:", e)
        said = heard.replace("[voice note] ", "")
        try:
            res = do_action(c, chat, said)
            if res:
                tg("sendMessage", chat_id=chat, text=gemini(SYSTEM, [{"role": "user", "parts": [{"text": res + " Sunny said (voice): " + said + "\nConfirm in one short warm line."}]}]))
        except Exception as e:
            print("voice action failed:", e)
        extract(c, said)
        return
    if not text:
        return
    if text.startswith("/start"):
        tg("sendMessage", chat_id=chat, text="Hey Sunny, KASH here 😊 Bolo, kya chal raha hai?"); return
    if text.startswith("/memories"):
        m = clean_facts(c)
        tg("sendMessage", chat_id=chat, text=("Yeh sab mujhe yaad hai:\n" + "\n".join(f"{i+1}. {f}" for i, f in enumerate(m))) if m else "Abhi kuch khaas yaad nahi, batao apne baare mein 🙂"); return
    if text.startswith("/reminders"):
        tg("sendMessage", chat_id=chat, text=list_reminders(c)); return
    if text.startswith("/cancel"):
        items = sorted(c.rem_open(), key=lambda d: d["due"])
        try:
            d = items[int(text.split()[1]) - 1]; d["status"] = "cancelled"; c.rem_put(d)
            tg("sendMessage", chat_id=chat, text=f"Cancelled: {d['text']} ✅")
        except Exception:
            tg("sendMessage", chat_id=chat, text="Number batao jaise /cancel 1 (list: /reminders)")
        return
    if text.startswith("/forget"):
        c.clear_facts()
        tg("sendMessage", chat_id=chat, text="Done, sab bhula diya."); return
    tg("sendChatAction", chat_id=chat, action="typing")
    try:
        note = None
        try: note = do_action(c, chat, text)
        except Exception as e: print("action failed:", repr(e)[:300])
        ans, _ = reply(c, chat, text, note=note)
    except Exception as e:
        print("gemini failed:", e)
        ans = "Ek sec, mera dimaag thoda atak gaya. Dobara bologe?"
    tg("sendMessage", chat_id=chat, text=ans[:4000], disable_web_page_preview=True)
    extract(c, text)

def main():
    serve_health()
    c = db()
    threading.Thread(target=scheduler, args=(c,), daemon=True).start()
    offset = None
    print("KASH is up. memory:", "mongodb" if MONGODB_URI else "local sqlite", flush=True)
    while True:
        try:
            r = tg("getUpdates", timeout=50, **({"offset": offset} if offset else {}))
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                if "message" in u:
                    handle(c, u["message"])
        except (urllib.error.URLError, TimeoutError) as e:
            print("net error:", e); time.sleep(5)
        except Exception as e:
            print("error:", e); time.sleep(3)

if __name__ == "__main__":
    main()
