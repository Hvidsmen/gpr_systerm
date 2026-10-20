"""Explicit company-admin provisioning from trusted AD search results."""
from uuid import UUID
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction,IntegrityError
from django.shortcuts import render,redirect
from django.views.decorators.http import require_http_methods
from core.permissions import require_roles,ROLES
from apps.projects.models import ConstructionObject
from .models import User,Role


def search_directory(query='',guid=None):
    if not settings.LDAP_ENABLED or not settings.LDAP_BIND_PASSWORD:raise ValidationError('LDAP не настроен. Проверьте LDAP_ENABLED и LDAP_BIND_PASSWORD на сервере.')
    try:
        from ldap3 import Server,Connection,SIMPLE,NONE,SUBTREE
        from ldap3.utils.conv import escape_filter_chars
        from ldap3.core.exceptions import LDAPException
    except ImportError:raise ValidationError('Установите зависимости из requirements.txt.')
    if guid:
        binary=''.join('\\'+format(b,'02x') for b in UUID(str(guid)).bytes_le)
        condition='(objectGUID='+binary+')'
    else:
        if not 2<=len(query.strip())<=100:raise ValidationError('Введите от 2 до 100 символов имени или логина.')
        value=escape_filter_chars(query.strip())
        condition='(|'+''.join('('+field+'=*'+value+'*)' for field in ['sAMAccountName','displayName','mail','givenName','sn'])+')'
    expression='(&(objectCategory=person)(objectClass=user)'+condition+'(!(userAccountControl:1.2.840.113556.1.4.803:=2)))'
    for host in settings.LDAP_SERVERS:
        connection=None
        try:
            server=Server(host,port=settings.LDAP_PORT,use_ssl=False,connect_timeout=settings.LDAP_TIMEOUT,get_info=NONE)
            connection=Connection(server,user=settings.LDAP_BIND_USER,password=settings.LDAP_BIND_PASSWORD,authentication=SIMPLE,auto_bind=True,auto_referrals=False,raise_exceptions=True,receive_timeout=settings.LDAP_TIMEOUT)
            connection.search(settings.LDAP_BASE_DN,expression,search_scope=SUBTREE,attributes=['objectGUID','sAMAccountName','displayName','givenName','sn','mail'],paged_size=50)
            rows=[]
            for entry in connection.entries[:50]:
                def val(key):return str(entry[key].value or '')
                rows.append(dict(guid=str(UUID(bytes_le=entry['objectGUID'].raw_values[0])),login=val('sAMAccountName'),name=val('displayName'),first_name=val('givenName')[:150],last_name=val('sn')[:150],email=val('mail')[:254]))
            return rows
        except (LDAPException,OSError,ValueError,KeyError,TypeError):pass
        finally:
            if connection:
                try:connection.unbind()
                except LDAPException:pass
    raise ValidationError('AD недоступен или служебная учетная запись не имеет доступа к поиску. Выполните check_ldap на сервере.')


class AccessForm(forms.Form):
    role=forms.ModelChoiceField(label='Роль в ГПР',queryset=Role.objects.none(),widget=forms.Select(attrs={'class':'form-select'}))
    objects=forms.ModelMultipleChoiceField(label='Объекты / субобъекты',help_text='Планировщик: просмотр и ввод планов только по этим объектам. Мастер и диспетчер: ввод факта. Без назначений доступ к объектам отсутствует.',queryset=ConstructionObject.objects.none(),required=False,widget=forms.SelectMultiple(attrs={'class':'form-select'}))
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['role'].queryset=Role.objects.filter(code__in=ROLES)
        self.fields['objects'].queryset=ConstructionObject.objects.filter(company=company)


def selection(user,token):
    try:
        data=signing.loads(token,salt='ad-add-user',max_age=900)
        if data['actor']!=user.pk or data['company']!=user.company_id:raise ValueError
        return UUID(data['guid'])
    except (signing.BadSignature,ValueError,KeyError,TypeError):raise ValidationError('Выбор устарел. Найдите пользователя в AD заново.')


@transaction.atomic
def provision(actor,identity,role,objects):
    require_roles(actor,{'ADMIN'})
    if role.code not in ROLES or any(obj.company_id!=actor.company_id for obj in objects):raise ValidationError('Недопустимые роль или объекты.')
    guid=UUID(identity['guid'])
    try:
        user,created=User.objects.get_or_create(ldap_guid=guid,defaults={'username':'ad:'+guid.hex,'auth_source':'LDAP','password':make_password(None)})
    except IntegrityError:raise ValidationError('Не удалось создать учетную запись. Обратитесь к суперпользователю.')
    user=User.objects.select_for_update().get(pk=user.pk)
    if user.auth_source!='LDAP' or user.is_superuser or user.is_staff:raise ValidationError('Этой учетной записью управляет суперпользователь.')
    if user.company_id and user.company_id!=actor.company_id:raise ValidationError('Пользователь уже назначен другой компании. Обратитесь к суперпользователю.')
    if user.company_id==actor.company_id:raise ValidationError('Пользователь уже добавлен. Измените его доступ в списке пользователей.')
    if not user.is_active:raise ValidationError('Учетная запись отключена. Обратитесь к суперпользователю.')
    user.company_id=actor.company_id;user.role=role
    for field in ['first_name','last_name','email']:setattr(user,field,identity[field])
    user.save(update_fields=['company','role','first_name','last_name','email'])
    user.assigned_objects.set(objects)
    return user


@login_required
@require_http_methods(['GET','POST'])
def add_from_ad(request):
    require_roles(request.user,{'ADMIN'})
    rows=[];chosen=None;form=None;query=request.GET.get('q','').strip();token=request.POST.get('selected','') if request.method=='POST' else request.GET.get('selected','')
    try:
        if token:
            guid=selection(request.user,token)
            result=search_directory(guid=guid)
            if len(result)!=1 or UUID(result[0]['guid'])!=guid:raise ValidationError('Пользователь больше не доступен в AD. Повторите поиск.')
            chosen=result[0]
            form=AccessForm(request.POST if request.method=='POST' else None,company=request.user.company)
            if request.method=='POST' and form.is_valid():
                user=provision(request.user,chosen,form.cleaned_data['role'],list(form.cleaned_data['objects']))
                messages.success(request,'Пользователь AD добавлен. Он может входить с доменным логином и паролем.')
                return redirect('accounts:user_access',pk=user.pk)
        elif query:
            rows=search_directory(query=query)
            for row in rows:
                row['selected']=signing.dumps(dict(guid=row['guid'],actor=request.user.pk,company=request.user.company_id),salt='ad-add-user')
    except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
    return render(request,'accounts/add_from_ad.html',dict(rows=rows,chosen=chosen,form=form,query=query,selected=token))
