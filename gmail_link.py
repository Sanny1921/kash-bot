"""Owner-only Gmail OAuth and a deterministic, metadata-only morning mail brief."""
import base64, hashlib, hmac, json, os, re, secrets, time
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
ACCOUNT_LOCK = RLock()
import urllib.parse, urllib.request, urllib.error
from email.utils import parseaddr
from cryptography.fernet import Fernet

SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
COMPOSE_SCOPE = 'https://www.googleapis.com/auth/gmail.compose'
BASE = os.environ.get('KASH_PUBLIC_URL', 'https://kash-bot.onrender.com').rstrip('/')
EMAIL = os.environ.get('GMAIL_ACCOUNT', 'sunnykrtiwari192114@gmail.com').lower()
SENSITIVE = re.compile(r'\bbank\b|banking|digilocker|\botp\b|one[ -]?time|password|passcode|verification|verify|security|authentication|authenticator|\bpin\b|credit.?card|debit.?card|account.?statement|\bupi\b|\bkyc\b|\baadhaar\b|\bpan\b|transaction|payment|wallet|\bpaytm\b|\bphonepe\b|\bgpay\b|\bpaypal\b|\bhdfc\b|\bicici\b|\bsbi\b|\bkotak\b|\baxis\b', re.I)
EXCLUSIONS = ['bank','banking','digilocker','OTP','"one time"','"one-time"','password','passcode','verification','verify','security','authentication','authenticator','PIN','"credit card"','"debit card"','"account statement"','UPI','KYC','Aadhaar','transaction','payment','wallet','Paytm','PhonePe','GPay','PayPal','HDFC','ICICI','SBI','Kotak','Axis']
QUERY = 'in:inbox newer_than:1d -category:promotions -category:social ' + ' '.join('-'+s for s in EXCLUSIONS)
IMPORTANT = re.compile(r'interview|offer|deadline|due|urgent|ticket|travel|flight|train|appointment|meeting|exam|assignment|action required', re.I)

def configured():
    return all(os.environ.get(k) for k in ('GMAIL_CLIENT_ID','GMAIL_CLIENT_SECRET','GMAIL_TOKEN_KEY'))

def cipher():
    return Fernet(os.environ['GMAIL_TOKEN_KEY'].encode())

def request_json(url, fields=None, token=None):
    headers = {'Accept':'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    data = urllib.parse.urlencode(fields).encode() if fields is not None else None
    if data: headers['Content-Type'] = 'application/x-www-form-urlencoded'
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=20) as r:
        return json.load(r)

def synchronized(fn):
    def wrapped(*args, **kwargs):
        with ACCOUNT_LOCK: return fn(*args, **kwargs)
    return wrapped

@synchronized
def accounts(c):
    saved = json.loads(c.kv_get('gmail_accounts') or '{}')
    # One-time migration preserves an already connected account and its ciphertext.
    legacy = c.kv_get('gmail_refresh')
    if legacy:
        email = (c.kv_get('gmail_connected') or EMAIL).lower()
        saved.setdefault(email, legacy)
        c.kv_set('gmail_accounts', json.dumps(saved))
        c.kv_set('gmail_refresh', ''); c.kv_set('gmail_connected', '')
    # Suite consent can serve Gmail without overwriting older encrypted Gmail tokens.
    sources=json.loads(c.kv_get('gmail_sources') or '{}')
    for email,entry in json.loads(c.kv_get('workspace_accounts') or '{}').items():
        if sources.get(email)=='disabled':saved.pop(email,None)
        elif sources.get(email)!='separate' and SCOPE in entry.get('scope','').split():saved[email]=entry['refresh']
    return saved

