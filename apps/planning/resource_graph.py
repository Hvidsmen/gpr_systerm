"""Work/resource connections, with shared counts and optimistic concurrency."""
import json
from copy import deepcopy
from calendar import monthrange
from datetime import date
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from core.permissions import scope_queryset
from .models import GlobalPlanVersion, DailySchedule, ResourceMonthAllocation, DailyScheduleEntry
from . import resource_editor, resource_targets, work_resources, estimate_review


def target_token(work,item=None):
    return f'item:{work}:{item}' if item else f'work:{work}'


def owner_for(user, kind, pk):
    model=DailySchedule if kind=='smg' else GlobalPlanVersion
    return get_object_or_404(scope_queryset(model.objects.all(),user),pk=pk)


@login_required
@require_GET
def graph(request, pk, kind='gpr'):
    owner=owner_for(request.user,kind,pk)
    start,end=estimate_review.review_period(owner)
    raw=request.GET.get('month',start.strftime('%Y-%m'))
    try:
        month=date.fromisoformat(raw+'-01' if len(raw)==7 else raw).replace(day=1)
        if month>end or month.replace(day=monthrange(month.year,month.month)[1])<start:raise ValueError
    except (ValueError,TypeError):raise Http404('Месяц вне периода плана.')
    context=resource_editor.context(request.user,owner,month)
    from . import resource_links
    context['resource_editor_data']['assignments'].extend(resource_links.resources(owner,month,request.user))
    context['resource_editor_data']['table_mode']=False
    form=context['inline_resource_form']
    choices=dict(form.fields['targets'].choices)
    work_ids=set()
    if kind=='gpr':
        allocations=owner.work_allocations.filter(month=month).exclude(quantity=0,item_quantities={})
        selected={}
        for row in allocations:
            selected.setdefault(row.work_id,set()).update(row.selected_item_ids or [i['id'] for t in context['resource_targets'] if t['id']==row.work_id for i in t['items']])
            work_ids.add(row.work_id)
        visible={token for token in choices if int(token.split(':')[1]) in work_ids and (token.startswith('work:') or int(token.split(':')[2]) in selected.get(int(token.split(':')[1]),set()))}
    else:visible=set(choices)
    # Existing links stay visible even when this month's volume was removed.
    active=[a for a in context['resource_editor_data']['assignments'] if a['start_date']<=context['resource_editor_data']['end_date'] and a['end_date']>=context['resource_editor_data']['start_date']]
    linked={target_token(t['work_id'],t['work_item_id']) for a in active if a['resource_kind']!='fuel' for t in a['targets']}
    nodes=[]
    for token,label in choices.items():
        parts=token.split(':'); work=next(t for t in context['resource_targets'] if t['id']==int(parts[1]))
        node={'token':token,'label':label,'name':next((i['name'] for i in work['items'] if len(parts)==3 and i['id']==int(parts[2])),work['name']),
            'object_id':str(work.get('object_id','')),'object_name':work.get('object_name','Без комплексного объекта'),'package_id':str(work['id']) if work['kind']=='COMPOSITE' else '', 'package_name':work['name'] if work['kind']=='COMPOSITE' else '', 'group':label.rsplit(' → ',1)[0] if len(parts)==3 else 'Самостоятельные работы','planned':token in visible}
        if token in visible|linked:nodes.append(node)
    known={n['token'] for n in nodes}
    for a in active:
        for t in a['targets']:
            token=target_token(t['work_id'],t['work_item_id'])
            if token not in known and a['resource_kind']!='fuel':
                nodes.append({'token':token,'label':t['label'],'name':t['item_name'] or t['work_name'],'group':'Ранее назначенные работы','planned':False});known.add(token)
    context['resource_graph_data']={'nodes':nodes,'can_edit':context['can_prepare_resources'],
        'save_url':reverse('planning:work_resources_links_'+kind,args=[owner.pk]),'month':month.strftime('%Y-%m'),
        'locked_before':owner.locked_before.isoformat() if isinstance(owner,DailySchedule) and owner.locked_before else None}
    context['legacy_resource_count']=(owner.resource_allocations.filter(month=month).exclude(count=0,hours=0,liters=0,balance=0).count() if kind=='gpr' else owner.entries.exclude(kind='work').exclude(key__startswith='assignment:').exclude(rows=[],original=[]).count())
    context.update(graph_kind=kind,graph_month=month,graph_owner=owner,
        graph_title=f'ГПР · v{owner.version_number}' if kind=='gpr' else f'СМГ · {month:%m.%Y} · ред. {owner.revision}',
        graph_back=reverse('planning:workspace_edit',args=[owner.pk])+'?month='+month.isoformat() if kind=='gpr' and owner.workspace_id and owner.status in ['DRAFT','REJECTED'] else reverse('planning:global_detail' if kind=='gpr' else 'planning:daily_schedule_detail',args=[owner.pk]))
    return render(request,'planning/resource_graph.html',context)


