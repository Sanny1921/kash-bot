"""Owner chat routing only. Prepares existing reviewed operations, never bypasses confirmation."""
import json,re,time,secrets
DOC=re.compile(r'^(?:please\s+)?(?:(?:go (?:on|to) )?google docs? and )?(?:create|make|start)\s+(?:a |an |new )*(?:(google)\s+)?(doc(?:ument)?|sheet|spreadsheet|file)\s+(?:called|named|titled)\s+(.+)$',re.I|re.S)
TASK=re.compile(r'^(?:please\s+)?(?:add|create|make)\s+(?:a |new )*(?:google )?task\s+(?:(?:called|named|to)\s+)?(.+)$',re.I|re.S)
SELECT=re.compile(r'^(?:use|choose|select)\s+(?:(?:account|calendar|list|file|contact)\s+)?(\d+|[^\s]+@[^\s]+)(?:\s+(?:please|and continue))?[.!]?$',re.I)
CONFIRM=re.compile(r'^(?:confirm|create it|save it|go ahead)\s+([a-f0-9]{8})[.!]?$',re.I)

def load(c,key):return json.loads(c.kv_get(key) or '{}')
def waiting(c,chat,domain,rows,request):
    c.kv_set('natural_google_choice_'+str(chat),json.dumps({'until':time.time()+900,'domain':domain,'rows':rows,'request':request}))
    return 'Which '+domain+' should I use?\n'+'\n'.join(str(i)+'. '+r['label'] for i,r in enumerate(rows,1))+'\nReply "use 1" (or another number). I will continue your request, not create anything yet.'
def named_account(text):
    matches=re.findall(r'\b(?:using|in|on|with) (?:my )?(personal|college)(?: (?:account|gmail))?\b|\b(personal|college) (?:account|gmail|calendar|drive|tasks|contacts)\b',text,re.I)
    return set(v.lower() for pair in matches for v in pair if v)
class AccountView:
    def __init__(self,c,email):self.base=c;self.email=email;self._natural_scoped=True
    def __getattr__(self,name):return getattr(self.base,name)
    def kv_set(self,key,value):
        if key in ('workspace_accounts','calendar_accounts','gmail_accounts'):raise ValueError('Scoped routing cannot change connections')
        return self.base.kv_set(key,value)
    def kv_get(self,key):
        value=self.base.kv_get(key)
        if key in ('workspace_accounts','calendar_accounts','gmail_accounts'):
            data=json.loads(value or '{}');return json.dumps({self.email:data[self.email]} if self.email in data else {})
        return value

def aliases(c):return load(c,'google_account_aliases')
def destination(c,chat,b,domain,request,account=None):
    names=named_account(request)
    if len(names)>1:return None,'Please choose personal or college for this request, not both.'
    labels=aliases(c)
    if names:
        label=next(iter(names))
        if not labels.get(label):return None,'Which email is your '+label+' account? Say "use '+label+' account youraddress@gmail.com", then repeat your request. I will not use a different account.'
        account=labels[label]
    elif not account and getattr(c,'_natural_scoped',False):account=c.email
    elif not account and labels.get('personal'):account=labels['personal']
    if account:
        connected=b.calendar_link.accounts(c) if domain=='calendar' else b.drive_link.accounts(c)
        if account not in connected:return None,'Your chosen account '+account+' is not connected for this tool. Connect it with /google '+account+'. I will not fall back to another account.'
    key={'account':'drive_destination_','task list':'task_destination_','calendar':'calendar_destination_'}[domain]+str(chat)
    saved=c.kv_get(key)
    if domain=='account':
        accounts=b.drive_link.accounts(c)
        if account:
            if account not in accounts:return None,'That exact Google account is not connected. Connect it with /google '+account+'.'
            c.kv_set(key,account);return account,None
        if saved in accounts:return saved,None
        rows=[{'value':a,'label':a} for a in sorted(accounts)]
    elif domain=='task list':
        rows,notices=b.tasks_link.lists(c)
        if notices:return None,'I could not verify all task lists. '+ '\n'.join(notices)
        rows=[{'value':r,'label':'['+r['account']+'] '+r.get('title','Task list')} for r in rows if not account or r['account']==account]
        if saved and any(r['value']==load(c,key) for r in rows):return load(c,key),None
    else:
        rows=[]
        for a in sorted(b.calendar_link.accounts(c)):
            if account and account!=a:continue
            for r in b.calendar_link.calendars(c,a):
                if r.get('accessRole')=='owner':
                    v={'account':a,'id':r['id'],'name':r.get('summary','Calendar'),'owned':True}
                    rows.append({'value':v,'label':'['+a+'] '+v['name']})
        if saved and any(r['value']==load(c,key) for r in rows):return load(c,key),None
    if not rows:return None,'No usable '+domain+' is available. Check your Google connection.'
    if len(rows)==1:
        value=rows[0]['value'];c.kv_set(key,value if isinstance(value,str) else json.dumps(value));return value,None
    return None,waiting(c,chat,domain,rows,request)

