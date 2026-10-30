"""Transport-origin planning type proposals use the existing catalogue queue."""
import json
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.db import transaction
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from apps.resources.models import EquipmentType,EquipmentCategory,CatalogueReviewRequest
from . import transport


@login_required
@require_POST
@transaction.atomic
def propose_type(request):
    from core.permissions import require_roles
    require_roles(request.user,transport.OPERATORS)
    try:
        data=json.loads(request.body)
        camp=get_object_or_404(transport.camps_for(request.user),pk=data.get('camp'))
        transport.authorize(request.user,camp)
        name=' '.join(str(data.get('name','')).split())
        if not name or len(name)>150:raise ValidationError('Укажите название до 150 символов.')
        group=EquipmentCategory.objects.filter(company=request.user.company,pk=data.get('group')).first() if data.get('group') else None
        if data.get('group') and not group:raise ValidationError('Группа техники недоступна.')
        transport._company_mutex(request.user)
        from apps.accounts.models import Company
        Company.objects.select_for_update().get(pk=request.user.company_id)
        if any(' '.join(t.name.split()).casefold()==name.casefold() for t in EquipmentType.objects.filter(company=request.user.company,parent=None,merge_source__isnull=True)):
            raise ValidationError('Такое название уже есть в справочнике или на проверке. Выберите существующую позицию.')
        equipment=EquipmentType.objects.create(company=request.user.company,name=name,category=group,unit='ед.',is_active=False)
        proposal=CatalogueReviewRequest.objects.create(company=request.user.company,kind='equipment',equipment_type=equipment,created_by=request.user,origin_label='Транспорт · '+camp.name)
        from apps.resources.catalogue_review import audit
        from apps.resources.catalogue_tasks import sync_task
        audit(request.user,proposal,'CREATE',{'name':name,'status':'PENDING','camp':camp.pk});sync_task(proposal)
        return JsonResponse({'id':equipment.pk,'name':equipment.name,'label':equipment.name+' · На проверке','category':group.pk if group else None,'status':'PENDING'})
    except (ValidationError,ValueError,TypeError) as exc:
        return JsonResponse({'error':'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Проверьте название и группу техники.'},status=400)
