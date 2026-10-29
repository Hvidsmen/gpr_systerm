from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from .models import DailyFact,LaborFact,FuelFact
from .dispatch_models import EmployeeActivity,EmployeeWorkLink


class ProductionCopyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_production_entry import ProductionEntryTests
        ProductionEntryTests.setUpTestData.__func__(cls)
    def setUp(self):
        self.user=self.users['DISPATCHER'];self.client.force_login(self.user);self.session=str(uuid4());self.previous=self.day-timedelta(days=1)
        self.old=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.previous,employee=self.employee,position=self.employee.position,shift=2,entry_direction='works',assignment_reviewed=True,work=self.work,reported_by=self.user)
        EmployeeWorkLink.objects.create(company=self.company,activity=self.old,work=self.work)
        DailyFact.objects.create(company=self.company,project_work=self.work,date=self.previous,actual_quantity=99)
    def copy(self,kind='people',**extra):
        return self.client.post(reverse('production:production_copy_previous'),{'construction_object':self.obj.pk,'date':str(self.day),'session':self.session,'kind':kind,'reset_epoch':0,**extra},content_type='application/json')
    def test_people_copy_is_explicit_additive_idempotent_and_does_not_copy_quantities_or_links(self):
        page=self.client.get(self.url,{'construction_object':self.obj.pk,'date':self.day,'section':'works'})
        self.assertContains(page,'data-copy-previous="people"');self.assertContains(page,'data-copy-previous="links"')
        self.assertFalse(any(form['employee'].value() for group in page.context['groups'] if group['prefix']=='labor' for form in group['formset']))
        response=self.copy();self.assertEqual(response.status_code,200,response.content);self.assertEqual(response.json()['added'],1)
        row=EmployeeActivity.objects.get(date=self.day);self.assertEqual(row.shift,2);self.assertFalse(row.assignment_reviewed);self.assertIsNone(row.work_id);self.assertEqual(row.reported_by,self.user)
        self.assertEqual(LaborFact.objects.get(date=self.day).actual_workers,1);self.assertFalse(DailyFact.objects.filter(date=self.day).exists())
        self.assertEqual(self.copy().json()['added'],0);self.assertEqual(EmployeeActivity.objects.filter(date=self.day).count(),1)
    def test_links_copy_only_to_existing_people_and_current_facts_and_keep_existing_relations(self):
        self.assertEqual(self.copy('links').json()['added'],0);self.copy()
        row=EmployeeActivity.objects.get(date=self.day)
        self.assertEqual(self.copy('links',revisions={str(row.pk):0}).json()['added'],0)
        DailyFact.objects.create(company=self.company,project_work=self.work,date=self.day,actual_quantity=0)
        response=self.copy('links',revisions={str(row.pk):0});self.assertEqual(response.json()['added'],1,response.content)
        row.refresh_from_db();self.assertTrue(row.assignment_reviewed);self.assertEqual(row.work_id,self.work.pk)
        self.assertEqual(DailyFact.objects.get(date=self.day).actual_quantity,0)
        self.assertEqual(self.copy('links',revisions={str(row.pk):0}).json()['added'],0)
        self.assertEqual(self.copy('links',revisions={str(row.pk):row.links_revision}).json()['skipped'],1)
        self.assertEqual(row.work_links.count(),1)
    def test_exact_previous_day_skip_inactive_local_drafts_and_other_direction(self):
        self.assertEqual(self.copy(keep=[f'employee:{self.employee.pk}:2']).json()['added'],0)
        self.employee.is_active=False;self.employee.save();self.assertEqual(self.copy().json()['added'],0)
        self.employee.is_active=True;self.employee.save()
        self.old.date=self.day-timedelta(days=2);self.old.save();self.assertEqual(self.copy().json()['added'],0)
        self.old.date=self.previous;self.old.entry_direction='labor';self.old.save();self.assertEqual(self.copy().json()['added'],0)
    def test_closed_day_reset_epoch_roles_and_scope_reject_without_mutations(self):
        from .day_reset import epoch,KIND,identity
        from apps.audit.models import AuditLog
        AuditLog.objects.create(user=self.user,action='DELETE',entity_type=KIND,entity_id=identity(self.obj,self.day))
        self.assertEqual(self.copy().status_code,409);self.assertFalse(EmployeeActivity.objects.filter(date=self.day).exists())
        with patch('core.fact_edit_window.timezone.localdate',return_value=self.day+timedelta(days=3)):
            self.assertEqual(self.copy(reset_epoch=epoch(self.obj,self.day)).status_code,409)
        self.client.force_login(self.users['PERSONNEL_DISPATCHER']);self.assertEqual(self.copy().status_code,403)
        self.client.force_login(self.second);self.assertEqual(self.copy(construction_object=self.other.pk).status_code,409)

    def test_legacy_staff_totals_require_existing_explicit_replacement_confirmation(self):
        LaborFact.objects.create(company=self.company,construction_object=self.obj,date=self.day,brigade=self.brigade,actual_workers=9)
        self.assertEqual(self.copy().status_code,409)
        self.assertFalse(EmployeeActivity.objects.filter(date=self.day).exists())
        self.assertEqual(LaborFact.objects.get(date=self.day).actual_workers,9)
        self.assertEqual(self.copy(replace_legacy=True).json()['added'],1)


class EquipmentCopyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_transport import TransportTests
        TransportTests.setUpTestData.__func__(cls)
    def setUp(self):
        from . import transport
        from django.utils import timezone
        self.day=timezone.localdate();self.transport=transport;self.previous=self.day-timedelta(days=1);self.session=str(uuid4());self.client.force_login(self.operator)
        for machine in [self.machine,self.second]:transport.move(self.operator,self.camp,self.previous,[machine.pk],'ARRIVAL',condition='READY',signed=transport.snapshot(self.operator,self.camp,self.previous))
        transport.save_day(self.operator,self.camp,self.previous,[self.machine.pk],'WORKED',construction_object=self.obj,work=self.work,signed=transport.snapshot(self.operator,self.camp,self.previous),comment='Смена')
        transport.save_day(self.operator,self.camp,self.previous,[self.second.pk],'IDLE',signed=transport.snapshot(self.operator,self.camp,self.previous))
    def copy(self,**extra):
        return self.client.post(reverse('production:transport_copy_previous'),{'camp':self.camp.pk,'date':str(self.day),'session':self.session,'signed':self.transport.snapshot(self.operator,self.camp,self.day),**extra},content_type='application/json')
    def test_work_target_and_drivers_copy_without_repair_fields_or_fuel_and_repeat_is_safe(self):
        from .transport_inline import save_cell
        from .transport_details_models import EquipmentDayDetails
        save_cell(self.operator,self.camp,self.previous,self.machine,'shift_one_employee',self.employee.pk,self.transport.snapshot(self.operator,self.camp,self.previous),self.session)
        save_cell(self.operator,self.camp,self.previous,self.machine,'defects','Не копировать ремонт',self.transport.snapshot(self.operator,self.camp,self.previous),self.session)
        self.transport.set_condition(self.operator,self.camp,self.day,[self.machine.pk],'REPAIR',signed=self.transport.snapshot(self.operator,self.camp,self.day))
        response=self.copy();self.assertEqual(response.status_code,200,response.content);self.assertEqual(response.json()['added'],2)
        row=self.machine.transport_days.get(date=self.day);self.assertEqual((row.status,row.work_id,row.comment),('WORKED',self.work.pk,'Смена'))
        detail=EquipmentDayDetails.objects.get(equipment=self.machine,date=self.day);self.assertEqual(detail.shift_one_employee_id,self.employee.pk)
        self.assertEqual(self.transport.condition_on(self.machine,self.day),'REPAIR');self.assertFalse(FuelFact.objects.exists())
        self.assertEqual(self.copy().json()['added'],0)
    def test_current_records_are_preserved_and_departed_machines_queued(self):
        self.transport.save_day(self.operator,self.camp,self.day,[self.machine.pk],'IDLE',comment='Сегодня',signed=self.transport.snapshot(self.operator,self.camp,self.day))
        self.transport.move(self.operator,self.camp,self.day,[self.second.pk],'DEPARTURE',signed=self.transport.snapshot(self.operator,self.camp,self.day))
        response=self.copy();self.assertEqual(response.status_code,200,response.content);self.assertEqual(response.json()['added'],0);self.assertTrue(response.json()['warnings'])
        row=self.machine.transport_days.get(date=self.day);self.assertEqual((row.status,row.comment),('IDLE','Сегодня'))
        self.assertFalse(self.second.transport_days.filter(date=self.day).exists())

    def test_inactive_driver_is_left_for_review_without_losing_valid_vehicle(self):
        from .transport_inline import save_cell
        from .transport_details_models import EquipmentDayDetails
        save_cell(self.operator,self.camp,self.previous,self.machine,'shift_one_employee',self.employee.pk,self.transport.snapshot(self.operator,self.camp,self.previous),self.session)
        self.employee.is_active=False;self.employee.save()
        response=self.copy();self.assertEqual(response.status_code,200,response.content)
        self.assertEqual((response.json()['added'],response.json()['skipped']),(2,0));self.assertTrue(response.json()['warnings'])
        self.assertEqual(self.machine.transport_days.get(date=self.day).status,'WORKED')
        self.assertFalse(EquipmentDayDetails.objects.filter(date=self.day,equipment=self.machine,shift_one_employee=self.employee).exists())

    def test_legacy_equipment_totals_are_preserved_and_attributed_machine_is_skipped(self):
        from .models import EquipmentFact
        EquipmentFact.objects.create(company=self.company,construction_object=self.obj,date=self.day,equipment_type=self.type,actual_count=8)
        response=self.copy();self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(self.machine.transport_days.filter(date=self.day).exists())
        self.assertEqual(EquipmentFact.objects.get(date=self.day).actual_count,8)
        self.assertEqual(response.json()['added'],1);self.assertTrue(response.json()['warnings'])
