from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch
from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, User, Role
from apps.resources import catalogue_tasks, catalogue_review as review
from . import test_catalogue_review as fixtures, task_services as tasks
from .models import WorkTask
from .task_admin import close_tasks, fingerprint


class CatalogueTaskTests(TestCase):
    setUpTestData=classmethod(fixtures.CatalogueReviewTests.setUpTestData.__func__)
    setUp=fixtures.CatalogueReviewTests.setUp
    proposal=fixtures.CatalogueReviewTests.proposal
    handoff=fixtures.CatalogueReviewTests.handoff
    checked=fixtures.CatalogueReviewTests.checked

    def test_proposal_creates_one_correctly_routed_task_and_plan_link(self):
        for kind,person in [('labor',self.hr),('equipment',self.tech)]:
            proposal=self.proposal(kind)
            task=proposal.work_task
            self.assertEqual((task.kind,task.assigned_to,task.created_by),('CATALOGUE',person,self.planner))
            self.assertEqual(task.target_role,person.role.code)
            self.assertEqual(task.construction_object,self.obj)
            self.assertIsNone(task.version_id)
            self.assertEqual(catalogue_tasks.sync_task(proposal).pk,task.pk)
            self.assertEqual(WorkTask.objects.filter(catalogue_request=proposal).count(),1)
            page=self.client.get(reverse('planning:workspace_edit',args=[self.version.pk]))
            self.assertContains(page,reverse('planning:task_detail',args=[task.pk]))
            outgoing=self.client.get(reverse('planning:task_list'),{'scope':'outgoing'})
            self.assertContains(outgoing,task.title)

    def test_reviewers_get_own_inbox_take_comment_and_source_request_only(self):
        labor=self.proposal();equipment=self.proposal('equipment')
        tasks.ensure(self.company,'catalogue-test-manual',kind='MANUAL',title='Скрытое поручение',assigned_to=self.hr)
        self.client.force_login(self.hr)
        with patch('apps.planning.task_services.generate_daily',side_effect=AssertionError('not a dispatcher')):
            page=self.client.get(reverse('planning:task_list'))
        self.assertContains(page,labor.work_task.title)
        self.assertNotContains(page,equipment.work_task.title)
        self.assertNotContains(page,'Скрытое поручение')
        self.assertFalse(page.context['can_create'])
        url=reverse('planning:task_detail',args=[labor.work_task.pk])
        detail=self.client.get(url)
        self.assertContains(detail,'Открыть заявку справочника')
        self.assertContains(detail,reverse('resources:catalogue_review_detail',args=[labor.pk]))
        self.assertEqual(self.client.post(url,{'action':'take'}).status_code,302)
        labor.work_task.refresh_from_db();self.assertIsNotNone(labor.work_task.started_at)
        self.assertEqual(self.client.post(url,{'action':'comment','text':'Проверяю штатное расписание'}).status_code,302)
        self.assertTrue(labor.work_task.events.filter(text='Проверяю штатное расписание').exists())
        self.client.post(url,{'action':'resolve','text':'Готово'})
        labor.work_task.refresh_from_db();self.assertEqual(labor.work_task.status,'OPEN')
        for forbidden in ['planning:task_create']:
            self.assertEqual(self.client.get(reverse(forbidden)).status_code,403)
        self.assertEqual(self.client.get(reverse('planning:workspace_edit',args=[self.version.pk])).status_code,403)
        self.assertEqual(self.client.get(reverse('planning:task_detail',args=[equipment.work_task.pk])).status_code,404)
        foreign=Company.objects.create(name='Foreign catalogue task')
        outsider=User.objects.create_user(username='foreign-task-reviewer',company=foreign,role=self.hr.role)
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(url).status_code,404)

    def test_shared_queue_claim_and_reassignment_preserve_single_task(self):
        second=User.objects.create_user(username='catalogue-second-hr',company=self.company,role=self.hr.role)
        proposal=self.proposal();task=proposal.work_task
        self.assertIsNone(task.assigned_to_id)
        self.assertTrue(tasks.visible(second).filter(pk=task.pk).exists())
        tasks.take(self.hr,task)
        self.assertFalse(tasks.visible(second).filter(pk=task.pk).exists())
        task.refresh_from_db();started=task.started_at
        self.assertEqual(catalogue_tasks.sync_task(proposal).started_at,started)
        self.hr.is_active=False;self.hr.save(update_fields=['is_active'])
        task=catalogue_tasks.sync_task(proposal)
        self.assertEqual(task.assigned_to,second);self.assertIsNone(task.started_at)
        self.assertEqual(WorkTask.objects.filter(catalogue_request=proposal).count(),1)

    def test_admin_gets_fallback_then_new_reviewer_gets_assignment(self):
        self.hr.is_active=False;self.hr.save(update_fields=['is_active'])
        admin=User.objects.create_user(username='catalogue-task-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        proposal=self.proposal();task=proposal.work_task
        self.assertEqual(task.assigned_to,admin)
        self.assertTrue(tasks.filter_scope(tasks.visible(admin),admin,'incoming').filter(pk=task.pk).exists())
        self.assertIn('не назначен',task.description)
        with self.assertRaisesMessage(ValidationError,'Закрытие задания не заменяет проверку'):
            close_tasks(admin,[task.pk],'Закрыть без проверки',fingerprint([task]))
        self.hr.is_active=True;self.hr.save(update_fields=['is_active'])
        self.assertEqual(catalogue_tasks.sync_task(proposal).assigned_to,self.hr)

    def test_decisions_close_tasks_with_history_and_rejection_keeps_plan_blocked(self):
        for action,name in [('confirm','Подтверждаемая'),('reject','Отклоняемая'),('replace','Заменяемая')]:
            proposal=self.proposal(name=name)
            kwargs={}
            if action=='replace':
                token,_=review.preview(self.hr,proposal,self.brigade)
                kwargs={'target':self.brigade,'token':token}
            review.decide(self.hr,proposal,action,proposal.revision,'Проверка завершена',**kwargs)
            task=WorkTask.objects.get(catalogue_request=proposal)
            self.assertEqual(task.status,'DONE')
            self.assertTrue(task.events.filter(actor=self.hr,text__contains='Проверка завершена').exists())
            if action=='reject':self.assertTrue(review.requests_for(self.version,blocking=True).filter(pk=proposal.pk).exists())
        self.client.force_login(self.hr)
        page=self.client.get(reverse('planning:task_list'),{'status':'history'})
        self.assertContains(page,'Подтверждаемая');self.assertContains(page,'Заменяемая')

    def test_existing_pending_backfill_is_idempotent_with_historical_models(self):
        pending=self.proposal();finished=self.proposal('equipment')
        review.decide(self.tech,finished,'confirm',0)
        WorkTask.objects.filter(catalogue_request__in=[pending,finished]).delete()
        migration=import_module('apps.planning.migrations.0052_catalogue_review_tasks')
        # Use migration state: data migrations must not rely on live model methods.
        from django.db.migrations.loader import MigrationLoader
        from django.db import connection
        historical=MigrationLoader(connection).project_state([('planning','0052_catalogue_review_tasks')]).apps
        editor=SimpleNamespace(connection=SimpleNamespace(alias='default'))
        migration.restore_pending_tasks(historical,editor)
        migration.restore_pending_tasks(historical,editor)
        self.assertEqual(WorkTask.objects.filter(catalogue_request=pending).count(),1)
        self.assertFalse(WorkTask.objects.filter(catalogue_request=finished).exists())
        task=WorkTask.objects.get(catalogue_request=pending)
        self.assertEqual(task.assigned_to,self.hr)
        self.assertEqual(task.events.filter(action='Создано').count(),1)

    def test_failed_task_creation_rolls_back_proposal_and_plan_resource(self):
        from apps.resources.models import CatalogueReviewRequest, Brigade
        count=Brigade.objects.count()
        with patch('apps.resources.catalogue_tasks.ensure',side_effect=ValidationError('Ошибка задания')):
            with self.assertRaises(ValidationError):self.proposal()
        self.assertFalse(CatalogueReviewRequest.objects.exists())
        self.assertFalse(self.version.resource_allocations.exists())
        self.assertEqual(Brigade.objects.count(),count)
