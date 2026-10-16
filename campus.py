"""KASH v8 - DigiCampus assignment watch inside KASH: twice a day, silent when nothing new.

Runs the existing read-only DigiCampus browser check through Sunny's paired laptop
helper (kash_browser.py on his machine), once in the morning and once in the
evening IST. Each result is compared with the last snapshot and he hears about it
ONLY when something changed: a new assignment, a moved due date, or a deadline
now within 48 hours. No change -> no message, as he asked. Missed checks (laptop
or helper off) stay quiet too, with one heads-up after several misses in a row.
Anything due within 48 hours also gets a normal KASH reminder, created once.
"""
import json, time, uuid
from datetime import datetime, timedelta, timezone

import device_agent

IST = timezone(timedelta(hours=5, minutes=30))
SITE = "msu.digiicampus.com"
SLOTS = ((8, 5), (20, 5))     # IST - morning and evening
SLOT_WINDOW = 90 * 60         # a slot may fire up to 90 min late (host sleep, restarts)
MISS_NOTIFY_AFTER = 4         # consecutive missed/failed checks before one heads-up
DUE_SOON_H = 48


def _load(c, k):
    return json.loads(c.kv_get(k) or '{}')


def _save(c, k, v):
    c.kv_set(k, json.dumps(v))


def _due_ts(iso):
    try:
        return datetime.fromisoformat(str(iso)[:10] + 'T23:59:59+05:30').timestamp()
    except Exception:
        return None


def _slot_due(c, now):
    t = datetime.fromtimestamp(now, IST)
    day = t.strftime('%Y-%m-%d')
    state = _load(c, 'campus_slots')
    done = state.get(day, [])
    for i, (h, m) in enumerate(SLOTS):
        key = str(i)
        if key in done:
            continue
        slot_ts = t.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
        if slot_ts <= now <= slot_ts + SLOT_WINDOW:
            done.append(key)
            state[day] = done
            for d in list(state):
                if d < day:
                    state.pop(d, None)
            _save(c, 'campus_slots', state)
            return True
    return False


def _extract(g, page_text):
    """Raw portal page text -> structured assignment rows via Gemini. None on failure."""
    prompt = ("This is raw text from a student portal's assignment pages (MSU DigiCampus). "
              "Extract every assignment item as a JSON list: "
              '[{"course":"course name or code","title":"assignment title","due":"YYYY-MM-DD or empty string","status":"pending|submitted|graded|unknown"}]. '
              "Include pending and already-submitted items. Guess nothing: if a field is not visible use an empty string or 'unknown'. "
              "If this does not look like an assignments page (login page, captcha, error, empty), return [].\n"
              "PAGE TEXT:\n" + page_text[:6000])
    try:
        out = json.loads(g('You extract assignment data. Output JSON only.',
                           [{'role': 'user', 'parts': [{'text': prompt}]}], True))
    except Exception as e:
        print('campus extract failed:', repr(e)[:200])
        return None
    rows = []
    for it in out if isinstance(out, list) else []:
        if not isinstance(it, dict) or not str(it.get('title') or '').strip():
            continue
        rows.append({'course': str(it.get('course', ''))[:120].strip(),
                     'title': str(it.get('title', ''))[:200].strip(),
                     'due': str(it.get('due', ''))[:10].strip(),
                     'status': str(it.get('status', 'unknown'))[:20].strip().lower()})
    return rows


def _news(c, items, now):
    old = {(i['course'], i['title']): i for i in _load(c, 'campus_snapshot').get('items', [])}
    flagged = _load(c, 'campus_flagged')
    news = []
    for it in items:
        if it['status'] in ('submitted', 'graded'):
            continue
        key = '|'.join((it['course'], it['title']))
        prev = old.get((it['course'], it['title']))
        if prev is None:
            news.append(('new', it))
        elif it.get('due') and prev.get('due') != it.get('due'):
            news.append(('moved', it))
        else:
            ts = _due_ts(it.get('due', ''))
            if ts and now < ts <= now + DUE_SOON_H * 3600 and not flagged.get(key):
                news.append(('due_soon', it))
    return news


