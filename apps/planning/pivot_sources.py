"""Explicit source selection: GPR, SMG or the independent whole-object plan."""
from collections import defaultdict
from decimal import Decimal
from django import forms
from django.db.models import Sum
from core.permissions import scope_queryset
from .models import GlobalPlanVersion,DailySchedule,ObjectWorkPlan
from .report_forms import MatrixReportForm
from .report_services import Source,selected_objects

KINDS=[('gpr','ГПР'),('smg','СМГ'),('object','План на весь объект')]

class PivotSourceForm(MatrixReportForm):
    plan_type=forms.ChoiceField(label='Вид плана',choices=KINDS)
    plan_version=forms.ChoiceField(label='Версия / вариант',required=False)
    def __init__(self,*args,user,**kwargs):
        kwargs.setdefault('version_statuses',())
        super().__init__(*args,user=user,**kwargs)
        self.user=user
        for name in self.version_fields:self.fields.pop(name,None)
        self.version_fields=[]
        self.fields['mode'].widget=forms.HiddenInput()
        self.fields['consolidated'].queryset=self.fields['consolidated'].queryset.none()
        kind=self.data.get('plan_type','gpr')
        if kind=='object':
            self.fields['sections'].choices=[('works','Работы')]
            self.data=self.data.copy()
            if hasattr(self.data,'setlist'):self.data.setlist('sections',['works'])
            else:self.data['sections']=['works']
        model={'gpr':GlobalPlanVersion,'smg':DailySchedule,'object':ObjectWorkPlan}.get(kind,GlobalPlanVersion)
        versions=scope_queryset(model.objects.all(),user).select_related('construction_object')
        if model==GlobalPlanVersion:versions=versions.exclude(workspace__is_archived=True).defer('snapshot')
        project=self.data.get('project');objects=self.data.getlist('objects') if hasattr(self.data,'getlist') else self.data.get('objects',[])
        if str(project or '').isdigit():versions=versions.filter(construction_object__project_id=project)
        if objects and all(str(v).isdigit() for v in objects):versions=versions.filter(construction_object_id__in=objects)
        self.versions={str(v.pk):v for v in versions.order_by('construction_object__name','-updated_at','-pk')}
        def label(v):
            if kind=='gpr':suffix=f'{v.title or "ГПР"} · v{v.version_number} · {v.get_status_display()} · {v.start_date:%m.%Y}–{v.end_date:%m.%Y}'
            elif kind=='smg':suffix=f'{v.name or "СМГ"} · {v.month:%m.%Y} · ред. {v.revision} · {v.get_status_display()}'
            else:suffix=f'{v.name} · вариант №{v.pk}'
            return f'{v.construction_object.name} · {suffix}'
        automatic={'gpr':'Автоматически: согласованные ГПР','smg':'Автоматически: применённые СМГ','object':'Последний вариант каждого объекта'}
        from calendar import monthrange
        self.version_periods={}
        for pk,v in self.versions.items():
            if kind=='gpr':start,end=v.start_date,v.end_date
            elif kind=='smg':start=v.month;end=start.replace(day=monthrange(start.year,start.month)[1])
            else:continue
            self.version_periods[pk]={'start':str(start),'end':str(end)}
        self.fields['plan_version'].choices=[('',automatic.get(kind,automatic['gpr'])),*[(pk,label(v)) for pk,v in self.versions.items()]]
        for name in ['plan_type','plan_version']:self.fields[name].widget.attrs.update({'class':'form-select','data-native-select':''})
    def clean(self):
        values=super().clean()
        value=values.get('plan_version')
        values['selected_plan']=self.versions.get(value) if value else None
        return values


