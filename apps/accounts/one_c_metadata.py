"""Read a supported subset of 1C serialized metadata, without executing code.

Format references: DaJet ConfigFileReader/DbNamesParser (zhichkin/dajet).
This is an independent limited reader, not a full configuration implementation.
"""
import re
import zlib
from uuid import UUID

MAX_BYTES=8*1024*1024
ROOTS={'Reference':'Справочник','Document':'Документ','Enum':'Перечисление','InfoRg':'РегистрСведений','AccumRg':'РегистрНакопления','AccRg':'РегистрБухгалтерии','CalcRg':'РегистрРасчета','Chrc':'ПланВидовХарактеристик','Acc':'ПланСчетов','Calc':'ПланВидовРасчета','BP':'БизнесПроцесс','BPr':'БизнесПроцесс','Node':'ПланОбмена','Task':'Задача','Const':'Константа','DocumentJournal':'ЖурналДокументов','Sequence':'Последовательность'}

def unpack(data):
    if len(data)>MAX_BYTES:raise ValueError('Metadata file exceeds 8 MB.')
    if data.startswith(b'\xef\xbb\xbf') or data.lstrip().startswith(b'{'):raw=data
    else:
        decoder=zlib.decompressobj(-15)
        try:raw=decoder.decompress(data,MAX_BYTES+1)
        except zlib.error as exc:raise ValueError('Unsupported compressed metadata format.') from exc
        if len(raw)>MAX_BYTES or decoder.unconsumed_tail or not decoder.eof:raise ValueError('Unsupported or oversized compressed metadata.')
    return raw.decode('utf-8-sig')

def parse(text):
    # Empty values and doubled quotes are part of the 1C format.
    i=0;nodes=0;size=len(text)
    def space():
        nonlocal i
        while i<size and text[i].isspace():i+=1
    def value(depth=0):
        nonlocal i,nodes
        nodes+=1
        if depth>150 or nodes>1000000:raise ValueError('Metadata nesting/size limit exceeded.')
        space()
        if i>=size:raise ValueError('Unexpected end of metadata.')
        if text[i]=='{':
            i+=1;result=[];space()
            if i<size and text[i]=='}':i+=1;return result
            while True:
                space()
                if i>=size:raise ValueError('Unclosed metadata object.')
                if text[i] in ',}':result.append('')
                else:result.append(value(depth+1))
                space()
                if i<size and text[i]=='}':i+=1;return result
                if i>=size or text[i]!=',':raise ValueError('Invalid metadata separator.')
                i+=1
        if text[i]=='"':
            i+=1;chars=[]
            while i<size:
                if text[i]=='"':
                    i+=1
                    if i<size and text[i]=='"':chars.append('"');i+=1;continue
                    return ''.join(chars)
                chars.append(text[i]);i+=1
            raise ValueError('Unclosed metadata string.')
        start=i
        while i<size and text[i] not in ',}':i+=1
        return text[start:i].strip()
    result=value();space()
    if i!=size:raise ValueError('Trailing metadata content.')
    return result

def walk(tree):
    stack=[tree]
    while stack:
        node=stack.pop()
        if not isinstance(node,list):continue
        yield node
        stack.extend(reversed([child for child in node if isinstance(child,list)]))

def guid(value):
    try:return str(UUID(value)) if isinstance(value,str) else None
    except (ValueError,TypeError,AttributeError):return None

def dbnames(data):
    result={}
    for node in walk(parse(unpack(data))):
        if len(node)!=3 or not guid(node[0]) or not isinstance(node[1],str):continue
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*',node[1]) or not isinstance(node[2],str) or not node[2].isdigit():continue
        code=int(node[2])
        if code>0:
            key=guid(node[0]);entry={'uuid':key,'prefix':node[1],'code':code}
            if key not in result:
                result[key]={**entry,'aliases':[]}
            elif any(result[key].get(k)!=entry[k] for k in ['prefix','code']):
                main=set(ROOTS)|{'Fld','VT'}
                old=result[key]
                if entry['prefix'] in main and old['prefix'] not in main:
                    result[key]={**entry,'aliases':[{'prefix':old['prefix'],'code':old['code']},*old['aliases']]}
                elif entry['prefix'] in main and old['prefix'] in main:
                    raise ValueError('Ambiguous main object in DBNames.')
                else:old['aliases'].append({'prefix':entry['prefix'],'code':entry['code']})
    if not result:raise ValueError('DBNames format is not recognized.')
    return result

def headers(data,index):
    names={}
    for node in walk(parse(unpack(data))):
        if len(node)<3 or not isinstance(node[1],list) or len(node[1])<3:continue
        identity=guid(node[1][2])
        if identity not in index or not isinstance(node[2],str) or not re.fullmatch(r'[^\W\d]\w*',node[2],re.UNICODE):continue
        if identity in names and names[identity]!=node[2]:raise ValueError('Ambiguous metadata object name.')
        names[identity]=node[2]
    return names

def resolve(index,object_headers,schema):
    available={}
    for field in schema:available.setdefault(field['schema']+'.'+field['table'],set()).add(field['column'])
    result={}
    for owner_uuid,names in object_headers.items():
        owner=index.get(owner_uuid,{})
        if owner.get('prefix') not in ROOTS or owner_uuid not in names:continue
        physical='_'+owner['prefix']+str(owner['code'])
        logical=ROOTS[owner['prefix']]+'.'+names[owner_uuid]
        # Child identifiers must be in this owner's metadata, never joined globally by guessed numbers.
        for key,columns in available.items():
            schema_name,table=key.split('.',1)
            variants={physical,*('_'+a['prefix']+str(a['code']) for a in owner.get('aliases',[]))}
            if not (table in variants or table.startswith(physical+'_')):continue
            label=logical
            part=re.search(r'_VT(\d+)(?:_|$)',table)
            if part:
                candidates=[identity for identity in names if index[identity]['prefix']=='VT' and str(index[identity]['code'])==part[1]]
                if len(candidates)!=1:continue
                label+='.'+names[candidates[0]]
            elif table!=physical:
                # Totals/change-tracking tables retain their physical suffix explicitly.
                label+=' · '+table
            fields={}
            for identity,name in names.items():
                entry=index[identity]
                if entry['prefix']!='Fld':continue
                base='_Fld'+str(entry['code'])
                for column in columns:
                    if column==base or re.fullmatch(re.escape(base)+r'(?:RRef|TRef|RRRef|_TYPE|_S|_N|_L|_T|_B|_RTRef|_RRRef)',column):fields[column]=name
            result[key]={'name':label,'columns':fields,'source':'Метаданные SQL: Params.DBNames + Config','uuid':owner_uuid}
    return result
