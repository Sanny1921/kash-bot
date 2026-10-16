"""Server side of KASH wake-word mode (/agent/say).

The laptop helper transcribes speech LOCALLY and posts only the text. We run it
through the exact same handler as a typed Telegram message, so every rule
(draft-only mail, review-before-change, ask-first device actions) still applies.
Replies are captured so the helper can read them aloud; they also go to Telegram.
"""
import re
import threading
import time

CONFIRM_RE = re.compile(
    r"^\s*(?:yes|yeah|yep|ok(?:ay)?|do it|confirm(?:ed)?|go ahead|approve[d]?|sure|haan|ha|kar do|kardo|"
    r"delete (?:it|that|this)|send it|run it)\W*$", re.I)
MAX_CHARS = 600
MAX_PER_HOUR = 40
_LOCK = threading.Lock()
_TIMES = []


def _rate_ok():
    now = time.time()
    with _LOCK:
        _TIMES[:] = [t for t in _TIMES if now - t < 3600]
        if len(_TIMES) >= MAX_PER_HOUR:
            return False
        _TIMES.append(now)
        return True


def say(c, bot, chat, text):
    """Run one spoken request. Returns the reply text for the helper to speak."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_CHARS]
    if not text:
        return "I didn't catch that."
    if not _rate_ok():
        return "That's a lot of voice requests this hour. Please use Telegram for now."
    if CONFIRM_RE.match(text):
        # Ambient audio / a TV must never be able to approve anything.
        msg = "I don't take approvals by voice. Please answer in Telegram."
        try:
            bot.tg("sendMessage", chat_id=chat, text="🎙️ Voice heard a bare approval (\"%s\"). Not accepted by voice - reply here if you meant it." % text)
        except Exception:
            pass
        return msg
    try:
        bot.tg("sendMessage", chat_id=chat, text="🎙️ (voice) " + text)
    except Exception:
        pass
    owner = getattr(bot, "OWNER_ID", None)
    uid = int(owner) if owner and str(owner).isdigit() else chat
    msg = {"chat": {"id": chat, "type": "private"}, "from": {"id": uid}, "text": text}
    bot._TL.cap = []
    try:
        bot.handle(c, msg)
        out = list(bot._TL.cap)
    finally:
        bot._TL.cap = None
    out = [t for t in out if t and not t.startswith("🎙️")]
    return "\n".join(out)[:2000] if out else "Done, check Telegram."
