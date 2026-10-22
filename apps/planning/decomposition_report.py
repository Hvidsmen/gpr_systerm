"""Reorder physical object / priced package / constituent without moving data."""
from calendar import monthrange
from decimal import Decimal
from django import forms
from django.shortcuts import get_object_or_404, render
from django.views import View
from django.utils import timezone
from core.permissions import require_roles, READ_ROLES, scope_queryset
from .models import GlobalPlanVersion, DailySchedule
from .report_forms import MatrixReportForm
from .report_services import build_matrix, Source

DIMENSIONS=[('complex_object','Комплексный объект'),('package','Комплекс работ'),('work','Работа')]
MONEY=['real_amount','real_fact_amount','estimated_amount','estimated_fact_amount']


class DecompositionForm(MatrixReportForm):
    level_1=forms.ChoiceField(label='Первый уровень',choices=DIMENSIONS,initial='complex_object')
    level_2=forms.ChoiceField(label='Второй уровень',choices=DIMENSIONS,initial='package')
    level_3=forms.ChoiceField(label='Третий уровень',choices=DIMENSIONS,initial='work')
    def clean(self):
        data=super().clean()
        levels=[data.get('level_'+str(i)) for i in [1,2,3]]
        if len(set(levels))!=3:self.add_error(None,'Каждый уровень должен использоваться ровно один раз.')
        return data


def atoms(report):
    result=[]
    def walk(nodes,building):
        for node in nodes:
            for package in node['rows']:
                dims=package.get('decomposition')
                if not dims:continue
                for leaf in package.get('children') or [package]:
                    result.append({'building':building,'complex_object':dims.get('complex_object_name') or ('Самостоятельные работы' if dims['kind']=='SIMPLE' else 'Без комплексного объекта'),'complex_object_id':dims.get('complex_object_id'),'package':dims['package_name'] if dims['kind']=='COMPOSITE' else 'Самостоятельные работы','work':leaf['label'],'unit':leaf['unit'],'plan':leaf['total']['plan'],'fact':leaf['total']['fact'],**{key:leaf['total'].get(key) for key in MONEY}})
            walk(node['children'],building)
    for obj in report['objects']:
        for section in obj['sections']:
            if section['kind']=='works':walk(section['groups'],str(obj['object']))
    return result


def summarize(rows):
    result={key:sum((r[key] for r in rows if r.get(key) is not None),Decimal(0)) if any(r.get(key) is not None for r in rows) else None for key in MONEY}
    units={}
    for row in rows:
        unit=units.setdefault(row['unit'],{'unit':row['unit'],'plan':Decimal(0),'fact':Decimal(0),'has_plan':False,'has_fact':False})
        for field in ['plan','fact']:
            if row[field] is not None:unit[field]+=row[field];unit['has_'+field]=True
    result['volumes']=list(units.values())
    result['complex_object_count']=len({r['complex_object_id'] for r in rows if r['complex_object_id']})
    return result


def grouped(rows,levels):
    if not levels:return []
    dimension=levels[0];groups={}
    for row in rows:
        # Work units are part of the grouping identity, never summed across units.
        key=(row[dimension].strip().casefold(),row['unit'] if dimension=='work' else '')
        groups.setdefault(key,[]).append(row)
    return [{'label':members[0][dimension]+(' · '+members[0]['unit'] if dimension=='work' else ''),'summary':summarize(members),'children':grouped(members,levels[1:]),'rows':members if len(levels)==1 else []} for key,members in sorted(groups.items())]


class DecompositionReport(View):
    kind=None
    def get(self,request,pk=None):
        require_roles(request.user,READ_ROLES)
        params=request.GET.copy();today=timezone.localdate()
        owner=None
        if self.kind:
            model=DailySchedule if self.kind=='smg' else GlobalPlanVersion
            owner=get_object_or_404(scope_queryset(model.objects.all(),request.user),pk=pk)
            from .estimate_review import review_period
            start,end=review_period(owner)
        else:start,end=today.replace(day=1),today
        defaults={'start':start.isoformat(),'end':end.isoformat(),'mode':'latest','level_1':'complex_object','level_2':'package','level_3':'work'}
        for key,value in defaults.items():params.setdefault(key,value)
        params.setlist('sections',['works'])
        form=DecompositionForm(params,user=request.user);context={'form':form,'owner':owner,'kind':self.kind}
        if form.is_valid():
            filters={**form.cleaned_data,'money_basis':'real'};overrides=None
            if owner:
                from apps.projects.models import ConstructionObject
                filters['objects']=ConstructionObject.objects.filter(pk=owner.construction_object_id,company=request.user.company)
                if isinstance(owner,DailySchedule):
                    from .daily_schedule import overlay_schedules
                    snapshot=overlay_schedules({'works':[],'resources':{}},[owner],filters['start'],filters['end'])
                    overrides={owner.construction_object_id:[Source(owner,snapshot,owner.month,owner.month.replace(day=monthrange(owner.month.year,owner.month.month)[1]))]}
                else:
                    from .global_services import build_snapshot
                    snapshot=build_snapshot(owner,validate_inputs=False) if owner.status in ['DRAFT','REJECTED'] else owner.snapshot
                    overrides={owner.construction_object_id:[Source(owner,snapshot,owner.start_date,owner.end_date)]}
            report=build_matrix(request.user,filters,source_overrides=overrides)
            rows=atoms(report)
            query=filters.get('works_q','').strip().casefold()
            if query:rows=[r for r in rows if query in ' '.join([r['work'],r['package'],r['complex_object']]).casefold()]
            levels=[filters['level_'+str(i)] for i in [1,2,3]]
            context.update(nodes=grouped(rows,levels),summary=summarize(rows),row_count=len(rows),report=report)
        return render(request,'planning/decomposition_report.html',context)
