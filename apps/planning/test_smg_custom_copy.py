from copy import deepcopy
from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError,PermissionDenied
from . import test_smg_variants as fixtures
from .smg_custom_copy import copy_variant,rename
from .daily_schedule_table import add_entry,save_table
from .independent_smg import create
from .models import DailySchedule,WorkResourceAssignment


class CustomSMGCopyTests(TestCase):
    setUpTestData=classmethod(fixtures.SMGVariantTests.setUpTestData.__func__)
    setUp=fixtures.SMGVariantTests.setUp
    new=fixtures.SMGVariantTests.new
    add=fixtures.SMGVariantTests.add
    source=fixtures.SMGVariantTests.source

    def test_rename_draft_and_stale_preview_are_scoped_and_keep_values(self):
        schedule=self.new('До');entry=self.add(schedule);rows=deepcopy(entry.rows)
        page=self.client.get(reverse('planning:smg_rename',args=[schedule.pk]))
        self.assertEqual(page.status_code,200)
        response=self.client.post(reverse('planning:smg_rename',args=[schedule.pk]),{'name':'После','version':schedule.updated_at.isoformat()})
        self.assertEqual(response.status_code,302)
        schedule.refresh_from_db();self.assertEqual(schedule.name,'После')
        entry.refresh_from_db();self.assertEqual(entry.rows,rows)
        with self.assertRaises(ValidationError):rename(self.planner,schedule,'Ошибка','not-a-version')
        DailySchedule.objects.filter(pk=schedule.pk).update(status='APPROVED')
        with self.assertRaises(ValidationError):rename(self.planner,schedule,'Ошибка',schedule.updated_at.isoformat())

    def test_copy_form_allows_name_and_same_month_without_approval(self):
        schedule=self.new('Исходный');entry=self.add(schedule)
        url=reverse('planning:smg_copy_variant',args=[schedule.pk])
        page=self.client.get(url);self.assertContains(page,'Месяц копии');self.assertContains(page,'Название копии')
        response=self.client.post(url,{'month':'2026-01','name':'Альтернативный'})
        self.assertEqual(response.status_code,302)
        new=DailySchedule.objects.exclude(pk=schedule.pk).get();self.assertEqual(new.name,'Альтернативный')
        self.assertEqual(new.month,schedule.month);self.assertEqual(new.entries.get().rows,entry.rows)
        self.assertFalse(new.approval_reviews.exists());self.assertEqual(new.status,'DRAFT')

    def test_new_month_moves_days_drops_extra_dates_and_keeps_source_unchanged(self):
        schedule=self.new('Январь');entry=self.add(schedule)
        save_table(self.planner,schedule,[{'id':entry.pk,'revision':entry.revision,'changes':[{'date':'2026-01-31','item_id':None,'field':'quantity','value':'5'}]}])
        entry.refresh_from_db();saved=deepcopy(entry.rows)
        new=copy_variant(self.planner,schedule,date(2026,2,1),'Февраль')
        self.assertEqual((new.month,new.name,new.locked_before),(date(2026,2,1),'Февраль',None))
        copied=new.entries.get();self.assertEqual(len(copied.rows),28)
        self.assertTrue(all(row['date'].startswith('2026-02-') for row in copied.rows))
        self.assertEqual(sum(Decimal(row['quantity']) for row in copied.rows),7)
        entry.refresh_from_db();self.assertEqual(entry.rows,saved)
        longer=copy_variant(self.planner,new,date(2026,3,1),'Март')
        self.assertEqual([Decimal(r['quantity']) for r in longer.entries.get().rows[-3:]],[0,0,0])

    def test_copied_resource_assignments_move_dates_with_captured_crews(self):
        schedule=self.new()
        row=WorkResourceAssignment.objects.create(company=self.company,schedule=schedule,equipment_type=self.equipment,
            start_date=date(2026,1,5),end_date=date(2026,1,31),count=3,shifts_per_day=2,hours_per_shift=8,include_crew=False,crew_snapshot=[])
        new=copy_variant(self.planner,schedule,date(2026,2,1),'Новый')
        copied=new.resource_assignments.get()
        self.assertEqual((copied.start_date,copied.end_date,copied.count,copied.shifts_per_day),(date(2026,2,5),date(2026,2,28),3,2))
        self.assertTrue(new.entries.filter(kind='equipment').exists())
        row.refresh_from_db();self.assertEqual(row.end_date,date(2026,1,31))
