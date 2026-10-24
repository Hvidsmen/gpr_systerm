"""Local helper for diagnose-1c.ps1. All input files are private metadata snapshots."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from apps.accounts.one_c_metadata import dbnames,headers,ROOTS,resolve

def main():
    mode,directory=sys.argv[1:3];root=Path(directory)
    index=dbnames((root/'dbnames.bin').read_bytes())
    if mode=='index':
        requests=[entry for entry in index.values() if entry['prefix'] in ROOTS]
        if len(requests)>5000:raise ValueError('More than 5000 metadata objects; reader limit exceeded.')
        (root/'requests.json').write_text(json.dumps(requests),encoding='utf-8')
        return
    found={};errors=[]
    for identity,entry in index.items():
        if entry['prefix'] not in ROOTS:continue
        path=root/(identity+'.bin')
        if not path.exists():continue
        try:found[identity]=headers(path.read_bytes(),index)
        except (ValueError,UnicodeError) as exc:errors.append({'uuid':identity,'error':str(exc)})
    schema=json.loads((root/'schema.json').read_text(encoding='utf-8-sig'))
    labels=resolve(index,found,schema)
    (root/'names.json').write_text(json.dumps({'labels':labels,'objects_read':len(found),'errors':errors},ensure_ascii=False),encoding='utf-8')

if __name__=='__main__':main()
