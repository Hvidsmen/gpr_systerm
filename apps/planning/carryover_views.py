from datetime import date
from django.utils import timezone
from datetime import timedelta
import hashlib
import json
from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views import View
from core.permissions import require_roles, PLAN_ROLES
from .workspace_views import WorkspaceEdit
from . import carryover

SALT='planning.work-carryover.v1'


def fingerprint(rows):
    return hashlib.sha256(json.dumps([{k:v for k,v in row.items() if k!='spec'} for row in rows],sort_keys=True).encode()).hexdigest()


class CarryoverView(View):
    def dispatch(self,request,*args,**kwargs):
        require_roles(request.user,PLAN_ROLES)
        self.version,self.month,_=WorkspaceEdit().setup_version(request,kwargs['pk'])
        return super().dispatch(request,*args,**kwargs)

    def resolve_source(self,value):
        from .macro_carryover import history_links
        if value=='history':
            if not history_links(self.version,self.month).exists():raise ValidationError('Подключенная история недоступна для этого месяца.')
            return None
        return carryover.source_versions(self.version,self.month).get(pk=value)

    def form(self,request):
        from .macro_carryover import history_links
        links=list(history_links(self.version,self.month))
        linked={link.review.version_id:link for link in links}
        choices=[]
        if links:choices.append(('history','Подключенная история: '+', '.join(link.construction_object.name for link in links)))
        for source in carryover.source_versions(self.version,self.month):
            link=linked.get(source.pk)
            label=f'{source.construction_object} · v{source.version_number}'
            if link:label+=f' · подключенный утвержденный раунд {link.review.number}'
            choices.append((str(source.pk),label))
        class SelectionForm(forms.Form):
            source=forms.ChoiceField(choices=[('','Выберите значение'),*choices],label='Утвержденный план прошлого месяца / подключенная история')
            mode=forms.ChoiceField(choices=[('missing','Добавить отсутствующие; существующие пометить остатком')],label='Режим переноса')
        return SelectionForm(request.POST if request.method=='POST' and not request.POST.get('token') else None,initial={'source':choices[0][0] if choices else None,'mode':'missing'})

    def get(self,request,pk):
        return self.display(request,self.form(request))

    def post(self,request,pk):
        form=self.form(request)
        if request.POST.get('token'):
            try:
                payload=signing.loads(request.POST['token'],salt=SALT,max_age=3600)
                if (payload['version'],payload['month'],payload['user'],payload['company'])!=(self.version.pk,self.month.isoformat(),request.user.pk,request.user.company_id):
                    raise signing.BadSignature()
                source=self.resolve_source(payload['source'])
                form.initial['source']=payload['source']
                rows=carryover.preview(self.version,self.month,source)
                if fingerprint(rows)!=payload['fingerprint']:
                    raise ValidationError('План, факт или остатки изменились. Сформируйте предварительный просмотр заново.')
                selected=set(request.POST.getlist('selected'))
                if not selected:
                    raise ValidationError('Выберите хотя бы одну работу.')
                count=carryover.apply(request.user,self.version,self.month,source,payload['mode'],selected)
                messages.success(request,f'Обработано работ: {count}. Отсутствующие добавлены; у существующих сохранена пометка остатка.')
                return redirect(reverse('planning:workspace_edit',args=[self.version.pk])+'?month='+self.month.isoformat())
            except (signing.BadSignature,ValidationError,carryover.GlobalPlanVersion.DoesNotExist) as exc:
                messages.error(request,'Предварительный просмотр устарел.' if isinstance(exc,signing.BadSignature) else '; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Исходная версия не найдена.')
                return self.display(request,form)
        if form.is_valid():
            try:
                source_key=form.cleaned_data['source'];source=self.resolve_source(source_key);mode=form.cleaned_data['mode']
                rows=carryover.preview(self.version,self.month,source)
                token=signing.dumps(dict(version=self.version.pk,month=self.month.isoformat(),user=request.user.pk,company=request.user.company_id,source=source_key,mode=mode,fingerprint=fingerprint(rows)),salt=SALT,compress=True)
                return self.display(request,form,rows=rows,token=token,mode=mode)
            except ValidationError as exc:form.add_error(None,exc)
        return self.display(request,form)

    def display(self,request,form,**context):
        start,end=carryover.previous_period(self.month)
        forecast=timezone.localdate()<=end
        cutoff=min(end,timezone.localdate()-timedelta(days=1)) if forecast else end
        return render(request,'planning/carryover.html',dict(forecast=forecast,cutoff=cutoff,form=form,version=self.version,month=self.month,previous_month=start,**context))