def sources(user,filters):
    selected=filters.get('selected_plan');kind=filters['plan_type'];objects=selected_objects(user,filters);ids={o.pk for o in objects};result=defaultdict(list)
    if selected and selected.construction_object_id not in ids:raise forms.ValidationError('Выбранная версия относится к другому объекту.')
    if kind=='gpr':
        from .report_services import days_between
        remaining={pk:set(days_between(filters['start'],filters['end'])) for pk in ids}
        from .report_projection import period_snapshot
        from .global_services import build_snapshot
        query=[selected] if selected else GlobalPlanVersion.objects.filter(company=user.company,construction_object_id__in=ids,status__in=['APPROVED','COMPLETED'],start_date__lte=filters['end'],end_date__gte=filters['start']).exclude(workspace__is_archived=True).order_by('-approved_at','-pk')
        for version in query:
            if not selected:
                covered={day for day in remaining[version.construction_object_id] if version.start_date<=day<=version.end_date}
                if not covered:continue
                remaining[version.construction_object_id].difference_update(covered)
            snapshot=build_snapshot(version,validate_inputs=False) if version.status in {'DRAFT','REJECTED'} else period_snapshot(version,filters['start'],filters['end'])
            result[version.construction_object_id].append(Source(version,snapshot,version.start_date,version.end_date))
    elif kind=='smg':
        from .daily_schedule import overlay_schedules,bounds
        query=[selected] if selected else DailySchedule.objects.filter(company=user.company,construction_object_id__in=ids,status='APPLIED',month__gte=filters['start'].replace(day=1),month__lte=filters['end']).select_related('source').defer('source__snapshot').prefetch_related('entries').order_by('construction_object_id','month','-revision','-pk')
        seen=set()
        for schedule in query:
            key=(schedule.construction_object_id,schedule.month)
            if key in seen:continue
            seen.add(key);start,end=bounds(schedule.month)
            snapshot=overlay_schedules({'works':[],'resources':{}},[schedule],filters['start'],filters['end'])
            labels={e.metadata.get('id'):e.label for e in schedule.entries.all() if e.kind=='work'}
            for spec in snapshot.get('works',[]):
                if not spec.get('name'):spec['name']=labels.get(spec.get('id'),'Работа')
            result[schedule.construction_object_id].append(Source(schedule,snapshot,start,end))
    return result


def object_records(user,filters,fact_report):
    from .pivot_report import records
    from apps.production.models import DailyFact
    from apps.works.prices import price_on
    from .report_services import selected_objects
    selected=filters.get('selected_plan');objects=selected_objects(user,filters);allowed={o.pk:o for o in objects}
    plans=[selected] if selected else ObjectWorkPlan.objects.filter(company=user.company,construction_object_id__in=allowed).select_related('construction_object').order_by('construction_object_id','-updated_at','-pk')
    seen=set();output=[];fact_index={}
    for record in records(fact_report):fact_index[record['row'].get('key')]=record['row']['total']
    for plan in plans:
        if plan.construction_object_id not in allowed:raise forms.ValidationError('План другого объекта.')
        if plan.construction_object_id in seen:continue
        seen.add(plan.construction_object_id)
        entries=list(plan.rows.select_related('work__section','work__complex_object','work_item').prefetch_related('work__items'))
        quantities={(r.work_id,r.work_item_id):r.quantity for r in entries}
        facts={(r['project_work_id'],r['work_item_id']):r['total'] for r in DailyFact.objects.filter(company=user.company,project_work__section__construction_object_id=plan.construction_object_id,date__gte=filters['start'],date__lte=filters['end']).values('project_work_id','work_item_id').annotate(total=Sum('actual_quantity'))}
        for entry in entries:
            work=entry.work;item=entry.work_item
            query=filters.get('works_q','').casefold()
            if query and query not in (work.name+' '+entry.label).casefold():continue
            if filters.get('work_group') and work.work_group_id!=filters['work_group'].pk:continue
            if work.kind=='COMPOSITE' and item is None:continue
            key=f'w{work.pk}-i{item.pk}' if item else f'o{plan.construction_object_id}-w{work.pk}'
            actual=fact_index.get(key,{})
            amount=None;real=None;rate=price_on(work,filters['end'])
            if item and item.quantity_per_unit>0:
                amount=entry.quantity/item.quantity_per_unit*item.weight/100*rate
                components=list(work.items.all())
                if components and sum((i.weight for i in components),Decimal(0))==100:
                    complete=min((quantities.get((work.pk,i.pk),Decimal(0))/i.quantity_per_unit for i in components if i.quantity_per_unit>0),default=Decimal(0))
                    if not work.allow_fractional:
                        from decimal import ROUND_FLOOR
                        complete=complete.to_integral_value(rounding=ROUND_FLOOR)
                    real=complete*rate*item.weight/100
            elif not item:amount=real=entry.quantity*rate
            total={**actual,'plan':entry.quantity,'fact':actual.get('fact',facts.get((work.pk,entry.work_item_id),Decimal(0))),'estimated_amount':amount,'real_amount':real}
            obj=allowed[plan.construction_object_id]
            output.append({'section':work.section.name,'building':str(obj.project)+' · '+obj.name,'complex_object':work.complex_object.name if work.complex_object_id else 'Без комплексного объекта','package':work.name if item else 'Самостоятельные работы','work':entry.label,'kind':'works','row':{'unit':entry.unit,'operation':'sum','cells':[],'total':total}})
    return output


def friendly_period(label):
    import re
    if re.fullmatch(r'\d{4}-\d{2}',str(label)):
        year,month=map(int,label.split('-'))
        names=['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь']
        if 1<=month<=12:return names[month-1]+' '+str(year)
    return label
