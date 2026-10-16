"""KASH v6 - local device access (helper pairing, job queue, owner approvals).

Helpers on Sunny's own devices poll this service OUTBOUND over HTTPS, so no
port on his machines is exposed to the internet. Scope approved by Sunny
(WhatsApp 2026-10-16): one folder per device (default ~/KASH); AUTO inside
that folder: list, read, create/write, append. ASK FIRST on Telegram:
delete, run command, open. Pairing uses a one-time 6-digit code from the
private chat; the helper exchanges it for a device token that never appears
in chat. State lives in the kv store, so MongoDB and sqlite both work.
"""
import hashlib, hmac, json, re, secrets, threading, time, urllib.parse

AUTO_ACTIONS = {"list", "read", "write", "append"}
ASK_ACTIONS = {"delete", "run", "open"}
ONLINE_SECONDS = 120
JOB_KEEP = 86400
PAIR_CODE_TTL = 600
REVIEW_TTL = 900
POLL_MAX_WAIT = 25
MAX_BODY = 1000000
_LOCK = threading.Lock()

TOPIC = re.compile(r'\b(?:laptops?|computers?|pc|desktops?|devices?|phones?|mobiles?)\b', re.I)
PAIR_RE = re.compile(r'\bpair\b|\bconnect\b|\bjod(?:o|na)?\b', re.I)
LISTQ_RE = re.compile(r'which (?:device|machine)|what machine|devices? (?:are )?(?:connected|paired|online)|(?:laptop|computer|phone) (?:online|connected|paired)|kahan chal rahi|kis machine|kaunse device', re.I)
ACT_RE = re.compile(r'\b(?:list|show|read|open|create|write|make|delete|remove|append|add|run|execute|install|files?|folder|directory|command|dikhao|banao|padho|hatao|chalao|kholo|likho)\b', re.I)
YES_RE = re.compile(r'(?:yes|yes please|do it|confirm|go ahead|ok|okay|haan|हाँ|हां)[.!]?', re.I)
NO_RE = re.compile(r'(?:no|no thanks|cancel|cancel it|nahi|नहीं)[.!]?', re.I)


def load(c, k):
    return json.loads(c.kv_get(k) or '{}')


