"""Bounded, deterministic suggestions; never merge by name automatically."""
import re
from collections import defaultdict,Counter
from difflib import SequenceMatcher


def suggestions(works,limit=20):
    works=list(works)
    names={w.pk:' '.join(re.findall(r'[\w]+',w.name.casefold().replace('ё','е'))) for w in works}
    tokens={key:set(value.split()) for key,value in names.items()}
    index=defaultdict(list)
    for w in works:
        for token in tokens[w.pk]:
            if len(token)>=3:index[(w.unit,w.kind,token)].append(w.pk)
    lookup={w.pk:w for w in works};pairs={}
    for w in works:
        candidates=Counter()
        for token in tokens[w.pk]:
            members=index.get((w.unit,w.kind,token),[])
            if not members or len(members)>100:continue
            for key in members:
                if key!=w.pk:candidates[key]+=1/len(members)
        for key,_ in candidates.most_common(40):
            pair=tuple(sorted((w.pk,key)))
            if pair in pairs:continue
            ratio=SequenceMatcher(None,names[w.pk],names[key],autojunk=False).ratio()
            overlap=len(tokens[w.pk]&tokens[key])/max(1,len(tokens[w.pk]|tokens[key]))
            score=.7*ratio+.3*overlap
            if ratio>=.68 and score>=.64:pairs[pair]=score
    return [{'first':lookup[a],'second':lookup[b],'score':round(score*100)} for (a,b),score in sorted(pairs.items(),key=lambda row:(-row[1],row[0]))[:limit]]
