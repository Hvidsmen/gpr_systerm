"""Project a personal cycle from actual residence without rewriting source calendars."""
from collections import Counter,defaultdict
from datetime import date,timedelta
from types import SimpleNamespace
import re


def work(code):
    try:return float(code)>0
    except (TypeError,ValueError):return code=='МВ'


def cycle_definition(pattern):
    if not pattern:return None
    key=(pattern.updated_at,pattern.name,pattern.on_days,pattern.off_days,id(pattern.calendar),id(pattern.cycle_codes))
    cached=getattr(pattern,'_rotation_cycle_cache',None)
    if cached and cached[0]==key:return cached[1]
    result=_cycle_definition(pattern)
    pattern._rotation_cycle_cache=(key,result)
    return result


def _cycle_definition(pattern):
    if not pattern:return None
    on,off=pattern.on_days,pattern.off_days
    if not on or not off:
        found=re.search(r'(?<!\d)(\d{1,3})\s*[/\\]\s*(\d{1,3})(?!\d)',pattern.name)
        if not found:return None
        on,off=map(int,found.groups())
    if not (1<=on<=365 and 1<=off<=365):return None
    length=on+off
    if len(pattern.cycle_codes)==length:codes=list(pattern.cycle_codes)
    else:
        cal=pattern.calendar;codes=None
        # Locate the first complete work phase after rest or an incoming trip.
        candidates=[]
        for key,value in sorted(cal.items()):
            if not (work(value) or value=='В'):continue
            current=date.fromisoformat(key);previous=cal.get(str(current-timedelta(days=1)))
            following=current
            while cal.get(str(following))=='В':following+=timedelta(days=1)
            if previous in {'ДП','МО'} and work(cal.get(str(following))):candidates.append(current)
        for candidate in candidates:
            following=next((item for item in candidates if item>candidate),None)
            observed=(following-candidate).days if following and (following-candidate).days<=730 else length
            values=[cal.get(str(candidate+timedelta(days=i))) for i in range(observed)]
            if not all(value is not None and value!='' for value in values):continue
            values=[str(v) for v in values]
            split=next((i for i,code in enumerate(values) if code in {'ДП','МО'}),None)
            if not split:continue
            working,rest=values[:split],values[split:]
            end=next((i for i,code in enumerate(rest) if work(code)),len(rest));rest=rest[:end]
            if 'МО' not in rest:continue
            # Imported calendars sometimes stretch a named 45/45 phase around
            # holidays. Actual movements use the stated lengths while keeping
            # the source's work codes, weekends and travel at phase boundaries.
            working=[working[i%len(working)] for i in range(on)]
            if len(rest)<off:
                tail=len(rest)
                while tail and rest[tail-1]=='ДП':tail-=1
                rest=rest[:tail]+['МО']*(off-len(rest))+rest[tail:]
            elif len(rest)>off:
                remove=len(rest)-off;discard=set()
                for preferred in ['МО','В','ДП']:
                    for i,code in enumerate(rest):
                        if code==preferred and remove:discard.add(i);remove-=1
                rest=[code for i,code in enumerate(rest) if i not in discard][:off]
            codes=working+rest
            break
        if codes is None:
            hours=Counter(str(v) for v in cal.values() if work(v)).most_common(1)
            codes=[hours[0][0] if hours else 'МВ']*on+['МО']*off
    return {'on_days':on,'off_days':off,'codes':codes}


def stays_by_person(company):
    from .models import CampStay
    grouped=defaultdict(list)
    for stay in CampStay.objects.filter(company=company,cancelled_at=None).order_by('arrived_on','pk'):
        grouped[stay.person_id].append(stay)
    from .models import HousingEvent
    for event in HousingEvent.objects.filter(company=company,kind='DEPARTURE',stay=None):
        grouped[event.person_id].append(SimpleNamespace(initial_departure_date=event.date,arrived_on=event.date,departed_on=event.date,cancelled_at=None,pk=event.pk))
    return grouped


