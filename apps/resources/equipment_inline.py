from django import forms
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from core.permissions import require_roles,PLAN_ROLES
from .models import EquipmentType,EquipmentCategory


class PlanningTypeForm(forms.ModelForm):
    class Meta:
        model=EquipmentType
        fields=['name','category','unit']
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs);self.instance.company=company
        from .group_order import ordered
        self.fields['category'].queryset=ordered(EquipmentCategory.objects.filter(company=company),kind='equipment')
    def clean_name(self):
        name=self.cleaned_data['name'].strip()
        if EquipmentType.objects.filter(company=self.instance.company,parent=None,merge_source__isnull=True,name__iexact=name).exists():raise forms.ValidationError('Такая техника для планирования уже есть. Выберите её в списке.')
        return name


@login_required
@require_POST
def create_planning_type(request):
    require_roles(request.user,PLAN_ROLES)
    with transaction.atomic():
        from apps.accounts.models import Company
        Company.objects.select_for_update().get(pk=request.user.company_id)
        form=PlanningTypeForm(request.POST,company=request.user.company)
        if not form.is_valid():return JsonResponse({'errors':form.errors},status=400)
        group=form.cleaned_data.get('category')
        if group and not EquipmentCategory.objects.select_for_update().filter(company=request.user.company,pk=group.pk).exists():return JsonResponse({'errors':{'category':['Группа уже удалена. Выберите другую.']}},status=400)
        row=form.save()
        from apps.audit.models import AuditLog
        AuditLog.objects.create(user=request.user,action='CREATE',entity_type='resources.EquipmentType',entity_id=str(row.pk),new_value={'name':row.name,'category_id':row.category_id},comment='Добавление техники для планирования из формы')
        return JsonResponse({'value':str(row.pk),'label':row.name,'category':row.category_id},status=201)
