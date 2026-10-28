"""Review the legacy table explicitly; don't fabricate arrivals or reset history."""
from collections import defaultdict
from datetime import date
from django.contrib import messages
from django.core import signing
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404,render,redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .models import Camp,StaffingSlot
from .views import access
from . import housing


def review(company,camp,day):
    rows=StaffingSlot.objects.filter(company=company,person__isnull=False).select_related('person__employee','housing_camp','group__board').order_by('person__full_name','pk')
    by_person=defaultdict(list)
    for row in rows:by_person[row.person_id].append(row)
    current={stay.person_id:stay for stay in housing.present_stays(company,day).select_related('camp')}
    result=[]
    for slots in by_person.values():
        person=slots[0].person;on_shift=[s for s in slots if s.status=='МВ'];active=current.get(person.pk)
        eligible=[s for s in on_shift if s.housing_camp_id==camp.pk or s.camp.strip().casefold()==camp.name.strip().casefold()]
        reason='';ready=False;arrival=None
        if active:reason='Уже в '+active.camp.name
        elif not on_shift:reason='В таблице отмечен отдых или не задан статус вахты'
        elif not person.employee_id:reason='Не связан со справочником сотрудников'
        elif not person.employee.is_active:reason='Сотрудник неактивен в справочнике'
        elif not eligible:reason='Другой ВЖГ или не указан ВЖГ. Уточните источник и размещение'
        elif person.stays.exists():reason='Есть история размещения. Старый статус таблицы не заменяет новый фактический заезд'
        else:
            dates={s.arrival_date for s in eligible if s.arrival_date and s.arrival_date<=day}
            if len(dates)==1:arrival=next(iter(dates));ready=True;reason='Можно перенести заезд из таблицы'
            elif len(dates)>1:reason='Несколько разных дат заезда — требуется уточнение'
            else:reason='Нет фактической даты заезда — требуется уточнение'
        result.append({'person':person,'slots':slots,'on_shift':bool(on_shift),'current':active,'ready':ready,'arrival':arrival,'reason':reason})
    return result,{'slots':rows.count(),'people':len(by_person),'on_shift':sum(r['on_shift'] for r in result),'ready':sum(r['ready'] for r in result),'placed':sum(bool(r['current']) for r in result)}


@login_required
@require_http_methods(['GET','POST'])
def reconcile(request):
    access(request)
    data=request.POST if request.method=='POST' else request.GET
    camp=get_object_or_404(Camp,company=request.user.company,pk=data.get('camp'))
    try:day=date.fromisoformat(data.get('date',''))
    except ValueError:
        from django.utils import timezone
        day=timezone.localdate()
    rows,totals=review(request.user.company,camp,day);error=''
    source={'company':request.user.company_id,'user':request.user.pk,'camp':camp.pk,'day':str(day),
        'rows':{str(row['person'].pk):{'date':str(row['arrival']),'slots':{str(slot.pk):slot.updated_at.isoformat() for slot in row['slots']}} for row in rows if row['ready']}}
    if request.method=='POST':
        try:
            try:previous=signing.loads(request.POST.get('source_snapshot',''),salt='rotation-table-review',max_age=1800)
            except signing.BadSignature:raise ValidationError('Сверка устарела. Откройте её заново.')
            if previous!=source:raise ValidationError('Кадровые таблицы изменились. Проверьте свежую сверку перед переносом.')
            selected=set(request.POST.getlist('person'))
            allowed={str(row['person'].pk):row for row in rows if row['ready']}
            if not selected or not selected<=allowed.keys():raise ValidationError('Выберите однозначные записи из списка. Данные могли измениться; проверьте сверку.')
            with transaction.atomic():
                for pk in sorted(selected):
                    row=allowed[pk]
                    housing.move(request.user,'ARRIVAL',camp,row['arrival'],[row['person']],comment='Перенос из кадровой таблицы после сверки',snapshot=request.POST.get('snapshot'))
        except ValidationError as exc:error='; '.join(exc.messages)
        else:
            messages.success(request,f'Перенесено сотрудников: {len(selected)}. Даты заезда и история сохранены.')
            return redirect(reverse('personnel:housing')+f'?camp={camp.pk}&date={day}')
    return render(request,'personnel/housing_reconcile.html',{'camp':camp,'day':day,'rows':rows,'totals':totals,'error':error,'snapshot':housing.movement_snapshot(request.user),'source_snapshot':signing.dumps(source,salt='rotation-table-review',compress=True)})
