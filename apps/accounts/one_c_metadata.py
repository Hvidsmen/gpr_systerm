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
    try:
        identity=UUID(value) if isinstance(value,str) else None
        return str(identity) if identity and identity.int else None
    except (ValueError,TypeError,AttributeError):return None

def variants(entry):
    return entry.get('variants') or [{'prefix':entry['prefix'],'code':entry['code']},*entry.get('aliases',[])]

def dbnames(data):
    grouped={}
    for node in walk(parse(unpack(data))):
        if len(node)!=3 or not guid(node[0]) or not isinstance(node[1],str):continue
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*',node[1]) or not isinstance(node[2],str) or not node[2].isdigit():continue
        code=int(node[2])
        if code>0:grouped.setdefault(guid(node[0]),set()).add((node[1],code))
    result={}
    for identity,items in grouped.items():
        # Keep every binding. Empty UUIDs are service entries, not metadata objects.
        ordered=sorted(items,key=lambda item:(0 if item[0] in ROOTS else 1 if item[0] in {'Fld','VT'} else 2,item[0],item[1]))
        entries=[{'prefix':prefix,'code':code} for prefix,code in ordered]
        result[identity]={'uuid':identity,**entries[0],'aliases':entries[1:],'variants':entries}
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

def resolve(index,object_headers,schema,warnings=None):
    warnings=warnings if warnings is not None else []
    available={};families={};by_table={}
    for field in schema:available.setdefault(field['schema']+'.'+field['table'],set()).add(field['column'])
    for key in available:
        table=key.split('.',1)[1];by_table.setdefault(table,set()).add(key)
        family=re.match(r'^(_[A-Za-z]+\d+)(?:_|$)',table)
        if family:families.setdefault(family[1],set()).add(key)
    result={};conflicts=set()
    for owner_uuid,names in object_headers.items():
        if owner_uuid not in names or owner_uuid not in index:continue
        bindings=variants(index[owner_uuid])
        roots=[v for v in bindings if v['prefix'] in ROOTS and ('_'+v['prefix']+str(v['code'])) in families]
        if len(roots)!=1:
            if len(roots)>1:warnings.append({'uuid':owner_uuid,'error':'Several main SQL tables match this UUID; object left unnamed: '+', '.join('_'+v['prefix']+str(v['code']) for v in roots)})
            continue
        owner=roots[0];physical='_'+owner['prefix']+str(owner['code'])
        logical=ROOTS[owner['prefix']]+'.'+names[owner_uuid]
        keys=set(families.get(physical,()))
        for binding in bindings:
            if binding['prefix'] not in ROOTS and binding['prefix'] not in {'Fld','VT'}:keys.update(by_table.get('_'+binding['prefix']+str(binding['code']),()))
        # Properties are resolved only within this owner's metadata and existing SQL columns.
        field_names={}
        for identity,name in names.items():
            for binding in variants(index[identity]):
                if binding['prefix']=='Fld':field_names.setdefault('_Fld'+str(binding['code']),set()).add((identity,name))
        for key in sorted(keys):
            table=key.split('.',1)[1];label=logical
            part=re.search(r'_VT(\d+)(?:_|$)',table)
            if part:
                candidates=[identity for identity in names if any(v['prefix']=='VT' and str(v['code'])==part[1] for v in variants(index[identity]))]
                if len(candidates)!=1:
                    warnings.append({'uuid':owner_uuid,'error':'Table part is not uniquely mapped: '+key});continue
                label+='.'+names[candidates[0]]
            elif table!=physical:label+=' · '+table
            fields={}
            for column in available[key]:
                match=re.fullmatch(r'(_Fld\d+)(?:RRef|TRef|RRRef|_TYPE|_S|_N|_L|_T|_B|_RTRef|_RRRef)?',column)
                candidates=field_names.get(match[1],set()) if match else set()
                if len(candidates)==1:fields[column]=next(iter(candidates))[1]
                elif len(candidates)>1:warnings.append({'uuid':owner_uuid,'error':'Field has conflicting metadata UUIDs: '+key+'.'+column})
            if key in conflicts:continue
            if key in result and result[key]['uuid']!=owner_uuid:
                warnings.append({'error':'SQL table has conflicting metadata UUIDs: '+key});result.pop(key);conflicts.add(key);continue
            result[key]={'name':label,'columns':fields,'source':'Метаданные SQL: Params.DBNames + Config','uuid':owner_uuid}
    return result
