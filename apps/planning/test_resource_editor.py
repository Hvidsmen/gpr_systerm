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

    def test_only_assigned_manager_or_admin_can_change(self):
        self.client.force_login(self.planner)
        self.assertEqual(self.client.post(self.url,self.data()).status_code,403)
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
        self.assertContains(response,'work-resource-dialog')
        self.client.force_login(self.planner)
        response=self.client.get(reverse('planning:workspace_edit',args=[self.version.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Добавить списком')
        self.assertNotContains(response,'Состав на весь период')

    def test_equipment_without_crew_is_explicit_choice(self):
        self.equipment.crew_positions.all().delete()
        response=self.client.post(self.url,self.data(include_crew=''))
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(len(response.json()['assignment']['projected']),1)
        response=self.client.post(self.url,self.data())
        self.assertEqual(response.status_code,400)
