from datetime import date
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods
from core.permissions import scope_queryset
from .models import GlobalPlanVersion, DailySchedule
from . import resource_replacement as service


class ReplaceForm(forms.Form):
    target=forms.ModelChoiceField(label='Новая позиция',queryset=None,empty_label='Выберите замену',widget=forms.Select(attrs={'class':'form-select','data-replacement-target':''}))
    add_crew=forms.BooleanField(label='Добавить экипаж новой техники из справочника',required=False,initial=True)
    snapshot=forms.CharField(widget=forms.HiddenInput)
    confirmation=forms.CharField(required=False,widget=forms.HiddenInput)

    def __init__(self,*args,company,kind,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['target'].queryset=service.choices(company,kind)
        self.fields['target'].label='Техника для планирования' if kind=='equipment' else 'Должность для планирования'
        if kind!='equipment':self.fields.pop('add_crew')


@login_required
@require_http_methods(['GET','POST'])
@transaction.atomic
def replace(request,pk,source_id,kind='gpr'):
    model=DailySchedule if kind=='smg' else GlobalPlanVersion
    owner=get_object_or_404(scope_queryset(model.objects.all(),request.user).select_for_update(),pk=pk)
    try:
        service.authorize(request.user,owner)
        raw_month=request.GET.get('month')
        month=date.fromisoformat(raw_month) if raw_month else None
        selected=service.sources(owner,source_id,month)
    except (ValueError,ValidationError) as exc:
        error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Проверьте месяц.'
        return render(request,'planning/_resource_replace_form.html',{'error':error},status=400)
    source=selected[0]
    initial={'snapshot':service.token(request.user,owner,source_id,month),'add_crew':True}
    form=ReplaceForm(request.POST if request.method=='POST' else None,company=request.user.company,kind=source.kind,initial=initial)
    context={'form':form,'source_label':service.label(source),'resource_kind':source.kind,'month':month,'month_count':len(selected),'is_daily':kind=='smg'}
    if request.method=='POST' and form.is_valid():
        data=form.cleaned_data;preview=request.POST.get('action')!='apply'
        try:
            result=service.replace_resource(request.user,owner,source_id,data['target'],month=month,add_crew=data.get('add_crew',False),signed=data['snapshot'] if preview else data['confirmation'],preview=preview)
        except ValidationError as exc:form.add_error(None,exc)
        except IntegrityError:form.add_error(None,'Такая позиция уже появилась в плане. Откройте замену заново.')
        else:
            if not preview:return JsonResponse({'saved':True})
            form.data=form.data.copy()
            form.data['confirmation']=service.token(request.user,owner,source_id,month,target=data['target'].pk,add_crew=data.get('add_crew',False),target_state=service.target_stamp(data['target']))
            context['preview']=result
    return render(request,'planning/_resource_replace_form.html',context,status=400 if form.errors else 200)