def valid_email(email):
    return bool(re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', email))

def connect_link(c, email=EMAIL, drafts=False):
    email = email.strip().lower()
    if not valid_email(email): raise ValueError('invalid email')
    if not configured(): return None
    key = secrets.token_urlsafe(32)
    digest = hashlib.sha256(key.encode()).hexdigest()
    c.kv_set('gmail_launch_' + digest, json.dumps({'email':email,'drafts':bool(drafts),'until':time.time()+1800}))
    return BASE + '/gmail/connect?' + urllib.parse.urlencode({'key':key})

def authorization(c, key):
    digest = hashlib.sha256(key.encode()).hexdigest()
    launch = json.loads(c.kv_get('gmail_launch_' + digest) or '{}')
    if launch.get('until',0) < time.time(): raise ValueError('expired link')
    c.kv_set('gmail_launch_' + digest, '{}')
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    statekey = 'gmail_state_' + hashlib.sha256(state.encode()).hexdigest()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    c.kv_set(statekey, json.dumps({'email':launch['email'],'drafts':launch.get('drafts',False),'verifier':verifier,'until':time.time()+900}))
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode({
        'client_id':os.environ['GMAIL_CLIENT_ID'],'redirect_uri':BASE+'/gmail/callback',
        'response_type':'code','scope':SCOPE + (' '+COMPOSE_SCOPE if launch.get('drafts') else ''),'access_type':'offline','prompt':'select_account consent',
        'login_hint':launch['email'],'state':state,'code_challenge':challenge,'code_challenge_method':'S256'})

def callback(c, args):
    supplied = args.get('state',[''])[0]
    statekey = 'gmail_state_' + hashlib.sha256(supplied.encode()).hexdigest()
    st = json.loads(c.kv_get(statekey) or '{}')
    if st.get('until',0)<time.time(): raise ValueError('invalid state')
    c.kv_set(statekey,'{}')
    if args.get('error'): raise ValueError('consent not granted')
    tok = request_json('https://oauth2.googleapis.com/token', {
        'client_id':os.environ['GMAIL_CLIENT_ID'],'client_secret':os.environ['GMAIL_CLIENT_SECRET'],
        'code':args.get('code',[''])[0],'code_verifier':st['verifier'],
        'grant_type':'authorization_code','redirect_uri':BASE+'/gmail/callback'})
    profile = request_json('https://gmail.googleapis.com/gmail/v1/users/me/profile',token=tok['access_token'])
    email = profile.get('emailAddress','').lower()
    if email != st['email']: raise ValueError('wrong Google account')
    if not tok.get('refresh_token'): raise ValueError('offline access not granted')
    if SCOPE not in tok.get('scope',SCOPE).split(): raise ValueError('read permission not granted')
    with ACCOUNT_LOCK:
        accounts(c)
        saved = json.loads(c.kv_get('gmail_accounts') or '{}')
        saved[email] = cipher().encrypt(tok['refresh_token'].encode()).decode()
        c.kv_set('gmail_accounts',json.dumps(saved))
        grants = json.loads(c.kv_get('gmail_scopes') or '{}')
        grants[email] = tok.get('scope', SCOPE)
        c.kv_set('gmail_scopes',json.dumps(grants))
        sources=json.loads(c.kv_get('gmail_sources') or '{}');sources[email]='separate';c.kv_set('gmail_sources',json.dumps(sources))
    return email

@synchronized
def disconnect(c, email=None):
    saved = accounts(c)
    if email:
        email = email.strip().lower()
        if email not in saved: return False
        raw=json.loads(c.kv_get('gmail_accounts') or '{}');raw.pop(email,None)
        sources=json.loads(c.kv_get('gmail_sources') or '{}');sources[email]='disabled';c.kv_set('gmail_sources',json.dumps(sources))
        grants=json.loads(c.kv_get('gmail_scopes') or '{}');grants.pop(email,None);c.kv_set('gmail_scopes',json.dumps(grants))
        c.kv_set('gmail_accounts',json.dumps(raw))
        return True
    c.kv_set('gmail_accounts','{}')
    c.kv_set('gmail_scopes','{}')
    sources=json.loads(c.kv_get('gmail_sources') or '{}')
    for email in json.loads(c.kv_get('workspace_accounts') or '{}'):sources[email]='disabled'
    c.kv_set('gmail_sources',json.dumps(sources))
    # Revoking one grant can invalidate other grants for the same project.
    # Revoke remotely only for an explicit disconnect-all command.
    for encrypted in saved.values():
        try:
            token = cipher().decrypt(encrypted.encode()).decode()
            req = urllib.request.Request('https://oauth2.googleapis.com/revoke', data=urllib.parse.urlencode({'token':token}).encode(), headers={'Content-Type':'application/x-www-form-urlencoded'})
            with urllib.request.urlopen(req,timeout=20): pass
        except Exception: pass
    return True

def safe_text(s, limit):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', s).strip()[:limit]

def account_mail(email, encrypted):
    try:
        refresh = cipher().decrypt(encrypted.encode()).decode()
        token = request_json('https://oauth2.googleapis.com/token',{
            'client_id':os.environ['GMAIL_CLIENT_ID'],'client_secret':os.environ['GMAIL_CLIENT_SECRET'],
            'refresh_token':refresh,'grant_type':'refresh_token'})['access_token']
        api = 'https://gmail.googleapis.com/gmail/v1/users/me/messages'
        listing = request_json(api+'?'+urllib.parse.urlencode({'q':QUERY,'maxResults':25}),token=token)
        items = []
        for item in listing.get('messages',[]):
            # Headers only: never download a message body, snippet or attachment.
            fields = [('format','metadata'),('metadataHeaders','From'),('metadataHeaders','Subject'),('fields','payload/headers,labelIds,internalDate')]
            m = request_json(api+'/'+item['id']+'?'+urllib.parse.urlencode(fields),token=token)
            headers = {h['name'].lower():h['value'] for h in m.get('payload',{}).get('headers',[])}
            subject, sender = headers.get('subject','(no subject)'),headers.get('from','')
            if SENSITIVE.search(subject+' '+sender): continue
            name, addr = parseaddr(sender)
            priority = 'IMPORTANT' in m.get('labelIds',[]) or bool(IMPORTANT.search(subject))
            items.append((priority,int(m.get('internalDate',0)),safe_text(name or addr,65),safe_text(subject,180)))
        return items, bool(listing.get('nextPageToken')), None
    except urllib.error.HTTPError as e:
        print('Gmail brief unavailable: HTTP',e.code)
        return [], False, 'Could not check ' + email + '. Reconnect with /gmail ' + email + '.'
    except Exception:
        print('Gmail brief unavailable')
        return [], False, 'Could not check ' + email + '. Reconnect with /gmail ' + email + '.'

def brief(c):
    saved = accounts(c)
    if not configured() or not saved:
        return 'YOUR MAIL\nGmail is not connected yet. Use /gmail youraddress@gmail.com to connect each account.'
    combined, notices, more = [], [], False
    # Each account fails independently. Never let one expired token hide other mail.
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda pair: (pair[0], account_mail(*pair)), sorted(saved.items())))
    for email, (items, truncated, error) in results:
        more = more or truncated
        if error: notices.append(error); continue
        if not items: notices.append(email + ': no non-sensitive inbox mail in the last 24 hours.')
        for priority, ts, sender, subject in items:
            combined.append((priority, ts, email, sender, subject))
    combined.sort(key=lambda x:(x[0],x[1]),reverse=True)
    lines = ['YOUR MAIL - last 24 hours, ' + str(len(saved)) + ' connected account(s) (sender + subject only)']
    shown = []
    counts = {}
    for item in combined:
        email = item[2]
        if counts.get(email,0) >= 2: continue
        shown.append(item); counts[email] = counts.get(email,0) + 1
        if len(shown) >= 8: break
    for priority, ts, email, sender, subject in shown:
        lines.append(('- [Important] ' if priority else '- ') + '[' + email + '] ' + sender + ': ' + subject)
    if len(combined)>len(shown) or more: lines.append('More mail is in Gmail; showing up to 8 across accounts, at most 2 per account, important mail first (up to 25 recent messages checked per account).' )
    lines.extend(notices)
    lines.append('Bank, DigiLocker, OTP and password/security mail are excluded. Mail is not marked as read.')
    return '\n'.join(lines)

