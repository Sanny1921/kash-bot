import json, os, re, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import device_agent

class FakeStore:
    def __init__(self): self.kv = {}
    def kv_get(self, k): return self.kv.get(k)
    def kv_set(self, k, v): self.kv[k] = str(v)

for stale in (os.path.expanduser("~/.kash/config.json"),):
    if os.path.exists(stale): os.remove(stale)
store = FakeStore()
NOTES = []
def notify(target, message): NOTES.append((target, message))

class H(BaseHTTPRequestHandler):
    def do_GET(self): device_agent.handle_http(self, self.server.store, self.server.notify)
    def do_POST(self): device_agent.handle_http(self, self.server.store, self.server.notify)
    def log_message(self, *a): pass

srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
srv.store, srv.notify = store, notify
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % PORT
CHAT = 12345

fails = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

# --- path safety unit tests ---
sys.path.insert(0, '.')
import kash_helper as kh
root = tempfile.mkdtemp()
kh.DEFAULT_ROOT = root
kh.CONFIG_DIR = os.path.join(root, '.cfg')
kh.CONFIG_PATH = os.path.join(kh.CONFIG_DIR, 'config.json')
try:
    kh.safe_path(root, "../etc/passwd"); check("escape .. blocked", False)
except ValueError: check("escape .. blocked", True)
try:
    kh.safe_path(root, "/etc/passwd"); check("absolute blocked", False)
except ValueError: check("absolute blocked", True)
try:
    kh.safe_path(root, "C:\\Windows"); check("drive blocked", False)
except ValueError: check("drive blocked", True)
os.makedirs(os.path.join(root, "real"))
os.symlink(os.path.join(root, "real"), os.path.join(root, "link"))
try:
    kh.safe_path(root, "link/../../etc/x"); check("symlink escape blocked", False)
except ValueError: check("symlink escape blocked", True)
check("normal path ok", kh.safe_path(root, "docs/a.txt").startswith(os.path.realpath(root)))

# --- pairing over HTTP ---
ans = device_agent._pair_start(store, CHAT)
code = re.search(r'(\d{6})', ans).group(1)
proc = subprocess.Popen([sys.executable, "-u", "kash_helper.py", "--server", BASE],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
proc.stdin.write(code + "\n"); proc.stdin.flush()
time.sleep(4)
d = device_agent.devices(store)
check("device paired via helper", len(d) == 1)
dev = list(d.values())[0] if d else {}
check("device name/os/root recorded", bool(dev.get('name')) and bool(dev.get('os')) and bool(dev.get('root')))
root = dev.get('root', root)
check("pair notification sent", any('paired' in m for _, m in NOTES))
# bad code rejected
req = urllib.request.Request(BASE + "/agent/pair", data=json.dumps({"code": "000000"}).encode(),
                             headers={"Content-Type": "application/json"}, method="POST")
try:
    urllib.request.urlopen(req, timeout=10); check("bad code rejected", False)
except urllib.error.HTTPError as e: check("bad code rejected", e.code == 403)

# --- auto job: write ---
class FakeBot:  # stands in for bot module (gemini classify)
    def gemini(self, system, contents, json_out=False, **kw):
        msg = contents[0]['parts'][0]['text']
        if 'create' in msg and 'notes.txt' in msg:
            return json.dumps({"action": "write", "path": "notes.txt", "content": "hello kash", "device": ""})
        return json.dumps({"action": "none"})
fakeb = FakeBot()
reply = device_agent.route(store, "create notes.txt containing hello kash on my laptop", CHAT, fakeb)
print("WRITE REPLY:", repr(reply))
check("write auto executed", reply is not None and '✅' in reply)
check("file actually written", open(os.path.join(root, "notes.txt")).read() == "hello kash")

# --- auto job: read (direct enqueue) ---
job = device_agent.enqueue(store, CHAT, dev['id'], 'read', {'path': 'notes.txt'}, True)
done = device_agent.wait_result(store, job['id'], timeout=10)
check("read result returned", done and done['status'] == 'done' and 'hello kash' in done.get('output', ''))

# --- ask-first job: delete, approval flow ---
reply = device_agent._dispatch(store, CHAT, fakeb, "delete notes.txt from my laptop",
                               {"action": "delete", "path": "notes.txt", "device": ""})
check("delete asks first", 'Reply "yes"' in (reply or '') and not os.path.exists(os.path.join(root, 'notes.txt')) == False)
check("file not deleted before yes", os.path.exists(os.path.join(root, "notes.txt")))
out = device_agent.approve(store, "yes", CHAT)
check("approve runs delete", out is not None and ('✅' in out or 'Allowed' in out))
time.sleep(3)
check("file deleted after yes", not os.path.exists(os.path.join(root, "notes.txt")))

# --- approval required for run; wrong-chat approve does nothing ---
reply = device_agent._dispatch(store, CHAT, fakeb, "run echo hi on my laptop", {"action": "run", "command": "echo hi", "device": ""})
check("run asks first", 'Reply "yes"' in (reply or ''))
job_id = json.loads(store.kv_get('device_review_' + str(CHAT)))['job_id']
out = device_agent.approve(store, "yes", 99999)
check("other chat cannot approve", out is None)
out = device_agent.approve(store, "no", CHAT)
check("no cancels", out is not None and 'Cancelled' in out)
j2 = device_agent._jobs(store)[job_id]
check("job cancelled", j2['status'] == 'cancelled')

# --- run auto-approved? no: run is ASK action ---
check("run is ask-first", 'run' in device_agent.ASK_ACTIONS and 'run' not in device_agent.AUTO_ACTIONS)

# --- expired review ---
device_agent._dispatch(store, CHAT, fakeb, "delete x.txt from my laptop", {"action": "delete", "path": "x.txt", "device": ""})
key = 'device_review_' + str(CHAT)
import json as _j
rev = _j.loads(store.kv_get(key)); rev['until'] = time.time() - 5; store.kv_set(key, _j.dumps(rev))
out = device_agent.approve(store, "yes", CHAT)
check("expired review refused", out is not None and 'expired' in out)

# --- device list + capability ---
txt = device_agent.device_list_text(store)
check("device list shows online", 'online now' in txt)
cap = device_agent.capability(store)
check("capability text", 'AUTO actions' in cap and dev['name'] in cap)

# --- unauth poll rejected ---
try:
    urllib.request.urlopen(BASE + "/agent/poll?device_id=" + dev['id'] + "&wait=1", timeout=10)
    check("unauth poll rejected", False)
except urllib.error.HTTPError as e:
    check("unauth poll rejected", e.code == 401)

proc.terminate()
try:
    tail = proc.communicate(timeout=5)[0]
except Exception:
    proc.kill(); tail = ""
print("--- helper output tail ---")
print("\n".join(tail.strip().splitlines()[-6:]))
check("helper saw jobs", 'job:' in tail)
print()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
