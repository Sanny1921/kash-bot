"""Separate Calendar consent and read/create-only private event interface."""
import base64,hashlib,json,os,secrets,time,urllib.parse,urllib.request,urllib.error
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import gmail_link as oauth
TZ=ZoneInfo('Asia/Kolkata')
PREFIX='https://www.googleapis.com/auth/'
SCOPES=' '.join(PREFIX+s for s in ('gmail.readonly','gmail.compose','calendar.calendarlist.readonly','calendar.events.readonly','calendar.events.owned','drive.readonly','drive.file','contacts.readonly','tasks'))
WIDE_SCOPES=SCOPES+' '+PREFIX+'drive'
BASE=oauth.BASE

def accounts(c):return json.loads(c.kv_get('calendar_accounts') or '{}')
def connect_link(c,email,wide=False):
    email=email.strip().lower()
    if not oauth.valid_email(email):raise ValueError('invalid email')
    if not oauth.configured():return None
    key=secrets.token_urlsafe(32)
    c.kv_set('calendar_launch_'+hashlib.sha256(key.encode()).hexdigest(),json.dumps({'email':email,'until':time.time()+1800,'wide':bool(wide)}))
    return BASE+'/calendar/connect?'+urllib.parse.urlencode({'key':key})
@oauth.synchronized
def authorization(c,key):
    k='calendar_launch_'+hashlib.sha256(key.encode()).hexdigest()
    launch=json.loads(c.kv_get(k) or '{}')
    if launch.get('until',0)<time.time():raise ValueError('expired link')
    c.kv_set(k,'{}');state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
    c.kv_set('calendar_state_'+hashlib.sha256(state.encode()).hexdigest(),json.dumps({'email':launch['email'],'verifier':verifier,'until':time.time()+900,'scopes':WIDE_SCOPES if launch.get('wide') else SCOPES}))
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return 'https://accounts.google.com/o/oauth2/v2/auth?'+urllib.parse.urlencode({'client_id':os.environ['GMAIL_CLIENT_ID'],'redirect_uri':BASE+'/gmail/callback','scope':WIDE_SCOPES if launch.get('wide') else SCOPES,'response_type':'code','access_type':'offline','prompt':'select_account consent','login_hint':launch['email'],'state':state,'code_challenge':challenge,'code_challenge_method':'S256'})
@oauth.synchronized
def callback(c,args):
    k='calendar_state_'+hashlib.sha256(args.get('state',[''])[0].encode()).hexdigest();st=json.loads(c.kv_get(k) or '{}')
    if st.get('until',0)<time.time():raise ValueError('invalid state')
    c.kv_set(k,'{}')
    if args.get('error'):raise ValueError('consent denied')
    tok=oauth.request_json('https://oauth2.googleapis.com/token',{'client_id':os.environ['GMAIL_CLIENT_ID'],'client_secret':os.environ['GMAIL_CLIENT_SECRET'],'code':args.get('code',[''])[0],'code_verifier':st['verifier'],'grant_type':'authorization_code','redirect_uri':BASE+'/gmail/callback'})
    if not all(s in tok.get('scope','').split() for s in st.get('scopes',SCOPES).split()):raise ValueError('required Calendar permissions missing')
    calendars=api(tok['access_token'],'users/me/calendarList',{'maxResults':250})
    primary=next((d for d in calendars.get('items',[]) if d.get('primary')),None)
    if not primary or primary.get('id','').lower()!=st['email']:raise ValueError('wrong Google account')
    if not tok.get('refresh_token'):raise ValueError('offline permission missing')
    encrypted=oauth.cipher().encrypt(tok['refresh_token'].encode()).decode()
    saved=accounts(c);saved[st['email']]=encrypted;c.kv_set('calendar_accounts',json.dumps(saved))
    suite=json.loads(c.kv_get('workspace_accounts') or '{}');suite[st['email']]={'refresh':encrypted,'scope':tok['scope']};c.kv_set('workspace_accounts',json.dumps(suite))
    sources=json.loads(c.kv_get('gmail_sources') or '{}');sources.pop(st['email'],None);c.kv_set('gmail_sources',json.dumps(sources))
    return st['email']
def token(c,email):
    refresh=oauth.cipher().decrypt(accounts(c)[email].encode()).decode()
    return oauth.request_json('https://oauth2.googleapis.com/token',{'client_id':os.environ['GMAIL_CLIENT_ID'],'client_secret':os.environ['GMAIL_CLIENT_SECRET'],'refresh_token':refresh,'grant_type':'refresh_token'})['access_token']
def api(token,path,params=None,body=None):
    url='https://www.googleapis.com/calendar/v3/'+path
    if params:url+='?'+urllib.parse.urlencode(params)
    headers={'Authorization':'Bearer '+token,'Accept':'application/json'};data=None
    if body is not None:headers['Content-Type']='application/json';data=json.dumps(body).encode()
    # The only write is events.insert; updates/deletes and invitations aren't implemented.
    req=urllib.request.Request(url,data=data,headers=headers,method='POST' if data else 'GET')
    with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)
def calendars(c,email):
    tok=token(c,email);out=[];page=None
    while True:
        p={'maxResults':250}
        if page:p['pageToken']=page
        data=api(tok,'users/me/calendarList',p);out.extend(data.get('items',[]));page=data.get('nextPageToken')
        if not page:break
    return out

