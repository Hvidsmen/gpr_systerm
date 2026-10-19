import json
from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError,PermissionDenied
from apps.works.models import ProjectWork
from . import test_daily_schedule as fixtures
from .daily_schedule_table import save_table,add_entry
from . import daily_schedule as service


class ScheduleTableTests(TestCase):
    setUpTestData=classmethod(fixtures.DailyScheduleTests.setUpTestData.__func__)
    prepare=fixtures.DailyScheduleTests.prepare

    def edits(self,entry,value='12',field='quantity',day='2026-02-01',item=None):
        return {'id':entry.pk,'revision':entry.revision,'changes':[{'date':day,'item_id':item,'field':field,'value':value}]}

    def test_inline_save_changes_one_day_and_preserves_other_days_and_source(self):
        self.prepare()
        url=reverse('planning:daily_schedule_detail',args=[self.schedule.pk])
        response=self.client.post(url,json.dumps({'entries':[self.edits(self.entry,'12')]}),content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.entry.refresh_from_db();self.assertEqual(self.entry.revision,1)
        self.assertEqual(Decimal(self.entry.rows[0]['quantity']),12)
        self.assertEqual(len(self.entry.rows),28)
        self.source.refresh_from_db();self.assertEqual(self.source.snapshot,self.original)
        self.assertContains(self.client.get(url),'Сохранить изменения СМГ')

    def test_atomic_batch_and_stale_revision_do_not_overwrite(self):
        self.prepare();labor=self.schedule.entries.get(kind='labor')
        with self.assertRaises(ValidationError):save_table(self.planner,self.schedule,[self.edits(self.entry),self.edits(labor,'-1','planned_count')])
        self.entry.refresh_from_db();self.assertEqual(self.entry.revision,0)
        save_table(self.planner,self.schedule,[self.edits(self.entry)])
        with self.assertRaises(ValidationError):save_table(self.planner,self.schedule,[self.edits(self.entry,'99')])
        self.entry.refresh_from_db();self.assertEqual(Decimal(self.entry.rows[0]['quantity']),12)

    def test_new_work_is_applied_and_carried_to_next_revision(self):
        self.prepare()
        work=ProjectWork.objects.create(company=self.company,section=self.simple.section,name='Additional',unit='m',unit_price=20)
        entry=add_entry(self.planner,self.schedule,{'kind':'work','work':work})
        self.assertEqual(entry.original,[])
        save_table(self.planner,self.schedule,[self.edits(entry,'5')])
        self.schedule=fixtures.activate(self.planner,self.schedule)
        result=service.overlay(self.source,self.original,date(2026,2,1),date(2026,2,28))
        spec=next(w for w in result['works'] if w['id']==work.pk)
        self.assertEqual(sum(Decimal(r['quantity']) for r in spec['daily']),5)
        next_schedule=service.create(self.planner,self.source,date(2026,2,1))
        copied=next_schedule.entries.get(key=entry.key)
        self.assertEqual(sum(Decimal(r['quantity']) for r in copied.rows),5)
        self.source.refresh_from_db();self.assertEqual(self.source.snapshot,self.original)
        with self.assertRaises(PermissionDenied):save_table(self.planner,self.schedule,[self.edits(entry)])

    def test_added_resource_retains_identity_and_rate(self):
        self.prepare()
        entry=add_entry(self.planner,self.schedule,{'kind':'equipment','equipment':self.equipment,'reference':'TEST-1','rate':Decimal(25)})
        save_table(self.planner,self.schedule,[self.edits(entry,'3','planned_count')])
        entry.refresh_from_db()
        self.assertEqual(entry.rows[0]['equipment_number'],'TEST-1')
        self.assertEqual(Decimal(entry.rows[0]['hourly_rate']),25)
        with self.assertRaises(ValidationError):add_entry(self.planner,self.schedule,{'kind':'equipment','equipment':self.equipment,'reference':'TEST-1','rate':Decimal(25)})

    def test_add_form_and_fuel_overlay(self):
        self.prepare()
        url=reverse('planning:daily_schedule_detail',args=[self.schedule.pk])
        response=self.client.post(url,{'kind':'fuel','fuel':'DIESEL','reference':'Site','rate':'55.00'})
        self.assertEqual(response.status_code,302)
        entry=self.schedule.entries.get(kind='fuel')
        save_table(self.planner,self.schedule,[self.edits(entry,'100','planned_liters'),] )
        fixtures.activate(self.planner,self.schedule)
        result=service.overlay(self.source,self.original,date(2026,2,1),date(2026,2,28))
        self.assertEqual(sum(Decimal(r['planned_liters']) for r in result['resources']['fuel']),100)
        self.assertEqual(Decimal(result['resources']['fuel'][0]['price_per_liter']),55)

    def test_observer_cannot_save_or_add(self):
        from apps.accounts.models import User,Role
        self.prepare()
        observer=User.objects.create_user(username='table-observer',company=self.company,role=Role.objects.get(code='OBSERVER'))
        self.client.force_login(observer)
        url=reverse('planning:daily_schedule_detail',args=[self.schedule.pk])
        self.assertEqual(self.client.post(url,json.dumps({'entries':[self.edits(self.entry)]}),content_type='application/json').status_code,403)
        self.assertEqual(self.client.post(url,{'kind':'fuel','fuel':'DIESEL'}).status_code,403)

    def test_estimator_sees_only_visible_monthly_smg_works_including_added_zero_row(self):
        from .models import DailyScheduleEntry
        from . import estimate_review
        from .approval_test_helpers import estimator_check
        self.prepare()
        historical=ProjectWork.objects.create(company=self.company,section=self.simple.section,name='Historical only',unit='m')
        DailyScheduleEntry.objects.create(schedule=self.schedule,key=f'work:{historical.pk}',kind='work',label=historical.name,metadata={'id':historical.pk},original=[],rows=[])
        added=ProjectWork.objects.create(company=self.company,section=self.simple.section,name='Added zero',unit='m')
        add_entry(self.planner,self.schedule,{'kind':'work','work':added})
        self.assertEqual(estimate_review.work_ids(self.schedule),{self.simple.pk,added.pk})
        response=self.client.get(reverse('planning:daily_schedule_detail',args=[self.schedule.pk]))
        visible={entry.metadata['id'] for section in response.context['sections'] if section['kind']=='work' for group in section['groups'] for entry in group['entries']}
        self.assertEqual(visible,estimate_review.work_ids(self.schedule))
        task=estimator_check(self.schedule,self.planner)
        self.assertNotIn(str(historical.pk),task.decisions)
        self.assertEqual(len(task.decisions),2)

    def test_new_schedule_does_not_seed_empty_historical_specs(self):
        from apps.works.progress import work_specification
        from .models import GlobalPlanVersion,DailySchedule,DailyScheduleEntry,ReportSnapshotProjection
        self.prepare()
        historical=ProjectWork.objects.create(company=self.company,section=self.simple.section,name='Old history',unit='m')
        spec=work_specification(historical);spec.update(plans=[],daily=[])
        snapshot=self.original.copy();snapshot['works']=[*snapshot['works'],spec]
        GlobalPlanVersion.objects.filter(pk=self.source.pk).update(snapshot=snapshot)
        ReportSnapshotProjection.objects.filter(version=self.source).delete()
        DailyScheduleEntry.objects.create(schedule=self.schedule,key=f'work:{historical.pk}',kind='work',label=historical.name,metadata={'id':historical.pk},original=[],rows=[])
        DailySchedule.objects.filter(pk=self.schedule.pk).update(status='APPLIED')
        self.source.refresh_from_db()
        new=service.create(self.planner,self.source,date(2026,2,1))
        self.assertFalse(new.entries.filter(key=f'work:{historical.pk}').exists())
        self.assertTrue(new.entries.filter(key=f'work:{self.simple.pk}').exists())

    def test_composite_children_are_editable_but_derived_work_is_not(self):
        self.prepare(composite=True)
        save_table(self.planner,self.schedule,[self.edits(self.entry,'40',item=self.a.pk)])
        self.entry.refresh_from_db()
        self.assertEqual(Decimal(next(r for r in self.entry.rows if r['item_id']==self.a.pk and r['date']=='2026-02-01')['quantity']),40)
        with self.assertRaises(ValidationError):save_table(self.planner,self.schedule,[self.edits(self.entry,'1',field='work_total')])