def save(c, k, v):
    c.kv_set(k, json.dumps(v))


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _ago(seconds):
    m = int(seconds // 60)
    if m < 1:
        return 'just now'
    if m < 60:
        return str(m) + ' min ago'
    h = m // 60
    if h < 48:
        return str(h) + ' hr ago'
    return str(h // 24) + ' days ago'


def _clean(text, n):
    return re.sub(r'\s+', ' ', str(text or '')).strip()[:n]


# ---------- device registry ----------

def devices(c):
    return load(c, 'device_registry')


def device_list_text(c):
    d = devices(c)
    if not d:
        return ('Koi device paired nahi hai abhi. Bolo "pair my laptop" - main ek one-time code dungi, '
                'jo tum helper mein daaloge. Uske baad main us machine pe kaam kar paungi.')
    now = time.time()
    lines = []
    for i, dev in enumerate(sorted(d.values(), key=lambda x: x.get('paired_ts', 0)), 1):
        online = now - dev.get('last_seen', 0) < ONLINE_SECONDS
        state = 'online now' if online else 'last seen ' + _ago(now - dev.get('last_seen', 0))
        lines.append('%d. %s (%s) - %s\n   folder: %s' % (i, dev['name'], dev.get('os', '?'), state, dev.get('root', '?')))
    return 'Your devices:\n' + '\n'.join(lines)


def capability(c):
    d = devices(c)
    if not d:
        return ('No paired local devices yet. If Sunny asks you to work on his laptop/phone, offer pairing: '
                'he says "pair my laptop", you give a one-time 6-digit code, he enters it in the helper on that device. '
                'Never claim you can see his machine before a device shows as paired here.')
    now = time.time()
    parts = []
    for dev in sorted(d.values(), key=lambda x: x.get('paired_ts', 0)):
        state = 'online' if now - dev.get('last_seen', 0) < ONLINE_SECONDS else 'offline'
        parts.append('%s (%s, %s, folder %s)' % (dev['name'], dev.get('os', '?'), state, dev.get('root', '~/KASH')))
    return ('Local devices: ' + '; '.join(parts) + '. AUTO actions inside each device folder: list, read, create, append. '
            'Delete, run command and open need his Telegram confirmation first. '
            'Never claim a device action happened unless a system note says it completed.')


# ---------- pairing ----------

def _pair_start(c, chat):
    code = '%06d' % secrets.randbelow(1000000)
    save(c, 'device_pair_' + code, {'until': time.time() + PAIR_CODE_TTL, 'chat': chat})
    return ('Pairing code: %s\nValid 10 minutes, one use only.\n\n'
            'On your laptop: run kash_helper.py and paste this code when it asks. '
            'The helper connects OUTWARD to me over HTTPS - no open ports on your machine. '
            'Windows and Linux both work. Code expires agar use nahi hua to naya bana dungi.' % code)


def _pair_throttled(c):
    p = load(c, 'device_pair_fail')
    return p.get('until', 0) > time.time() and p.get('n', 0) >= 10


def _pair_failed(c):
    p = load(c, 'device_pair_fail')
    now = time.time()
    if p.get('until', 0) <= now:
        p = {'n': 0, 'until': now + 600}
    p['n'] = p.get('n', 0) + 1
    save(c, 'device_pair_fail', p)


# ---------- jobs ----------

def _jobs(c):
    return {k: v for k, v in load(c, 'device_jobs').items() if time.time() - v.get('created', time.time()) < JOB_KEEP}


def enqueue(c, chat, device_id, action, args, auto):
    with _LOCK:
        j = _jobs(c)
        job = {'id': secrets.token_hex(4), 'device': device_id, 'action': action, 'args': args,
               'status': 'approved' if auto else 'awaiting_approval', 'code': secrets.token_hex(4),
               'chat': chat, 'created': time.time(), 'updated': time.time()}
        j[job['id']] = job
        save(c, 'device_jobs', j)
        return job


def _update_job(c, job):
    with _LOCK:
        j = _jobs(c)
        job['updated'] = time.time()
        j[job['id']] = job
        save(c, 'device_jobs', j)


def take_next(c, device_id):
    with _LOCK:
        j = _jobs(c)
        for job in sorted(j.values(), key=lambda x: x['created']):
            if job['device'] == device_id and job['status'] == 'approved':
                job['status'] = 'running'
                job['updated'] = time.time()
                j[job['id']] = job
                save(c, 'device_jobs', j)
                return job
    return None


def wait_result(c, job_id, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = _jobs(c).get(job_id)
        if not job or job['status'] in ('done', 'failed'):
            return job
        time.sleep(1)
    return None


def _result_text(dev, job):
    label = '%s (%s)' % (dev['name'], dev.get('os', '?')) if dev else 'device'
    action, args = job['action'], job.get('args', {})
    target = args.get('path') or args.get('command') or ''
    if job['status'] == 'failed':
        return '❌ %s: %s %s failed - %s' % (label, action, target, _clean(job.get('error'), 400))
    out = _clean(job.get('output'), 0) and str(job.get('output', ''))
    if action in ('read', 'list'):
        out = str(job.get('output', ''))[:3400]
        return '📂 %s - %s %s:\n%s' % (label, action, target, out or '(empty)')
    tail = ('\n' + str(job.get('output', ''))[:1200]) if job.get('output') else ''
    return '✅ %s: %s %s ho gaya.%s' % (label, action, target, tail)


# ---------- owner approvals (plain words, mirrors reviews.py) ----------

def approve(c, text, chat):
    if not text or not (YES_RE.fullmatch(text.strip()) or NO_RE.fullmatch(text.strip())):
        return None
    key = 'device_review_' + str(chat)
    r = load(c, key)
    if not r:
        return None
    job = _jobs(c).get(r.get('job_id', ''))
    if r.get('until', 0) < time.time() or not job:
        return 'That device review expired. Ask for a fresh one; nothing ran.'
    if r.get('status') != 'waiting':
        return 'That device review was already handled. No repeat action.'
    if NO_RE.fullmatch(text.strip()):
        job['status'] = 'cancelled'
        _update_job(c, job)
        r['status'] = 'cancelled'
        save(c, key, r)
        return 'Cancelled. Kuch nahi hua on %s.' % (devices(c).get(job['device'], {}).get('name', 'device'))
    r['status'] = 'handling'
    save(c, key, r)
    job['status'] = 'approved'
    _update_job(c, job)
    r['status'] = 'handled'
    save(c, key, r)
    dev = devices(c).get(job['device'])
    done = wait_result(c, job['id'], timeout=10)
    if done and done['status'] in ('done', 'failed'):
        return _result_text(dev, done)
    return 'Allowed. %s pe %s %s kar rahi hoon - result bhejungi jab complete ho.' % (
        (dev or {}).get('name', 'device'), job['action'], job.get('args', {}).get('path') or job.get('args', {}).get('command') or '')


# ---------- intent extraction ----------

def _classify(b, text):
    prompt = ('Extract a LOCAL-DEVICE command from the owner message. JSON only: '
              '{"action":"list|read|write|append|delete|run|open|pair|devices|none",'
              '"path":"relative path inside the KASH folder, like notes.txt or docs/todo.txt, or empty",'
              '"content":"exact text to write or append, or empty",'
              '"command":"exact shell command, or empty",'
              '"device":"device words he used, like laptop, windows laptop, phone, or empty"}. '
              'Rules: never invent paths or commands. "write" creates a NEW file; "append" adds to one. '
              'Requests to change or overwrite existing file content -> none. Absolute paths (C:\\, /home, /Users) -> none. '
              'Casual talk about devices (broken, buying, prices), reminders, Google Drive/Docs/Gmail requests -> none. '
              'Message: ' + text)
    try:
        out = json.loads(b.gemini('You classify device commands. Output JSON only.', [{'role': 'user', 'parts': [{'text': prompt}]}], True))
        return out if isinstance(out, dict) else None
    except Exception as e:
        print('device classify failed:', repr(e)[:200])
        return None


def _pick_device(c, chat, request, data):
    d = devices(c)
    if not d:
        return None, ('Koi device paired nahi hai abhi. Pehle bolo "pair my laptop" - main code dungi, '
                      'helper mein daalna, phir main machine pe kaam kar paungi.')
    named = ((data.get('device') or '') + ' ' + request).lower()
    hits = []
    for dev in d.values():
        osname = str(dev.get('os', '')).lower()
        tokens = [t for t in re.split(r'\W+', dev['name'].lower()) if len(t) > 2]
        if any(t in named for t in tokens) or ('windows' in named and 'windows' in osname) or \
           ('linux' in named and 'linux' in osname) or (re.search(r'\b(?:phone|mobile)\b', named) and any(k in osname for k in ('android', 'termux'))):
            hits.append(dev)
    if len(d) == 1:
        return next(iter(d.values())), None
    if len(hits) == 1:
        return hits[0], None
    rows = sorted(d.values(), key=lambda x: x.get('paired_ts', 0))
    save(c, 'device_choice_' + str(chat), {'until': time.time() + 600, 'request': request,
                                           'devices': [dev['id'] for dev in rows]})
    listing = '\n'.join('%d. %s (%s)' % (i, dev['name'], dev.get('os', '?')) for i, dev in enumerate(rows, 1))
    return None, 'Which device?\n' + listing + '\nReply "1" or "2". I will continue the same request, nothing ran yet.'


def _valid_rel(path):
    if not path or len(path) > 300:
        return False
    if path.startswith(('/', '\\')) or re.match(r'^[a-zA-Z]:', path):
        return False
    parts = re.split(r'[/\\]+', path)
    return all(p not in ('', '.', '..') for p in parts)


def _dispatch(c, chat, b, request, data=None):
    data = data or _classify(b, request)
    if not data:
        return 'Samjhi nahi exactly kya karna hai device pe. Ek line mein batao, jaise "list files on my laptop".'
    action = data.get('action')
    if action in (None, 'none'):
        return None
    if action == 'pair':
        return _pair_start(c, chat)
    if action == 'devices':
        return device_list_text(c)
    if action not in AUTO_ACTIONS | ASK_ACTIONS:
        return None
    dev, err = _pick_device(c, chat, request, data)
    if not dev:
        return err
    args = {}
    if action == 'run':
        cmd = _clean(data.get('command'), 500)
        if not cmd:
            return 'Exact command batao jo run karna hai, jaise "run python --version on my laptop".'
        args['command'] = cmd
    elif action in ('list',):
        path = _clean(data.get('path'), 300) or '.'
        if path != '.' and not _valid_rel(path):
            return 'Sirf us machine ke KASH folder ke andar dekh sakti hoon. Relative path do, jaise docs/notes.txt.'
        args['path'] = path
    else:
        path = _clean(data.get('path'), 300)
        if not _valid_rel(path):
            return 'Path samajh nahi aaya. Sirf KASH folder ke andar ka relative path do, jaise notes.txt ya docs/todo.txt.'
        args['path'] = path
    if action in ('write', 'append'):
        content = str(data.get('content') or '')
        if not content:
            return 'File mein likhna kya hai? Exact text batao, jaise "create shopping.txt containing milk, eggs".'
        if len(content) > 20000:
            return 'Itna lamba content ek message mein nahi - 20,000 characters tak hi likh paungi.'
        args['content'] = content
    auto = action in AUTO_ACTIONS
    job = enqueue(c, chat, dev['id'], action, args, auto)
    if auto:
        done = wait_result(c, job['id'], timeout=10)
        if done and done['status'] in ('done', 'failed'):
            return _result_text(dev, done)
        return ('%s abhi offline lag raha hai. Job ready hai - jaise hi machine online aayegi, '
                '%s %s karke bataungi.' % (dev['name'], action, args.get('path') or args.get('command') or ''))
    save(c, 'device_review_' + str(chat), {'job_id': job['id'], 'code': job['code'], 'until': time.time() + REVIEW_TTL, 'status': 'waiting'})
    desc = ('run command: ' + args['command']) if action == 'run' else (action + ' ' + args['path'])
    return ('Review device action:\nDevice: %s (%s)\nAction: %s\nFolder: %s\n'
            'Reply "yes" to allow exactly this, or "no" to cancel. This review expires in 15 minutes.'
            % (dev['name'], dev.get('os', '?'), desc, dev.get('root', '~/KASH')))


def route(c, text, chat, b):
    """Plain-language device requests. Returns an answer string, or None to fall through."""
    if not text or text.startswith('/'):
        return None
    stripped = text.strip()
    if LISTQ_RE.search(stripped):
        return device_list_text(c)
    if PAIR_RE.search(stripped) and TOPIC.search(stripped):
        return _pair_start(c, chat)
    sel = re.fullmatch(r'(?:use |device |number )?(\d+)[.!]?', stripped, re.I)
    if sel:
        p = load(c, 'device_choice_' + str(chat))
        if p.get('until', 0) > time.time():
            idx = int(sel.group(1)) - 1
            ids = p.get('devices', [])
            d = devices(c)
            if 0 <= idx < len(ids) and ids[idx] in d:
                save(c, 'device_choice_' + str(chat), {})
                data = _classify(b, p['request'])
                if not data or data.get('action') in (None, 'none', 'pair', 'devices'):
                    return 'Woh request phir se bataoge? Exact kaam ek line mein.'
                dev = d[ids[idx]]
                return _dispatch_for(c, chat, dev, p['request'], data)
        return None
    if not TOPIC.search(stripped) or not ACT_RE.search(stripped):
        return None
    return _dispatch(c, chat, b, stripped)


def _dispatch_for(c, chat, dev, request, data):
    action = data.get('action')
    if action not in AUTO_ACTIONS | ASK_ACTIONS:
        return 'Woh action main device pe nahi kar sakti. list/read/create/append/delete/run/open try karo.'
    args = {}
    if action == 'run':
        cmd = _clean(data.get('command'), 500)
        if not cmd:
            return 'Exact command batao.'
        args['command'] = cmd
    elif action == 'list':
        args['path'] = _clean(data.get('path'), 300) or '.'
    else:
        path = _clean(data.get('path'), 300)
        if not _valid_rel(path):
            return 'Sirf KASH folder ke andar ka relative path do.'
        args['path'] = path
    if action in ('write', 'append'):
        content = str(data.get('content') or '')
        if not content:
            return 'File mein likhna kya hai? Exact text batao.'
        args['content'] = content[:20000]
    auto = action in AUTO_ACTIONS
    job = enqueue(c, chat, dev['id'], action, args, auto)
    if auto:
        done = wait_result(c, job['id'], timeout=10)
        if done and done['status'] in ('done', 'failed'):
            return _result_text(dev, done)
        return '%s abhi offline lag raha hai. Online aate hi %s karke bataungi.' % (dev['name'], action)
    save(c, 'device_review_' + str(chat), {'job_id': job['id'], 'code': job['code'], 'until': time.time() + REVIEW_TTL, 'status': 'waiting'})
    desc = ('run command: ' + args['command']) if action == 'run' else (action + ' ' + args.get('path', ''))
    return ('Review device action:\nDevice: %s (%s)\nAction: %s\nFolder: %s\n'
            'Reply "yes" to allow exactly this, or "no" to cancel. This review expires in 15 minutes.'
            % (dev['name'], dev.get('os', '?'), desc, dev.get('root', '~/KASH')))


# ---------- slash fallback ----------

def command(c, text, chat):
    parts = text.split(maxsplit=1)
    cmd = parts[0].split('@')[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ''
    if cmd == '/pair':
        return _pair_start(c, chat)
    if cmd == '/devices':
        return device_list_text(c)
    if cmd == '/unpair':
        d = devices(c)
        rows = sorted(d.values(), key=lambda x: x.get('paired_ts', 0))
        target = None
        if arg.isdigit() and 1 <= int(arg) <= len(rows):
            target = rows[int(arg) - 1]
        elif arg:
            matches = [dev for dev in rows if arg.lower() in dev['name'].lower()]
            if len(matches) == 1:
                target = matches[0]
        if not target:
            return 'Which device? ' + '; '.join('%d. %s' % (i, dev['name']) for i, dev in enumerate(rows, 1)) + '. Use /unpair NUMBER.'
        with _LOCK:
            d = devices(c)
            d.pop(target['id'], None)
            save(c, 'device_registry', d)
        return '%s unpaired. Its token is dead now - helper on that machine will stop working.' % target['name']
    return ('Device commands: "pair my laptop", "which devices are online", "list files on my laptop", '
            '"read notes.txt on my laptop", "create todo.txt containing ...", "delete old.txt from my laptop", '
            '"run python --version on my laptop". Delete/run/open ask you first. /devices, /pair, /unpair NUMBER.')


# ---------- scheduler tick ----------

def tick(c, notify):
    now = time.time()
    with _LOCK:
        j = _jobs(c)
        changed = False
        notes = []
        for job in j.values():
            age = now - job['created']
            if job['status'] == 'awaiting_approval' and now - job['updated'] > REVIEW_TTL:
                job['status'] = 'expired'
                job['updated'] = now
                changed = True
                notes.append((job['chat'], '⏳ Device review expired: %s %s. Dobara mangoge to fresh review bana dungi.' % (
                    job['action'], job.get('args', {}).get('path') or job.get('args', {}).get('command') or '')))
            elif job['status'] == 'approved' and now - job['updated'] > 150 and not job.get('offline_noted'):
                job['offline_noted'] = True
                changed = True
                notes.append((job['chat'], '📴 %s pe %s %s abhi pending hai - machine offline lag rahi hai. Online aate hi kar dungi.' % (
                    devices(c).get(job['device'], {}).get('name', 'device'), job['action'],
                    job.get('args', {}).get('path') or job.get('args', {}).get('command') or '')))
            elif job['status'] == 'running' and now - job['updated'] > 300:
                job['status'] = 'failed'
                job['error'] = 'helper did not report back in 5 minutes'
                job['updated'] = now
                changed = True
                notes.append((job['chat'], '❌ %s: %s timed out - helper ne 5 minute mein jawab nahi diya.' % (
                    devices(c).get(job['device'], {}).get('name', 'device'), job['action'])))
            elif age > JOB_KEEP:
                pass
        if changed:
            save(c, 'device_jobs', j)
    for target, message in notes:
        try:
            notify(target, message)
        except Exception as e:
            print('device tick notify failed:', repr(e)[:200])


# ---------- HTTP endpoints (helper side) ----------

def _send(h, status, obj):
    body = json.dumps(obj).encode() if obj is not None else b''
    h.send_response(status)
    h.send_header('Content-Type', 'application/json')
    h.send_header('Content-Length', str(len(body)))
    h.end_headers()
    if body:
        h.wfile.write(body)


def _read_json(h):
    try:
        length = int(h.headers.get('Content-Length') or 0)
    except ValueError:
        return None
    if length <= 0 or length > MAX_BODY:
        return None
    try:
        return json.loads(h.rfile.read(length))
    except Exception:
        return None


def _auth(c, h, device_id):
    auth = h.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None
    dev = devices(c).get(device_id)
    if not dev:
        return None
    if not hmac.compare_digest(_hash(auth[7:].strip()), dev['token_hash']):
        return None
    return dev


def handle_http(h, store, notify=None):
    """True when the request was an /agent/* endpoint (answered)."""
    path = urllib.parse.urlsplit(h.path).path
    if not path.startswith('/agent/'):
        return False
    notify = notify or (lambda target, message: print('notify', target, message[:80]))
    c = store
    if c is None:
        _send(h, 503, {'error': 'starting'})
        return True
    if path == '/agent/pair' and h.command == 'POST':
        body = _read_json(h) or {}
        code = str(body.get('code', '')).strip()
        if _pair_throttled(c):
            _send(h, 429, {'error': 'too many tries, wait 10 minutes'})
            return True
        p = load(c, 'device_pair_' + code)
        if not p or p.get('until', 0) < time.time():
            _pair_failed(c)
            _send(h, 403, {'error': 'invalid or expired code'})
            return True
        name = _clean(body.get('name'), 60) or 'device'
        osinfo = _clean(body.get('os'), 120) or '?'
        root = _clean(body.get('root'), 200) or '~/KASH'
        token = secrets.token_urlsafe(32)
        with _LOCK:
            d = devices(c)
            base = name
            n = 2
            while any(dev['name'] == name for dev in d.values()):
                name = '%s-%d' % (base, n)
                n += 1
            device_id = secrets.token_hex(6)
            d[device_id] = {'id': device_id, 'name': name, 'os': osinfo, 'root': root,
                            'token_hash': _hash(token), 'paired_ts': time.time(), 'last_seen': time.time(),
                            'chat': p['chat']}
            save(c, 'device_registry', d)
            save(c, 'device_pair_' + code, {})
        notify(p['chat'], '🔗 %s (%s) paired! Ab main us machine ke %s folder mein kaam kar sakti hoon.' % (name, osinfo, root))
        print('device paired:', name, osinfo)
        _send(h, 200, {'device_id': device_id, 'token': token, 'name': name})
        return True
    if path == '/agent/poll' and h.command == 'GET':
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(h.path).query)
        device_id = (q.get('device_id') or [''])[0]
        dev = _auth(c, h, device_id)
        if not dev:
            _send(h, 401, {'error': 'unauthorized'})
            return True
        try:
            wait = min(float((q.get('wait') or ['25'])[0]), POLL_MAX_WAIT)
        except ValueError:
            wait = 5
        with _LOCK:
            d = devices(c)
            d[device_id]['last_seen'] = time.time()
            save(c, 'device_registry', d)
        deadline = time.time() + wait
        while True:
            job = take_next(c, device_id)
            if job:
                _send(h, 200, {'job': {'id': job['id'], 'action': job['action'], 'args': job['args']}})
                return True
            if time.time() >= deadline:
                _send(h, 204, None)
                return True
            time.sleep(1.5)
    if path == '/agent/result' and h.command == 'POST':
        body = _read_json(h) or {}
        device_id = str(body.get('device_id', ''))
        dev = _auth(c, h, device_id)
        if not dev:
            _send(h, 401, {'error': 'unauthorized'})
            return True
        job_id = str(body.get('job_id', ''))
        with _LOCK:
            j = _jobs(c)
            job = j.get(job_id)
            if not job or job['device'] != device_id or job['status'] != 'running':
                _send(h, 409, {'error': 'unknown or stale job'})
                return True
            job['status'] = 'done' if body.get('ok') else 'failed'
            job['output'] = str(body.get('output', ''))[:20000]
            job['error'] = str(body.get('error', ''))[:1000]
            job['updated'] = time.time()
            j[job_id] = job
            save(c, 'device_jobs', j)
        try:
            notify(job['chat'], _result_text(dev, job))
        except Exception as e:
            print('device result notify failed:', repr(e)[:200])
        _send(h, 200, {'ok': True})
        return True
    _send(h, 404, {'error': 'not found'})
    return True
