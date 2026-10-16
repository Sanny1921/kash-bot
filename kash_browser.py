"""KASH browser skill - runs inside kash_helper.py on Sunny's own laptop.

Opens a real (visible) Chrome/Edge window with its own persistent profile, signs in
to a site ONLY if Sunny saved that login on this machine, and READS pages.
Read-only: it never submits forms other than the login form, never clicks
buttons like Submit/Delete/Pay, and returns text only to his private Telegram.

Login storage: you type the password into THIS program's terminal once
(`python kash_helper.py --save-login msu.digiicampus.com`). It never goes through
Telegram, Render or the repo. Windows: encrypted with your Windows account (DPAPI).
Linux: a file readable only by your user (honest limit: obscured, not encrypted
at rest - anyone with your Linux login can read it).
Needs: pip install playwright  (uses your installed Chrome or Edge; no big download)
"""
import base64, getpass, json, os, re, sys, time

DIR = os.path.join(os.path.expanduser("~"), ".kash")
LOGINS = os.path.join(DIR, "logins.json")
PROFILE = os.path.join(DIR, "browser-profile")
PROFILE_MEDIA = os.path.join(DIR, "browser-profile-media")  # separate: YouTube playback, no saved logins needed
UNSAFE = re.compile(r"submit|delete|remove|pay|buy|upload|send|confirm|save|post|logout|log out|sign out|enroll|withdraw", re.I)
NAV = re.compile(r"assignment|pending|to.?do|task|submission|due|upcoming|course|deadline", re.I)


# ---------- local secret store ----------
def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    inb = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _seal(secret):
    if os.name == "nt":
        return "dpapi:" + base64.b64encode(_dpapi(secret.encode(), True)).decode()
    return "b64:" + base64.b64encode(secret.encode()).decode()


def _unseal(token):
    kind, _, body = token.partition(":")
    raw = base64.b64decode(body)
    return (_dpapi(raw, False) if kind == "dpapi" else raw).decode()


