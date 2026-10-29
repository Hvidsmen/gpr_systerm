from datetime import timedelta
from uuid import uuid4
from django.test import TestCase
from django.urls import reverse
from apps.audit.models import AuditLog
from .test_production_entry import ProductionEntryTests
from .models import DailyFact,LaborFact,EquipmentFact,FuelFact,DayActuation
from .dispatch_models import EmployeeActivity,EmployeeWorkLink,EquipmentActivity
from .day_reset import epoch,records
from .history_models import FactRevision


class ProductionResetTests(TestCase):
    setUpTestData=classmethod(ProductionEntryTests.setUpTestData.__func__)
    payload=ProductionEntryTests.payload
    def setUp(self):
        self.fact=DailyFact.objects.create(company=self.company,project_work=self.work,date=self.day,actual_quantity=5,reported_by=self.users['DISPATCHER'])
        self.person=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=self.employee,position=self.employee.position,work=self.work,entry_direction='works',reported_by=self.users['DISPATCHER'])
        EmployeeWorkLink.objects.create(company=self.company,activity=self.person,work=self.work)
        self.staff=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=self.cook,position=self.cook.position,general=True,entry_direction='labor',reported_by=self.users['PERSONNEL_DISPATCHER'])
        self.machine_fact=EquipmentActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,equipment=self.machine,general=True,activity='Общая работа')
        self.gear=EquipmentFact.objects.create(company=self.company,construction_object=self.obj,date=self.day,equipment_type=self.type,actual_count=1)
        self.fuel=FuelFact.objects.create(company=self.company,construction_object=self.obj,date=self.day,actual_liters=10)
        self.labor=LaborFact.objects.create(company=self.company,construction_object=self.obj,date=self.day,brigade=self.brigade,actual_workers=1)
        self.general_labor=LaborFact.objects.create(company=self.company,construction_object=self.obj,date=self.day,brigade=self.cook.position.planning_mapping.brigade,actual_workers=1,actual_hours=10)
        self.actuation=DayActuation.objects.create(company=self.company,construction_object=self.obj,date=self.day,reason='Исходная актировка',reported_by=self.users['ADMIN'])
        self.previous=DailyFact.objects.create(company=self.company,project_work=self.work,date=self.day-timedelta(days=1),actual_quantity=7)
        self.client.force_login(self.users['ADMIN']);self.endpoint=reverse('production:production_day_reset');self.scope={'construction_object':self.obj.pk,'date':str(self.day)}
    def confirmation(self):
        page=self.client.get(self.endpoint,self.scope);self.assertEqual(page.status_code,200)
        return {**self.scope,'version':page.context['form'].initial['version'],'reason':'Исправление ошибочного дневного ввода','confirm':'on'}
    def test_reset_removes_only_production_and_keeps_other_directions_plan_and_history(self):
        original=list(self.schedule.entries.values_list('pk','rows'))
        data=self.confirmation();response=self.client.post(self.endpoint,data);self.assertEqual(response.status_code,302,response.content)
        self.assertFalse(records(self.obj,self.day)['works'].exists());self.assertFalse(EmployeeActivity.objects.filter(pk=self.person.pk).exists());self.assertFalse(EmployeeWorkLink.objects.filter(activity_id=self.person.pk).exists());self.assertFalse(DayActuation.objects.filter(pk=self.actuation.pk).exists())
        for row in [self.staff,self.machine_fact,self.gear,self.fuel,self.general_labor,self.previous]:self.assertTrue(type(row).objects.filter(pk=row.pk).exists())
        self.assertFalse(LaborFact.objects.filter(pk=self.labor.pk).exists());self.general_labor.refresh_from_db();self.assertEqual(self.general_labor.actual_hours,10)
        self.assertEqual(list(self.schedule.entries.values_list('pk','rows')),original)
        self.assertTrue(FactRevision.objects.filter(entity_type='production.dailyfact',entity_id=str(self.fact.pk),operation='DELETE',actor=self.users['ADMIN'],reason__icontains='Сброс').exists())
        self.assertEqual(AuditLog.objects.filter(entity_type='production.day_reset').count(),1)
        self.assertGreater(epoch(self.obj,self.day),0)
        # The same confirmation cannot erase a newly entered day.
        self.assertContains(self.client.post(self.endpoint,data),'Факт изменился')
    def test_roles_scope_and_changed_preview_are_checked(self):
        data=self.confirmation();self.client.force_login(self.users['DISPATCHER'])
        self.assertEqual(self.client.get(self.endpoint,self.scope).status_code,403);self.assertEqual(self.client.post(self.endpoint,data).status_code,403)
        self.client.force_login(self.users['ADMIN']);self.actuation.reason='Актировка изменена после открытия подтверждения';self.actuation.save()
        self.assertContains(self.client.post(self.endpoint,data),'Факт изменился');self.assertTrue(DailyFact.objects.filter(pk=self.fact.pk).exists())
        data=self.confirmation();data['construction_object']=self.other.pk
        self.assertContains(self.client.post(self.endpoint,data),'другой форме')
    def test_old_production_forms_and_sessions_cannot_restore_cleared_data(self):
        old=self.payload('DISPATCHER','works');self.client.force_login(self.users['ADMIN']);self.client.post(self.endpoint,self.confirmation())
        self.client.force_login(self.users['DISPATCHER']);old.update(autosave='1');response=self.client.post(self.url,old)
        self.assertEqual(response.status_code,400);self.assertIn('сброшена',response.json()['error']);self.assertFalse(DailyFact.objects.filter(pk=self.fact.pk).exists())
        scope={**self.scope,'session':str(uuid4()),'action':'take','key':f'work:{self.work.pk}'}
        response=self.client.post(reverse('production:work_claims'),scope,content_type='application/json');self.assertEqual(response.status_code,409)
        response=self.client.post(reverse('production:production_live'),scope,content_type='application/json');self.assertEqual(response.status_code,409);self.assertTrue(response.json()['reset_required'])
        fresh=self.client.get(self.url,{**self.scope,'section':'works'});self.assertEqual(fresh.status_code,200);self.assertGreater(fresh.context['production_reset_epoch'],0)
