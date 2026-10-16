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
def _launch(p):
    os.makedirs(PROFILE, exist_ok=True)
    last = None
    for channel in ("chrome", "msedge", None):
        try:
            kw = {"channel": channel} if channel else {}
            return p.chromium.launch_persistent_context(PROFILE, headless=False, **kw)
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
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
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
                            page.wait_for_load_state("networkidle", timeout=15000)
                        except Exception:
                            pass
                        out.append("SECTION '%s' - %s\n%s" % (label[:60], page.url, _text(page, 4000)))
            return True, "\n\n".join(out)[:12000], ""
        finally:
            ctx.close()
