"""Act-as context keeps the authenticated administrator in the session."""
from contextvars import ContextVar
import json
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models.signals import pre_save,post_save,post_delete
from django.dispatch import receiver
from django.utils import timezone

context=ContextVar('gpr_act_as',default=None)
KEY='gpr_act_as'


def record(actor,target,comment,**data):
    from apps.audit.models import AuditLog
    return AuditLog.objects.create(user=actor,action='UPDATE',entity_type='accounts.impersonation',entity_id=str(target.pk),new_value=dict(acting_user_id=target.pk,company_id=actor.company_id,**data),comment=comment)


def permitted(actor,target):
    from core.permissions import role_code
    return actor.is_active and actor.company_id and role_code(actor)=='ADMIN' and target.is_active and target.company_id==actor.company_id and not target.is_superuser and not target.is_staff and role_code(target)!='ADMIN'


class ActAsMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        try:
            response=self.get_response(request)
            if getattr(request,'acting_admin',None) and request.method not in {'GET','HEAD','OPTIONS'}:
                record(request.acting_admin,request.user,'Запрос администратора в режиме пользователя',path=request.path,method=request.method,status=response.status_code)
            return response
        finally:
            token=getattr(request,'_act_as_token',None)
            if token is not None:context.reset(token)

    def process_view(self,request,view,*args):
        from apps.accounts.models import User
        data=request.session.get(KEY)
        if not data:return
        actor=request.user
        match=request.resolver_match
        if not actor.is_authenticated:
            request.session.pop(KEY,None);return
        if match.view_name=='accounts:impersonation_stop':return
        target=User.objects.select_related('role','company').filter(pk=data.get('target')).first()
        valid=target and data.get('actor')==actor.pk and timezone.now().timestamp()-data.get('started',0)<1800 and permitted(actor,target)
        if not valid:
            request.session.pop(KEY,None)
            raise PermissionDenied('Режим пользователя завершен: время истекло или доступ изменился.')
        request.acting_admin=actor
        request.user=target;request._cached_user=target
        request._act_as_token=context.set(dict(actor=actor,target=target,path=request.path))
        if 'admin' in match.namespaces or match.namespace=='accounts' and match.url_name not in {'settings','user_detail','access_pending'}:
            raise PermissionDenied('Вернитесь в учетную запись администратора для управления пользователями и входом.')


def values(instance):
    result={}
    for field in instance._meta.concrete_fields:
        if isinstance(field,(models.JSONField,models.BinaryField)) or any(part in field.name.lower() for part in ['password','secret','token','credential']):continue
        result[field.attname]=field.value_from_object(instance)
    return json.loads(json.dumps(result,cls=DjangoJSONEncoder,default=str))


def track(sender):
    return context.get() and sender._meta.app_label not in {'audit','sessions','contenttypes','auth'}


@receiver(pre_save,dispatch_uid='gpr_act_as_pre')
def before_save(sender,instance,**kwargs):
    info=context.get()
    if not track(sender):return
    if getattr(instance,'actor_id',None)==info['target'].pk:
        if hasattr(instance,'actor_name'):
            suffix=f" (через администратора {info['actor'].username})"
            if suffix not in instance.actor_name:instance.actor_name=(instance.actor_name or info['target'].full_name)[:200]+suffix[:100]
        elif hasattr(instance,'details') and isinstance(instance.details,dict):
            instance.details={**instance.details,'acting_admin_id':info['actor'].pk,'acting_admin':info['actor'].username}
    fields=[field.name for field in sender._meta.concrete_fields if not isinstance(field,(models.JSONField,models.BinaryField)) and not any(part in field.name.lower() for part in ['password','secret','token','credential'])]
    previous=sender.objects.filter(pk=instance.pk).only(*fields).first() if not instance._state.adding else None
    instance._act_as_previous=values(previous) if previous else {}


@receiver(post_save,dispatch_uid='gpr_act_as_save')
def after_save(sender,instance,created,**kwargs):
    if not track(sender):return
    from apps.audit.models import AuditLog
    info=context.get();old=getattr(instance,'_act_as_previous',{});new=values(instance)
    changed={key:value for key,value in new.items() if old.get(key)!=value}
    AuditLog.objects.create(user=info['actor'],action='CREATE' if created else 'UPDATE',entity_type=sender._meta.label_lower,entity_id=str(instance.pk),old_value={key:old.get(key) for key in changed},new_value=dict(acting_user_id=info['target'].pk,company_id=info['actor'].company_id,changes=changed),comment=f"Через пользователя {info['target'].username}; {info['path']}")


@receiver(post_delete,dispatch_uid='gpr_act_as_delete')
def after_delete(sender,instance,**kwargs):
    if not track(sender):return
    from apps.audit.models import AuditLog
    info=context.get()
    AuditLog.objects.create(user=info['actor'],action='DELETE',entity_type=sender._meta.label_lower,entity_id=str(instance.pk),old_value=values(instance),new_value={'acting_user_id':info['target'].pk,'company_id':info['actor'].company_id},comment=f"Через пользователя {info['target'].username}; {info['path']}")
