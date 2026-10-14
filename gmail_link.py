"""Owner-only Gmail OAuth and a deterministic, metadata-only morning mail brief."""
import base64, hashlib, hmac, json, os, re, secrets, time
import urllib.parse, urllib.request, urllib.error
from email.utils import parseaddr
from cryptography.fernet import Fernet

SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
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

def connect_link(c):
    if not configured(): return None
    key = secrets.token_urlsafe(32)
    c.kv_set('gmail_launch', json.dumps({'hash':hashlib.sha256(key.encode()).hexdigest(),'until':time.time()+1800}))
    return BASE + '/gmail/connect?' + urllib.parse.urlencode({'key':key})

def authorization(c, key):
    launch = json.loads(c.kv_get('gmail_launch') or '{}')
    if launch.get('until',0) < time.time() or not hmac.compare_digest(launch.get('hash',''),hashlib.sha256(key.encode()).hexdigest()):
        raise ValueError('expired link')
    c.kv_set('gmail_launch', '{}')
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    c.kv_set('gmail_state', json.dumps({'hash':hashlib.sha256(state.encode()).hexdigest(),'verifier':verifier,'until':time.time()+900}))
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode({
        'client_id':os.environ['GMAIL_CLIENT_ID'],'redirect_uri':BASE+'/gmail/callback',
        'response_type':'code','scope':SCOPE,'access_type':'offline','prompt':'consent',
        'login_hint':EMAIL,'state':state,'code_challenge':challenge,'code_challenge_method':'S256'})

def callback(c, args):
    st = json.loads(c.kv_get('gmail_state') or '{}')
    supplied = args.get('state',[''])[0]
    if st.get('until',0)<time.time() or not hmac.compare_digest(st.get('hash',''),hashlib.sha256(supplied.encode()).hexdigest()):
        raise ValueError('invalid state')
    c.kv_set('gmail_state','{}')
    if args.get('error'): raise ValueError('consent not granted')
    tok = request_json('https://oauth2.googleapis.com/token', {
        'client_id':os.environ['GMAIL_CLIENT_ID'],'client_secret':os.environ['GMAIL_CLIENT_SECRET'],
        'code':args.get('code',[''])[0],'code_verifier':st['verifier'],
        'grant_type':'authorization_code','redirect_uri':BASE+'/gmail/callback'})
    profile = request_json('https://gmail.googleapis.com/gmail/v1/users/me/profile',token=tok['access_token'])
    if profile.get('emailAddress','').lower()!=EMAIL:
        raise ValueError('wrong Google account')
    if not tok.get('refresh_token'): raise ValueError('offline access not granted')
    c.kv_set('gmail_refresh',cipher().encrypt(tok['refresh_token'].encode()).decode())
    c.kv_set('gmail_connected',EMAIL)
    return EMAIL

def disconnect(c):
    # Remove the local grant even if Google's revoke endpoint is unavailable.
    encrypted = c.kv_get('gmail_refresh')
    c.kv_set('gmail_refresh',''); c.kv_set('gmail_connected','')
    if encrypted:
        try:
            token = cipher().decrypt(encrypted.encode()).decode()
            req = urllib.request.Request('https://oauth2.googleapis.com/revoke', data=urllib.parse.urlencode({'token':token}).encode(), headers={'Content-Type':'application/x-www-form-urlencoded'})
            with urllib.request.urlopen(req,timeout=20): pass
        except Exception: pass

def safe_text(s, limit):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', s).strip()[:limit]

def brief(c):
    if not configured() or not c.kv_get('gmail_refresh'):
        return 'YOUR MAIL\nGmail is not connected yet. Use /gmail to connect it.'
    try:
        refresh = cipher().decrypt(c.kv_get('gmail_refresh').encode()).decode()
        token = request_json('https://oauth2.googleapis.com/token',{
            'client_id':os.environ['GMAIL_CLIENT_ID'],'client_secret':os.environ['GMAIL_CLIENT_SECRET'],
            'refresh_token':refresh,'grant_type':'refresh_token'})['access_token']
        api = 'https://gmail.googleapis.com/gmail/v1/users/me/messages'
        listing = request_json(api+'?'+urllib.parse.urlencode({'q':QUERY,'maxResults':50}),token=token)
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
        items.sort(key=lambda x:(x[0],x[1]),reverse=True)
        if not items: return 'YOUR MAIL\nNo non-sensitive inbox mail found in the last 24 hours. Bank, DigiLocker and security mail are excluded.'
        lines = ['YOUR MAIL - last 24 hours (sender + subject only)']
        for priority,ts,sender,subject in items[:8]:
            lines.append(('- [Important] ' if priority else '- ')+sender+': '+subject)
        if len(items)>8 or listing.get('nextPageToken'): lines.append('More mail is in Gmail; showing up to 8, important mail first.')
        lines.append('Bank, DigiLocker, OTP and password/security mail are excluded. Mail is not marked as read.')
        return '\n'.join(lines)
    except urllib.error.HTTPError as e:
        print('Gmail brief unavailable: HTTP',e.code)  # never log tokens or mail
        return 'YOUR MAIL\nCould not check Gmail this morning. If access expired, use /gmail to reconnect. This does not mean there was no mail.'
    except Exception:
        print('Gmail brief unavailable')
        return 'YOUR MAIL\nCould not check Gmail this morning. Use /gmail to check the connection. This does not mean there was no mail.'

def handle_http(handler,c):
    route = urllib.parse.urlsplit(handler.path)
    if route.path not in ('/gmail/connect','/gmail/callback'): return False
    args = urllib.parse.parse_qs(route.query)
    try:
        if route.path == '/gmail/connect':
            location = authorization(c,args.get('key',[''])[0])
            handler.send_response(302); handler.send_header('Location',location)
            handler.send_header('Cache-Control','no-store'); handler.end_headers(); return True
        callback(c,args)
        text = 'Gmail connected to KASH. Your 8am brief will include recent non-sensitive inbox mail. In Telegram, send /gmailtest to preview your mail section. You can disconnect with /gmaildisconnect.'
    except Exception:
        text = 'Gmail was not connected. Return to KASH on Telegram and send /gmail for a new link. Use the Google account named in the connection message.'
    handler.send_response(200); handler.send_header('Content-Type','text/plain; charset=utf-8')
    handler.send_header('Cache-Control','no-store'); handler.send_header('Referrer-Policy','no-referrer')
    handler.end_headers(); handler.wfile.write(text.encode()); return True
