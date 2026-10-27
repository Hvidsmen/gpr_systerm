from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import PermissionDenied, ValidationError
from apps.resources.models import Position
from . import test_preparation as fixtures, preparation, work_resources
from .models import WorkMonthAllocation
from .global_services import GlobalPlanService


class SingleGPRTests(TestCase):
    setUpTestData=classmethod(fixtures.PreparationTests.setUpTestData.__func__)
    setUp=fixtures.PreparationTests.setUp
    handoff=fixtures.PreparationTests.handoff
    checked=fixtures.PreparationTests.checked

    def test_handoff_returns_to_same_gpr_and_reuses_active_tasks(self):
        self.client.force_login(self.planner)
        page=reverse('planning:workspace_edit',args=[self.version.pk])
        response=self.client.get(page)
        self.assertContains(response,'data-gpr-handoff')
        data={'action':'handoff','manager':self.manager.pk,'estimator':self.estimator.pk,'return_to_gpr':'1','month':'2026-01-01'}
        endpoint=reverse('planning:preparation_gpr',args=[self.version.pk])
        response=self.client.post(endpoint,data)
        self.assertRedirects(response,page+'?month=2026-01-01',fetch_redirect_response=False)
        from .models import WorkTask
        ids=list(WorkTask.objects.filter(version=self.version).values_list('pk',flat=True))
        self.client.post(endpoint,data)
        self.assertEqual(list(WorkTask.objects.filter(version=self.version).values_list('pk',flat=True)),ids)
        response=self.client.get(page)
        self.assertEqual(response.context['gpr_path'][0]['state'],'done')
        self.assertEqual(response.context['gpr_path'][1]['state'],'current')

    def test_single_url_switches_from_editor_to_frozen_review_and_back(self):
        self.client.force_login(self.planner)
        url=reverse('planning:workspace_edit',args=[self.version.pk])
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'data-plan-month-picker')
        self.assertNotContains(response,'План и согласование')
        self.assertNotContains(response,'Согласование · раунд')
        self.assertEqual(response.context['gpr_path'][0]['state'],'current')
        self.handoff();self.checked();preparation.ready(self.manager,self.version)
        self.client.force_login(self.manager)
        response=self.client.post(reverse('planning:global_action',args=[self.version.pk,'submit']))
        self.assertRedirects(response,url,fetch_redirect_response=False)
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'data-plan-approval-stages')
        self.assertContains(response,'Согласование · раунд 1')
        self.assertNotContains(response,'id="allocation-editor"')
        self.assertFalse(response.context['can_prepare_resources'])
        states={s['key']:s['state'] for s in response.context['gpr_path']}
        self.assertEqual(states['approval'],'current')
        self.client.force_login(self.planner)
        self.assertEqual(self.client.post(url,{}).status_code,403)
        self.version.refresh_from_db()
        GlobalPlanService.transition(self.version,self.approvers['PRODUCTION_HEAD'],'reject','Уточните объёмы')
        response=self.client.get(url)
        self.assertContains(response,'id="allocation-editor"')
        self.assertContains(response,'Уточните объёмы')
        self.assertEqual(response.context['gpr_path'][0]['state'],'current')

    def test_planner_resource_draft_invalidates_manager_confirmation_and_cannot_submit(self):
        self.handoff();self.checked();preparation.ready(self.manager,self.version)
        position=Position.objects.create(company=self.company,name='Предварительный ресурс')
        work_resources.brigade_for(position)
        from apps.resources.models import Employee
        Employee.objects.create(company=self.company, position=position, first_name='Иван', last_name='Иванов')
        self.client.force_login(self.planner)
        endpoint=reverse('planning:preparation_add_assignment',args=[self.version.pk])
        response=self.client.post(endpoint,{'resource_kind':'labor','position':position.pk,'work':self.simple.pk,'work_item':'',
            'start_date':'2026-01-01','end_date':'2026-01-02','count':2,'shifts_per_day':1,'hours_per_shift':8})
        self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(response.json()['workflow']['resources_ready'])
        self.assertTrue(response.json()['workflow']['estimator_approved'])
        with self.assertRaises(PermissionDenied):preparation.ready(self.planner,self.version)
        with self.assertRaises(ValidationError):preparation.require_submission(self.manager,self.version)
        self.assertEqual(self.client.post(reverse('planning:global_action',args=[self.version.pk,'submit'])).status_code,403)
        response=self.client.get(reverse('planning:workspace_edit',args=[self.version.pk]))
        self.assertTrue(response.context['can_prepare_resources'])
        self.assertFalse(response.context['can_confirm_resources'])
        self.assertContains(response,'Работы и предварительные ресурсы переданы руководителю')
        self.assertNotContains(response,'name="action" value="ready"')
        preparation.ready(self.manager,self.version)
        preparation.require_submission(self.manager,self.version)

    def test_list_opens_current_version_and_search_respects_company(self):
        self.client.force_login(self.planner)
        response=self.client.get(reverse('planning:workspace_list'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,reverse('planning:workspace_edit',args=[self.version.pk]))
        self.assertContains(response,'Текущий этап')
        self.assertContains(response,'Создать ГПР')
        self.assertNotContains(self.client.get(reverse('planning:workspace_list'),{'q':'no matching plan'}),self.version.workspace.name)
