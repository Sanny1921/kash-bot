#!/usr/bin/env python3
"""KASH helper - runs on Sunny's own laptop/phone and connects OUTWARD to
kash-bot.onrender.com over HTTPS. No open ports, nothing to expose.

One-time setup: ask KASH on Telegram to "pair my laptop", she gives a
6-digit code, enter it here once. The device token is saved locally in
~/.kash/config.json and never shown again.

Scope (Sunny's rules): KASH works only inside one folder (default ~/KASH).
She can list/read/create/append files on her own; deleting, running
commands and opening apps happen only after his Telegram "yes".
Requires: Python 3.8+ (no pip installs, standard library only).
"""
import json
import os
import platform
import shutil
import subprocess
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import urllib.error
import urllib.request

DEFAULT_SERVER = "https://kash-bot.onrender.com"
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".kash")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
DEFAULT_ROOT = os.path.join(os.path.expanduser("~"), "KASH")
READ_LIMIT = 20000
RUN_TIMEOUT = 60
RUN_OUTPUT_LIMIT = 8000
LIST_LIMIT = 400


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass  # Windows has no chmod; the file stays in the user's home dir.


def api(cfg, method, path, body=None, timeout=40):
    url = cfg["server"].rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + cfg["token"])
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return r.status, json.loads(raw) if raw else {}


def pair(server, code):
    name = platform.node() or "my-device"
    system = platform.system()
    osinfo = "%s %s" % (system, platform.release())
    if os.environ.get("PREFIX", "").find("com.termux") != -1:
        osinfo = "Android (Termux)"
    root = DEFAULT_ROOT
    os.makedirs(root, exist_ok=True)
    body = {"code": code.strip(), "name": name, "os": osinfo, "root": root}
    req = urllib.request.Request(server.rstrip("/") + "/agent/pair",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", str(e))
        except Exception:
            msg = str(e)
        print("Pairing failed:", msg)
        sys.exit(1)
    cfg = {"server": server.rstrip("/"), "device_id": out["device_id"], "token": out["token"],
           "name": out.get("name", name), "root": root}
    save_config(cfg)
    return cfg


def safe_path(root, rel):
    """Resolve rel inside root; refuse anything that escapes it."""
    if not rel or rel in (".", "./"):
        return os.path.realpath(root)
    if os.path.isabs(rel) or (len(rel) > 1 and rel[1] == ":"):
        raise ValueError("absolute paths are not allowed")
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, rel))
    if target != root_real and not target.startswith(root_real + os.sep):
        raise ValueError("path escapes the KASH folder")
    return target


def do_list(root, rel):
    base = safe_path(root, rel)
    if not os.path.exists(base):
        return False, "", "path does not exist: " + rel
    lines = []
    count = 0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            full = os.path.join(dirpath, name)
            relshow = os.path.relpath(full, os.path.realpath(root))
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            lines.append("%s (%d bytes)" % (relshow, size))
            count += 1
            if count >= LIST_LIMIT:
                lines.append("... and more (list truncated at %d files)" % LIST_LIMIT)
                return True, "\n".join(lines), ""
    if not lines:
        return True, "(folder is empty)", ""
    return True, "\n".join(lines), ""


def do_read(root, rel):
    target = safe_path(root, rel)
    if os.path.isdir(target):
        return False, "", "that is a folder - use list instead"
    if not os.path.exists(target):
        return False, "", "no such file: " + rel
    if os.path.getsize(target) > 500000:
        return False, "", "file too big to read in chat (over 500 KB)"
    with open(target, "rb") as f:
        raw = f.read(READ_LIMIT + 1)
    truncated = len(raw) > READ_LIMIT
    text = raw[:READ_LIMIT].decode("utf-8", errors="replace")
    if truncated:
        text += "\n... (truncated at %d characters)" % READ_LIMIT
    return True, text, ""


