from calendar import monthrange
from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from apps.planning.models import GlobalPlanVersion,DailySchedule,DailyScheduleEntry
from apps.projects.models import ConstructionObject
from .test_housing import HousingTests
from .housing_plan import monthly_plan,add_month
from apps.resources.position_catalog import brigade_for


class HousingPlanTests(TestCase):
    @classmethod
    def setUpTestData(cls):HousingTests.setUpTestData.__func__(cls)
    def setUp(self):
        self.parent=ConstructionObject.objects.create(company=self.company,project=self.project,name='КТП общий')
        for obj in [self.obj,self.obj2]:obj.parent=self.parent;obj.save()
        self.month=self.day.replace(day=1);self.brigade=brigade_for(self.position)
        values=[]
        for offset in range(5):
            month=add_month(self.month,offset)
            for n in range(monthrange(month.year,month.month)[1]):values.append({'date':str(month+timedelta(days=n)),'brigade_id':self.brigade,'planned_workers':'10','label':'Монтажник'})
        self.gpr=GlobalPlanVersion.objects.create(company=self.company,construction_object=self.parent,start_date=self.month,end_date=add_month(self.month,5)-timedelta(days=1),version_number=1,status='APPROVED',snapshot={'works':[],'resources':{'labor':values}})
    def test_parent_gpr_fallback_is_named_and_deduplicated_for_two_children(self):
        result=monthly_plan(self.hr,self.a,self.day)
        self.assertEqual(len(result['housing_plan_months']),5)
        self.assertEqual(len(result['housing_plan_groups']),1)
        group=result['housing_plan_groups'][0];self.assertEqual(group['object'],self.parent);self.assertEqual(group['kind'],'ГПР')
        self.assertEqual(group['rows'][0]['values'],[Decimal(10)]*5)
        self.assertEqual(set(group['used_for']),{self.obj.name,self.obj2.name})
    def test_partial_child_smg_and_parent_gpr_are_separate_without_combined_total(self):
        smg=DailySchedule.objects.create(company=self.company,construction_object=self.obj,month=self.month,status='APPLIED',revision=1,created_by=self.hr)
        DailyScheduleEntry.objects.create(schedule=smg,key='labor:test',kind='labor',label='Монтажник',rows=[{'date':str(self.month+timedelta(days=n)),'brigade_id':self.brigade,'planned_count':'3'} for n in range(monthrange(self.month.year,self.month.month)[1])])
        result=monthly_plan(self.hr,self.a,self.day);self.assertNotIn('housing_plan_totals',result)
        groups={(g['object'].pk,g['kind']):g for g in result['housing_plan_groups']}
        self.assertEqual(groups[self.obj.pk,'СМГ']['rows'][0]['values'][0],3)
        self.assertEqual(groups[self.parent.pk,'ГПР']['rows'][0]['values'][0],10)
        self.assertIsNone(groups[self.obj.pk,'СМГ']['rows'][0]['values'][1])
    def test_unknown_plan_is_not_zero_and_december_crosses_year(self):
        self.b.objects_served.remove(self.obj2)
        result=monthly_plan(self.hr,self.b,self.day)
        self.assertEqual(result['housing_plan_groups'],[])
        self.assertEqual(str(add_month(self.month.replace(month=12),1)),f'{self.month.year+1}-01-01')

    def test_position_header_contains_separate_sources_and_totals_are_per_source(self):
        from .housing_plan import position_headers
        from .rotation import rotation_rows
        from .models import StaffingSlot
        StaffingSlot.objects.create(company=self.company,group=self.group,person=self.second_person,housing_camp=self.a,status='МО')
        smg=DailySchedule.objects.create(company=self.company,construction_object=self.obj,month=self.month,status='APPLIED',revision=1,created_by=self.hr)
        DailyScheduleEntry.objects.create(schedule=smg,key='labor:header',kind='labor',label='Монтажник',rows=[{'date':str(self.month+timedelta(days=n)),'brigade_id':self.brigade,'planned_count':'3'} for n in range(monthrange(self.month.year,self.month.month)[1])])
        context=monthly_plan(self.hr,self.a,self.day);rows,_,stats=rotation_rows(self.company,[self.a],self.day)
        groups=position_headers(self.company,rows,context)
        self.assertEqual(len(groups),1);self.assertEqual(groups[0]['people'],2);self.assertEqual(groups[0]['rest'],1)
        plans={p['kind']:p for p in groups[0]['plans']}
        self.assertEqual(plans['СМГ']['months'][0]['value'],3);self.assertEqual(plans['ГПР']['months'][0]['value'],10)
        self.assertEqual(sorted(s['current_total'] for s in context['housing_plan_groups']),[3,10]);self.assertNotIn('housing_plan_totals',context)

    def test_planned_position_without_people_is_visible_without_fabricating_a_vacancy(self):
        from .housing_plan import position_headers
        result=monthly_plan(self.hr,self.a,self.day)
        groups=position_headers(self.company,[],result)
        self.assertEqual(len(groups),1);self.assertEqual(groups[0]['people'],0);self.assertEqual(groups[0]['vacancies'],0)
        self.assertEqual(len(groups[0]['plans'][0]['months']),5)
        self.assertEqual(position_headers(self.company,[],result,query='несуществующий сотрудник'),[])