def schedule(c,start,end):
    rows=[];notices=[]
    for email in sorted(accounts(c)):
        try:
            tok=token(c,email)
            for cal in calendars(c,email):
                if cal.get('deleted') or cal.get('hidden') or cal.get('selected') is False or cal.get('accessRole')=='freeBusyReader':continue
                page=None
                while True:
                    p={'timeMin':start.isoformat(),'timeMax':end.isoformat(),'singleEvents':'true','orderBy':'startTime','maxResults':100}
                    if page:p['pageToken']=page
                    data=api(tok,'calendars/'+urllib.parse.quote(cal['id'],safe='')+'/events',p)
                    for e in data.get('items',[]):
                        if e.get('status')=='cancelled' or any(a.get('self') and a.get('responseStatus')=='declined' for a in e.get('attendees',[])):continue
                        rows.append({'account':email,'calendar':cal.get('summary','Calendar'),'title':oauth.safe_text(e.get('summary','Busy'),150),'start':e.get('start',{}),'end':e.get('end',{}),'location':oauth.safe_text(e.get('location',''),150),'url':e.get('htmlLink','')})
                    page=data.get('nextPageToken')
                    if not page:break
        except Exception:notices.append('Could not read Calendar for '+email+'. Try /calendar '+email+' if Google access expired or is blocked.')
    rows.sort(key=lambda r:r['start'].get('dateTime',r['start'].get('date','')))
    return rows,notices

def display(c,start,end):
    if not accounts(c):return 'Calendar is not connected. Use /calendar youraddress@gmail.com. Gmail connections stay unchanged.'
    rows,notices=schedule(c,start,end)
    lines=['YOUR CALENDAR: '+start.strftime('%a, %d %b %Y')+' to '+(end-timedelta(seconds=1)).strftime('%a, %d %b %Y')+' (IST)']
    for r in rows[:50]:
        s=r['start'];e=r['end']
        if s.get('dateTime'):
            a=datetime.fromisoformat(s['dateTime'].replace('Z','+00:00')).astimezone(TZ);b=datetime.fromisoformat(e.get('dateTime',s['dateTime']).replace('Z','+00:00')).astimezone(TZ)
            when=a.strftime('%a %d %b %I:%M%p')+'-'+b.strftime('%I:%M%p')
        else:when=s.get('date','')+' (all day)'
        lines.append(when+' | '+r['title']+' ['+r['account']+' / '+r['calendar']+']'+(' | '+r['location'] if r['location'] else ''))
    if not rows and not notices:lines.append('No events in this window.')
    if len(rows)>50:lines.append('Showing first 50 events; ask for a smaller date range.')
    return '\n'.join(lines+notices)

def existing(c,email,calendar_id,event_id):
    path='calendars/'+urllib.parse.quote(calendar_id,safe='')+'/events/'+event_id
    try:return api(token(c,email),path)
    except urllib.error.HTTPError as e:
        if e.code==404:return None
        raise

def create(c,email,calendar_id,event):
    # Event must have been previewed to the owner, with a separate owner confirmation.
    allowed={'summary','start','end','location','id'}
    if set(event)-allowed:raise ValueError('unsupported event fields')
    cal=next((v for v in calendars(c,email) if v['id']==calendar_id),None)
    if not cal or cal.get('accessRole')!='owner':raise ValueError('choose a calendar you own')
    if not event.get('summary') or not event.get('id'):raise ValueError('missing event data')
    start=datetime.fromisoformat(event['start']['dateTime']);end=datetime.fromisoformat(event['end']['dateTime'])
    if start.tzinfo is None or end.tzinfo is None or end<=start:raise ValueError('invalid event time')
    tok=token(c,email);path='calendars/'+urllib.parse.quote(calendar_id,safe='')+'/events'
    # Client-chosen ID makes uncertain insert retries idempotent.
    try:
        previous=api(tok,path+'/'+event['id'])
        return previous
    except urllib.error.HTTPError as e:
        if e.code!=404:raise
    return api(tok,path,{'sendUpdates':'none'},body=event)

def handle_http(handler,c):
    route=urllib.parse.urlsplit(handler.path)
    if route.path=='/gmail/callback':
        state=urllib.parse.parse_qs(route.query).get('state',[''])[0]
        if not c.kv_get('calendar_state_'+hashlib.sha256(state.encode()).hexdigest()):return False
    elif route.path not in ('/calendar/connect','/calendar/callback'):return False
    try:
        args=urllib.parse.parse_qs(route.query)
        if route.path=='/calendar/connect':
            url=authorization(c,args.get('key',[''])[0]);handler.send_response(302);handler.send_header('Location',url);handler.end_headers();return True
        email=callback(c,args);text=email+' Google suite connected. Any newly granted Drive scope is now saved for this account. Account names and other connections were preserved. Return to KASH: reviewed file changes still require your yes; no sharing or permanent deletion. Gmail remains draft-only, never send.'
    except Exception:text='Calendar was not connected. Existing connections are unchanged. Return to KASH and use /calendar youraddress@gmail.com for a fresh link; choose that exact Google account.'
    handler.send_response(200);handler.send_header('Content-Type','text/plain; charset=utf-8');handler.end_headers();handler.wfile.write(text.encode());return True
