"""Owner-confirmed, hourly, exact-thread reply watches. Metadata only, no sends."""
import base64,hashlib,json,re,time,urllib.parse
from email.utils import parseaddr
import gmail_link as gmail

def get_token(c,email):
    saved=gmail.accounts(c)
    if email not in saved:raise ValueError('Gmail account disconnected')
    refresh=gmail.cipher().decrypt(saved[email].encode()).decode()
    return gmail.request_json('https://oauth2.googleapis.com/token',{'client_id':__import__('os').environ['GMAIL_CLIENT_ID'],'client_secret':__import__('os').environ['GMAIL_CLIENT_SECRET'],'refresh_token':refresh,'grant_type':'refresh_token'})['access_token']
def api(tok,path,params=None):
    url='https://gmail.googleapis.com/gmail/v1/users/me/'+path
    if params:url+='?'+urllib.parse.urlencode(params,doseq=True)
    return gmail.request_json(url,token=tok)
def headers(m):return {x['name'].lower():x['value'] for x in m.get('payload',{}).get('headers',[])}
def thread(c,email,tid):
    return api(get_token(c,email),'threads/'+urllib.parse.quote(tid,safe=''),{'format':'metadata','metadataHeaders':['From','To','Subject','Message-ID'],'fields':'id,messages(id,threadId,internalDate,labelIds,payload/headers)'})
def candidates(c,email,sender):
    if not gmail.valid_email(sender):raise ValueError('invalid sender')
    tok=get_token(c,email)
    q='in:sent newer_than:90d to:'+sender+' '+ ' '.join('-'+x for x in gmail.EXCLUSIONS)
    data=api(tok,'messages',{'q':q,'maxResults':10});seen=set();rows=[]
    for item in data.get('messages',[]):
        m=api(tok,'messages/'+item['id'],{'format':'metadata','metadataHeaders':['Subject','To','From'],'fields':'id,threadId,internalDate,labelIds,payload/headers'})
        h=headers(m);tid=m.get('threadId','')
        if not tid or tid in seen or gmail.SENSITIVE.search(' '.join(h.values())):continue
        seen.add(tid)
        # Require the owner really sent to the exact selected address, not a query alias.
        from email.utils import getaddresses
        if sender.lower() not in [v.lower() for _,v in getaddresses([h.get('to','')])]:continue
        rows.append({'thread':tid,'subject':gmail.safe_text(h.get('subject','(no subject)'),200),'sent_ms':int(m.get('internalDate',0))})
    rows.sort(key=lambda r:r['sent_ms'],reverse=True);return rows[:5]

def inspect(c,email,sender,tid):
    data=thread(c,email,tid);messages=data.get('messages',[])
    if not messages:raise ValueError('thread unavailable')
    newest=max(messages,key=lambda m:int(m.get('internalDate',0)));h=headers(newest)
    if gmail.SENSITIVE.search(' '.join(h.values())):return 'sensitive',None
    latest_from=parseaddr(h.get('from',''))[1].lower()
    if latest_from==sender.lower() and 'SENT' not in newest.get('labelIds',[]):return 'already_replied',newest
    if 'SENT' not in newest.get('labelIds',[]):return 'thread_changed',newest
    return 'awaiting',newest

def all_watches(c):return json.loads(c.kv_get('reply_watches') or '{}')
@gmail.synchronized
def activate(c,record):
    state,last=inspect(c,record['account'],record['sender'],record['thread'])
    if state!='awaiting':return state,None
    watches=all_watches(c)
    for key,existing in watches.items():
        if existing.get('status')=='active' and existing['account']==record['account'] and existing['thread']==record['thread'] and existing['sender']==record['sender']:return 'duplicate',key
    if sum(x.get('status')=='active' for x in watches.values())>=20:return 'limit',None
    rid=__import__('secrets').token_hex(4);now=time.time()
    watches[rid]={**record,'created':now,'baseline_ms':int(last.get('internalDate',0)),'next_check':now+3600,'expires':now+30*86400,'status':'active','failures':0}
    c.kv_set('reply_watches',json.dumps(watches));return 'active',rid

@gmail.synchronized
def cancel(c,rid):
    watches=all_watches(c)
    if rid not in watches or watches[rid].get('status')!='active':return False
    watches[rid]['status']='cancelled';c.kv_set('reply_watches',json.dumps(watches));return True

@gmail.synchronized
def check(c,send):
    now=time.time();watches=all_watches(c)
    for rid,w in watches.items():
        if w.get('status')!='active' or now<w.get('next_check',0):continue
        w['next_check']=now+3600
        if now>=w['expires']:
            w['status']='expired';c.kv_set('reply_watches',json.dumps(watches));send(w['chat'],'Reply watch expired after 30 days: '+w['reminder']+'. Nothing was sent to the contact.');continue
        try:
            data=thread(c,w['account'],w['thread']);msgs=sorted(data.get('messages',[]),key=lambda m:int(m.get('internalDate',0)))
            incoming=[m for m in msgs if int(m.get('internalDate',0))>max(w['baseline_ms'],int(w['created']*1000)) and parseaddr(headers(m).get('from',''))[1].lower()==w['sender'].lower() and 'SENT' not in m.get('labelIds',[]) and not gmail.SENSITIVE.search(' '.join(headers(m).values()))]
            # Don't remind after the owner has already replied to the new incoming message.
            if incoming:
                latest=incoming[-1];after=[m for m in msgs if int(m.get('internalDate',0))>int(latest.get('internalDate',0)) and 'SENT' in m.get('labelIds',[])]
                if after:w['status']='handled';c.kv_set('reply_watches',json.dumps(watches));continue
                w['status']='notifying';c.kv_set('reply_watches',json.dumps(watches))
                try:send(w['chat'],'A new message matched the sender address '+w['sender']+' in ['+w['account']+'] '+w['subject']+'.\nReminder: '+w['reminder']+'\nThis was an hourly check; sender authenticity is not verified. No reply was sent.')
                except Exception:
                    # Telegram send may have landed; no automatic duplicate notifications.
                    w['status']='notification_unverified'
                else:w['status']='fired'
            w['failures']=0
        except Exception:
            w['failures']=w.get('failures',0)+1
            if w['failures']>=3:
                w['status']='blocked';c.kv_set('reply_watches',json.dumps(watches))
                send(w['chat'],'Reply watch paused because Gmail could not be checked: ['+w['account']+'] '+w['subject']+'. Please check Gmail access and set the watch again. I cannot confirm whether a reply arrived.')
        c.kv_set('reply_watches',json.dumps(watches))
