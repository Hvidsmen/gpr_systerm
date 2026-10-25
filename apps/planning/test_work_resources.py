from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError,PermissionDenied
from apps.resources.models import Position, EquipmentCrewPosition
from . import test_workspace as fixtures
from .models import WorkResourceAssignment, WorkMonthAllocation
from .workspace_services import WorkspaceService, build_workspace_snapshot
from . import work_resources as service
from .independent_smg import create,revise
from .daily_schedule import save_entry,overlay_schedules
from .test_daily_schedule import activate
from .estimate_review import signature

JAN=fixtures.JAN


class WorkResourceTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)
    def setUp(self):
        self.client.force_login(self.planner)
        self.position=Position.objects.create(company=self.company,name='Машинист',default_hourly_rate=100)
        self.helper=Position.objects.create(company=self.company,name='Помощник',default_hourly_rate=50)
        service.brigade_for(self.position)
        service.brigade_for(self.helper)
        self.crew=EquipmentCrewPosition.objects.create(company=self.company,equipment_type=self.equipment,position=self.position,count=1,is_replacement=True)
        EquipmentCrewPosition.objects.create(company=self.company,equipment_type=self.equipment,position=self.helper,count=1)
        self.version=WorkspaceService.create(self.planner,self.obj,'Staff',JAN,date(2026,1,31)).baseline_version
        from apps.accounts.models import User,Role
        self.resource_estimator=User.objects.create_user(username='resource-estimator',company=self.company,role=Role.objects.get(code='ESTIMATOR'))
        self.prepare_owner(self.version)

    def prepare_owner(self,owner):
        from .preparation import record,handoff
        if not record(owner):handoff(self.planner,owner,self.manager,self.resource_estimator)

    def assignment(self,owner,**kwargs):
        self.prepare_owner(owner)
        row=WorkResourceAssignment(equipment_type=self.equipment,work=self.composite,work_item=self.a,start_date=JAN,end_date=date(2026,1,2),count=2,shifts_per_day=2,hours_per_shift=8)
        for key,value in kwargs.items():setattr(row,key,value)
        return service.save(self.manager,owner,row)

    def test_crews_shift_counts_and_common_people_in_gpr_snapshot(self):
        assignment=self.assignment(self.version)
        self.assignment(self.version,equipment_type=None,position=self.position,work=None,work_item=None,count=3,shifts_per_day=1)
        snapshot=build_workspace_snapshot(self.version)
        people=snapshot['resources']['labor']
        by_position={p.pk:sum(Decimal(r['planned_count']) for r in people if r['position_id']==p.pk and r['date']==str(JAN)) for p in [self.position,self.helper]}
        self.assertEqual(by_position,{self.position.pk:7,self.helper.pk:4})
        machines=snapshot['resources']['equipment'];self.assertEqual(machines[0]['planned_count'],'2');self.assertEqual(Decimal(machines[0]['planned_machine_hours']),32)
        self.assertTrue(any(r['is_replacement'] for r in people))
        before=signature(self.version)
        assignment.count=3;service.save(self.manager,self.version,assignment)
        self.assertNotEqual(signature(self.version),before)
        response=self.client.get(reverse('planning:work_resources_gpr',args=[self.version.pk]))
        self.assertEqual(response.status_code,200);self.assertContains(response,'Машинист');self.assertContains(response,'*')

    def test_capture_and_explicit_refresh_preserve_old_crews(self):
        row=self.assignment(self.version)
        before=service.expand(row)
        self.crew.count=5;self.crew.is_replacement=False;self.crew.save()
        self.assertEqual(service.expand(row),before)
        service.save(self.manager,self.version,row,refresh_crew=True)
        row.refresh_from_db();self.assertEqual(row.crew_snapshot[0]['count'],5)
        self.assertFalse(row.crew_snapshot[0]['is_replacement'])

    def test_smg_managed_entries_revision_and_no_double_count(self):
        schedule=create(self.planner,self.obj,JAN)
        row=self.assignment(schedule)
        original_count=schedule.entries.count()
        row.count=3;service.save(self.manager,schedule,row)
        self.assertEqual(schedule.entries.count(),original_count)
        entry=schedule.entries.filter(kind='equipment').get()
        with self.assertRaises(ValidationError):save_entry(self.manager,entry,[],entry.revision)
        snapshot=overlay_schedules({'works':[],'resources':{}},[schedule],JAN,date(2026,1,31))
        self.assertEqual(sum(Decimal(r['planned_count']) for r in snapshot['resources']['equipment']),6)
        schedule=activate(self.planner,schedule)
        with self.assertRaises(ValidationError):service.save(self.manager,schedule,row)
        new=revise(self.planner,schedule)
        self.assertEqual(new.resource_assignments.count(),1)
        self.assertEqual(new.entries.count(),original_count)
        self.assertEqual(sum(Decimal(r['planned_count']) for r in new.entries.get(kind='equipment').rows),6)

    def test_invalid_cross_work_dates_and_hours_are_rejected(self):
        for values in [{'hours_per_shift':13},{'count':0},{'work':self.simple,'work_item':self.a},{'end_date':date(2026,2,1)}]:
            with self.subTest(values=values),self.assertRaises(ValidationError):self.assignment(self.version,**values)
        self.planner.assigned_objects.clear()
        with self.assertRaises(PermissionDenied):service.save(self.planner,self.version,WorkResourceAssignment())
        self.assertEqual(self.client.get(reverse('planning:work_resources_gpr',args=[self.version.pk])).status_code,404)

    def test_catalogue_crew_and_position_pages(self):
        response=self.client.get(reverse('resources:equipment_crew',args=[self.equipment.pk]))
        self.assertContains(response,'замещение');self.assertContains(response,'Сохранить экипаж')
        response=self.client.get(reverse('resources:position_list'),follow=True);self.assertContains(response,'Машинист')

    def test_forecast_keeps_assignment_dates_and_counts_once(self):
        self.assignment(self.version)
        version=fixtures.WorkspaceTests.approve(self,self.version)
        forecast=WorkspaceService.forecast(version.workspace,self.planner,JAN,'BASELINE')
        self.assertEqual(forecast.resource_assignments.count(),1)
        snapshot=build_workspace_snapshot(forecast)
        self.assertEqual(sum(Decimal(r['planned_count']) for r in snapshot['resources']['equipment']),4)
        self.assertEqual({r['date'] for r in snapshot['resources']['equipment'] if Decimal(r['planned_count'])}, {'2026-01-01','2026-01-02'})

    def test_copied_gpr_resources_remain_linked_to_machine_crews(self):
        self.assignment(self.version)
        version=fixtures.WorkspaceTests.approve(self,self.version)
        schedule=create(self.planner,self.obj,JAN,version,copy_from_gpr=True)
        self.prepare_owner(schedule)
        self.assertEqual(schedule.resource_assignments.count(),1)
        from .daily_schedule import schedule_differences
        self.assertEqual(schedule_differences(schedule),{})
        row=schedule.resource_assignments.get();row.count=1
        service.save(self.manager,schedule,row)
        self.assertTrue(schedule_differences(schedule))
        snapshot=overlay_schedules({'works':[],'resources':{}},[schedule],JAN,date(2026,1,31))
        self.assertEqual(sum(Decimal(r['planned_count']) for r in snapshot['resources']['equipment']),2)
        operators=[r for r in snapshot['resources']['labor'] if r['position_id']==self.position.pk]
        self.assertEqual(sum(Decimal(r['planned_count']) for r in operators),4)

    def test_crew_form_updates_marker_without_changing_count(self):
        records=list(self.equipment.crew_positions.order_by('pk'))
        data={'form-TOTAL_FORMS':'2','form-INITIAL_FORMS':'2'}
        for index,row in enumerate(records):
            data.update({f'form-{index}-id':row.pk,f'form-{index}-position':row.position_id,f'form-{index}-count':row.count})
        response=self.client.post(reverse('resources:equipment_crew',args=[self.equipment.pk]),data)
        self.assertEqual(response.status_code,302)
        self.crew.refresh_from_db();self.assertFalse(self.crew.is_replacement);self.assertEqual(self.crew.count,1)

    def test_gpr_personnel_counts_survive_copy_and_daily_edit(self):
        from .models import ResourceMonthAllocation
        ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,kind='labor',brigade=self.brigade,count=3,hours=248)
        version=fixtures.WorkspaceTests.approve(self,self.version)
        schedule=create(self.planner,self.obj,JAN,version,copy_from_gpr=True)
        self.prepare_owner(schedule)
        entry=schedule.entries.get(kind='labor')
        from .daily_schedule import totals,identities
        self.assertEqual(totals(entry,entry.rows)['planned_count'],93)
        values=[{'planned_count':4 if day.day==1 else 3,'planned_hours':8} for day,item in identities(entry)]
        entry=save_entry(self.manager,entry,values,entry.revision)
        self.assertEqual(entry.rows[0]['planned_workers'],'4')
        snapshot=overlay_schedules({'works':[],'resources':{}},[schedule],JAN,date(2026,1,31))
        self.assertEqual(sum(Decimal(r['planned_workers']) for r in snapshot['resources']['labor']),94)

    def test_closed_days_keep_crews_when_future_counts_change(self):
        schedule=create(self.planner,self.obj,JAN)
        row=self.assignment(schedule)
        schedule.locked_before=date(2026,1,2);schedule.save(update_fields=['locked_before'])
        row.count=3;service.save(self.manager,schedule,row)
        entry=schedule.entries.get(kind='equipment')
        self.assertEqual([Decimal(r['planned_count']) for r in sorted(entry.rows,key=lambda r:r['date'])],[2,3])
        service.remove(self.manager,schedule,row)
        entry.refresh_from_db()
        self.assertEqual(entry.rows[0]['date'],'2026-01-01')
        self.assertEqual(len(entry.rows),1);self.assertEqual(Decimal(entry.rows[0]['planned_count']),2)
        response=self.client.get(reverse('planning:work_resources_smg',args=[schedule.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['totals'][0]['count'],4)

    def test_duplicate_positions_in_crew_are_validation_error(self):
        records=list(self.equipment.crew_positions.order_by('pk'))
        data={'form-TOTAL_FORMS':'2','form-INITIAL_FORMS':'2'}
        for index,row in enumerate(records):data.update({f'form-{index}-id':row.pk,f'form-{index}-position':self.position.pk,f'form-{index}-count':1})
        response=self.client.post(reverse('resources:equipment_crew',args=[self.equipment.pk]),data)
        self.assertEqual(response.status_code,200);self.assertTrue(response.context['formset'].non_form_errors())

    def test_assigned_catalogue_records_cannot_be_deleted(self):
        self.assignment(self.version)
        response=self.client.post(reverse('works:work_delete',args=[self.composite.pk]))
        self.assertEqual(response.status_code,403)
        response=self.client.post(reverse('works:work_item_delete',args=[self.a.pk]))
        self.assertEqual(response.status_code,403)
        response=self.client.post(reverse('resources:equipment_type_delete',args=[self.equipment.pk]))
        self.assertEqual(response.status_code,403)
        self.assertTrue(type(self.equipment).objects.filter(pk=self.equipment.pk).exists())
