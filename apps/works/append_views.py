from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect
from django.views import View
from core.permissions import PLAN_ROLES, require_roles, scope_queryset
from .models import ProjectWork
from .merge_views import MergeForm
from .merge_service import source_works
from .append_service import validate_target, append_preview, apply_append

SALT='append-composite-preview-v1'


class AppendForm(MergeForm):
    def __init__(self,*args,user,works,**kwargs):
        super().__init__(*args,company=user.company,works=works,**kwargs)
        self.user=user;self.works=works
        for key in ['name','section','unit','work_group','allow_fractional']:
            self.fields.pop(key)
        self.fields['target']=forms.ModelChoiceField(label='Добавить в комплекс',queryset=scope_queryset(ProjectWork.objects.filter(kind='COMPOSITE',merged_source__isnull=True,section__construction_object_id=works[0].section.construction_object_id),user).select_related('section'),widget=forms.Select(attrs={'class':'form-select'}))
        self.fields['target'].label_from_instance=lambda w:f'{w.name} · {w.unit} · {w.section.name} · №{w.pk}'
        self.order_fields(['target',*self.fields])

    def clean(self):
        values=super().clean()
        target=values.get('target')
        if target:
            try:
                target=validate_target(self.user,target.pk,self.works)
                values.update(section=target.section,name=target.name,unit=target.unit,allow_fractional=target.allow_fractional,work_group=target.work_group)
            except ValidationError as exc:self.add_error('target',exc)
        return values


class WorkAppendView(View):
    def get(self,request):
        require_roles(request.user,PLAN_ROLES)
        messages.info(request,'Выберите самостоятельные работы в справочнике и нажмите «Добавить в комплекс».')
        return redirect('works:work_list')

    def post(self,request):
        require_roles(request.user,PLAN_ROLES)
        try:
            if request.POST.get('stage')=='confirm':
                payload=signing.loads(request.POST.get('preview',''),salt=SALT,max_age=3600)
                if payload['actor']!=request.user.pk or payload['company']!=request.user.company_id:raise signing.BadSignature
                works=source_works(request.user,payload['ids'],minimum=1)
                form=AppendForm(payload['data'],user=request.user,works=works)
                if not form.is_valid():raise ValidationError('Настройки изменились. Выполните просмотр заново.')
                target,revisions=apply_append(request.user,payload['ids'],form.cleaned_data,payload['fingerprint'])
                messages.success(request,f'В комплекс «{target.name}» добавлено работ: {len(works)}. Факт и объёмы сохранены. Новых черновиков ГПР: {len(revisions)}; проверьте и согласуйте их.')
                return redirect('works:work_detail',pk=target.pk)
            works=source_works(request.user,request.POST.getlist('selected'),minimum=1)
            form=AppendForm(request.POST if request.POST.get('stage')=='preview' else None,user=request.user,works=works)
            context={'form':form,'sources':works,'norm_rows':form.norm_rows}
            if form.is_bound and form.is_valid():
                report=append_preview(request.user,works,form.cleaned_data)
                context['report']=report
                context['preview']=signing.dumps({'actor':request.user.pk,'company':request.user.company_id,'ids':[w.pk for w in works],'data':{key:value for key,value in request.POST.items() if key not in {'csrfmiddlewaretoken','selected','stage'}},'fingerprint':report['fingerprint']},salt=SALT,compress=True)
            return render(request,'works/work_append.html',context)
        except (signing.BadSignature,TypeError,KeyError,ValueError):messages.error(request,'Просмотр устарел или изменён. Выберите работы заново.')
        except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
        return redirect('works:work_list')
