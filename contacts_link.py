"""Read-only Contacts lookup. Never message, edit or export contacts in bulk."""
import urllib.parse
import drive_link as workspace

def search(c,query):
    query=query.strip()[:100]
    if len(query)<2:raise ValueError('use at least two letters')
    rows=[];notices=[]
    for email in sorted(workspace.accounts(c)):
        try:
            token=workspace.token(c,email);base='https://people.googleapis.com/v1/people:searchContacts?'
            # Google's cache warmup. Empty query returns no contacts, not a bulk export.
            workspace.call(token,base+urllib.parse.urlencode({'query':'','readMask':'names,emailAddresses,phoneNumbers','pageSize':10}))
            data=workspace.call(token,base+urllib.parse.urlencode({'query':query,'readMask':'names,emailAddresses,phoneNumbers','pageSize':10}))
            for item in data.get('results',[]):
                person=item.get('person',{})
                rows.append({'account':email,'resource':person.get('resourceName',''),'names':[n.get('displayName','') for n in person.get('names',[])],'emails':[v.get('value','') for v in person.get('emailAddresses',[])],'phones':[v.get('canonicalForm',v.get('value','')) for v in person.get('phoneNumbers',[])]})
        except Exception:notices.append('Could not search Contacts for '+email+'. Check the suite consent with /google '+email+'.')
    return rows,notices

def get(c,email,resource):
    if not resource.startswith('people/') or '/' in resource[7:]:raise ValueError('invalid contact')
    p=workspace.call(workspace.token(c,email),'https://people.googleapis.com/v1/'+urllib.parse.quote(resource,safe='/')+'?personFields=names,emailAddresses,phoneNumbers')
    return {'account':email,'resource':resource,'names':[v.get('displayName','') for v in p.get('names',[])],'emails':[v.get('value','') for v in p.get('emailAddresses',[])],'phones':[v.get('canonicalForm',v.get('value','')) for v in p.get('phoneNumbers',[])]}