def handle_http(handler,c):
    route = urllib.parse.urlsplit(handler.path)
    if route.path not in ('/gmail/connect','/gmail/callback'): return False
    args = urllib.parse.parse_qs(route.query)
    try:
        if route.path == '/gmail/connect':
            location = authorization(c,args.get('key',[''])[0])
            handler.send_response(302); handler.send_header('Location',location)
            handler.send_header('Cache-Control','no-store'); handler.end_headers(); return True
        email = callback(c,args)
        text = email + ' connected to KASH alongside your other accounts. Your 8am brief will include recent non-sensitive inbox mail. In Telegram, send /gmailtest to preview your mail section. Use /gmailaccounts to see connections, /gmaildisconnect email to remove one locally, or /gmaildisconnect all to remove all.'
    except Exception:
        text = 'Gmail was not connected. Return to KASH on Telegram and send /gmail for a new link. Use the Google account named in the connection message.'
    handler.send_response(200); handler.send_header('Content-Type','text/plain; charset=utf-8')
    handler.send_header('Cache-Control','no-store'); handler.send_header('Referrer-Policy','no-referrer')
    handler.end_headers(); handler.wfile.write(text.encode()); return True

# Content reads are explicit user-requested actions, separate from the header-only morning brief.
def body_text(payload):
    if payload.get('filename') or payload.get('body',{}).get('attachmentId'): return ''
    kind = payload.get('mimeType','')
    if kind.startswith('multipart/'):
        parts = payload.get('parts',[])
        plain = [p for p in parts if p.get('mimeType')=='text/plain' and not p.get('filename')]
        return '\n'.join(body_text(p) for p in (plain or parts))
    if kind not in ('text/plain','text/html'): return ''
    data = payload.get('body',{}).get('data','')
    if not data: return ''
    text = base64.urlsafe_b64decode(data + '='*(-len(data)%4)).decode('utf-8','replace')
    if kind=='text/html':
        import html
        text = re.sub(r'(?is)<(script|style).*?</\1>',' ',text)
        text = html.unescape(re.sub(r'<[^>]+>',' ',text))
    return re.sub(r'\s+',' ',text).strip()[:12000]