@login_required
@require_POST
def links(request,pk,kind='gpr'):
    owner=owner_for(request.user,kind,pk)
    try:
        payload=json.loads(request.body)
        if not isinstance(payload,dict) or set(payload)-{'assignment_id','revision','targets'}:raise ValueError
        assignment_id=payload.get('assignment_id')
        table_key=isinstance(assignment_id,str) and assignment_id.startswith(('allocation:','entry:'))
        if type(assignment_id) is not int and not table_key:raise ValueError
        tokens=payload.get('targets')
        if not isinstance(tokens,list) or len(tokens)>500 or any(not isinstance(t,str) for t in tokens) or len(tokens)!=len(set(tokens)):raise ValueError
        with transaction.atomic():
            owner=type(owner).objects.select_for_update().get(pk=owner.pk,company=request.user.company)
            work_resources.editable(request.user,owner)
            if table_key:
                from . import resource_links
                kind_key,raw_id=assignment_id.split(':')
                query=owner.resource_allocations if kind_key=='allocation' and not isinstance(owner,DailySchedule) else owner.entries if kind_key=='entry' and isinstance(owner,DailySchedule) else None
                if query is None:raise ValueError
                source=get_object_or_404(query.select_for_update(),pk=int(raw_id))
                if kind_key=='entry' and (source.kind=='work' or source.key.startswith('assignment:')):raise ValueError
                if payload.get('revision')!=resource_links.revision(source):return JsonResponse({'error':'Ресурс уже изменён. Обновите страницу.'},status=409)
                if isinstance(owner,DailySchedule) and owner.locked_before and any(r['date']<owner.locked_before.isoformat() for r in source.rows):raise ValidationError('Связи этого ресурса зафиксированы в закрытых днях СМГ.')
                form=resource_editor.InlineAssignmentForm(owner=owner)
                allowed=dict(form.fields['targets'].choices)
                previous={target_token(w,i) for w,i in resource_links.pairs(source)}
                if any(t not in allowed and t not in previous for t in tokens):raise ValidationError('Выберите сохранённые работы этого плана.')
                pairs=[]
                for t in tokens:
                    bits=t.split(':')
                    if bits[0] not in ['work','item'] or len(bits)!=(3 if bits[0]=='item' else 2):raise ValueError
                    pairs.append((int(bits[1]),int(bits[2]) if len(bits)==3 else None))
                resource_links.replace(source,owner,pairs)
                if kind_key=='entry':
                    source.rows=resource_links.annotate_rows(deepcopy(source.rows),pairs)
                    source.metadata={**source.metadata,'work_targets':[{'work_id':w,'work_item_id':i} for w,i in pairs]};source.revision+=1
                    source.save(update_fields=['rows','metadata','revision'])
                else:source.save(update_fields=['updated_at'])
                from apps.audit.models import AuditLog
                AuditLog.objects.create(user=request.user,action='UPDATE',entity_type='planning.ResourceWorkLink',entity_id=assignment_id,new_value={'targets':pairs},comment='Изменены только связи табличного ресурса с работами')
                return JsonResponse({'assignment':resource_links.serialize(source,owner,request.user),'workflow':resource_editor.workflow_state(owner)})
            assignment=get_object_or_404(owner.resource_assignments.select_for_update(),pk=assignment_id)
            if payload.get('revision')!=assignment.updated_at.isoformat():return JsonResponse({'error':'Назначение уже изменено. Обновите страницу, чтобы получить актуальные связи.'},status=409)
            form=resource_editor.InlineAssignmentForm(owner=owner,instance=assignment)
            allowed=dict(form.fields['targets'].choices)
            previous={target_token(w,i) for w,i in resource_targets.identities(assignment)}
            if any(t not in allowed and t not in previous for t in tokens):raise ValidationError('Выберите сохранённые работы этого плана.')
            pairs=[]
            for token in tokens:
                bits=token.split(':')
                if len(bits) not in [2,3] or bits[0] not in ['work','item'] or (bits[0]=='item')!=(len(bits)==3):raise ValueError
                pairs.append((int(bits[1]),int(bits[2]) if len(bits)==3 else None))
            saved=work_resources.save(request.user,owner,assignment,targets=pairs)
            return JsonResponse({'assignment':resource_editor.serialize(saved),'workflow':resource_editor.workflow_state(owner)})
    except (ValueError,TypeError,UnicodeDecodeError):return JsonResponse({'error':'Некорректные данные связей.'},status=400)
    except ValidationError as exc:return JsonResponse({'error':' '.join(exc.messages)},status=400)
