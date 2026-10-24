"""Local helper for diagnose-1c.ps1. All input files are private metadata snapshots."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from apps.accounts.one_c_metadata import dbnames,headers,ROOTS,resolve,reference_info,attach_references

def main():
    mode,directory=sys.argv[1:3];root=Path(directory)
    index=dbnames((root/'dbnames.bin').read_bytes())
    if mode=='index':
        requests=[entry for entry in index.values() if entry['prefix'] in ROOTS]
        if len(requests)>5000:raise ValueError('More than 5000 metadata objects; reader limit exceeded.')
        (root/'requests.json').write_text(json.dumps({'objects':requests}),encoding='utf-8')
        return
    found={};infos={};errors=[]
    for identity,entry in index.items():
        if entry['prefix'] not in ROOTS:continue
        path=root/(identity+'.bin')
        if not path.exists():continue
        try:
            blob=path.read_bytes()
            found[identity]=headers(blob,index)
            infos[identity]=reference_info(blob,index,identity)
        except (ValueError,UnicodeError) as exc:errors.append({'uuid':identity,'error':str(exc)})
    schema=json.loads((root/'schema.json').read_text(encoding='utf-8-sig'))
    labels=resolve(index,found,schema,warnings=errors)
    attach_references(labels,index,infos)
    (root/'names.json').write_text(json.dumps({'labels':labels,'objects_read':len(found),'errors':errors},ensure_ascii=False),encoding='utf-8')

if __name__=='__main__':
    root=Path(sys.argv[2]) if len(sys.argv)>2 else None
    error_path=root/'reader-error.json' if root else None
    if error_path and error_path.exists():error_path.unlink()
    try:main()
    except Exception as exc:
        data={'stage':sys.argv[1] if len(sys.argv)>1 else 'startup','error_type':type(exc).__name__,'error':str(exc)[:6000]}
        if root:
            blob=root/'dbnames.bin'
            data['dbnames_bytes']=blob.stat().st_size if blob.exists() else 0
            error_path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        sys.exit(1)

