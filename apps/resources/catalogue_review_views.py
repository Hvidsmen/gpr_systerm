from datetime import date
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from core.permissions import (role_code, scope_queryset, require_roles, CATALOG_MANAGER_ROLES)
from apps.planning.models import GlobalPlanVersion, DailySchedule
from .models import CatalogueReviewRequest, BrigadeGroup, EquipmentCategory
from . import catalogue_review as service


def plan_owner(request, owner_kind, pk):
    if owner_kind not in {'gpr','smg'}: raise PermissionDenied
    model = DailySchedule if owner_kind == 'smg' else GlobalPlanVersion
    owner = get_object_or_404(scope_queryset(model.objects.all(), request.user), pk=pk)
    from apps.planning.work_resources import editable
    try: editable(request.user, owner)
    except ValidationError as exc: raise PermissionDenied('; '.join(exc.messages))
    return owner


class ProposalForm(forms.Form):
    name = forms.CharField(label='Название новой позиции', max_length=150)
    group = forms.ModelChoiceField(label='Группа', queryset=BrigadeGroup.objects.none(), required=False)
    months = forms.MultipleChoiceField(label='Добавить в месяцы', widget=forms.CheckboxSelectMultiple, required=False)
    def __init__(self, *args, owner, kind, **kwargs):
        super().__init__(*args, **kwargs)
        model = BrigadeGroup if kind == 'labor' else EquipmentCategory
        self.fields['group'].queryset = model.objects.filter(company_id=owner.company_id).order_by('name')
        if isinstance(owner, DailySchedule): self.fields.pop('months')
        else:
            from apps.planning.bulk_add import editable_months
            months = editable_months(owner)
            self.fields['months'].choices = [(str(m),m.strftime('%m.%Y')) for m in months]
            self.fields['months'].required = True
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxSelectMultiple): field.widget.attrs['class'] = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'


