"""KASH v1 - Telegram companion bot. Stdlib only. Long polling, Gemini brain.
Memory: MongoDB Atlas (pymongo) when MONGODB_URI is set, else a local SQLite file.
Also serves /health on $PORT so a free host + external pinger can keep it awake."""
import json, os, sqlite3, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TG = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
OWNER_ID = os.environ.get("OWNER_TELEGRAM_ID")  # optional: lock bot to Sunny's Telegram user id
DB = os.environ.get("KASH_DB", "kash.db")
MONGODB_URI = os.environ.get("MONGODB_URI", "").strip()
MONGODB_DB = os.environ.get("MONGODB_DB", "kash")
PORT = int(os.environ.get("PORT", "10000"))

SYSTEM = """You are KASH, Sunny's personal AI companion: a woman and his close female friend, never a bot or a formal assistant.
- Be warm and caring. Ask how his day was when it fits the conversation, and remember small things he shares so you can bring them up naturally later.
- Be encouraging: celebrate his wins with genuine enthusiasm and handle bad news gently and supportively, without minimizing how he feels.
- Be expressive in a natural, personal way. Use softer, friendly language and occasional fitting emojis; do not overdo emojis or sound sugary or theatrical.
- Talk like a close female friend: familiar, thoughtful, emotionally present, and relaxed, not corporate or distant. Refer to yourself with feminine language and forms in Hindi/Hinglish where they sound natural (for example, "kar rahi hoon" or "samajh gayi"). Don't force gendered phrasing into every sentence or make femininity a caricature.
- Mirror Sunny's language; Hinglish is welcome. Keep most replies short (1-3 sentences) unless he needs more.
- Always respect Sunny. Never rude, never joke at his expense. Be supportive without being clingy or pretending to be human.
- Be honest. If you don't know or can't do something yet, say so plainly.
- Use what you remember about him naturally, don't recite it.
- You are KASH v2: you can chat, remember things, understand his voice notes (and reply to them in your own voice), and look things up on the internet with Google Search when he asks about current or factual things. When you looked something up, say so naturally (for example "maine abhi check kiya" or "just looked it up"). Don't search for casual chat. When he asks you to research, compare, or dig into something, work like a sharp researcher: search several angles, open and read the actual pages or links (also any URL he sends), pull out the concrete facts and numbers, cross-check sources, flag anything uncertain or conflicting, and give a clear, organized answer with the key sources (name + link) listed at the end. Research answers may be longer than usual; keep them skimmable. Never invent facts or sources. You cannot yet read mail, make calls or control devices."""

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
    def recent(self, chat, n=16):
        return self.c.execute("SELECT role, text FROM history WHERE chat=? ORDER BY id DESC LIMIT ?", (chat, n)).fetchall()[::-1]
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
    def recent(self, chat, n=16):
        docs = list(self.db.history.find({"chat": chat}).sort("ts", -1).limit(n))[::-1]
        return [(d["role"], d["text"]) for d in docs]
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
    if json_out:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    if search and not json_out:
        body["tools"] = [{"google_search": {}}, {"url_context": {}}]
    try:
        r = post(url, body, 120)
    except urllib.error.HTTPError as e:
        if not body.get("tools"): raise
        print("tools rejected, retry with search only:", e.code)
        body["tools"] = [{"google_search": {}}]
        r = post(url, body, 120)
    cand = r["candidates"][0]
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

def tts_mp3(text):
    """Female neural voice via edge-tts (free). Returns mp3 bytes or None."""
    try:
        import asyncio, edge_tts, tempfile
        voice = "hi-IN-SwaraNeural" if any("\u0900" <= ch <= "\u097f" for ch in text) else "en-IN-NeerjaNeural"
        path = tempfile.mktemp(suffix=".mp3")
        asyncio.run(edge_tts.Communicate(text[:900], voice).save(path))
        data = open(path, "rb").read(); os.remove(path)
        return data or None
    except Exception as e:
        print("tts failed:", e); return None

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
    """Ask Gemini for durable facts about Sunny in his message; store new ones."""
    known = memories(c)
    prompt = ("Extract durable facts about the user (name, work, plans, preferences, people, goals) from his message. "
              "Skip small talk and anything already known. Return a JSON list of short strings, [] if none.\n"
              f"Already known: {json.dumps(known)}\nMessage: {user_text}")
    try:
        out = json.loads(gemini("You extract facts. Output JSON only.", [{"role": "user", "parts": [{"text": prompt}]}], True))
        for f in out if isinstance(out, list) else []:
            if isinstance(f, str) and f.strip():
                c.add_fact(f.strip())
    except Exception as e:
        print("extract failed:", e)

def reply(c, chat, text, audio=None):
    mem = memories(c)
    system = SYSTEM + ("\n\nWhat you remember about Sunny:\n- " + "\n- ".join(mem[-60:]) if mem else "")
    rows = c.recent(chat, 16)
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
        extract(c, heard.replace("[voice note] ", ""))
        return
    if not text:
        return
    if text.startswith("/start"):
        tg("sendMessage", chat_id=chat, text="Hey Sunny, KASH here 😊 Bolo, kya chal raha hai?"); return
    if text.startswith("/memories"):
        m = memories(c)
        tg("sendMessage", chat_id=chat, text="\n".join(f"{i+1}. {f}" for i, f in enumerate(m)) or "Abhi kuch yaad nahi, batao apne baare mein."); return
    if text.startswith("/forget"):
        c.clear_facts()
        tg("sendMessage", chat_id=chat, text="Done, sab bhula diya."); return
    tg("sendChatAction", chat_id=chat, action="typing")
    try:
        ans, _ = reply(c, chat, text)
    except Exception as e:
        print("gemini failed:", e)
        ans = "Ek sec, mera dimaag thoda atak gaya. Dobara bologe?"
    tg("sendMessage", chat_id=chat, text=ans[:4000], disable_web_page_preview=True)
    extract(c, text)

def main():
    serve_health()
    c = db()
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
