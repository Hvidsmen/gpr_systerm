from datetime import timedelta,date
from decimal import Decimal
from django.test import TestCase
from . import test_work_entry_ui as fixtures
from .work_entry_ui import plan_before
from .models import DailyFact
from apps.planning.models import DailySchedule,DailyScheduleEntry

class WorkProgressIndicatorsTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkEntryPresentationTests.setUpTestData.__func__)
    setUp=fixtures.WorkEntryPresentationTests.setUp
    page=fixtures.WorkEntryPresentationTests.page
    def test_cumulative_previous_month_latest_revision_excludes_today_and_draft(self):
        previous=(self.day.replace(day=1)-timedelta(days=1)).replace(day=1)
        for revision,status,qty in [(1,'ARCHIVED',100),(2,'APPLIED',200),(3,'DRAFT',900)]:
            schedule=DailySchedule.objects.create(company=self.company,construction_object=self.obj,month=previous,revision=revision,status=status,created_by=self.users['ADMIN'])
            DailyScheduleEntry.objects.create(schedule=schedule,key='work:'+str(self.work.pk),kind='work',label=self.work.name,metadata={'id':self.work.pk},rows=[{'date':str(previous),'item_id':None,'quantity':str(qty)}])
        DailyFact.objects.create(company=self.company,project_work=self.work,date=previous,actual_quantity=160)
        DailyFact.objects.create(company=self.company,project_work=self.work,date=self.day,actual_quantity=999)
        totals=plan_before(self.obj,self.day);self.assertEqual(totals[(self.work.pk,None)],200)
        response=self.page();choice=next(c for c in response.context['production_work_choices'] if c['work']==self.work.pk and c['item'] is None)
        self.assertEqual(choice['display']['previous'],160);self.assertEqual(choice['display']['deviation'],Decimal('-20'))
        self.assertContains(response,'План до этой даты');self.assertContains(response,'progress-behind')
