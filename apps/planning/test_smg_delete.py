from unittest.mock import patch
from django.test import TestCase,Client
from django.urls import reverse
from apps.accounts.models import User,Role
from . import test_workspace as fixtures,independent_smg
from .models import DailySchedule,DailyScheduleEntry


class SMGDeleteTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)
    def setUp(self):
        self.client.force_login(self.planner)
        self.schedule=independent_smg.create(self.planner,self.obj,fixtures.JAN)
        self.url=reverse('planning:smg_delete',args=[self.schedule.pk])
        self.entry=DailyScheduleEntry.objects.create(schedule=self.schedule,key='labor:test',kind='labor',label='Работники',rows=[{'date':'2026-01-01','planned_count':'2'}])

    def token(self):return self.client.get(self.url).context['preview_token']
    def confirm(self,token=None):return self.client.post(self.url,{'preview_token':token or self.token(),'confirm':'1'})

    def test_preview_then_deletes_only_this_draft(self):
        self.assertEqual(self.client.get(self.url).status_code,200)
        self.assertTrue(DailySchedule.objects.filter(pk=self.schedule.pk).exists())
        response=self.confirm();self.assertRedirects(response,reverse('planning:daily_schedule_list'))
        self.assertFalse(DailySchedule.objects.filter(pk=self.schedule.pk).exists())
        self.assertFalse(DailyScheduleEntry.objects.filter(pk=self.entry.pk).exists())

    def test_closed_days_and_non_draft_statuses_are_protected(self):
        token=self.token()
        for status in ['SUBMITTED','APPROVED','APPLIED','ARCHIVED']:
            DailySchedule.objects.filter(pk=self.schedule.pk).update(status=status)
            self.assertEqual(self.confirm(token).status_code,400)
        DailySchedule.objects.filter(pk=self.schedule.pk).update(status='DRAFT',locked_before=fixtures.FEB)
        self.assertEqual(self.confirm(token).status_code,400)
        self.assertTrue(DailySchedule.objects.filter(pk=self.schedule.pk).exists())

    def test_requires_current_confirmation_and_csrf(self):
        token=self.token();self.entry.revision+=1;self.entry.save()
        self.assertEqual(self.confirm(token).status_code,400)
        self.assertEqual(self.client.post(self.url,{'confirm':'1'}).status_code,400)
        client=Client(enforce_csrf_checks=True);client.force_login(self.planner)
        self.assertEqual(client.post(self.url,{'preview_token':self.token(),'confirm':'1'}).status_code,403)

    def test_manager_foreign_company_and_unassigned_planner_cannot_delete(self):
        token=self.token();self.client.force_login(self.manager)
        self.assertEqual(self.confirm(token).status_code,403)
        self.client.force_login(self.planner);self.planner.assigned_objects.clear()
        self.assertEqual(self.client.post(self.url,{'preview_token':token,'confirm':'1'}).status_code,404)
        foreign=fixtures.Company.objects.create(name='Foreign deletion')
        user=User.objects.create_user(username='foreign-delete-admin',company=foreign,role=Role.objects.get(code='ADMIN'))
        self.client.force_login(user);self.assertEqual(self.client.get(self.url).status_code,404)

    def test_audit_failure_rolls_back(self):
        token=self.token()
        with patch('apps.audit.models.AuditLog.objects.create',side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):self.confirm(token)
        self.assertTrue(DailySchedule.objects.filter(pk=self.schedule.pk).exists())
        self.assertTrue(DailyScheduleEntry.objects.filter(pk=self.entry.pk).exists())
