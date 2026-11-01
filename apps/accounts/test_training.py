from datetime import datetime, timezone as dt_timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import uuid
from django.conf import settings
from django.core import signing
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from ops.windows_runtime import atomic_json
from .models import Company, User, Role, TrainingCopy, TrainingTicketUse
from . import training


class TrainingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Обучение')
        cls.other_company = Company.objects.create(name='Другая компания')
        cls.admin = User.objects.create_user(username='training-admin', company=cls.company, role=Role.objects.get(code='ADMIN'))
        cls.member = User.objects.create_user(username='trainee', company=cls.company, role=Role.objects.get(code='DISPATCHER'))
        cls.other = User.objects.create_user(username='outside', company=cls.other_company, role=Role.objects.get(code='DISPATCHER'))
        cls.copy = TrainingCopy.objects.create(company=cls.company, name='Группа 1', port=8101, created_by=cls.admin)
        cls.copy.members.add(cls.member)

    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.override = override_settings(TRAINING_ROOT=self.folder)
        self.override.enable(); self.addCleanup(self.override.disable)

    def test_admin_creates_group_without_promoting_production_users(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('accounts:training_list'), {'name':'Группа 2','port':8102,'enabled':'on','members':[self.member.pk,self.admin.pk]})
        self.assertEqual(response.status_code,302)
        group = TrainingCopy.objects.get(name='Группа 2')
        self.assertEqual(set(training.member_ids(group)),{self.member.pk,self.admin.pk})
        self.member.refresh_from_db(); self.assertEqual(self.member.role.code,'DISPATCHER')
        from apps.planning.models import WorkTask
        self.assertEqual(WorkTask.objects.filter(kind='TRAINING',key__startswith=f'training:{group.pk}:').count(),2)
        self.assertTrue((training.folder(group)/'access.json').is_file())

    def test_form_rejects_foreign_members_and_bad_or_duplicate_ports(self):
        from .training_views import TrainingForm
        for members,port in [([self.other.pk],8102),([self.member.pk],8001),([self.member.pk],8101)]:
            form=TrainingForm({'name':'New','members':members,'port':port,'enabled':'on'},user=self.admin)
            self.assertFalse(form.is_valid())

    def test_non_admin_has_only_own_entry_no_group_configuration(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse('accounts:training_list')).status_code,403)
        self.assertEqual(self.client.get(reverse('accounts:training_enter',args=[self.copy.pk])).status_code,503)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('accounts:training_enter',args=[self.copy.pk])).status_code,404)

    def test_entry_works_for_fuel_and_catalogue_roles(self):
        for role in ['FUEL_WAREHOUSE','PERSONNEL_CATALOG_MANAGER','EQUIPMENT_CATALOG_MANAGER','COMMANDANT']:
            User.objects.filter(pk=self.member.pk).update(role=Role.objects.get(code=role))
            self.member.refresh_from_db();self.client.force_login(self.member)
            self.assertEqual(self.client.get(reverse('accounts:training_enter',args=[self.copy.pk])).status_code,503,role)

    def test_tasks_visible_to_catalogue_members_and_link_to_copy(self):
        from apps.planning.task_services import visible
        from apps.planning.task_views import link
        User.objects.filter(pk=self.member.pk).update(role=Role.objects.get(code='PERSONNEL_CATALOG_MANAGER'))
        self.member.refresh_from_db();training.sync_tasks(self.copy);training.sync_tasks(self.copy)
        tasks=visible(self.member).filter(kind='TRAINING')
        self.assertEqual(tasks.count(),1)
        self.assertEqual(link(tasks.get()),reverse('accounts:training_enter',args=[self.copy.pk]))
        self.client.force_login(self.member)
        self.assertContains(self.client.get(reverse('planning:task_list')),'Учебная копия')

    def test_removal_disables_entry_and_supersedes_task(self):
        training.queue(self.copy)
        self.copy.members.clear();training.queue(self.copy)
        self.assertFalse(training.permitted(self.member,self.copy))
        from apps.planning.models import WorkTask
        self.assertEqual(WorkTask.objects.get(kind='TRAINING').status,'SUPERSEDED')

    def test_reset_requires_post_confirmation_and_own_company_admin(self):
        self.client.force_login(self.admin)
        url=reverse('accounts:training_reset',args=[self.copy.pk])
        self.assertEqual(self.client.get(url).status_code,405)
        self.assertEqual(self.client.post(url).status_code,400)
        self.assertEqual(self.client.post(url,{'confirm':'yes'}).status_code,302)
        self.copy.refresh_from_db();self.assertIsNotNone(self.copy.requested_at)

    def test_moscow_schedule_before_at_and_after_seven(self):
        before=datetime(2026,11,1,3,59,tzinfo=dt_timezone.utc)
        at=before+timedelta(minutes=1)
        self.assertEqual(training.reset_slot(before).date().isoformat(),'2026-10-31')
        self.assertEqual(training.reset_slot(at).hour,7)
        self.assertEqual(training.reset_slot(at).astimezone(dt_timezone.utc),at)
        self.assertEqual(training.next_reset(at),at+timedelta(days=1))

    def test_live_server_cannot_prepare_copy_or_consume_ticket(self):
        with self.assertRaises(CommandError):call_command('prepare_training_copy')
        response=self.client.post(reverse('accounts:training_consume'),{'ticket':'bad'})
        self.assertEqual(response.status_code,403)
        self.member.refresh_from_db();self.assertEqual(self.member.role.code,'DISPATCHER')

    def child_context(self):
        training.write_access(self.copy)
        control=training.folder(self.copy);generation=uuid.uuid4().hex
        (control/'ticket.key').write_text('test-key-not-a-live-secret')
        atomic_json(control/'current.json',{'generation':generation,'return_url':'http://testserver/'})
        middleware=list(settings.MIDDLEWARE)
        middleware.insert(middleware.index('core.impersonation.ActAsMiddleware'),'apps.accounts.training_middleware.TrainingGuard')
        override=override_settings(TRAINING_MODE=True,TRAINING_CONTROL=control,TRAINING_GENERATION=generation,
            AUTHENTICATION_BACKENDS=['django.contrib.auth.backends.ModelBackend'],MIDDLEWARE=middleware)
        override.enable();self.addCleanup(override.disable)
        return control,generation

    def ticket(self,current_generation,**extra):
        return signing.dumps({'copy':str(self.copy.pk),'generation':current_generation,'company':self.company.pk,
            'user':self.member.pk,'nonce':str(uuid.uuid4()),**extra},key='test-key-not-a-live-secret',salt='training-entry')

    def test_training_ticket_is_single_use_and_does_not_create_superuser(self):
        _,generation=self.child_context()
        ticket=self.ticket(generation)
        self.assertEqual(self.client.post(reverse('accounts:training_consume'),{'ticket':ticket}).status_code,302)
        self.member.refresh_from_db();self.assertEqual(self.member.role.code,'ADMIN')
        self.assertFalse(self.member.is_staff or self.member.is_superuser)
        self.assertEqual(self.client.post(reverse('accounts:training_consume'),{'ticket':ticket}).status_code,403)
        self.assertEqual(TrainingTicketUse.objects.count(),1)

    def test_ticket_rejects_other_group_generation_user_and_bad_signature(self):
        _,generation=self.child_context()
        for extra in [{'copy':str(uuid.uuid4())},{'generation':'old'},{'user':self.other.pk},{'company':self.other_company.pk}]:
            self.assertEqual(self.client.post(reverse('accounts:training_consume'),{'ticket':self.ticket(generation,**extra)}).status_code,403)
        self.assertEqual(self.client.post(reverse('accounts:training_consume'),{'ticket':'wrong'}).status_code,403)

    def test_expired_ticket_does_not_log_in(self):
        _,generation=self.child_context()
        with patch('django.core.signing.time.time',return_value=0):
            ticket=self.ticket(generation)
        self.assertEqual(self.client.post(reverse('accounts:training_consume'),{'ticket':ticket}).status_code,403)

    def test_active_session_revoked_from_control_file_and_sensitive_routes_blocked(self):
        control,_=self.child_context()
        self.client.force_login(self.member,backend='django.contrib.auth.backends.ModelBackend')
        for name in ['one_c','portal_updates','invitation_settings','training_list','login']:
            self.assertEqual(self.client.get(reverse('accounts:'+name)).status_code,403,name)
        atomic_json(control/'access.json',{'copy':str(self.copy.pk),'company':self.company.pk,'enabled':True,'members':[]})
        self.assertEqual(self.client.get(reverse('planning:task_list')).status_code,403)

    def test_old_generation_is_unavailable(self):
        control,_=self.child_context()
        atomic_json(control/'current.json',{'generation':'replaced'})
        self.assertEqual(self.client.get(reverse('accounts:training_health')).status_code,503)
