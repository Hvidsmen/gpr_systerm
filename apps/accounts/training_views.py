import uuid
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, IntegrityError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST, require_GET
from django.views.decorators.cache import never_cache
from core.permissions import require_roles
from ops.windows_runtime import read_json
from .models import User, Role
from .training_models import TrainingCopy, TrainingTicketUse
from . import training


class TrainingForm(forms.ModelForm):
    class Meta:
        model = TrainingCopy
        fields = ['name','members','port','enabled']
        widgets = {'members': forms.CheckboxSelectMultiple}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.company = user.company
        self.fields['members'].queryset = User.objects.filter(company=user.company, is_active=True, archived_at=None, role__isnull=False).order_by('last_name','first_name','username')
        self.fields['port'].help_text = 'Отдельный порт 8101–8199 на том же сервере. Его нужно открыть для внутренней сети.'
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.CheckboxSelectMultiple, forms.CheckboxInput)):
                field.widget.attrs['class'] = 'form-control'

    def clean_port(self):
        port = self.cleaned_data['port']
        if not 8101 <= port <= 8199:
            raise forms.ValidationError('Выберите порт от 8101 до 8199.')
        return port

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if TrainingCopy.objects.filter(company=self.instance.company,name=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('В этой компании уже есть учебная группа с таким названием.')
        return name


@login_required
@never_cache
@require_http_methods(['GET','POST'])
def groups(request, pk=None):
    require_roles(request.user, {'ADMIN'})
    copy = get_object_or_404(TrainingCopy, pk=pk, company=request.user.company) if pk else None
    form = TrainingForm(request.POST or None, instance=copy, user=request.user)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            group = form.save(commit=False)
            if not group.created_by_id:
                group.created_by = request.user
            group.save(); form.save_m2m()
            training.queue(group)
        messages.success(request, 'Учебная группа сохранена. Запуск или обновление поставлены в очередь.')
        return redirect('accounts:training_list')
    copies = list(TrainingCopy.objects.filter(company=request.user.company).prefetch_related('members').order_by('name'))
    for group in copies:
        group.member_count = group.members.count()
        group.may_enter = training.permitted(request.user, group)
        group.participant_url = urljoin(settings.PORTAL_PUBLIC_URL or request.build_absolute_uri('/'), reverse('accounts:training_enter',args=[group.pk]))
    runs = [] if copy is None else copy.runs.order_by('-started_at')[:10]
    return render(request, 'accounts/training_list.html', {'copies':copies,'form':form,'editing':copy,'runs':runs,'next_reset':training.next_reset(timezone.now())})


@login_required
@require_POST
def reset(request, pk):
    require_roles(request.user, {'ADMIN'})
    copy = get_object_or_404(TrainingCopy, pk=pk, company=request.user.company)
    if request.POST.get('confirm') != 'yes':
        return HttpResponse('Подтвердите удаление учебных изменений.', status=400)
    training.queue(copy)
    messages.success(request, 'Обновление учебной базы поставлено в очередь. Основная база не изменяется.')
    return redirect('accounts:training_edit', pk=copy.pk)


@login_required
@never_cache
@require_http_methods(['GET','POST'])
def enter(request, pk):
    copy = get_object_or_404(TrainingCopy, pk=pk, company=request.user.company)
    training.require_member(request.user, copy)
    ready = training.available(copy)
    if request.method == 'POST' and ready:
        current = read_json(training.folder(copy) / 'current.json', {})
        key = (training.folder(copy) / 'ticket.key').read_text().strip()
        ticket = signing.dumps({'copy':str(copy.pk),'generation':current['generation'],
            'user':request.user.pk,'company':copy.company_id,'nonce':str(uuid.uuid4())}, key=key, salt='training-entry')
        host = urlsplit(request.build_absolute_uri('/')).hostname
        host = '['+host+']' if ':' in host else host
        origin = f'{request.scheme}://{host}:{copy.port}'
        response = render(request, 'accounts/training_handoff.html', {'target':origin+reverse('accounts:training_consume'),'ticket':ticket})
        response['Referrer-Policy'] = 'no-referrer'
        return response
    return render(request, 'accounts/training_enter.html', {'copy':copy,'ready':ready}, status=200 if ready else 503)


@csrf_exempt
@never_cache
@require_POST
def consume(request):
    # No passwords or production cookies are accepted by a training process.
    if not getattr(settings, 'TRAINING_MODE', False):
        raise PermissionDenied
    try:
        key = (settings.TRAINING_CONTROL / 'ticket.key').read_text().strip()
        data = signing.loads(request.POST.get('ticket',''), key=key, salt='training-entry', max_age=60)
        access = request.training_copy
        nonce = uuid.UUID(data['nonce'])
        if data['copy'] != access['copy'] or data['generation'] != settings.TRAINING_GENERATION or data['company'] != access['company'] or data['user'] not in access['members']:
            raise ValueError
        user = User.objects.get(pk=data['user'], company_id=access['company'])
        if not user.is_active or user.archived_at:
            raise ValueError
        with transaction.atomic():
            TrainingTicketUse.objects.create(nonce=nonce)
            User.objects.filter(pk=user.pk).update(role=Role.objects.get(code='ADMIN'), is_staff=False, is_superuser=False, access_group=None, access_group_from_ad=False)
        user.refresh_from_db()
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    except (signing.BadSignature, KeyError, ValueError, TypeError, User.DoesNotExist, IntegrityError):
        return HttpResponse('Ссылка входа истекла или уже использована. Откройте учебную копию заново из основного портала.', status=403)
    return redirect('dashboard:index')


@never_cache
@require_GET
def health(request):
    if not getattr(settings, 'TRAINING_MODE', False):
        raise PermissionDenied
    return JsonResponse({'generation':settings.TRAINING_GENERATION})
