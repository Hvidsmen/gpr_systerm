from datetime import date
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

    def form(self,request):
        class SelectionForm(forms.Form):
            source=forms.ModelChoiceField(queryset=carryover.source_versions(self.version,self.month),label='Утвержденный план прошлого месяца')
            mode=forms.ChoiceField(choices=[('missing','Добавить отсутствующие; существующие пометить остатком')],label='Режим переноса')
        first = carryover.source_versions(self.version,self.month).first()
        return SelectionForm(request.POST if request.method=='POST' and not request.POST.get('token') else None,initial={'source':first.pk if first else None,'mode':'missing'})

    def get(self,request,pk):
        return self.display(request,self.form(request))

    def post(self,request,pk):
        form=self.form(request)
        if request.POST.get('token'):
            try:
                payload=signing.loads(request.POST['token'],salt=SALT,max_age=3600)
                if (payload['version'],payload['month'],payload['user'],payload['company'])!=(self.version.pk,self.month.isoformat(),request.user.pk,request.user.company_id):
                    raise signing.BadSignature()
                source=carryover.source_versions(self.version,self.month).get(pk=payload['source'])
                form.initial['source']=source.pk
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
                source=form.cleaned_data['source'];mode=form.cleaned_data['mode']
                rows=carryover.preview(self.version,self.month,source)
                token=signing.dumps(dict(version=self.version.pk,month=self.month.isoformat(),user=request.user.pk,company=request.user.company_id,source=source.pk,mode=mode,fingerprint=fingerprint(rows)),salt=SALT,compress=True)
                return self.display(request,form,rows=rows,token=token,mode=mode)
            except ValidationError as exc:form.add_error(None,exc)
        return self.display(request,form)

    def display(self,request,form,**context):
        start,end=carryover.previous_period(self.month)
        return render(request,'planning/carryover.html',dict(form=form,version=self.version,month=self.month,previous_month=start,**context))
