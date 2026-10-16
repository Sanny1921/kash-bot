"""KASH wake-word mode - runs inside kash_helper.py on Sunny's own laptop.

OPT-IN ONLY: nothing listens unless you start it with  python kash_helper.py --wake
- Wake detection AND speech-to-text run locally (Vosk, offline, free).
- Microphone audio is never saved and never sent anywhere. Only the TEXT of
  what you said after "Hey KASH" goes to KASH, and a copy shows in Telegram.
- A visible indicator is always printed while the mic is open.
- Stop: Ctrl+C, close the window, or say "Hey KASH, stop listening".
  It also auto-stops after --wake-minutes (default 120) so the mic is never left open.
- Approvals (delete, run, send, etc.) are never accepted by voice: KASH asks
  in Telegram and you answer there.
Needs:  pip install vosk sounddevice   (Python 3.8+)
"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

MODEL_NAME = "vosk-model-small-en-in-0.4"   # Indian English, ~36 MB
MODEL_URL = "https://alphacephei.com/vosk/models/%s.zip" % MODEL_NAME
MODEL_DIR = os.path.join(os.path.expanduser("~"), ".kash", "vosk")
RATE = 16000
ARM_SECONDS = 8          # after a bare "Hey KASH", how long she waits for the request
MAX_SPOKEN_CHARS = 500   # reply read aloud; the full text is always in Telegram

# speech-to-text may spell it either way: kash / cash (also cache, kush)
WAKE_RE = re.compile(r"\b(?:hey|hay|hi+|hii+|hai)\s+(?:kash|cash|cache|kush|kass)\b[\s,.!?:-]*", re.I)
GRAMMAR = json.dumps(["hey kash", "hey cash", "hi kash", "hi cash", "hay kash", "hay cash", "[unk]"])
GW_RE = re.compile(r"\b(?:hey|hi|hay)\s+(?:kash|cash)\b")
STOP_RE = re.compile(r"\bstop(?:ped)?\s+listening\b", re.I)


def split_wake(text):
    """Return (heard_wake, rest_of_sentence_after_the_wake_phrase)."""
    m = WAKE_RE.search(text or "")
    if not m:
        return False, ""
    return True, text[m.end():].strip()


def clean_for_speech(text):
    text = re.sub(r"https?://\S+", "link in Telegram", text)
    text = re.sub(r"[*_`#>~|]", "", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_SPOKEN_CHARS:
        cut = text[:MAX_SPOKEN_CHARS]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        text = (cut[:end + 1] if end > 150 else cut) + " The rest is in Telegram."
    return text


def speak(text):
    """Read text aloud with the OS voice (offline). Returns False if none available."""
    text = clean_for_speech(text)
    if not text:
        return True
    system = platform.system()
    try:
        if system == "Windows":
            ps = ("Add-Type -AssemblyName System.Speech;"
                  "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                  "$v=$s.GetInstalledVoices()|Where-Object{$_.VoiceInfo.Gender -eq 'Female'}|Select-Object -First 1;"
                  "if($v){$s.SelectVoice($v.VoiceInfo.Name)};"
                  "$s.Speak([Console]::In.ReadToEnd())")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], input=text.encode("utf-8"),
                           timeout=120, creationflags=0x08000000)
            return True
        if system == "Darwin":
            subprocess.run(["say", "-v", "Samantha"], input=text.encode("utf-8"), timeout=120)
            return True
        for exe, args in (("espeak-ng", ["-v", "en-in+f3", "--stdin"]), ("espeak", ["-v", "en+f3", "--stdin"])):
            if shutil.which(exe):
                subprocess.run([exe] + args, input=text.encode("utf-8"), timeout=120)
                return True
        if shutil.which("spd-say"):
            subprocess.run(["spd-say", "-w", text], timeout=120)
            return True
    except Exception as e:
        print("  (voice playback failed: %s)" % type(e).__name__)
        return False
    return False


def ensure_model():
    path = os.path.join(MODEL_DIR, MODEL_NAME)
    if os.path.isdir(path):
        return path
    print("First run: KASH needs a small offline speech model (%s, ~36 MB, free)." % MODEL_NAME)
    print("It is downloaded once from %s and kept in %s" % (MODEL_URL, MODEL_DIR))
    if input("Download it now? [y/N] ").strip().lower() not in ("y", "yes"):
        print("OK, not downloading. Wake mode not started.")
        sys.exit(0)
    os.makedirs(MODEL_DIR, exist_ok=True)
    zpath = os.path.join(MODEL_DIR, MODEL_NAME + ".zip")
    urllib.request.urlretrieve(MODEL_URL, zpath)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(MODEL_DIR)
    os.remove(zpath)
    return path


def _words(res):
    return [(w.get("word", ""), w.get("start", 0.0), w.get("end", 0.0)) for w in res.get("result", [])]


def _wake_end(gres):
    """End time (seconds) of 'hey/hi kash/cash' in a grammar-recognizer result, else None."""
    ws = _words(gres)
    for i in range(len(ws) - 1):
        if ws[i][0] in ("hey", "hi", "hay") and ws[i + 1][0] in ("kash", "cash"):
            return ws[i + 1][2]
    return None


def run(cfg, api, minutes=120, say_aloud=True):
    try:
        import sounddevice as sd
        from vosk import Model, KaldiRecognizer, SetLogLevel
    except ImportError:
        print("Wake mode needs two packages:  pip install vosk sounddevice")
        if platform.system() == "Linux":
            print("On Linux you may also need:  sudo apt install libportaudio2 espeak-ng")
        return
    SetLogLevel(-1)
    model = Model(ensure_model())
    # Two local recognizers on the same audio: a tiny grammar one that is very
    # reliable at spotting only the wake phrase, and a full one that writes down
    # the request. Both run offline; no audio leaves memory.
    gram = KaldiRecognizer(model, RATE, GRAMMAR)
    full = KaldiRecognizer(model, RATE)
    gram.SetWords(True)
    full.SetWords(True)
    deadline = time.time() + max(1, minutes) * 60
    armed_until = 0.0
    can_speak = say_aloud and bool(shutil.which("espeak-ng") or shutil.which("espeak") or shutil.which("spd-say")
                                   or platform.system() in ("Windows", "Darwin"))
    print("=" * 60)
    print(" KASH wake mode ON - mic is OPEN and listening locally")
    print("    Say: 'Hey KASH' + your request   (also 'hey cash', 'hi kash')")
    print("    Stop: Ctrl+C, close this window, or say 'Hey KASH, stop listening'")
    print("    Auto-stops in %d min. Audio is not saved or uploaded; only the" % minutes)
    print("    text of your request is sent to KASH (copy appears in Telegram).")
    if not can_speak:
        print("    (No offline voice found - replies will print here and show in Telegram.)")
    print("=" * 60)

    def ask(request):
        print("  -> you said: %s" % request)
        try:
            status, data = api(cfg, "POST", "/agent/say", {"device_id": cfg["device_id"], "text": request}, timeout=120)
        except Exception as e:
            print("  could not reach KASH (%s)" % type(e).__name__)
            return
        reply = (data or {}).get("reply") or "Done, check Telegram."
        print("  KASH: %s" % reply[:400])
        if can_speak:
            print("  (speaking - mic paused)")
            speak(reply)

    def flush(stream):
        try:
            n = stream.read_available
            if n:
                stream.read(n)
        except Exception:
            pass
        gram.Reset()
        full.Reset()

    wake_ends = []      # wake-phrase end times seen by the grammar recognizer
    pending = None      # (full result, chunks_left): wait a moment so the grammar result can catch up
    try:
        with sd.RawInputStream(samplerate=RATE, blocksize=4000, dtype="int16", channels=1) as stream:
            while time.time() < deadline:
                data, _ = stream.read(4000)
                raw = bytes(data)
                if gram.AcceptWaveform(raw):
                    we = _wake_end(json.loads(gram.Result()))
                    if we is not None:
                        wake_ends.append(we)
                if full.AcceptWaveform(raw):
                    res = json.loads(full.Result())
                    if (res.get("text") or "").strip():
                        pending = [res, 3]
                if pending is None:
                    continue
                pending[1] -= 1
                if pending[1] > 0:
                    continue
                res, _n = pending
                pending = None
                words = _words(res)
                text = (res.get("text") or "").strip()
                t0 = words[0][1] if words else 0
                t1 = words[-1][2] if words else 0
                hit = [w for w in wake_ends if t0 - 0.6 <= w <= t1 + 0.6]
                wake_ends = []
                heard, rest = split_wake(text)
                if hit and not heard:
                    heard = True
                    rest = " ".join(w for w, st, en in words if st >= hit[0] - 0.15).strip()
                if STOP_RE.search(text) and (heard or time.time() < armed_until):
                    print("  Stopped by voice command. Mic closed.")
                    return
                if heard:
                    if rest:
                        ask(rest)
                        armed_until = 0.0
                    else:
                        armed_until = time.time() + ARM_SECONDS
                        print("  * heard 'Hey KASH' - speak now...\a")
                    flush(stream)
                elif time.time() < armed_until:
                    armed_until = 0.0
                    ask(text)
                    flush(stream)
            print("  Auto-stop reached (%d min). Mic closed. Start again with --wake." % minutes)
    except KeyboardInterrupt:
        print("\n  Wake mode stopped. Mic closed.")
