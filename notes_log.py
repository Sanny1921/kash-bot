"""Private literal quick capture. Stored outside conversational memory and never sent to a model."""
import hashlib,json,time,unicodedata,secrets,re
import gmail_link as locking

def index(c):return json.loads(c.kv_get('notes_index') or '[]')
@locking.synchronized
def save(c,chat,text,source=None):
    text=text.strip()
    if not text or len(text)>12000:raise ValueError('note must be 1-12000 characters')
    if re.search(r'password|passcode|\botp\b|api[ _-]?key|client[ _-]?secret|private[ _-]?key|recovery codes|\bcvv\b|\bcvc\b|card number|\b(?:\d[ -]?){13,19}\b',text,re.I):raise ValueError('secret-looking text is not stored')
    key='note_source_'+hashlib.sha256((str(chat)+'|'+str(source)).encode()).hexdigest() if source is not None else None
    if key and c.kv_get(key):
        prior=c.kv_get('note_'+c.kv_get(key))
        if prior:return json.loads(prior),True
    ids=index(c)
    if len(ids)>=5000:raise ValueError('note capacity reached')
    rid=hashlib.sha256((str(chat)+'|'+str(source)).encode()).hexdigest()[:10] if source is not None else secrets.token_hex(5)
    prior=c.kv_get('note_'+rid)
    if prior:
        if rid not in ids:c.kv_set('notes_index',json.dumps(ids+[rid]))
        return json.loads(prior),True
    record={'id':rid,'chat':str(chat),'text':text,'created':time.time()}
    c.kv_set('note_'+rid,json.dumps(record,ensure_ascii=False));c.kv_set('notes_index',json.dumps(ids+[rid]))
    if key:c.kv_set(key,rid)
    return record,False

def search(c,chat,query='',limit=15):
    words=unicodedata.normalize('NFKC',query).casefold().split();rows=[]
    for rid in reversed(index(c)):
        raw=c.kv_get('note_'+rid)
        if not raw:continue
        note=json.loads(raw)
        if note['chat']!=str(chat):continue
        hay=unicodedata.normalize('NFKC',note['text']).casefold()
        if all(w in hay for w in words):rows.append(note)
    return rows[:limit],len(rows)

def get(c,chat,rid):
    value=c.kv_get('note_'+rid)
    if not value:return None
    record=json.loads(value)
    return record if record['chat']==str(chat) else None
