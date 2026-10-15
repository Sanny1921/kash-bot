"""Read Drive and create private text Docs/Sheets. No share/delete/edit-existing API."""
import json,urllib.parse,urllib.request,urllib.error,os
import gmail_link as oauth

def accounts(c):return json.loads(c.kv_get('workspace_accounts') or '{}')
def token(c,email):
    encrypted=accounts(c)[email]['refresh'];refresh=oauth.cipher().decrypt(encrypted.encode()).decode()
    return oauth.request_json('https://oauth2.googleapis.com/token',{'client_id':os.environ['GMAIL_CLIENT_ID'],'client_secret':os.environ['GMAIL_CLIENT_SECRET'],'refresh_token':refresh,'grant_type':'refresh_token'})['access_token']
def call(token,url,body=None,method=None,text=False):
    headers={'Authorization':'Bearer '+token};data=None
    if body is not None:headers['Content-Type']='application/json';data=json.dumps(body).encode()
    req=urllib.request.Request(url,data=data,headers=headers,method=method or ('POST' if data else 'GET'))
    with urllib.request.urlopen(req,timeout=25) as r:
        raw=r.read(2000000)
        if text:return raw.decode('utf-8',errors='replace')
        return json.loads(raw)
def metadata(c,email,fid):
    return call(token(c,email),'https://www.googleapis.com/drive/v3/files/'+urllib.parse.quote(fid,safe='')+'?'+urllib.parse.urlencode({'fields':'id,name,mimeType,webViewLink,trashed,permissions(type,role),capabilities(canEdit)'}))
def search(c,query):
    query=query.strip()[:180]
    if not query:raise ValueError('search words missing')
    safe=query.replace('\\','\\\\').replace("'","\\'")
    rows=[];notices=[]
    for email in sorted(accounts(c)):
        try:
            data=call(token(c,email),'https://www.googleapis.com/drive/v3/files?'+urllib.parse.urlencode({'q':"trashed = false and (name contains '"+safe+"' or fullText contains '"+safe+"')",'pageSize':10,'orderBy':'modifiedTime desc','fields':'files(id,name,mimeType,webViewLink,modifiedTime),nextPageToken'}))
            rows.extend({'account':email,**d} for d in data.get('files',[]))
            if data.get('nextPageToken'):notices.append(email+': showing the 10 newest matches; narrow the search for more.')
        except Exception:notices.append('Could not search Drive for '+email+'. Check /google '+email+' consent.')
    return rows[:30],notices

def read(c,email,fid):
    meta=metadata(c,email,fid)
    if meta.get('trashed'):raise ValueError('file is trashed')
    tok=token(c,email);kind=meta.get('mimeType');base='https://www.googleapis.com/drive/v3/files/'+urllib.parse.quote(fid,safe='')
    if kind=='application/vnd.google-apps.document':
        text=call(tok,base+'/export?'+urllib.parse.urlencode({'mimeType':'text/plain'}),text=True)
    elif kind=='application/vnd.google-apps.spreadsheet':
        sheet=call(tok,'https://sheets.googleapis.com/v4/spreadsheets/'+urllib.parse.quote(fid,safe='')+'?fields=properties(title),sheets(properties(title))')
        sections=[]
        for tab in sheet.get('sheets',[])[:4]:
            name=tab['properties']['title'];rng="'"+name.replace("'","''")+"'!A1:Z100"
            data=call(tok,'https://sheets.googleapis.com/v4/spreadsheets/'+urllib.parse.quote(fid,safe='')+'/values/'+urllib.parse.quote(rng,safe=''))
            sections.append(name+' (A1:Z100):\n'+json.dumps(data.get('values',[]),ensure_ascii=False))
        text='\n\n'.join(sections)
    elif kind in ('text/plain','text/csv','text/markdown'):
        text=call(tok,base+'?alt=media',text=True)
    else:return meta,None,'This release reads text Google Docs, the first four Sheets tabs (A1:Z100), and plain text/CSV/Markdown files. Other formats can be opened using the original link.'
    truncated=len(text)>18000
    return meta,text[:18000],('Limited extract: first 18,000 characters. Formatting, images, comments and attachments are not read. Sheets are limited to the first four tabs, A1:Z100.' if truncated or kind.endswith('spreadsheet') else 'Text only; formatting, images, comments and attachments are not read.')

