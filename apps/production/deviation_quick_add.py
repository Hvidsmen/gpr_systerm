import json
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from core.permissions import require_roles,FACT_ROLES
from .models import DeviationReason
from .transport import _company_mutex,audit


@login_required
@require_POST
def create(request):
    require_roles(request.user,FACT_ROLES)
    try:
        data=json.loads(request.body);name=' '.join(str(data.get('name','')).split())
        if not name or len(name)>150:raise ValidationError('Укажите причину длиной до 150 символов.')
        with transaction.atomic():
            _company_mutex(request.user)
            from apps.accounts.models import Company
            Company.objects.select_for_update().get(pk=request.user.company_id)
            row=next((reason for reason in DeviationReason.objects.filter(company=request.user.company) if reason.name.casefold()==name.casefold()),None)
            if row and not row.is_active:raise ValidationError('Такая причина отключена. Обратитесь к администратору.')
            if not row:
                row=DeviationReason.objects.create(company=request.user.company,name=name)
                audit(request.user,row,'CREATE',{'name':name})
        return JsonResponse({'id':row.pk,'name':row.name})
    except (ValueError,ValidationError) as exc:return JsonResponse({'error':'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Неверные данные.'},status=400)
