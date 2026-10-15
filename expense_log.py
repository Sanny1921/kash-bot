"""Private manual expenses. Integer minor units, no banking access or payments."""
import hashlib,json,re,secrets,time
from datetime import date,timedelta
from decimal import Decimal,InvalidOperation
import gmail_link as locking
CURRENCIES={'INR','USD','EUR','GBP'}

def amount_minor(value):
    try:a=Decimal(str(value).replace(',',''))
    except InvalidOperation:raise ValueError('invalid amount')
    if not a.is_finite() or a<=0 or a>Decimal('1000000000') or a.as_tuple().exponent < -2:raise ValueError('use a positive amount with up to 2 decimal places')
    return int(a*100)
def index(c):return json.loads(c.kv_get('expense_index') or '[]')
@locking.synchronized
def add(c,chat,entry,source):
    rid=hashlib.sha256((str(chat)+'|'+str(source)).encode()).hexdigest()[:12]
    prior=c.kv_get('expense_'+rid);ids=index(c)
    if prior:
        if rid not in ids:c.kv_set('expense_index',json.dumps(ids+[rid]))
        return json.loads(prior),True
    if len(ids)>=20000:raise ValueError('expense capacity reached')
    if entry['currency'] not in CURRENCIES:raise ValueError('unsupported currency')
    if not isinstance(entry['minor'],int) or entry['minor']<=0:raise ValueError('invalid amount')
    date.fromisoformat(entry['date'])
    record={**entry,'id':rid,'chat':str(chat),'created':time.time()}
    c.kv_set('expense_'+rid,json.dumps(record,ensure_ascii=False));c.kv_set('expense_index',json.dumps(ids+[rid]));return record,False

def entries(c,chat,start,end):
    rows=[]
    for rid in index(c):
        value=c.kv_get('expense_'+rid)
        if not value:continue
        r=json.loads(value)
        if r['chat']==str(chat) and start<=date.fromisoformat(r['date'])<end:rows.append(r)
    return sorted(rows,key=lambda r:(r['date'],r['created']),reverse=True)

def totals(rows):
    result={}
    for r in rows:result[r['currency']]=result.get(r['currency'],0)+r['minor']
    return result

def money(minor,currency):return currency+' '+str(Decimal(minor)/100 if minor%100 else minor//100)
