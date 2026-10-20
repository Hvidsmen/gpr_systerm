from uuid import uuid4
from django.test import TestCase,Client
from django.urls import reverse
from .models import Company,Role,User
from apps.audit.models import AuditLog
from core.impersonation import KEY,context


class ActAsTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Act as')
        self.admin=User.objects.create_user(username='real-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.target=User.objects.create_user(username='ad:person',company=self.company,role=Role.objects.get(code='PLANNER'),auth_source='LDAP',ldap_guid=uuid4())
        self.client.force_login(self.admin)
        self.start=reverse('accounts:impersonation_start',args=[self.target.pk])
        self.stop=reverse('accounts:impersonation_stop')

    def test_start_action_audit_and_return_keep_real_authenticated_admin(self):
        self.assertEqual(self.client.get(self.start).status_code,405)
        self.assertEqual(self.client.post(self.start).status_code,302)
        self.assertEqual(int(self.client.session['_auth_user_id']),self.admin.pk)
        response=self.client.get(reverse('accounts:settings'))
        self.assertContains(response,'Вернуться к администратору')
        self.assertEqual(response.context['user'].pk,self.target.pk)
        self.assertEqual(response.context['request'].acting_admin.pk,self.admin.pk)
        response=self.client.post(reverse('accounts:settings'),{'first_name':'Changed','last_name':'Person','email':'person@example.org','phone':'123'})
        self.assertEqual(response.status_code,302)
        self.target.refresh_from_db();self.assertEqual(self.target.first_name,'Changed')
        log=AuditLog.objects.filter(user=self.admin,entity_type='accounts.user',entity_id=str(self.target.pk)).latest('pk')
        self.assertEqual(log.new_value['acting_user_id'],self.target.pk)
        self.assertEqual(log.new_value['changes']['first_name'],'Changed')
        self.assertNotIn('password',str(log.new_value))
        self.assertIsNone(context.get())
        self.assertEqual(self.client.post(self.stop).status_code,302)
        self.assertNotIn(KEY,self.client.session)
        self.assertEqual(self.client.get(reverse('accounts:user_list')).context['user'].pk,self.admin.pk)

    def test_target_permissions_are_enforced_and_auth_management_blocked(self):
        self.target.role=Role.objects.get(code='OBSERVER');self.target.save()
        self.client.post(self.start)
        response=self.client.post(reverse('accounts:settings'),{'first_name':'Bad'})
        self.assertEqual(response.status_code,403)
        for url in [reverse('accounts:user_create'),reverse('accounts:password_change'),reverse('accounts:logout'),reverse('accounts:add_from_ad')]:
            response=self.client.get(url);self.assertEqual(response.status_code,403)
            self.assertContains(response,'Вернуться к администратору',status_code=403)
        self.assertEqual(self.client.post(self.stop).status_code,302)

    def test_no_cross_company_privileged_disabled_or_nested_switches(self):
        for changes in [dict(company=Company.objects.create(name='Other')),dict(is_superuser=True),dict(is_staff=True),dict(is_active=False),dict(role=Role.objects.get(code='ADMIN'))]:
            user=User.objects.create_user(username='blocked-'+str(User.objects.count()),company=changes.pop('company',self.company),**changes)
            self.assertIn(self.client.post(reverse('accounts:impersonation_start',args=[user.pk])).status_code,[403,404])
        self.client.post(self.start)
        self.assertEqual(self.client.post(self.start).status_code,403)
        self.target.is_active=False;self.target.save()
        self.assertEqual(self.client.get(reverse('accounts:settings')).status_code,403)
        self.assertNotIn(KEY,self.client.session)

    def test_event_attribution_and_csrf(self):
        from apps.planning.task_services import ensure
        task=ensure(self.company,'act-as-manual',kind='MANUAL',title='Task',assigned_to=self.target,created_by=self.admin)
        self.client.post(self.start)
        self.client.post(reverse('planning:task_detail',args=[task.pk]),{'action':'comment','text':'By support'})
        event=task.events.latest('pk')
        self.assertEqual(event.actor_id,self.target.pk)
        self.assertIn(self.admin.username,event.actor_name)
        self.assertIsNone(context.get())
        client=Client(enforce_csrf_checks=True);client.force_login(self.admin)
        self.assertEqual(client.post(self.start).status_code,403)

    def test_company_scoped_history_and_expiry(self):
        self.client.post(self.start)
        session=self.client.session;session[KEY]={**session[KEY],'started':0};session.save()
        self.assertEqual(self.client.get(reverse('accounts:settings')).status_code,403)
        self.assertNotIn(KEY,self.client.session)
        response=self.client.get(reverse('accounts:impersonation_history'))
        self.assertContains(response,'Администратор начал работу')
        other=Company.objects.create(name='Unrelated')
        AuditLog.objects.create(user=self.admin,action='UPDATE',entity_type='accounts.impersonation',entity_id='0',new_value={'acting_user_id':999,'company_id':other.pk},comment='Other company secret')
        self.assertNotContains(self.client.get(reverse('accounts:impersonation_history')),'Other company secret')