def do_write(root, rel, content):
    target = safe_path(root, rel)
    if os.path.exists(target):
        return False, "", "file already exists - KASH will not overwrite it (delete it first or use append)"
    os.makedirs(os.path.dirname(target) or root, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    return True, "created %s (%d bytes)" % (rel, len(content.encode())), ""


def do_append(root, rel, content):
    target = safe_path(root, rel)
    os.makedirs(os.path.dirname(target) or root, exist_ok=True)
    with open(target, "a", encoding="utf-8") as f:
        f.write(content)
    return True, "appended %d bytes to %s" % (len(content.encode()), rel), ""


def do_delete(root, rel):
    target = safe_path(root, rel)
    if not os.path.exists(target):
        return False, "", "no such file or folder: " + rel
    if os.path.isdir(target):
        shutil.rmtree(target)
        return True, "deleted folder " + rel, ""
    os.remove(target)
    return True, "deleted " + rel, ""


def do_run(root, command):
    try:
        out = subprocess.run(command, shell=True, cwd=root, capture_output=True,
                             text=True, timeout=RUN_TIMEOUT, errors="replace")
        text = (out.stdout or "") + (("\n[stderr]\n" + out.stderr) if out.stderr else "")
        text = text[:RUN_OUTPUT_LIMIT]
        note = "exit code %d" % out.returncode
        return out.returncode == 0, (text + ("\n" if text else "") + note), ("" if out.returncode == 0 else note)
    except subprocess.TimeoutExpired:
        return False, "", "command ran longer than %d seconds - killed" % RUN_TIMEOUT


def do_open(root, rel):
    target = safe_path(root, rel)
    if not os.path.exists(target):
        return False, "", "no such file or folder: " + rel
    system = platform.system()
    if system == "Windows":
        os.startfile(target)  # noqa: S606 - user-approved open
    elif system == "Darwin":
        subprocess.Popen(["open", target])
    elif shutil.which("termux-open"):
        subprocess.Popen(["termux-open", target])
    else:
        subprocess.Popen(["xdg-open", target])
    return True, "opened " + rel, ""


def execute(root, job):
    action = job.get("action")
    args = job.get("args", {})
    try:
        if action == "list":
            return do_list(root, args.get("path", "."))
        if action == "read":
            return do_read(root, args.get("path", ""))
        if action == "write":
            return do_write(root, args.get("path", ""), args.get("content", ""))
        if action == "append":
            return do_append(root, args.get("path", ""), args.get("content", ""))
        if action == "delete":
            return do_delete(root, args.get("path", ""))
        if action == "run":
            return do_run(root, args.get("command", ""))
        if action == "open":
            return do_open(root, args.get("path", ""))
        if action == "browse":
            try:
                import kash_browser
            except ImportError:
                return False, "", "kash_browser.py must sit in the same folder as kash_helper.py - download it from the repo."
            return kash_browser.browse(args)
        return False, "", "unknown action: " + str(action)
    except ValueError as e:
        return False, "", str(e)
    except Exception as e:
        return False, "", "%s: %s" % (type(e).__name__, e)


def main():
    server = DEFAULT_SERVER
    args = sys.argv[1:]
    if "--server" in args:
        i = args.index("--server")
        server = args[i + 1]
    for flag, fn in (("--save-login", "save_login"), ("--forget-login", "forget_login")):
        if flag in args:
            import kash_browser
            i = args.index(flag)
            getattr(kash_browser, fn)(args[i + 1] if len(args) > i + 1 else "")
            return
    if "--list-logins" in args:
        import kash_browser
        kash_browser.list_logins()
        return
    cfg = load_config()
    if "--unpair" in args:
        if cfg and os.path.exists(CONFIG_PATH):
            os.remove(CONFIG_PATH)
            print("Unpaired locally. Ask KASH to /unpair this device too, so she forgets it.")
        else:
            print("Nothing to unpair.")
        return
    if "--status" in args:
        if cfg:
            print("Paired as %s\nFolder: %s\nServer: %s" % (cfg.get("name"), cfg.get("root"), cfg.get("server")))
        else:
            print("Not paired yet. Ask KASH on Telegram to 'pair my laptop', then run this and enter the code.")
        return
    if not cfg:
        print("KASH helper - first time setup")
        print("Working folder will be:", DEFAULT_ROOT)
        code = input("Enter the 6-digit pairing code from KASH (Telegram): ").strip()
        cfg = pair(server, code)
        print("Paired as '%s'. KASH can now work in: %s" % (cfg["name"], cfg["root"]))
        print("Keep this window open while you want her to have access. Ctrl+C to stop.\n")
    root = cfg.get("root") or DEFAULT_ROOT
    os.makedirs(root, exist_ok=True)
    backoff = 3
    print("KASH helper running. Folder:", root)
    while True:
        try:
            status, data = api(cfg, "GET", "/agent/poll?device_id=%s&wait=25" % cfg["device_id"], timeout=40)
            backoff = 3
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print("\nKASH rejected this device's token (unpaired?). Re-pair with a new code.")
                return
            print("Server error %s - retrying in %ds" % e.code, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        except Exception as e:
            print("Connection trouble (%s) - retrying in %ds" % (type(e).__name__, backoff))
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        job = (data or {}).get("job")
        if not job:
            continue
        print("job:", job.get("action"), job.get("args", {}).get("path") or job.get("args", {}).get("command") or "")
        ok, output, error = execute(root, job)
        try:
            api(cfg, "POST", "/agent/result",
                {"device_id": cfg["device_id"], "job_id": job["id"], "ok": ok, "output": output, "error": error})
        except Exception as e:
            print("could not report result (%s) - will retry on next poll" % type(e).__name__)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nHelper stopped. KASH can't reach this machine until you start it again.")
