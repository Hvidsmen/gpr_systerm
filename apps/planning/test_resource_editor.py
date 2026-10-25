from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from . import test_work_resources as fixtures
from .models import WorkResourceAssignment,WorkMonthAllocation,ResourceMonthAllocation
from .workspace_services import build_workspace_snapshot
from . import work_resources,resource_editor


class InlineResourceTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkResourceTests.setUpTestData.__func__)
    prepare_owner=fixtures.WorkResourceTests.prepare_owner

    def setUp(self):
        fixtures.WorkResourceTests.setUp(self)
        WorkMonthAllocation.objects.create(version=self.version,company=self.company,month=fixtures.JAN,work=self.composite,quantity=1)
        self.client.force_login(self.manager)
        self.url=reverse('planning:preparation_add_assignment',args=[self.version.pk])

    def data(self,kind='equipment',**changes):
        data={'resource_kind':kind,'work':self.composite.pk,'work_item':self.a.pk,
              'start_date':'2026-01-01','end_date':'2026-01-02',
              'count':2,'shifts_per_day':1,'hours_per_shift':8,'include_crew':'on'}
        data.update({'equipment_type':self.equipment.pk} if kind=='equipment' else {'position':self.position.pk} if kind=='labor' else {'fuel_type':'DIESEL','daily_liters':'12.5','fuel_price':'60'})
        data.update(changes);return data

    def test_only_child_simple_work_or_object_accept_new_resources(self):
        response=self.client.post(self.url,self.data(work_item=''))
        self.assertEqual(response.status_code,400)
        self.assertFalse(self.version.resource_assignments.exists())
        self.assertEqual(self.client.post(self.url,self.data()).status_code,200)
        self.assertEqual(self.client.post(self.url,self.data(work='',work_item='')).status_code,200)
        WorkMonthAllocation.objects.create(version=self.version,company=self.company,month=fixtures.JAN,work=self.simple,quantity=1)
        self.assertEqual(self.client.post(self.url,self.data(work=self.simple.pk,work_item='')).status_code,200)

    def test_legacy_package_resource_keeps_totals_until_explicit_reassignment(self):
        response=self.client.post(self.url,self.data())
        row=self.version.resource_assignments.get(pk=response.json()['assignment']['id'])
        WorkResourceAssignment.objects.filter(pk=row.pk).update(work_item=None)
        row.refresh_from_db()
        self.assertTrue(resource_editor.serialize(row)['legacy_package'])
        before=resource_editor.serialize(row)['projected']
        response=self.client.post(self.url,self.data(assignment_id=row.pk,work='',work_item=''))
        self.assertEqual(response.status_code,200,response.content)
        result=response.json()['assignment']
        self.assertFalse(result['legacy_package']);self.assertFalse(result['work'])
        self.assertEqual(result['projected'],before)
        self.assertEqual(self.version.resource_assignments.count(),1)

    def test_crew_position_can_be_added_alone_without_machine(self):
        response=self.client.post(self.url,self.data('labor'))
        self.assertEqual(response.status_code,200,response.content)
        saved=response.json()['assignment']
        self.assertEqual(saved['resource_kind'],'labor')
        self.assertFalse(saved['equipment_type'])
        self.assertFalse(saved['include_crew'])
        self.assertEqual([p['kind'] for p in saved['projected']],['labor'])
        self.assertEqual(saved['projected'][0]['count'],2)

    def test_resource_for_work_in_common_snapshot_once_and_tagged(self):
        response=self.client.post(self.url,self.data())
        self.assertEqual(response.status_code,200,response.content)
        saved=response.json()['assignment'];self.assertEqual(saved['target'],self.a.name)
        self.assertEqual([r['kind'] for r in saved['projected']],['equipment','labor','labor'])
        self.assertFalse(ResourceMonthAllocation.objects.filter(version=self.version).exists())
        first=build_workspace_snapshot(self.version)
        second=build_workspace_snapshot(self.version)
        self.assertEqual(first['resources'],second['resources'])
        machines=first['resources']['equipment']
        self.assertEqual(len(machines),2)
        self.assertEqual(sum(Decimal(r['planned_count']) for r in machines),4)
        self.assertTrue(all(r['work_id']==self.composite.pk and r['work_item_id']==self.a.pk for r in machines))

    def test_fuel_and_people_outside_works_and_copy(self):
        response=self.client.post(self.url,self.data('fuel'))
        self.assertEqual(response.status_code,200,response.content)
        fuel=WorkResourceAssignment.objects.get(pk=response.json()['assignment']['id'])
        self.assertFalse(fuel.crew_snapshot)
        common=self.client.post(self.url,self.data('labor',work='',work_item=''))
        self.assertEqual(common.status_code,200,common.content)
        self.assertEqual(common.json()['assignment']['target'],'Вне работ')
        snapshot=build_workspace_snapshot(self.version)
        self.assertEqual(sum(Decimal(r['planned_liters']) for r in snapshot['resources']['fuel']),25)
        self.assertEqual(snapshot['resources']['fuel'][0]['work_item_id'],self.a.pk)
        from .independent_smg import create
        schedule=create(self.planner,self.obj,fixtures.JAN)
        work_resources.copy_assignments(self.version,schedule)
        copied=schedule.resource_assignments.get(fuel_type='DIESEL')
        self.assertEqual(copied.daily_liters,Decimal('12.5'))
        self.assertEqual(len(schedule.entries.get(kind='fuel').rows),2)

    def test_edit_and_remove_assignment(self):
        created=self.client.post(self.url,self.data('fuel')).json()['assignment']
        response=self.client.post(self.url,self.data('fuel',assignment_id=created['id'],daily_liters='20'))
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(WorkResourceAssignment.objects.filter(version=self.version).count(),1)
        response=self.client.post(self.url,{'action':'delete','assignment_id':created['id']})
        self.assertEqual(response.status_code,200)
        self.assertFalse(self.version.resource_assignments.exists())

    def test_unplanned_target_and_unselected_item_rejected(self):
        WorkMonthAllocation.objects.filter(version=self.version).update(selected_item_ids=[self.a.pk])
        response=self.client.post(self.url,self.data(work_item=self.b.pk))
        self.assertEqual(response.status_code,400)
        WorkMonthAllocation.objects.filter(version=self.version).delete()
        self.assertEqual(self.client.post(self.url,self.data()).status_code,400)
        self.assertFalse(self.version.resource_assignments.exists())

    def test_planner_can_draft_resources_but_other_manager_cannot(self):
        self.client.force_login(self.planner)
        self.assertEqual(self.client.post(self.url,self.data()).status_code,200)
        from .preparation import ready
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):ready(self.planner,self.version)
        from apps.accounts.models import User
        other=User.objects.create_user(username='other-resource-manager',company=self.company,role=self.manager.role)
        self.client.force_login(other)
        self.assertEqual(self.client.post(self.url,self.data()).status_code,403)
        self.client.force_login(self.manager)
        type(self.version).objects.filter(pk=self.version.pk).update(status='SUBMITTED')
        self.assertEqual(self.client.post(self.url,self.data()).status_code,400)

    def test_pages_show_inline_actions_and_only_one_bulk_add(self):
        response=self.client.get(reverse('planning:global_detail',args=[self.version.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'data-add-resource="fuel"')
        self.assertContains(response,'work-resource-editor-template')
        self.assertNotContains(response,'<dialog id="work-resource-dialog"')
        self.assertContains(response,'data-work-resource-list')
        self.client.force_login(self.planner)
        response=self.client.get(reverse('planning:workspace_edit',args=[self.version.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Добавить списком')
        self.assertNotContains(response,'Состав на весь период')
        self.assertNotContains(response,'Назначения ресурсов на работы')
        self.assertNotContains(response,'data-resource-assignment-list')

    def test_equipment_without_crew_is_explicit_choice(self):
        self.equipment.crew_positions.all().delete()
        response=self.client.post(self.url,self.data(include_crew=''))
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(len(response.json()['assignment']['projected']),1)
        response=self.client.post(self.url,self.data())
        self.assertEqual(response.status_code,400)

    def test_manager_sees_calculated_work_plan_and_units_beside_resources(self):
        allocation=self.version.work_allocations.get()
        allocation.item_quantities={str(self.a.pk):'17',str(self.b.pk):'5'}
        allocation.save()
        response=self.client.get(reverse('planning:global_detail',args=[self.version.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'data-resource-work-plan')
        target=response.context['resource_targets'][0]
        item=next(item for item in target['items'] if item['id']==self.a.pk)
        self.assertEqual(item['unit'],self.a.unit)
        self.assertEqual(Decimal(item['plan'][0]['quantity']),17)
        self.assertEqual(item['plan'][0]['label'],'01.2026')
        self.assertEqual(self.client.post(reverse('planning:workspace_edit',args=[self.version.pk]),{}).status_code,403)

    def test_handoff_link_opens_form_for_admin_without_creating_tasks(self):
        from apps.accounts.models import User,Role
        from .models import WorkTask
        admin=User.objects.create_user(username='resource-handoff-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.client.force_login(admin)
        count=WorkTask.objects.count()
        response=self.client.get(reverse('planning:preparation_gpr',args=[self.version.pk]),{'handoff':'1'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'name="action" value="handoff"')
        self.assertContains(response,'data-preparation-tasks')
        self.assertEqual(WorkTask.objects.count(),count)
        self.assertEqual(self.client.get(reverse('planning:preparation_gpr',args=[self.version.pk])).status_code,302)

    def test_single_save_and_submit_actions_and_unified_matrix(self):
        response=self.client.get(reverse('planning:global_detail',args=[self.version.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'data-plan-action-header',count=1)
        self.assertContains(response,'data-matrix-resource-row',count=2)
        self.assertNotContains(response,'<div class="table-responsive mt-2 mb-2" data-resource-work-plan>')
        self.assertContains(response,'Отправить на согласование',count=1)
        self.client.force_login(self.planner)
        response=self.client.get(reverse('planning:workspace_edit',args=[self.version.pk]))
        self.assertContains(response,'form="allocation-editor" name="action" value="save"',count=1)
        self.assertNotContains(response,'Сохранить этот месяц')
        self.assertNotContains(response,'Сохранить месяц и сформировать план')
