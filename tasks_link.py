"""Google Tasks read/create only. No completion, edits, deletion or assignments."""
import json,urllib.parse
import drive_link as workspace
BASE='https://tasks.googleapis.com/tasks/v1/'
def lists(c):
    rows=[];notices=[]
    for email in sorted(workspace.accounts(c)):
        try:
            tok=workspace.token(c,email);page=None
            while True:
                params={'maxResults':100}
                if page:params['pageToken']=page
                data=workspace.call(tok,BASE+'users/@me/lists?'+urllib.parse.urlencode(params))
                rows.extend({'account':email,**r} for r in data.get('items',[]));page=data.get('nextPageToken')
                if not page:break
        except Exception:notices.append('Could not read task lists for '+email+'. Check /google '+email+' consent.')
    return rows,notices

def tasks(c,email,list_id):
    tok=workspace.token(c,email)
    data=workspace.call(tok,BASE+'lists/'+urllib.parse.quote(list_id,safe='')+'/tasks?'+urllib.parse.urlencode({'maxResults':100,'showCompleted':'false','showHidden':'false','showDeleted':'false'}))
    return data.get('items',[]),bool(data.get('nextPageToken'))

def create(c,email,list_id,body,key):
    if set(body)-{'title','notes','due'} or not body.get('title'):raise ValueError('invalid task')
    saved=json.loads(c.kv_get(key) or '{}')
    if saved:return saved
    # Validate list belongs to the selected account immediately before insert.
    current,notices=lists(c)
    if notices or not any(x['account']==email and x['id']==list_id for x in current):raise ValueError('task list unavailable')
    c.kv_set(key,json.dumps({'status':'unverified'}))
    try:
        result=workspace.call(workspace.token(c,email),BASE+'lists/'+urllib.parse.quote(list_id,safe='')+'/tasks',body)
        taskid=result['id'];actual=workspace.call(workspace.token(c,email),BASE+'lists/'+urllib.parse.quote(list_id,safe='')+'/tasks/'+urllib.parse.quote(taskid,safe=''))
        status='created' if actual.get('title')==body['title'] and actual.get('notes','')==body.get('notes','') and actual.get('due','')[:10]==body.get('due','')[:10] else 'partial'
        record={'status':status,'id':taskid,'url':actual.get('webViewLink',''),'account':email,'list_id':list_id};c.kv_set(key,json.dumps(record));return record
    except Exception:
        # No client ID for Tasks insert. An uncertain call must not be retried blindly.
        return {'status':'unverified'}