def route(c,text,chat,b):
    if not text or text.startswith('/'):return None
    stripped=text.strip()
    alias=re.fullmatch(r'use (personal|college) account ([\w.+-]+@[\w.-]+)[.!]?',stripped,re.I)
    if alias:
        label,email=alias.group(1).lower(),alias.group(2).lower().rstrip('.!')
        if email not in b.drive_link.accounts(c):return 'Connect '+email+' first with /google '+email+', then name it again. No other account selected.'
        values=aliases(c);values[label]=email;c.kv_set('google_account_aliases',json.dumps(values))
        return label.capitalize()+' now means '+email+'. New requests default to personal when you do not name an account; college requests never fall back to personal. Every write still has a preview.'
    if not getattr(c,'_natural_scoped',False):
        names=named_account(stripped);values=aliases(c)
        if len(names)>1:return 'Please choose personal or college, not both, for this request.'
        label=next(iter(names)) if names else 'personal' if values.get('personal') else None
        # Scope tool requests only, not casual chat or literal file contents.
        tool=DOC.match(stripped) or TASK.match(stripped) or b.CAL_TOPIC.search(stripped) or b.DRIVE_TOPIC.search(stripped) or b.CONTACT_TOPIC.search(stripped) or b.GOOGLE_TASK_TOPIC.search(stripped) or b.MAIL_TOPIC.search(stripped)
        if label and tool:
            email=values.get(label)
            if not email:return 'Which email is your '+label+' account? Say "use '+label+' account youraddress@gmail.com". I will not choose another account.'
            available=set(b.drive_link.accounts(c))|set(b.calendar_link.accounts(c))|set(b.gmail_link.accounts(c))
            if email not in available:return 'Your '+label+' account '+email+' is not connected. Use /google '+email+'. No fallback to another account.'
            return b.reply(AccountView(c,email),chat,stripped)[0]
    select=SELECT.fullmatch(stripped)
    if select:
        p=load(c,'natural_google_choice_'+str(chat))
        if p.get('until',0)<time.time():return None
        arg=select.group(1).rstrip('.!');rows=p['rows']
        matches=[r for i,r in enumerate(rows,1) if arg==str(i) or (isinstance(r['value'],str) and r['value']==arg.lower())]
        if len(matches)!=1:return 'Choose one number from the latest list.'
        v=matches[0]['value'];key={'account':'drive_destination_','task list':'task_destination_','calendar':'calendar_destination_'}[p['domain']]
        c.kv_set(key+str(chat),v if isinstance(v,str) else json.dumps(v));c.kv_set('natural_google_choice_'+str(chat),'{}')
        return route(c,p['request'],chat,b) if p['domain']!='calendar' else b.live_calendar_answer(c,p['request'],chat)
    confirm=CONFIRM.fullmatch(stripped)
    if confirm:
        code=confirm.group(1).lower();matches=[]
        for prefix,field,command,handler in [('drive_pending_','code','/fileconfirm',b.drive_command),('task_pending_','code','/taskconfirm',b.tasks_command),('calendar_pending_','confirm','/calendarconfirm',b.calendar_command)]:
            p=load(c,prefix+str(chat))
            if p.get(field)==code and p.get('until',0)>time.time():matches.append((command,handler))
        if len(matches)!=1:return 'That review code is invalid, expired or ambiguous. Prepare a fresh preview.'
        command,handler=matches[0];return handler(c,command+' '+code,chat)
    doc=DOC.match(stripped)
    if doc:
        if not b.drive_link.accounts(c):return b.drive_capability(c)
        _,kind,value=doc.groups()
        c.kv_set('natural_google_choice_'+str(chat),'{}')
        if re.search(r'\.(?:docx|xlsx|pdf)\b',value,re.I):return 'Do you want a native Google Doc/Sheet or a downloadable Word/Excel/PDF file? This release creates native Google Docs and Sheets only. For a native file, ask "create a doc called sunny". Nothing created.'
        if kind.lower()=='file' and re.search(r'google docs? and',stripped,re.I):kind='doc'
        if kind.lower()=='file':return 'Should this be a Google Doc or a Google Sheet? Please name the type and title.'
        # Text after a newline or "containing:" is literal content, never generated.
        chunks=re.split(r'\s+(?:containing|with (?:the )?(?:text|content))\s*:\s*|\n',value,maxsplit=1,flags=re.I)
        title=chunks[0].strip().strip('"');title=re.sub(r'\s+(?:in|on|using|with) (?:my )?(?:personal|college)(?: (?:account|gmail))?$', '',title,flags=re.I);content=chunks[1] if len(chunks)>1 else ''
        account=None
        acc=re.search(r'\s+(?:in|using|on)\s+([\w.+-]+@[\w.-]+)\s*$',title,re.I)
        if acc:account=acc.group(1).lower();title=title[:acc.start()].strip()
        if not title or len(title)>180 or len(content)>6000:return 'Give a title up to 180 characters and exact content up to 6,000 characters.'
        if re.search(r'\b(?:share|invite|folder|send|overwrite|replace|edit|delete)\b',title,re.I):return 'This release creates new private files in My Drive only. Please give just the title and exact content; no shares or existing-file edits.'
        email,error=destination(c,chat,b,'account',stripped,account)
        if error:return error
        kind='sheet' if kind.lower() in ('sheet','spreadsheet') else 'doc'
        rows=[line.split('\t') for line in content.splitlines()] if kind=='sheet' else content
        if kind=='sheet' and (len(rows)>100 or any(len(r)>26 for r in rows)):return 'Use up to 100 rows and 26 columns, columns separated by tabs.'
        code=secrets.token_hex(4)
        c.kv_set('drive_pending_'+str(chat),json.dumps({'until':time.time()+900,'code':code,'account':email,'kind':kind,'title':title,'content':rows}))
        return 'Review NEW Google '+('Doc' if kind=='doc' else 'Sheet')+' in '+email+' My Drive:\nTitle: '+title+'\n'+('Exact content:\n'+content if content else 'Blank file, no content supplied.')+'\nPrivate basic text/data file. No sharing or existing-file changes. Nothing created yet. Reply "confirm '+code+'" or /fileconfirm '+code+' within 15 minutes.'
    task=TASK.match(stripped)
    if task:
        if not b.drive_link.accounts(c):return b.tasks_capability(c)
        c.kv_set('natural_google_choice_'+str(chat),'{}')
        title=task.group(1).strip()
        title=re.sub(r'\s+(?:in|on|using|with) (?:my )?(?:personal|college)(?: (?:account|gmail))?$', '',title,flags=re.I)
        if re.search(r'\b(?:tomorrow|today|tonight|next|monday|tuesday|wednesday|thursday|friday|saturday|sunday|at \d)\b',title,re.I):return 'For a due date, use an exact date: "add a Google task buy milk due 2026-10-20". Tasks dates have no timed alarm.'
        m=re.search(r'\s+due\s+(\d{4}-\d{2}-\d{2})$',title,re.I)
        arg=title if not m else title[:m.start()]+'\nDue: '+m.group(1)
        dest,error=destination(c,chat,b,'task list',stripped)
        return error or b.tasks_command(c,'/taskadd '+arg,chat)
    if re.fullmatch(r'(?:show|list|read) (?:my )?google tasks[.!]?',stripped,re.I):
        if not b.drive_link.accounts(c):return b.tasks_capability(c)
        dest,error=destination(c,chat,b,'task list',stripped)
        return error or b.tasks_command(c,'/tasks',chat)
    find=re.fullmatch(r'(?:find|search for) (?:my |a |the )?(?:google )?(?:doc|document|sheet|spreadsheet|file)(?: about| named| called)? (.+)',stripped,re.I)
    if find:return b.drive_command(c,'/drive '+find.group(1).strip(),chat)
    # Latest numbered file/contact results are usable without slash syntax.
    m=re.fullmatch(r'(?:open|read|summarize) (?:file |document )?(\d+)',stripped,re.I)
    if m:return b.drive_command(c,'/openfile '+m.group(1),chat)
    m=re.fullmatch(r'(?:show|open) contact (\d+)',stripped,re.I)
    if m:return b.contact_command(c,'/contact '+m.group(1),chat)
    return None