def anchors(stays):
    """A continuous camp-to-camp transfer keeps the original shift start."""
    stays=list(stays)
    initial=[(s.initial_departure_date,'DEPARTURE') for s in stays if hasattr(s,'initial_departure_date')]
    intervals=[]
    for stay in sorted((s for s in stays if not s.cancelled_at and not hasattr(s,'initial_departure_date')),key=lambda s:(s.arrived_on,s.pk)):
        if intervals and intervals[-1][1]==stay.arrived_on:
            intervals[-1][1]=stay.departed_on
        else:intervals.append([stay.arrived_on,stay.departed_on])
    result=list(initial)
    for arrival,departure in intervals:
        result.append((arrival,'ARRIVAL'))
        if departure:result.append((departure,'DEPARTURE'))
    return sorted(result,key=lambda item:(item[0],item[1]=='DEPARTURE'))


def anchor_for(stays,day):
    eligible=[item for item in anchors(stays) if item[0]<=day]
    return eligible[-1] if eligible else None


def description(slot,day,stays=None):
    cycle=cycle_definition(slot.pattern) if slot and slot.pattern_id else None
    if not cycle:return 'Цикл не задан: укажите дни вахты и отдыха в графике сотрудника.'
    if stays is None:
        from .models import CampStay
        stays=stays_by_person(slot.company).get(slot.person_id,[])
    anchor=anchor_for(stays,day)
    label=f"{cycle['on_days']}/{cycle['off_days']}"
    if not anchor:return label+' · календарь из таблицы; фактический заезд или выезд не оформлен.'
    return label+f" · от фактического {'заезда' if anchor[1]=='ARRIVAL' else 'выезда'} {anchor[0]:%d.%m.%Y}"


def project(slot,base,*,start=None,end=None,stays=None):
    if not slot.pattern_id:return base,None
    cycle=cycle_definition(slot.pattern)
    if not cycle:return base,None
    if stays is None:
        from .models import CampStay
        stays=stays_by_person(slot.company).get(slot.person_id,[]) if slot.person_id else []
    timeline=anchors(stays)
    from django.utils import timezone
    start=start or timezone.localdate().replace(day=1)
    end=end or start+timedelta(days=730)
    result=dict(base)
    if not timeline:
        # Extend the source plan without turning an initial balance into a fact.
        anchor=slot.departure_date if slot.status=='МО' else slot.arrival_date
        offset=cycle['on_days'] if slot.status=='МО' else 0
        if not anchor:
            candidates=[date.fromisoformat(key) for key,value in base.items() if work(value) and base.get(str(date.fromisoformat(key)-timedelta(days=1))) in {'МО','ДП'}]
            anchor=max(candidates) if candidates else None;offset=0
        if anchor:
            current=max(start,anchor)
            while current<=end:
                result.setdefault(str(current),cycle['codes'][((current-anchor).days+offset)%len(cycle['codes'])]);current+=timedelta(days=1)
        result.update(slot.calendar)
        return result,None
    for index,(anchor,kind) in enumerate(timeline):
        stop=min(end,timeline[index+1][0]-timedelta(days=1)) if index+1<len(timeline) else end
        offset=0 if kind=='ARRIVAL' else cycle['on_days']
        # The first cycle retains the original missed deadline; the requested
        # horizon supplies current/future days without expanding years of history.
        for begin,finish in [(anchor,min(stop,anchor+timedelta(days=len(cycle['codes'])))),(max(start,anchor),stop)]:
            current=begin
            while current<=finish:
                result[str(current)]=cycle['codes'][((current-anchor).days+offset)%len(cycle['codes'])]
                current+=timedelta(days=1)
    result.update(slot.calendar)
    applicable=[item for item in timeline if item[0]<=start]
    latest=applicable[-1] if applicable else timeline[0]
    return result,{'date':str(latest[0]),'kind':latest[1],'on_days':cycle['on_days'],'off_days':cycle['off_days']}