def _fmt_items(items):
    pend = [i for i in items if i['status'] not in ('submitted', 'graded')]
    if not pend:
        return 'Abhi kuch pending nahi dikh raha - sab submitted! 🎉'
    lines = []
    for i in pend[:12]:
        ts = _due_ts(i.get('due', ''))
        due = (' - due ' + datetime.fromtimestamp(ts, IST).strftime('%a %d %b')) if ts else ''
        lines.append('- %s (%s)%s' % (i['title'], i['course'] or 'course', due))
    return '\n'.join(lines)


def _flag_reminders(c, items, now):
    flagged = _load(c, 'campus_flagged')
    open_text = ' '.join(d['text'].lower() for d in c.rem_open())
    changed = False
    for it in items:
        if it['status'] in ('submitted', 'graded'):
            continue
        ts = _due_ts(it.get('due', ''))
        key = '|'.join((it['course'], it['title']))
        if not ts or not (now < ts <= now + DUE_SOON_H * 3600) or flagged.get(key):
            continue
        flagged[key] = True
        changed = True
        if it['title'].lower()[:30] in open_text:
            continue  # he already has a matching reminder
        c.rem_put({'id': uuid.uuid4().hex[:8], 'chat': int(c.kv_get('owner_chat') or 0),
                   'text': 'DigiCampus: %s (%s) submit karna hai' % (it['title'], it['course'] or 'course'),
                   'due': ts - 6 * 3600, 'repeat': 'none', 'status': 'pending', 'nudged': False, 'fired_ts': None})
    if changed:
        _save(c, 'campus_flagged', flagged)


def _missed(c, notify, why):
    n = _load(c, 'campus_misses').get('n', 0) + 1
    _save(c, 'campus_misses', {'n': n})
    chat = int(c.kv_get('owner_chat') or 0)
    if n == MISS_NOTIFY_AFTER and chat:
        notify(chat, ('DigiCampus check pichle %d baar se miss ho raha hai (%s). '
                      'Laptop pe helper chal raha hai na? Main har subah-shaam try karti rahungi 🙂') % (n, why))


def result(c, job, g, notify):
    chat = job.get('chat') or int(c.kv_get('owner_chat') or 0)
    if job['status'] != 'done':
        _missed(c, notify, 'check fail ho gaya')
        return
    items = _extract(g, str(job.get('output', '')))
    if items is None:
        _missed(c, notify, 'result process nahi hua')
        return
    if not items:
        _missed(c, notify, 'login ya captcha atka hua lagta hai')
        return
    _save(c, 'campus_misses', {'n': 0})
    now = time.time()
    news = _news(c, items, now)
    _save(c, 'campus_snapshot', {'ts': now, 'items': items})
    _flag_reminders(c, items, now)
    if news and chat:
        reasons = {'new': 'naya assignment', 'moved': 'due date badli', 'due_soon': 'deadline paas hai'}
        why = ', '.join(sorted({reasons[k] for k, _ in news}))
        notify(chat, '📚 DigiCampus update (%s):\n\n%s' % (why, _fmt_items(items)))
    # nothing new -> stay silent, exactly as he asked


def tick(c, g, notify):
    """Called from the scheduler loop: deliver finished campus checks, then fire due slots."""
    now = time.time()
    with device_agent._LOCK:
        jobs = device_agent._jobs(c)
        mine = [j for j in jobs.values()
                if j.get('action') == 'browse' and j.get('args', {}).get('origin') == 'campus'
                and j['status'] in ('done', 'failed') and not j.get('delivered')]
        for j in mine:
            j['delivered'] = True
        if mine:
            device_agent.save(c, 'device_jobs', jobs)
    for j in mine:
        try:
            result(c, j, g, notify)
        except Exception as e:
            print('campus result failed:', repr(e)[:200])
    if not _slot_due(c, now):
        return
    chat = int(c.kv_get('owner_chat') or 0)
    if not chat:
        return
    online = [x for x in device_agent.devices(c).values()
              if now - x.get('last_seen', 0) < device_agent.ONLINE_SECONDS]
    if not online:
        _missed(c, notify, 'laptop offline')
        return
    dev = sorted(online, key=lambda x: -x.get('last_seen', 0))[0]
    device_agent.enqueue(c, chat, dev['id'], 'browse',
                         {'site': SITE, 'task': 'assignments', 'origin': 'campus'}, True)
    print('campus check queued on', dev['name'])
