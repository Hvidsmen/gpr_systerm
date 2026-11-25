from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import redirect,render
from django.views.decorators.http import require_GET,require_POST

from core.permissions import PERSONNEL_CATALOG_MANAGER,require_roles
from . import base_cities as service
from .models import BaseCity
from .recruitment import READ


MANAGERS={'ADMIN','HR_HEAD','RECRUITMENT_HEAD','PEREVAKHTA_SPECIALIST',PERSONNEL_CATALOG_MANAGER}


def _message(request,callback,*args,success,**kwargs):
    try:
        callback(*args,company=request.user.company,**kwargs);messages.success(request,success)
    except BaseCity.DoesNotExist:messages.error(request,'Город не найден или относится к другой компании.')
    except (ValidationError,ValueError,TypeError) as exc:
        text='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Некорректно выбраны города.'
        messages.error(request,text)
    return redirect('personnel:base_city_list')


@login_required
@require_GET
def city_list(request):
    require_roles(request.user,READ|{PERSONNEL_CATALOG_MANAGER})
    status=request.GET.get('status','active');query=' '.join(request.GET.get('q','').split())
    cities=BaseCity.objects.filter(company=request.user.company).select_related('archived_by','merged_into')
    if status=='archive':cities=cities.filter(is_active=False)
    elif status!='all':status='active';cities=cities.filter(is_active=True)
    if query:cities=cities.filter(name__icontains=query)
    cities=cities.order_by('name','pk')
    page=Paginator(cities,50).get_page(request.GET.get('page'))
    rows=[];usage=service.usage_map(request.user.company)
    for city in page.object_list:
        city.usage_counts=usage.get(city.pk,{'candidates':0,'source_only':0,'movements':0,'merge_targets':0,'total':0});rows.append(city)
    targets=BaseCity.objects.filter(company=request.user.company,is_active=True,merged_into__isnull=True).order_by('name','pk')
    return render(request,'personnel/base_city_list.html',{'cities':rows,'city_page':page,'targets':targets,'query':query,
        'status':status,'can_manage':request.user.is_superuser or request.user.role.code in MANAGERS,
        'active_count':BaseCity.objects.filter(company=request.user.company,is_active=True).count(),
        'archive_count':BaseCity.objects.filter(company=request.user.company,is_active=False).count()})


@login_required
@require_POST
def city_create(request):
    require_roles(request.user,MANAGERS)
    return _message(request,service.create,name=request.POST.get('name',''),success='Город добавлен.')


@login_required
@require_POST
def city_update(request,pk):
    require_roles(request.user,MANAGERS)
    return _message(request,service.rename,city_id=pk,name=request.POST.get('name',''),success='Название города изменено.')


@login_required
@require_POST
def city_archive(request,pk):
    require_roles(request.user,MANAGERS)
    return _message(request,service.archive,city_id=pk,actor=request.user,reason=request.POST.get('reason',''),success='Город перемещён в архив.')


@login_required
@require_POST
def city_restore(request,pk):
    require_roles(request.user,MANAGERS)
    return _message(request,service.restore,city_id=pk,success='Город восстановлен из архива.')


@login_required
@require_POST
def city_merge(request,pk):
    require_roles(request.user,MANAGERS)
    target=request.POST.get('target')
    if not target:
        selected=service.find(request.user.company,request.POST.get('target_name',''),active=True)
        target=selected.pk if selected and not selected.merged_into_id else None
    return _message(request,service.merge,source_id=pk,target_id=target,actor=request.user,
        reason=request.POST.get('reason',''),success='Города объединены, связанные записи перенесены.')


@login_required
@require_POST
def city_delete(request,pk):
    require_roles(request.user,MANAGERS)
    return _message(request,service.delete,city_id=pk,success='Неиспользуемый город удалён.')
