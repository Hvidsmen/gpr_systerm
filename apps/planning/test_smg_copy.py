from datetime import date
from decimal import Decimal
from copy import deepcopy
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError,PermissionDenied
from apps.accounts.models import User,Role
from apps.projects.models import ConstructionObject
from apps.resources.position_catalog import position_for
from . import test_workspace as fixtures
from .models import GlobalPlanVersion,WorkMonthAllocation,ResourceMonthAllocation,WorkResourceAssignment,DailySchedule
from .workspace_services import WorkspaceService,build_workspace_snapshot
from . import independent_smg as independent,daily_schedule as daily
from .smg_copy import copy_from_gpr


class SMGCopyTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)

    def setUp(self):
        self.admin=User.objects.create_user(username='smg-copy-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.client.force_login(self.admin)
        self.source=WorkspaceService.create(self.planner,self.obj,'Approved copy source',fixtures.JAN,date(2026,1,31)).baseline_version
        for work,quantity in [(self.simple,10),(self.composite,2)]:WorkMonthAllocation.objects.create(company=self.company,version=self.source,work=work,month=fixtures.JAN,quantity=quantity)
        for kind,fields in [('labor',dict(brigade=self.brigade,count=2,hours=248)),('equipment',dict(equipment_type=self.equipment,count=1,hours=248)),('fuel',dict(fuel_type='DIESEL',liters=31))]:ResourceMonthAllocation.objects.create(company=self.company,version=self.source,month=fixtures.JAN,kind=kind,**fields)
        position=position_for(self.brigade)
        WorkResourceAssignment.objects.create(company=self.company,version=self.source,work=self.composite,work_item=self.a,equipment_type=self.equipment,start_date=fixtures.JAN,end_date=date(2026,1,31),count=2,crew_snapshot=[dict(position_id=position.pk,name=position.name,count=1,hourly_rate='50',brigade_id=self.brigade.pk,is_replacement=False)])
        WorkResourceAssignment.objects.create(company=self.company,version=self.source,work=self.composite,work_item=self.b,fuel_type='DIESEL',daily_liters=Decimal('2.5'),fuel_price=50,start_date=fixtures.JAN,end_date=date(2026,1,31),include_crew=False)
        snapshot=build_workspace_snapshot(self.source)
        GlobalPlanVersion.objects.filter(pk=self.source.pk).update(status='APPROVED',snapshot=snapshot)
        self.source.refresh_from_db()
        self.schedule=independent.create(self.planner,self.obj,fixtures.JAN,self.source)
        self.url=reverse('planning:smg_copy_gpr',args=[self.schedule.pk])

    def total(self,kind,field):
        return sum((Decimal(str(daily.row_value(kind,row,field))) for entry in self.schedule.entries.filter(kind=kind) for row in entry.rows),Decimal(0))

    def test_copy_all_works_composition_and_resources_once(self):
        before=deepcopy(self.source.snapshot)
        result=copy_from_gpr(self.planner,self.schedule,self.source,'all')
        self.assertEqual(result['works'],2)
        self.assertGreater(result['resources'],0)
        work=self.schedule.entries.get(key='work:'+str(self.composite.pk))
        self.assertEqual(sum(Decimal(row['quantity']) for row in work.rows if row['item_id']==self.a.pk),self.a.quantity_per_unit*2)
        self.assertEqual(self.total('labor','planned_count'),Decimal(31*4))
        self.assertEqual(self.total('equipment','planned_count'),Decimal(31*3))
        self.assertEqual(self.total('fuel','planned_liters'),Decimal('108.5'))
        self.assertEqual(self.schedule.resource_assignments.count(),2)
        rows={entry.pk:deepcopy(entry.rows) for entry in self.schedule.entries.all()}
        again=copy_from_gpr(self.planner,self.schedule,self.source,'all')
        self.assertEqual(again['works'],0)
        self.assertEqual({entry.pk:entry.rows for entry in self.schedule.entries.all()},rows)
        self.assertEqual(self.schedule.resource_assignments.count(),2)
        self.source.refresh_from_db();self.assertEqual(self.source.snapshot,before)

    def test_button_and_post_existing_schedule(self):
        response=self.client.get(reverse('planning:daily_schedule_detail',args=[self.schedule.pk]))
        self.assertContains(response,'Скопировать из ГПР')
        self.assertContains(response,'id="smg-copy-gpr"')
        self.assertEqual(self.client.get(self.url).status_code,405)
        response=self.client.post(self.url,{'source':self.source.pk,'scope':'all'})
        self.assertEqual(response.status_code,302)
        self.assertGreater(self.total('labor','planned_count'),0)

    def test_manual_work_and_resources_preserved_by_default(self):
        work=self.schedule.entries.get(key='work:'+str(self.simple.pk))
        daily.save_entry(self.planner,work,[{'quantity':7 if day.day==5 else 0} for day,item in daily.identities(work)],work.revision)
        labor=self.schedule.entries.filter(kind='labor').first()
        from .preparation import handoff
        estimator=User.objects.create_user(username='copy-estimator',company=self.company,role=Role.objects.get(code='ESTIMATOR'))
        handoff(self.planner,self.schedule,self.manager,estimator)
        daily.save_entry(self.manager,labor,[dict(planned_count=3,planned_hours=24) for day,item in daily.identities(labor)],labor.revision)
        old_work=self.schedule.entries.get(pk=work.pk).rows;old_labor=self.schedule.entries.get(pk=labor.pk).rows
        result=copy_from_gpr(self.planner,self.schedule,self.source,'all')
        self.assertEqual(result['kept_works'],1);self.assertGreater(result['kept_resources'],0)
        self.assertEqual(self.schedule.entries.get(pk=work.pk).rows,old_work)
        self.assertEqual(self.schedule.entries.get(pk=labor.pk).rows,old_labor)
        self.assertFalse(self.schedule.resource_assignments.exists())
        copy_from_gpr(self.planner,self.schedule,self.source,'work',True)
        self.assertEqual(sum(Decimal(row['quantity']) for row in self.schedule.entries.get(pk=work.pk).rows),10)

    def test_locked_days_preserved_and_copied_assignments_start_in_open_period(self):
        self.schedule.locked_before=date(2026,1,16);self.schedule.save(update_fields=['locked_before'])
        past={entry.key:[deepcopy(row) for row in entry.rows if row['date']<'2026-01-16'] for entry in self.schedule.entries.all()}
        copy_from_gpr(self.planner,self.schedule,self.source,'all',True)
        for entry in self.schedule.entries.exclude(key__startswith='assignment:'):
            self.assertEqual([row for row in entry.rows if row['date']<'2026-01-16'],past.get(entry.key,[]))
        self.assertTrue(all(row.start_date==date(2026,1,16) for row in self.schedule.resource_assignments.all()))
        self.assertTrue(all(row['date']>='2026-01-16' for entry in self.schedule.entries.filter(key__startswith='assignment:') for row in entry.rows))
        self.assertGreater(self.total('fuel','planned_liters'),0)

    def test_status_source_and_permission_checks(self):
        self.client.force_login(self.manager)
        with self.assertRaises(PermissionDenied):copy_from_gpr(self.manager,self.schedule,self.source,'all')
        observer=User.objects.create_user(username='smg-copy-observer',company=self.company,role=Role.objects.get(code='OBSERVER'))
        self.client.force_login(observer);self.assertEqual(self.client.post(self.url,{'source':self.source.pk,'scope':'all'}).status_code,403)
        DailySchedule.objects.filter(pk=self.schedule.pk).update(status='SUBMITTED')
        with self.assertRaises(ValidationError):copy_from_gpr(self.admin,self.schedule,self.source,'all')
        DailySchedule.objects.filter(pk=self.schedule.pk).update(status='DRAFT')
        GlobalPlanVersion.objects.filter(pk=self.source.pk).update(status='DRAFT')
        with self.assertRaises(ValidationError):copy_from_gpr(self.admin,self.schedule,self.source,'all')

    def test_cross_object_source_rejected_and_audit_failure_rolls_back(self):
        other=ConstructionObject.objects.create(company=self.company,project=self.obj.project,name='Another copy object')
        source=WorkspaceService.create(self.admin,other,'Other source',fixtures.JAN,date(2026,1,31)).baseline_version
        with self.assertRaises(PermissionDenied):copy_from_gpr(self.admin,self.schedule,source,'all')
        before={entry.pk:entry.rows for entry in self.schedule.entries.all()}
        with patch('apps.planning.independent_smg.audit',side_effect=ValidationError('Audit error')):
            with self.assertRaises(ValidationError):copy_from_gpr(self.admin,self.schedule,self.source,'all')
        self.assertEqual({entry.pk:entry.rows for entry in self.schedule.entries.all()},before)
        self.assertFalse(self.schedule.resource_assignments.exists())