def account_content(email, encrypted, query):
    try:
        refresh = cipher().decrypt(encrypted.encode()).decode()
        token = request_json('https://oauth2.googleapis.com/token',{
            'client_id':os.environ['GMAIL_CLIENT_ID'],'client_secret':os.environ['GMAIL_CLIENT_SECRET'],
            'refresh_token':refresh,'grant_type':'refresh_token'})['access_token']
        api = 'https://gmail.googleapis.com/gmail/v1/users/me/messages'
        # Gmail's search applies exclusions to indexed bodies BEFORE a full-content fetch.
        listing = request_json(api+'?'+urllib.parse.urlencode({'q':query,'maxResults':12}),token=token)
        candidates=[]
        for item in listing.get('messages',[]):
            params=[('format','metadata'),('metadataHeaders','From'),('metadataHeaders','Subject'),('fields','payload/headers,labelIds,internalDate')]
            m=request_json(api+'/'+item['id']+'?'+urllib.parse.urlencode(params),token=token)
            h={v['name'].lower():v['value'] for v in m.get('payload',{}).get('headers',[])}
            subject,sender=h.get('subject','(no subject)'),h.get('from','')
            if SENSITIVE.search(subject+' '+sender): continue
            candidates.append(('IMPORTANT' in m.get('labelIds',[]) or bool(IMPORTANT.search(subject)),int(m.get('internalDate',0)),item['id'],sender,subject))
        candidates.sort(reverse=True)
        docs=[]
        for priority,ts,mid,sender,subject in candidates[:3]:
            m=request_json(api+'/'+mid+'?'+urllib.parse.urlencode({'format':'full','fields':'id,threadId,payload,internalDate'}),token=token)
            text=body_text(m.get('payload',{}))
            # Defense in depth: never surface text that escaped Gmail's search exclusions.
            if SENSITIVE.search(text):continue
            if not text:continue
            headers={h['name'].lower():h['value'] for h in m.get('payload',{}).get('headers',[])}
            docs.append({'id':mid,'thread_id':m.get('threadId',''),'message_id':headers.get('message-id',''),'reply_to':headers.get('reply-to',sender),'account':email,'sender':safe_text(sender,200),'subject':safe_text(subject,250),'received_ms':ts,'body':text[:6000]})
        return docs,None
    except urllib.error.HTTPError as e:
        if e.code in (401,403): return [],email+': access could not be used; check /gmailaccounts and reconnect only if Google permission expired or was blocked.'
        return [],email+': Gmail request failed this time; this does not mean no mail exists.'
    except Exception:
        return [],email+': could not fetch mail this time; the saved connection was not removed.'