@login_required
@require_http_methods(['GET','POST'])
def propose(request, owner_kind, pk, kind):
    require_roles(request.user, {'ADMIN','PLANNER','MANAGER'})
    owner = plan_owner(request, owner_kind, pk)
    if kind not in {'labor','equipment'}: raise PermissionDenied
    initial = {}
    if isinstance(owner, GlobalPlanVersion):
        from apps.planning.bulk_add import editable_months
        months = editable_months(owner)
        preferred = request.GET.get('month')
        initial['months'] = [preferred if preferred in {str(m) for m in months} else str(owner.planning_month or months[0])]
    form = ProposalForm(request.POST if request.method == 'POST' else None, owner=owner, kind=kind, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            service.propose(request.user, owner, kind, form.cleaned_data['name'], form.cleaned_data['group'],
                            [date.fromisoformat(v) for v in form.cleaned_data.get('months',[])])
            messages.success(request, 'Новая позиция добавлена в план и направлена ответственному за справочник. Заполняйте количества; отправка на согласование доступна после проверки.')
            url = service.owner_url(owner)
            if form.cleaned_data.get('months'): url += '?month=' + form.cleaned_data['months'][0]
            return redirect(url)
        except ValidationError as exc: form.add_error(None, exc)
    return render(request, 'planning/catalogue_propose.html', {'form':form,'owner':owner,'kind':kind,
                  'title':'Создать должность' if kind == 'labor' else 'Создать технику для планирования','return_url':service.owner_url(owner)})


def queue_for(user):
    role = role_code(user)
    require_roles(user, {'ADMIN','PLANNER','MANAGER','MECHANIZATION_DISPATCHER','ALL_FACT_DISPATCHER'} | CATALOG_MANAGER_ROLES)
    query = CatalogueReviewRequest.objects.filter(company=user.company).select_related(
        'brigade','equipment_type','machine__type__parent','machine__catalogue_group','created_by','reviewed_by','target_brigade','target_equipment')
    if role in CATALOG_MANAGER_ROLES:
        query = query.filter(kind__in=['labor'] if role == service.REVIEW_ROLES['labor'] else ['equipment','machine'])
    elif role != 'ADMIN': query = query.filter(created_by=user)
    return query


@login_required
@require_http_methods(['GET'])
def review_list(request):
    query = queue_for(request.user)
    history = request.GET.get('history') == '1'
    query = query.exclude(status='PENDING') if history else query.filter(status='PENDING')
    page = Paginator(query, 30).get_page(request.GET.get('page'))
    return render(request, 'resources/catalogue_review_list.html', {'page':page,'history':history})


class DecisionForm(forms.Form):
    action = forms.ChoiceField(label='Решение', choices=[('confirm','Подтвердить новую позицию'),
                ('replace','Использовать существующую'),('reject','Отклонить')])
    target = forms.ModelChoiceField(label='Существующая позиция', queryset=BrigadeGroup.objects.none(), required=False)
    comment = forms.CharField(label='Комментарий / причина отказа', max_length=4000, required=False, widget=forms.Textarea(attrs={'rows':3}))
    revision = forms.IntegerField(widget=forms.HiddenInput)
    token = forms.CharField(required=False, widget=forms.HiddenInput)
    def __init__(self,*args,proposal,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['target'].queryset = service.confirmed_choices(proposal.company, proposal.kind)
        self.fields['target'].help_text = 'Заполняется только при замене новой позиции существующей.'
        if proposal.kind=='machine':
            self.fields['action'].choices=[('confirm','Подтвердить машину'),('reject','Отклонить')]
            self.fields.pop('target')
            self.fields['comment'].help_text='При отказе причина обязательна. История машины сохраняется, новые назначения будут отключены.'
        if proposal.kind!='machine':self.fields['comment'].help_text = 'При отказе причина обязательна. Строка и введённые значения остаются в плане.'
        for field in self.fields.values():
            if not field.widget.is_hidden: field.widget.attrs['class'] = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
    def clean(self):
        data = super().clean()
        if data.get('action') == 'replace' and not data.get('target'): self.add_error('target','Выберите существующую позицию.')
        if data.get('action') == 'reject' and not data.get('comment','').strip(): self.add_error('comment','Укажите причину отказа.')
        return data


def history_events(proposal):
    from django.utils.dateparse import parse_datetime
    labels = {'confirm':'Подтверждено', 'replace':'Заменено существующей позицией',
              'reject':'Отклонено', 'PLAN_REPLACEMENT':'Позиция заменена в плане'}
    return [{**event, 'label':labels.get(event.get('action'),'Решение'),
             'when':parse_datetime(event['at']) if event.get('at') else None} for event in proposal.history]


@login_required
@require_http_methods(['GET','POST'])
def review_detail(request, pk):
    proposal = get_object_or_404(queue_for(request.user), pk=pk)
    can_decide = role_code(request.user) in {'ADMIN',service.REVIEW_ROLES[proposal.kind]} and proposal.status == 'PENDING'
    if request.method == 'POST' and not can_decide: raise PermissionDenied
    form = DecisionForm(request.POST if request.method == 'POST' else None, proposal=proposal, initial={'revision':proposal.revision})
    preview = None
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        try:
            if data['revision'] != proposal.revision: raise ValidationError('Заявка уже изменена. Обновите страницу.')
            if data['action'] == 'replace' and request.POST.get('stage') != 'apply':
                token, owners = service.preview(request.user, proposal, data['target'])
                preview = {'token':token,'owners':owners,'target':data['target'],'summary':service.replacement_summary(proposal, data['target'], owners)}
            else:
                service.decide(request.user, proposal, data['action'], data['revision'], data.get('comment',''), data.get('target'), data.get('token'))
                messages.success(request, 'Решение сохранено. При замене ресурсы плана нужно подтвердить заново.')
                return redirect('resources:catalogue_review_detail', pk=pk)
        except ValidationError as exc: form.add_error(None, exc)
    return render(request, 'resources/catalogue_review_detail.html', {'proposal':proposal,'form':form,
                  'history_events':history_events(proposal),'can_decide':can_decide,'preview':preview,'usage':service.affected_owners(proposal),
                  'source_edit_route':'resources:equipment_unit_update' if proposal.kind=='machine' else 'resources:brigade_update' if proposal.kind=='labor' else 'resources:equipment_type_update',
                  'source_edit_url':reverse('resources:equipment_unit_update' if proposal.kind=='machine' else 'resources:brigade_update' if proposal.kind=='labor' else 'resources:equipment_type_update',args=[proposal.resource.pk])})


@login_required
@require_http_methods(['GET','POST'])
def fix(request, owner_kind, pk, request_pk):
    require_roles(request.user, {'ADMIN','PLANNER','MANAGER'})
    owner = plan_owner(request, owner_kind, pk)
    proposal = get_object_or_404(service.requests_for(owner, blocking=True), pk=request_pk)
    data = request.POST.copy() if request.method == 'POST' else None
    if data is not None: data['action'] = 'replace'
    form = DecisionForm(data, proposal=proposal, initial={'action':'replace','revision':proposal.revision})
    form.fields['action'].widget = forms.HiddenInput()
    preview = None
    if request.method == 'POST' and form.is_valid():
        try:
            target = form.cleaned_data['target']
            if request.POST.get('stage') == 'apply':
                with transaction.atomic():
                    owner = type(owner).objects.select_for_update().get(pk=owner.pk, company=request.user.company)
                    from apps.planning.work_resources import editable
                    editable(request.user, owner)
                    proposal = get_object_or_404(service.requests_for(owner, blocking=True).select_for_update(of=('self',)), pk=request_pk)
                    try: payload = service.signing.loads(form.cleaned_data['token'],salt=service.SALT,max_age=1800)
                    except service.signing.BadSignature: raise ValidationError('Выполните предпросмотр заново.')
                    expected = {'user':request.user.pk,'company':request.user.company_id,'request':proposal.pk,
                        'revision':proposal.revision,'target':target.pk,'fingerprint':service.fingerprint(proposal,[owner]),'catalogue_stamp':service.catalogue_stamp(proposal,target)}
                    if payload != expected: raise ValidationError('План или заявка изменились. Проверьте замену заново.')
                    service.validate_target(proposal, target)
                    service.replace_owner(owner, proposal, target)
                    proposal.history = [*proposal.history, {'action':'PLAN_REPLACEMENT','actor':request.user.pk,'name':request.user.full_name,'at':service.timezone.now().isoformat(),'target':target.pk,'plan':list(service.owner_key(owner))}]
                    proposal.revision += 1
                    proposal.save(update_fields=['history','revision','updated_at'])
                    service.audit(request.user, proposal, 'UPDATE', {'plan':list(service.owner_key(owner)),'replacement':target.pk})
                messages.success(request, 'Позиция в плане заменена. Количества сохранены; подтвердите ресурсы заново.')
                return redirect(service.owner_url(owner))
            service.validate_target(proposal, target)
            payload = {'user':request.user.pk,'company':request.user.company_id,'request':proposal.pk,
                'revision':proposal.revision,'target':target.pk,'fingerprint':service.fingerprint(proposal,[owner]),'catalogue_stamp':service.catalogue_stamp(proposal,target)}
            preview = {'token':service.signing.dumps(payload,salt=service.SALT),'owners':[owner],'target':target,
                       'summary':service.replacement_summary(proposal,target,[owner])}
        except ValidationError as exc: form.add_error(None,exc)
    return render(request,'resources/catalogue_review_detail.html',{'proposal':proposal,'form':form,'can_decide':True,
                  'history_events':history_events(proposal),'preview':preview,'fixing':True,'return_url':service.owner_url(owner),'usage':[owner]})
