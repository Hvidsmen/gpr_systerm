from copy import deepcopy
from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, Role, User
from apps.audit.models import AuditLog
from apps.resources.models import EquipmentType, EquipmentCrewPosition, Position
from apps.resources.position_catalog import brigade_for
from . import test_work_resources as fixtures
from . import resource_replacement as service
from . import table_resource_crew as crew
from .models import ResourceMonthAllocation, ResourceWorkLink, DailyScheduleEntry, WorkMonthAllocation
from .independent_smg import create
from .daily_schedule_table import add_entry,save_table

JAN=fixtures.JAN


class ResourceReplacementTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkResourceTests.setUpTestData.__func__)
    prepare_owner=fixtures.WorkResourceTests.prepare_owner

    def setUp(self):
        fixtures.WorkResourceTests.setUp(self)
        self.target=EquipmentType.objects.create(company=self.company,name='Кран с новым экипажем')
        self.new_position=Position.objects.create(company=self.company,name='Крановщик',default_hourly_rate=80)
        self.new_brigade_id=brigade_for(self.new_position)
        EquipmentCrewPosition.objects.create(company=self.company,equipment_type=self.target,position=self.new_position,count=2)
        self.old_brigade_id=brigade_for(self.position)
        from apps.resources.models import Brigade
        Brigade.objects.filter(pk__in=[self.old_brigade_id,self.new_brigade_id,brigade_for(self.helper)]).update(staffing_current=True)
        self.allocation=ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,kind='equipment',equipment_type=self.equipment,count=2,hours=48,rate=17,crew_data={'shifts_per_day':2})
        crew.sync_monthly(self.planner,self.version,JAN)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,work=self.simple,quantity=10)
        self.link=ResourceWorkLink.objects.create(company=self.company,allocation=self.allocation,work=self.simple)

    def apply(self,owner,source,target,month=JAN,add_crew=True,preview=False):
        signed=service.token(self.planner,owner,source.pk,month,target=None if preview else target.pk,add_crew=add_crew,target_state=None if preview else service.target_stamp(target))
        return service.replace_resource(self.planner,owner,source.pk,target,month=month,add_crew=add_crew,signed=signed,preview=preview)

    def daily(self):
        schedule=create(self.planner,self.obj,JAN,name='Замена')
        entry=add_entry(self.planner,schedule,{'kind':'equipment','equipment':self.equipment,'rate':17,'shifts_per_day':2,'add_crew':True})
        save_table(self.planner,schedule,[{'id':entry.pk,'revision':entry.revision,'changes':[{'date':'2026-01-01','item_id':None,'field':'planned_count','value':'2'},{'date':'2026-01-02','item_id':None,'field':'planned_count','value':'3'}]}])
        entry.refresh_from_db();return schedule,entry

    def test_monthly_keeps_identity_links_values_and_manual_people(self):
        another=ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,kind='equipment',equipment_type=self.equipment,equipment_number='OTHER',count=1)
        crew.sync_monthly(self.planner,self.version,JAN)
        people=self.version.resource_allocations.get(month=JAN,kind='labor',brigade_id=self.old_brigade_id)
        people.count+=3;people.save()
        result=self.apply(self.version,self.allocation,self.target)
        self.allocation.refresh_from_db();people.refresh_from_db();self.link.refresh_from_db()
        self.assertEqual(self.allocation.equipment_type,self.target)
        self.assertEqual((self.allocation.count,self.allocation.hours,self.allocation.rate),(2,48,17))
        self.assertEqual(self.allocation.crew_data['shifts_per_day'],2)
        self.assertEqual(self.link.allocation_id,self.allocation.pk)
        self.assertEqual(people.count,4) # Three additional people plus the other machine.
        self.assertEqual(self.version.resource_allocations.get(month=JAN,kind='labor',brigade_id=self.new_brigade_id).count,8)
        self.assertTrue(result['changes'])
        self.assertTrue(AuditLog.objects.filter(entity_type='planning.resource_replacement').exists())
        self.assertEqual(list(self.equipment.crew_positions.values_list('position_id',flat=True)),[self.position.pk,self.helper.pk])

    def test_preview_writes_nothing_and_no_crew_option_is_respected(self):
        before=list(self.version.resource_allocations.values());logs=AuditLog.objects.count()
        self.apply(self.version,self.allocation,self.target,preview=True)
        self.assertEqual(list(self.version.resource_allocations.values()),before)
        self.assertEqual(AuditLog.objects.count(),logs)
        self.apply(self.version,self.allocation,self.target,add_crew=False)
        self.allocation.refresh_from_db();self.assertEqual(self.allocation.crew_data['members'],[])
        self.assertFalse(self.version.resource_allocations.filter(kind='labor',brigade_id=self.new_brigade_id).exists())

    def test_all_months_changes_only_editable_rows(self):
        feb=date(2026,2,1)
        self.version.end_date=date(2026,2,28);self.version.save()
        type(self.version.workspace).objects.filter(pk=self.version.workspace_id).update(continuation_start=feb)
        self.version.workspace.refresh_from_db()
        future=ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=feb,kind='equipment',equipment_type=self.equipment,count=5,rate=21)
        self.apply(self.version,self.allocation,self.target,month=None)
        self.allocation.refresh_from_db();future.refresh_from_db()
        self.assertEqual(self.allocation.equipment_type,self.equipment)
        self.assertEqual(future.equipment_type,self.target)
        self.assertEqual((future.count,future.rate),(5,21))

    def test_labor_replacement_remaps_plan_crew_without_changing_catalogue(self):
        from apps.resources.models import Brigade
        target=Brigade.objects.get(pk=self.new_brigade_id)
        labor=self.version.resource_allocations.get(month=JAN,kind='labor',brigade_id=self.old_brigade_id)
        pk=labor.pk;labor.count+=3;labor.save()
        self.apply(self.version,labor,target)
        labor.refresh_from_db();self.allocation.refresh_from_db()
        self.assertEqual((labor.pk,labor.brigade_id,labor.count),(pk,target.pk,7))
        self.assertIn(target.pk,[member['brigade_id'] for member in self.allocation.crew_data['members']])
        self.assertFalse(self.version.resource_allocations.filter(month=JAN,kind='labor',brigade_id=self.old_brigade_id).exists())
        self.assertTrue(self.equipment.crew_positions.filter(position=self.position).exists())
        self.allocation.count=3;self.allocation.save();crew.sync_monthly(self.planner,self.version,JAN)
        labor.refresh_from_db();self.assertEqual(labor.count,9)

    def test_daily_keeps_each_day_shifts_and_graph_links(self):
        schedule,entry=self.daily();rows=deepcopy(entry.rows);pk=entry.pk
        link=ResourceWorkLink.objects.create(company=self.company,entry=entry,work=self.simple)
        labor=schedule.entries.get(kind='labor',label=self.position.name)
        values=deepcopy(labor.rows);values[0]['planned_count']=values[0]['planned_workers']='7';labor.rows=values;labor.save()
        self.apply(schedule,entry,self.target,month=None)
        entry.refresh_from_db();labor.refresh_from_db();link.refresh_from_db()
        self.assertEqual(entry.pk,pk);self.assertEqual(link.entry_id,pk)
        self.assertEqual([row['planned_count'] for row in entry.rows],[row['planned_count'] for row in rows])
        self.assertTrue(all(row['hourly_rate']=='17' for row in entry.rows))
        self.assertTrue(all(row['equipment_type_id']==self.target.pk for row in entry.rows))
        self.assertEqual(entry.metadata['shifts_per_day'],2)
        self.assertEqual(labor.rows[0]['planned_count'],'3')
        new=schedule.entries.get(kind='labor',label=self.new_position.name)
        self.assertEqual([row['planned_count'] for row in new.rows[:2]],['8','12'])
        # Subsequent normal saves keep the new identity and crew.
        save_table(self.planner,schedule,[{'id':pk,'revision':entry.revision,'changes':[{'date':'2026-01-01','item_id':None,'field':'planned_count','value':'4'}]}])
        entry.refresh_from_db();new.refresh_from_db()
        self.assertEqual(entry.rows[0]['equipment_type_id'],self.target.pk)
        self.assertEqual(new.rows[0]['planned_count'],'16')

    def test_daily_labor_replacement_keeps_auto_crew_connection(self):
        from apps.resources.models import Brigade
        schedule,entry=self.daily();labor=schedule.entries.get(kind='labor',label=self.position.name)
        target=Brigade.objects.get(pk=self.new_brigade_id)
        self.apply(schedule,labor,target,month=None)
        labor.refresh_from_db();entry.refresh_from_db()
        self.assertEqual(labor.rows[0]['brigade_id'],target.pk)
        self.assertEqual(labor.rows[0]['planned_count'],'4')
        self.assertIn(target.pk,[member['brigade_id'] for member in entry.metadata['table_crew']])

    def test_duplicate_and_closed_days_do_not_partially_change_plan(self):
        ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,kind='equipment',equipment_type=self.target,count=9)
        with self.assertRaises(ValidationError):self.apply(self.version,self.allocation,self.target)
        self.allocation.refresh_from_db();self.assertEqual(self.allocation.equipment_type,self.equipment)
        schedule,entry=self.daily();schedule.locked_before=date(2026,1,15);schedule.save()
        original=deepcopy(entry.rows)
        with self.assertRaises(ValidationError):self.apply(schedule,entry,self.target,month=None)
        entry.refresh_from_db();self.assertEqual(entry.rows,original)

    def test_stale_plan_and_changed_target_crew_require_another_preview(self):
        signed=service.token(self.planner,self.version,self.allocation.pk,JAN,target=self.target.pk,target_state=service.target_stamp(self.target))
        EquipmentCrewPosition.objects.filter(equipment_type=self.target).update(count=3)
        with self.assertRaises(ValidationError):service.replace_resource(self.planner,self.version,self.allocation.pk,self.target,month=JAN,signed=signed)
        signed=service.token(self.planner,self.version,self.allocation.pk,JAN,target=self.target.pk,target_state=service.target_stamp(self.target))
        self.allocation.count=4;self.allocation.save()
        with self.assertRaises(ValidationError):service.replace_resource(self.planner,self.version,self.allocation.pk,self.target,month=JAN,signed=signed)
        self.allocation.refresh_from_db();self.assertEqual(self.allocation.equipment_type,self.equipment)

    def test_http_preview_apply_foreign_choices_and_role_permissions(self):
        url=reverse('planning:resource_replace_gpr',args=[self.version.pk,self.allocation.pk])+'?month='+JAN.isoformat()
        self.client.force_login(self.planner)
        page=self.client.get(url);self.assertEqual(page.status_code,200)
        data={'target':self.target.pk,'snapshot':page.context['form']['snapshot'].value(),'add_crew':'on','action':'preview'}
        page=self.client.post(url,data);self.assertEqual(page.status_code,200)
        self.assertContains(page,'Заменить в плане')
        data.update(confirmation=page.context['form']['confirmation'].value(),action='apply')
        self.assertEqual(self.client.post(url,data).json(),{'saved':True})
        self.allocation.refresh_from_db();self.assertEqual(self.allocation.equipment_type,self.target)
        foreign=EquipmentType.objects.create(company=Company.objects.create(name='Foreign replacement'),name='Чужая')
        self.assertEqual(self.client.post(url,data|{'target':foreign.pk}).status_code,400)
        observer=User.objects.create_user(username='replace-observer',company=self.company,role=Role.objects.get(code='OBSERVER'))
        self.client.force_login(observer);self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.planner);self.planner.assigned_objects.clear()
        self.assertEqual(self.client.get(url).status_code,404)

    def test_current_month_all_months_and_smg_show_replace_buttons(self):
        self.client.force_login(self.planner)
        for url in [reverse('planning:workspace_edit',args=[self.version.pk])+'?month='+str(JAN),reverse('planning:workspace_months',args=[self.version.pk])]:
            response=self.client.get(url);self.assertEqual(response.status_code,200)
            self.assertContains(response,'data-resource-replace-url')
        schedule,entry=self.daily()
        response=self.client.get(reverse('planning:daily_schedule_detail',args=[schedule.pk]))
        self.assertContains(response,reverse('planning:resource_replace_smg',args=[schedule.pk,entry.pk]))

    def test_smg_replacement_compares_with_new_resource_baseline_and_keeps_gpr(self):
        schedule,entry=self.daily()
        frozen={'works':[],'resources':{'equipment':[{'date':'2026-01-01','equipment_type_id':self.target.pk,'equipment_number':'','planned_count':'9','planned_machine_hours':'11','hourly_rate':'99'}]}}
        type(self.version).objects.filter(pk=self.version.pk).update(status='APPROVED',snapshot=frozen)
        schedule.source_id=self.version.pk;schedule.save(update_fields=['source'])
        self.apply(schedule,entry,self.target,month=None)
        entry.refresh_from_db();self.version.refresh_from_db()
        self.assertEqual(self.version.snapshot,frozen)
        self.assertEqual(entry.original[0]['planned_count'],'9')
        self.assertEqual(entry.rows[0]['planned_count'],'2')
        self.assertEqual(entry.rows[0]['hourly_rate'],'17')
        self.assertEqual(entry.original[0]['equipment_type_id'],self.target.pk)

    def test_empty_new_crew_is_visible_before_apply_and_inactive_targets_are_rejected(self):
        empty=EquipmentType.objects.create(company=self.company,name='Без экипажа')
        result=self.apply(self.version,self.allocation,empty,preview=True)
        self.assertTrue(result['empty_crew'])
        empty.is_active=False;empty.save()
        with self.assertRaises(ValidationError):self.apply(self.version,self.allocation,empty)
        self.allocation.refresh_from_db();self.assertEqual(self.allocation.equipment_type,self.equipment)
