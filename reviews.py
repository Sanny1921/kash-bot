"""One latest owner-reviewed Google action; five-minute plain-word approval."""
import hashlib,json,re,time
KEYS=[('file_change_pending_','code','file'),('drive_pending_','code','create'),('task_pending_','code','task'),('calendar_pending_','confirm','calendar')]
def load(c,k):return json.loads(c.kv_get(k) or '{}')
def shown(c,chat,answer):
    found=[]
    for prefix,field,kind in KEYS:
        p=load(c,prefix+str(chat));code=p.get(field)
        if code and code in answer and p.get('until',0)>time.time():found.append((prefix,field,kind,p))
    if len(found)!=1:return answer
    prefix,field,kind,p=found[0]
    until=min(p['until'],time.time()+300)
    record={'prefix':prefix,'field':field,'kind':kind,'code':p[field],'until':until,'status':'waiting'}
    # Keep legacy commands accepted, but do not require users to see/remember them.
    answer=re.sub(r'Reply "confirm [a-f0-9]{8}" or /fileconfirm [a-f0-9]{8} within 15 minutes[.]?', '',answer)
    answer=re.sub(r'Reply "confirm [a-f0-9]{8}" within 10 minutes[.]?', '',answer)
    answer=re.sub(r'To create this file use /fileconfirm [a-f0-9]{8} within 15 minutes[.]?', '',answer)
    answer=re.sub(r'Nothing created yet[.] Use /taskconfirm [a-f0-9]{8} within 15 minutes to create this task[.]?', 'Nothing created yet.',answer)
    answer=re.sub(r'To create exactly this, use /calendarconfirm [a-f0-9]{8} within 10 minutes[.]?', '',answer)
    result=answer.strip()+'\nReply "yes" to do exactly this, or "no" to cancel. This review expires in 5 minutes.'
    record['preview_hash']=hashlib.sha256(result.encode()).hexdigest();c.kv_set('latest_google_review_'+str(chat),json.dumps(record));return result
def approve(c,text,chat,b):
    if not re.fullmatch(r'(?:yes|yes please|do it|confirm|go ahead|no|no thanks|cancel|cancel it|हाँ|हां|नहीं)[.!]?',text.strip(),re.I):return None
    key='latest_google_review_'+str(chat);r=load(c,key)
    if not r:return None
    if r.get('until',0)<time.time():return 'That review expired. Ask for a fresh preview; nothing changed by this reply.'
    if r.get('status')!='waiting':return 'That review was already handled. No repeat action.'
    p=load(c,r['prefix']+str(chat))
    if p.get(r['field'])!=r['code']:return 'That preview has been replaced. Ask for a fresh review; nothing changed.'
    if re.fullmatch(r'(?:no|no thanks|cancel|cancel it|नहीं)[.!]?',text.strip(),re.I):
        r['status']='cancelled';p['until']=0;c.kv_set(key,json.dumps(r));c.kv_set(r['prefix']+str(chat),json.dumps(p));return 'Cancelled that preview. Nothing changed.'
    r['status']='handling';c.kv_set(key,json.dumps(r))
    if r['kind']=='file':out=b.file_crud.confirm_change(c,chat,r['code'])
    elif r['kind']=='create':out=b.drive_command(c,'/fileconfirm '+r['code'],chat)
    elif r['kind']=='task':out=b.tasks_command(c,'/taskconfirm '+r['code'],chat)
    else:out=b.calendar_command(c,'/calendarconfirm '+r['code'],chat)
    r['status']='handled';c.kv_set(key,json.dumps(r));return out
