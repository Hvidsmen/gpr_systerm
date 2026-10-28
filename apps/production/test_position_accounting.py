from django.test import TestCase
from django.urls import reverse
from apps.resources.models import Brigade
from apps.resources.position_catalog import position_for
from apps.planning.models import DailyScheduleEntry
from .test_production_entry import ProductionEntryTests
from .dispatch_models import EmployeeActivity
from .models import LaborFact


class PositionAccountingTests(TestCase):
    @classmethod
    def setUpTestData(cls):ProductionEntryTests.setUpTestData.__func__(cls)
    payload=ProductionEntryTests.payload
    def test_different_staff_role_can_cover_smg_role_without_changing_employee(self):
        brigade=Brigade.objects.create(company=self.company,name='Должность плана')
        position=position_for(brigade)
        DailyScheduleEntry.objects.create(schedule=self.schedule,key='labor:planned',kind='labor',label=brigade.name,rows=[{'date':str(self.day),'brigade_id':brigade.pk,'planned_count':'2'}])
        self.client.force_login(self.users['DISPATCHER']);data=self.payload('DISPATCHER','works')
        original=self.employee.position_id;data.update({'labor-0-employee':self.employee.pk,'labor-0-work_mode':'general','labor-0-planning_position':position.pk})
        response=self.client.post(self.url,data);self.assertEqual(response.status_code,302)
        row=EmployeeActivity.objects.get(employee=self.employee);self.assertEqual(row.position_id,original);self.assertEqual(row.planning_position_id,position.pk)
        self.employee.refresh_from_db();self.assertEqual(self.employee.position_id,original)
        self.assertEqual(LaborFact.objects.get(brigade=brigade).actual_workers,1)
        page=self.client.get(self.url,{'construction_object':self.obj.pk,'date':str(self.day),'section':'works'});self.assertContains(page,'План по должностям');self.assertNotContains(page,'class="nav nav-tabs mb-3"')
    def test_unplanned_position_cannot_be_forged_and_reason_can_be_created_inline(self):
        position=position_for(Brigade.objects.create(company=self.company,name='Не запланирована'))
        self.client.force_login(self.users['DISPATCHER']);data=self.payload('DISPATCHER','works')
        data.update({'labor-0-employee':self.employee.pk,'labor-0-work_mode':'general','labor-0-planning_position':position.pk})
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        response=self.client.post(reverse('production:deviation_quick_add'),{'name':'Нет допуска на работу'},content_type='application/json');self.assertEqual(response.status_code,200)
        duplicate=self.client.post(reverse('production:deviation_quick_add'),{'name':'Нет допуска на работу'},content_type='application/json');self.assertEqual(response.json()['id'],duplicate.json()['id'])