def private(meta):return 'permissions' in meta and not any(p.get('type') in ('anyone','domain','group') or (p.get('type')=='user' and p.get('role')!='owner') for p in meta.get('permissions',[]))

def create(c,email,kind,title,content,key):
    record=json.loads(c.kv_get(key) or '{}')
    if record.get('id'):return {'status':record.get('status','partial'),**record}
    if record.get('status')=='attempting':return {'status':'unverified'}
    if kind not in ('doc','sheet') or not title.strip():raise ValueError('invalid file type/title')
    if email not in accounts(c):raise ValueError('disconnected account')
    tok=token(c,email);c.kv_set(key,json.dumps({'status':'attempting','account':email}))
    # Do not retry ambiguous create calls: they don't have a client-chosen ID.
    try:
        if kind=='doc':
            created=call(tok,'https://docs.googleapis.com/v1/documents',{'title':title});fid=created['documentId']
        else:
            created=call(tok,'https://sheets.googleapis.com/v4/spreadsheets',{'properties':{'title':title},'sheets':[{'properties':{'title':'Sheet1'}}]});fid=created['spreadsheetId']
        record={'id':fid,'account':email,'status':'partial'};c.kv_set(key,json.dumps(record))
        if kind=='doc' and content:
            before=call(tok,'https://docs.googleapis.com/v1/documents/'+fid+'?includeTabsContent=true')
            tab=before.get('tabs',[{}])[0].get('documentTab',{});tabid=before.get('tabs',[{}])[0].get('tabProperties',{}).get('tabId')
            loc={'index':1}
            if tabid:loc['tabId']=tabid
            body={'requests':[{'insertText':{'location':loc,'text':content}}]}
            if before.get('revisionId'):body['writeControl']={'requiredRevisionId':before['revisionId']}
            call(tok,'https://docs.googleapis.com/v1/documents/'+fid+':batchUpdate',body)
            after=call(tok,'https://docs.googleapis.com/v1/documents/'+fid+'?includeTabsContent=true')
            plain=[]
            def walk(v):
                if isinstance(v,dict):
                    if 'textRun' in v:plain.append(v['textRun'].get('content',''))
                    for k,x in v.items():
                        if k!='textRun':walk(x)
                elif isinstance(v,list):
                    for x in v:walk(x)
            walk(after)
            if content not in ''.join(plain):raise ValueError('document content verification failed')
        elif kind=='sheet' and content:
            rows=content
            call(tok,'https://sheets.googleapis.com/v4/spreadsheets/'+fid+'/values/Sheet1!A1?valueInputOption=RAW',{'range':'Sheet1!A1','majorDimension':'ROWS','values':rows},method='PUT')
            after=call(tok,'https://sheets.googleapis.com/v4/spreadsheets/'+fid+'/values/Sheet1!A1:Z100')
            got=after.get('values',[])
            # API drops trailing empty cells/rows.
            normalize=lambda r:[list(x) for x in r if any(str(v) for v in x)]
            if [[str(v) for v in row] for row in normalize(got)]!=[[str(v) for v in row] for row in normalize(rows)]:raise ValueError('sheet verification failed')
        meta=metadata(c,email,fid);record.update(status='created',url=meta.get('webViewLink',''),private=private(meta))
        c.kv_set(key,json.dumps(record));return record
    except Exception:
        existing=json.loads(c.kv_get(key) or '{}')
        if existing.get('id'):
            try:existing['url']=metadata(c,email,existing['id']).get('webViewLink','')
            except Exception:pass
            existing['status']='partial';c.kv_set(key,json.dumps(existing));return existing
        return {'status':'unverified'}