def content(c, question):
    saved=accounts(c)
    if not configured() or not saved:return [],['No Gmail account connected. Use /gmail youraddress@gmail.com.']
    query=QUERY
    # A quoted sender/subject phrase narrows the search without accepting Gmail operators.
    phrases=re.findall(r'["\u201c]([^"\u201d]{2,80})["\u201d]',question)
    if phrases:
        phrase=re.sub(r'[^\w @.\-]',' ',phrases[0]).strip()
        if phrase:query=QUERY.replace('newer_than:1d','newer_than:30d')+' "'+phrase+'"'
    docs,notices=[],[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda pair:account_content(*pair,query),sorted(saved.items())))
    for found,error in results:
        docs.extend(found)
        if error:notices.append(error)
    docs.sort(key=lambda d:d['received_ms'],reverse=True)
    return docs[:8],notices


def draft_access(c, email):
    source=json.loads(c.kv_get('gmail_sources') or '{}').get(email)
    if source=='disabled':return False
    suite=json.loads(c.kv_get('workspace_accounts') or '{}').get(email,{})
    if source!='separate' and SCOPE in suite.get('scope','').split():return COMPOSE_SCOPE in suite['scope'].split()
    return COMPOSE_SCOPE in json.loads(c.kv_get('gmail_scopes') or '{}').get(email,'').split()

def post_draft(token, thread, raw):
    # Only draft creation exists. Never add messages.send, drafts.send, modify or delete.
    payload={'message':{'raw':raw,'threadId':thread}}
    req=urllib.request.Request('https://gmail.googleapis.com/gmail/v1/users/me/drafts',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)

@synchronized
def create_reply_draft(c, doc, body):
    from email.message import EmailMessage
    email=doc['account'];saved=accounts(c)
    if email not in saved:return {'status':'not_connected'}
    if not draft_access(c,email):return {'status':'consent_needed','account':email}
    key='gmail_draft_'+hashlib.sha256((email+'|'+doc['id']).encode()).hexdigest()
    previous=json.loads(c.kv_get(key) or '{}')
    if previous:return {'status':'exists',**previous}
    # Fetch thread immediately before preparing a draft, so replied mail isn't pending.
    refresh=cipher().decrypt(saved[email].encode()).decode()
    tokeninfo=request_json('https://oauth2.googleapis.com/token',{'client_id':os.environ['GMAIL_CLIENT_ID'],'client_secret':os.environ['GMAIL_CLIENT_SECRET'],'refresh_token':refresh,'grant_type':'refresh_token'})
    if COMPOSE_SCOPE not in tokeninfo.get('scope','').split():return {'status':'consent_needed','account':email}
    token=tokeninfo['access_token']
    thread=request_json('https://gmail.googleapis.com/gmail/v1/users/me/threads/'+doc['thread_id']+'?'+urllib.parse.urlencode({'format':'metadata','metadataHeaders':'From','fields':'messages(id,labelIds,payload/headers)'}),token=token)
    messages=thread.get('messages',[])
    if not messages or messages[-1].get('id')!=doc['id']:return {'status':'thread_changed'}
    if 'SENT' in messages[-1].get('labelIds',[]):return {'status':'already_replied'}
    sender=parseaddr(doc['sender'])[1];target=parseaddr(doc.get('reply_to',''))[1]
    if not valid_email(target) or sender.lower()!=target.lower():return {'status':'recipient_review_needed'}
    if re.search(r'no[._-]?reply|do[._-]?not[._-]?reply|mailer-daemon',target,re.I):return {'status':'not_replyable'}
    if SENSITIVE.search(doc['sender']+' '+doc['subject']+' '+doc['body']+' '+body):return {'status':'sensitive_skipped'}
    msg=EmailMessage();msg['From']=email;msg['To']=target
    msg['Subject']=doc['subject'] if doc['subject'].lower().startswith('re:') else 'Re: '+doc['subject']
    if doc.get('message_id'):
        msg['In-Reply-To']=doc['message_id'];msg['References']=doc['message_id']
    msg.set_content(body)
    c.kv_set(key,json.dumps({'status':'creation_pending','account':email,'subject':doc['subject']}))
    try:result=post_draft(token,doc['thread_id'],base64.urlsafe_b64encode(msg.as_bytes()).decode())
    except Exception:
        # Ambiguous HTTP/network writes must not be auto-retried into duplicates.
        c.kv_set(key,json.dumps({'status':'creation_unverified','account':email,'subject':doc['subject']}))
        return {'status':'creation_unverified'}
    record={'account':email,'subject':doc['subject'],'draft_id':result.get('id',''),'recipient':target}
    c.kv_set(key,json.dumps(record))
    return {'status':'created',**record}
