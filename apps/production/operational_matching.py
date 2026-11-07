from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from core.permissions import PLAN_ROLES, require_roles, scope_queryset
from apps.works.models import ProjectWork, ProjectWorkItem
from .models import DailyFact, OperationalWorkMatch, OperationalWorkMatchRule


def normalize(value):
    return ' '.join((value or '').strip().casefold().split())


def target_unit(work, item):
    return (item.unit if item else work.unit).strip()


def resolve_target(user, token, obj):
    try: kind, raw_pk = token.split(':', 1);pk = int(raw_pk)
    except (AttributeError, TypeError, ValueError): raise ValidationError('Выберите работу справочника.')
    works = scope_queryset(ProjectWork.objects.filter(company=user.company, merged_source__isnull=True, section__construction_object=obj), user)
    if kind == 'work':
        work = get_object_or_404(works, pk=pk)
        if work.kind == 'COMPOSITE':raise ValidationError('Для комплекса выберите конкретную работу из его состава.')
        return work, None
    if kind == 'item':
        item = get_object_or_404(ProjectWorkItem.objects.select_related('project_work').filter(company=user.company, project_work__in=works), pk=pk)
        return item.project_work, item
    raise ValidationError('Выберите работу справочника.')


def fact_scope(user):
    return scope_queryset(DailyFact.objects.filter(company=user.company, operational_key__isnull=False).select_related(
        'project_work__section__construction_object', 'reported_by', 'operational_match__target_work', 'operational_match__target_item',
    ), user)


def candidates(user, obj=None):
    works = scope_queryset(ProjectWork.objects.filter(company=user.company, merged_source__isnull=True).select_related(
        'section__construction_object', 'complex_object').prefetch_related('items'), user)
    if obj:works=works.filter(section__construction_object=obj)
    result=[]
    for work in works.order_by('section__construction_object__name','section__name','name'):
        if work.kind == 'SIMPLE':result.append({'token':f'work:{work.pk}','label':work.name,'unit':work.unit,'path':str(work.section),
            'complex_object':'Без комплексного объекта','complex':'Самостоятельные работы','member':work.name})
        else:
            complex_object=work.complex_object.name if work.complex_object_id else 'Без комплексного объекта'
            for item in work.items.all():result.append({'token':f'item:{item.pk}','label':item.name,'unit':item.unit,'path':f'{work.section} → {work.name}',
                'complex_object':complex_object,'complex':work.name,'member':item.name})
    return result


@login_required
@require_http_methods(['GET','POST'])
def matches(request):
    require_roles(request.user, PLAN_ROLES)
    facts=fact_scope(request.user)
    selected=None
    fact_id=request.GET.get('fact') or request.POST.get('fact')
    if fact_id:
        selected=get_object_or_404(facts,pk=fact_id)
    if request.method=='POST':
        if request.POST.get('action')=='remove':
            if not selected:return HttpResponseBadRequest('Выберите прочую работу.')
            OperationalWorkMatch.objects.filter(company=request.user.company,fact=selected).delete()
            messages.success(request,'Соответствие снято. Исходный факт сохранён.')
        else:
            if not selected:return HttpResponseBadRequest('Выберите прочую работу.')
            obj=selected.project_work.section.construction_object
            try:work,item=resolve_target(request.user,request.POST.get('target'),obj)
            except ValidationError as exc:
                messages.error(request,'; '.join(exc.messages));return redirect(reverse('production:operational_matches')+f'?fact={selected.pk}')
            group=request.POST.get('apply_group')=='1'
            remember=request.POST.get('remember_future')=='1'
            targets=facts.filter(project_work__section__construction_object=obj)
            if group:
                targets=targets.filter(operational_name__iexact=selected.operational_name.strip())
            else:targets=targets.filter(pk=selected.pk)
            with transaction.atomic():
                count=0
                for fact in targets:
                    OperationalWorkMatch.objects.update_or_create(company=request.user.company,fact=fact,defaults={'target_work':work,'target_item':item,'matched_by':request.user});count+=1
                if remember:
                    OperationalWorkMatchRule.objects.update_or_create(company=request.user.company,construction_object=obj,
                        normalized_name=normalize(selected.operational_name),
                        defaults={'target_work':work,'target_item':item,'created_by':request.user})
            messages.success(request,f'Соответствие установлено для записей: {count}.')
        return redirect('production:operational_matches')
    unmatched=facts.filter(operational_match__isnull=True).order_by('-date','project_work__section__construction_object__name','operational_name')
    return render(request,'production/operational_matches.html',{'facts':unmatched[:500],'selected':selected,
        'candidates':candidates(request.user,selected.project_work.section.construction_object if selected else None) if selected else [],
        'matched_count':facts.filter(operational_match__isnull=False).count()})


def apply_rule(sender, instance, created=False, raw=False, **kwargs):
    if raw or not instance.operational_key or hasattr(instance,'operational_match'):return
    obj=instance.project_work.section.construction_object
    rule=OperationalWorkMatchRule.objects.filter(company=instance.company,construction_object=obj,
        normalized_name=normalize(instance.operational_name)).first()
    if rule:
        OperationalWorkMatch.objects.get_or_create(company=instance.company,fact=instance,defaults={
            'target_work':rule.target_work,'target_item':rule.target_item,'matched_by':rule.created_by})
