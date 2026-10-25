from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import PermissionDenied,ValidationError
from apps.accounts.models import Company,Role,User
from .models import WorkTask,EstimateReviewTask
from . import task_services as service
from .task_admin import close_tasks,fingerprint
from . import test_workspace as fixtures


class AdminTaskClosureTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)
    create=fixtures.WorkspaceTests.create

    def setUp(self):
        self.admin=User.objects.create_user(username='task-close-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.client.force_login(self.admin)
        self.url=reverse('planning:task_force_close')
        self.task=service.ensure(self.company,'close:manual',kind='MANUAL',title='Старое поручение',assigned_to=self.planner)
        self.review=service.ensure(self.company,'close:review',kind='MANUAL',title='На проверке',status='IN_REVIEW')
        self.done=service.ensure(self.company,'close:done',kind='MANUAL',title='Уже выполнено',status='DONE')

    def preview(self,ids=None,**extra):
        response=self.client.post(self.url,{'selected':ids or [self.task.pk],**extra})
        self.assertEqual(response.status_code,200)
        return response

    def confirm(self,response,reason='Устаревшее задание'):
        return self.client.post(self.url,{'confirm':'1','token':response.context['token'],'reason':reason})

    def test_selected_closure_requires_reason_keeps_history_and_finished_tasks(self):
        response=self.preview([self.task.pk,self.review.pk,self.done.pk])
        self.assertEqual(response.context['active_count'],2)
        self.assertContains(self.confirm(response,reason=''),'Обязательное поле')
        stale=WorkTask.objects.get(pk=self.task.pk)
        self.assertEqual(self.confirm(response).status_code,302)
        service.state(stale,'DONE',text='Устаревшая синхронизация')
        self.task.refresh_from_db();self.review.refresh_from_db();self.done.refresh_from_db()
        self.assertEqual(self.task.status,'CLOSED_ADMIN');self.assertEqual(self.review.status,'CLOSED_ADMIN')
        self.assertEqual(self.done.status,'DONE')
        event=self.task.events.latest('pk')
        self.assertEqual(event.actor,self.admin)
        self.assertEqual(event.text,'Устаревшее задание')
        service.state(self.task,'OPEN',text='Автоматическая синхронизация')
        self.task.refresh_from_db();self.assertEqual(self.task.status,'CLOSED_ADMIN')

    def test_filtered_closure_does_not_touch_other_kind_or_company(self):
        fact=service.ensure(self.company,'close:fact',kind='FACT',title='Факт')
        other=Company.objects.create(name='Other closure')
        foreign=service.ensure(other,'close:manual',kind='MANUAL',title='Чужое задание')
        response=self.preview(mode='filtered',status='active',kind='MANUAL',scope='all')
        self.assertEqual(self.confirm(response).status_code,302)
        self.assertEqual(WorkTask.objects.get(pk=fact.pk).status,'OPEN')
        self.assertEqual(WorkTask.objects.get(pk=foreign.pk).status,'OPEN')
        self.assertEqual(WorkTask.objects.get(pk=self.task.pk).status,'CLOSED_ADMIN')

    def test_stale_preview_and_audit_error_roll_back(self):
        response=self.preview()
        self.task.title='Изменено';self.task.save()
        self.assertContains(self.confirm(response),'Задания изменились')
        response=self.preview()
        with patch('apps.planning.task_admin.AuditLog.objects.create',side_effect=ValidationError('Ошибка аудита')):
            self.assertContains(self.confirm(response),'Ошибка аудита')
        self.task.refresh_from_db();self.assertEqual(self.task.status,'OPEN')
        self.assertFalse(self.task.events.filter(action='Принудительно закрыто').exists())

    def test_only_admin_and_company_scoping(self):
        self.client.force_login(self.planner)
        self.assertEqual(self.client.post(self.url,{'selected':[self.task.pk]}).status_code,403)
        with self.assertRaises(PermissionDenied):close_tasks(self.planner,[self.task.pk],'Причина',fingerprint([self.task]))
        self.client.force_login(self.admin)
        other=Company.objects.create(name='Other closure scope')
        foreign=service.ensure(other,'close:foreign',kind='MANUAL',title='Чужое')
        self.assertEqual(self.client.post(self.url,{'selected':[foreign.pk]}).status_code,403)
        self.assertEqual(self.client.get(self.url).status_code,405)

    def test_estimator_sync_does_not_reopen_closed_task_or_approve_plan(self):
        workspace=self.create();version=workspace.baseline_version
        estimate=EstimateReviewTask.objects.create(company=self.company,version=version,assigned_to=self.planner,created_by=self.admin)
        task=service.estimate_task(estimate)
        response=self.preview([task.pk])
        self.assertEqual(self.confirm(response).status_code,302)
        synced=service.estimate_task(estimate)
        self.assertEqual(synced.pk,task.pk);self.assertEqual(synced.status,'CLOSED_ADMIN')
        version.refresh_from_db();estimate.refresh_from_db()
        self.assertEqual(version.status,'DRAFT');self.assertEqual(estimate.status,'OPEN')