def _load():
    try:
        with open(LOGINS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _store(d):
    os.makedirs(DIR, exist_ok=True)
    with open(LOGINS, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    try:
        os.chmod(LOGINS, 0o600)
    except OSError:
        pass


def site_key(s):
    s = (s or "").strip().lower()
    s = re.sub(r"^https?://", "", s).split("/")[0]
    return s


def save_login(site):
    key = site_key(site)
    if not key:
        print("Usage: python kash_helper.py --save-login msu.digiicampus.com")
        return
    user = input("Username / email for %s: " % key).strip()
    pw = getpass.getpass("Password (typing is hidden, stays on this computer): ")
    d = _load()
    d[key] = {"user": user, "pw": _seal(pw)}
    _store(d)
    print("Saved for %s. KASH can now read pages there. Nothing was sent anywhere." % key)


def forget_login(site):
    d = _load()
    d.pop(site_key(site), None)
    _store(d)
    print("Removed.")


def list_logins():
    for k, v in _load().items():
        print(k, "-", v.get("user"))


# ---------- browsing (read-only) ----------
def _launch(p, profile=PROFILE):
    os.makedirs(profile, exist_ok=True)
    last = None
    for channel in ("chrome", "msedge", None):
        try:
            kw = {"channel": channel} if channel else {}
            return p.chromium.launch_persistent_context(profile, headless=False, **kw)
        except Exception as e:
            last = e
    raise RuntimeError("No Chrome/Edge found and no Playwright browser installed (%s). "
                       "Run: python -m playwright install chromium" % str(last)[:120])


def _has_pw(page):
    try:
        return page.locator("input[type=password]:visible").count() > 0
    except Exception:
        return False


def _login(page, entry):
    pw_field = page.locator("input[type=password]:visible").first
    users = page.locator("input:visible:not([type=password]):not([type=hidden]):not([type=checkbox]):not([type=submit]):not([type=button])")
    if users.count():
        users.first.fill(entry["user"])
    pw_field.fill(_unseal(entry["pw"]))
    btn = page.locator("button[type=submit]:visible, input[type=submit]:visible, button:visible:text-matches('log ?in|sign ?in|login', 'i')").first
    if btn.count():
        btn.click()
    else:
        pw_field.press("Enter")


def _text(page, limit=6000):
    try:
        t = page.inner_text("body", timeout=8000)
    except Exception:
        t = ""
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t[:limit]


LOADING = re.compile(r"^\W*(loading|please wait)[\w\s.,!]*$", re.I)


def _settle(page, budget=40):
    """Wait for a single-page app to actually render: network idle, then real text (not 'Loading, please wait')."""
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    end = time.time() + budget
    stable, last = 0, ""
    while time.time() < end:
        t = _text(page, 3000)
        if len(t) > 80 and not LOADING.match(t) and "loading, please wait" not in t.lower()[:200]:
            stable = stable + 1 if t == last else 0
            last = t
            if stable >= 1:
                return True
        else:
            last = ""
            stable = 0
        time.sleep(2)
    return False


def browse(args):
    """args: site, task ('assignments' | 'read'), url (optional). Returns (ok, output, error)."""
    site = site_key(args.get("site"))
    entry = _load().get(site)
    if not entry:
        return False, "", ("No login saved on this computer for %s. Run in a terminal: "
                           "python kash_helper.py --save-login %s" % (site, site))
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False, "", "Playwright missing. Run: pip install playwright  (then restart the helper)"
    url = args.get("url") or "https://" + site
    if site_key(url) != site:
        return False, "", "Only the saved site is allowed."
    out = []
    with sync_playwright() as p:
        ctx = _launch(p)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(4)
            if _has_pw(page):
                _login(page, entry)
                # Give Sunny up to 2 minutes to solve a captcha / MFA in the visible window.
                deadline = time.time() + 120
                time.sleep(4)
                while time.time() < deadline and _has_pw(page):
                    time.sleep(3)
                if _has_pw(page):
                    return False, "", "Still on the login page - captcha/MFA/wrong password. Solve it in the open window, or re-save the login."
            if not _settle(page):
                return False, "", ("Page abhi bhi load nahi hua (sirf 'Loading' dikh raha). Internet/site slow ho sakta hai - thodi der baad dobara try karo.")
            if _has_pw(page):
                return False, "", "Login nahi ho paya - password field abhi bhi dikh raha hai. Login dobara save karo: python kash_helper.py --save-login %s" % site
            out.append("PAGE: %s\n%s" % (page.url, _text(page, 3000)))
            if args.get("task") == "assignments":
                links = page.locator("a:visible").all()[:200]
                seen = set()
                for a in links:
                    try:
                        label = (a.inner_text(timeout=1000) or "").strip()
                    except Exception:
                        continue
                    if label and NAV.search(label) and not UNSAFE.search(label) and label.lower() not in seen and len(seen) < 2:
                        seen.add(label.lower())
                        try:
                            a.click(timeout=5000)
                            _settle(page, 20)
                        except Exception:
                            pass
                        out.append("SECTION '%s' - %s\n%s" % (label[:60], page.url, _text(page, 4000)))
            return True, "\n\n".join(out)[:12000], ""
        finally:
            ctx.close()


# ---------- YouTube playback browser (persistent, KASH-controlled) ----------
_MEDIA = {"p": None, "ctx": None, "page": None, "history": [], "idx": -1}


def _media_reset():
    ctx, p = _MEDIA.get("ctx"), _MEDIA.get("p")
    _MEDIA.update({"p": None, "ctx": None, "page": None, "history": [], "idx": -1})
    try:
        if ctx is not None:
            ctx.close()
    except Exception:
        pass
    try:
        if p is not None:
            p.stop()
    except Exception:
        pass


def _media_page(launch):
    """Live page in the persistent media browser. launch=False never starts one
    (control ops should fail cleanly when nothing is open)."""
    ctx = _MEDIA.get("ctx")
    if ctx is not None:
        try:
            page = _MEDIA.get("page")
            if page is None or page.is_closed():
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                _MEDIA["page"] = page
            return page, ""
        except Exception:
            _media_reset()  # window was closed / context died
    if not launch:
        return None, "no media browser"
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None, "Playwright missing. Run: pip install playwright  (then restart the helper)"
    try:
        if _MEDIA.get("p") is None:
            _MEDIA["p"] = sync_playwright().start()
        ctx = _launch(_MEDIA["p"], PROFILE_MEDIA)
        _MEDIA["ctx"] = ctx
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        _MEDIA["page"] = page
        return page, ""
    except Exception as e:
        _media_reset()
        return None, "browser nahi khul paya: %s" % str(e)[:150]


def _title(page):
    try:
        t = page.evaluate("""() => {
            const h = document.querySelector('h1.ytd-watch-metadata yt-formatted-string, h1.title yt-formatted-string');
            return (h && h.textContent.trim()) || document.title.replace(/ - YouTube$/, '');
        }""")
        return (t or "").strip()[:120]
    except Exception:
        return ""


def open_url(args):
    """Open an https YouTube page in the persistent media browser so playback stays
    controllable. Narrow allowlist: https youtube.com / youtu.be only."""
    from urllib.parse import urlparse
    u = urlparse(str(args.get("url", "")))
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not (host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be"):
        return False, "", "Sirf YouTube links khol sakti hoon."
    url = u.geturl()
    page, err = _media_page(launch=True)
    if page is None:
        # Fallback: default browser opens the song, but playback control will not work there.
        try:
            import webbrowser
            if webbrowser.open(url):
                return True, "opened " + url + " (default browser, no playback control: %s)" % err, ""
            return False, "", "browser nahi khul paya: %s" % err
        except Exception as e:
            return False, "", str(e)[:150]
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.bring_to_front()
    except Exception as e:
        return False, "", "YouTube nahi khul paya: %s" % str(e)[:150]
    hist, idx = _MEDIA["history"], _MEDIA["idx"]
    if idx < 0 or hist[idx] != url:
        _MEDIA["history"] = hist[:idx + 1] + [url]
        _MEDIA["idx"] = idx + 1
    return True, "opened " + url, ""


_VIDEO_JS = """(op) => {
    const v = document.querySelector('video');
    if (!v) return 'novideo';
    if (op === 'pause') { v.pause(); return 'ok'; }
    if (op === 'resume') { v.play(); return 'ok'; }
    if (op === 'replay') { v.currentTime = 0; v.play(); return 'ok'; }
    if (op === 'volume_up') { v.muted = false; v.volume = Math.min(1, Math.round((v.volume + 0.2) * 5) / 5); return String(Math.round(v.volume * 100)); }
    if (op === 'volume_down') { v.volume = Math.max(0, Math.round((v.volume - 0.2) * 5) / 5); return String(Math.round(v.volume * 100)); }
    if (op === 'mute') { v.muted = true; return 'ok'; }
    if (op === 'unmute') { v.muted = false; return 'ok'; }
    return 'badop';
}"""


def _wait_title(page, timeout=12):
    """Poll for a real video title after navigation (h1 and document.title lag; the
    page shows 'YouTube' or '- YouTube' until the watch metadata hydrates)."""
    end = time.time() + timeout
    while time.time() < end:
        t = _title(page)
        if t and t.strip().lstrip("-").strip().lower() not in ("", "youtube"):
            return t
        time.sleep(0.7)
    return ""


def _vid(url):
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", url or "")
    if m:
        return m.group(1)
    m = re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else ""


_RELATED_JS = """(cur) => {
    const out = [];
    for (const a of document.querySelectorAll("ytd-compact-video-renderer a#thumbnail[href*='watch'], ytd-video-renderer a#thumbnail[href*='watch'], a#thumbnail[href*='watch']")) {
        if (a.href && !a.href.includes(cur) && !out.includes(a.href)) out.push(a.href);
        if (out.length >= 3) break;
    }
    return out;
}"""


def media_control(args):
    """Playback control on the YouTube tab KASH opened. Never launches a browser."""
    op = str(args.get("op", "")).strip().lower()
    page, _ = _media_page(launch=False)
    if page is None:
        return False, "", ("Abhi koi YouTube tab mere control mein nahi hai is device pe - "
                           "pehle koi gaana chalwao, jaise 'play majaal song'.")
    try:
        if op == "close":
            try:
                page.close()
            except Exception:
                pass
            _MEDIA["page"] = None
            return True, "closed", ""
        if op == "previous":
            # Own watch-history stack: full navigations only. YouTube's SPA soft-nav
            # (next-button click / history back) can leave the player unhydrated.
            hist, idx = _MEDIA["history"], _MEDIA["idx"]
            if idx <= 0:
                return False, "", "Koi pichla gaana nahi hai - yahi pehla gaana tha."
            _MEDIA["idx"] = idx - 1
            page.goto(hist[idx - 1], wait_until="domcontentloaded", timeout=45000)
            return True, "playing: " + _wait_title(page), ""
        if op == "next":
            hist, idx = _MEDIA["history"], _MEDIA["idx"]
            if idx + 1 < len(hist):
                url = hist[idx + 1]
            else:
                urls = page.evaluate(_RELATED_JS, _vid(page.url))
                if not urls:
                    # DOM may still be rendering after a recent navigation - retry once.
                    time.sleep(1.5)
                    urls = page.evaluate(_RELATED_JS, _vid(page.url))
                if not urls:
                    return False, "", "Agla gaana nahi mila is page pe."
                url = urls[0]
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if idx + 1 < len(_MEDIA["history"]):
                _MEDIA["idx"] = idx + 1
            else:
                _MEDIA["history"] = hist[:idx + 1] + [url]
                _MEDIA["idx"] = idx + 1
            return True, "playing: " + _wait_title(page), ""
        if op in ("pause", "resume", "replay", "volume_up", "volume_down", "mute", "unmute"):
            r = page.evaluate(_VIDEO_JS, op)
            if r in ("novideo", "badop"):
                return False, "", "Is tab pe koi video nahi chal raha."
            if op == "pause":
                return True, "paused", ""
            if op == "resume":
                return True, "playing: " + _title(page), ""
            if op == "replay":
                return True, "replaying: " + _title(page), ""
            if op in ("volume_up", "volume_down"):
                return True, "volume %s%%" % r, ""
            return True, ("muted" if op == "mute" else "unmuted"), ""
        return False, "", "unknown control op: " + op
    except Exception as e:
        return False, "", "playback control nahi ho paya: %s" % str(e)[:150]
