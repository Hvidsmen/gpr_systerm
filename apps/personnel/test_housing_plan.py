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
        self.assertEqual(plans['СМГ']['peak'],3);self.assertEqual(plans['СМГ']['shortage'],2)
        self.assertEqual(plans['ГПР']['peak'],10);self.assertEqual(plans['ГПР']['shortage'],9)
        self.assertEqual(sorted(s['current_total'] for s in context['housing_plan_groups']),[3,10]);self.assertNotIn('housing_plan_totals',context)

    def test_planned_position_without_people_is_visible_without_fabricating_a_vacancy(self):
        from .housing_plan import position_headers
        result=monthly_plan(self.hr,self.a,self.day)
        groups=position_headers(self.company,[],result)
        self.assertEqual(len(groups),1);self.assertEqual(groups[0]['people'],0);self.assertEqual(groups[0]['vacancies'],0)
        self.assertEqual(groups[0]['working'],0);self.assertEqual(groups[0]['travelling'],0)
        self.assertEqual(len(groups[0]['plans'][0]['months']),5)
        self.assertEqual(position_headers(self.company,[],result,query='несуществующий сотрудник'),[])

    def test_peak_is_maximum_of_month_totals_not_sum_of_individual_role_peaks(self):
        from .housing_plan import position_headers
        from apps.resources.models import Brigade
        other=Brigade.objects.create(company=self.company,name='Инженер')
        context={'housing_plan_months':[self.month,add_month(self.month,1)],'housing_plan_groups':[{'object':self.parent,'kind':'ГПР','used_for':[],'used_for_ids':[self.obj.pk],'columns':[{'month':self.month,'available':True,'versions':'1'},{'month':add_month(self.month,1),'available':True,'versions':'1'}],'rows':[{'id':self.brigade,'name':'Монтажник','values':[Decimal(10),Decimal(0)]},{'id':other.pk,'name':'Инженер','values':[Decimal(0),Decimal(20)]}]}]}
        groups=position_headers(self.company,[],context)
        self.assertEqual(context['housing_plan_groups'][0]['peak_total'],20)
        self.assertEqual(sum(p['peak'] for g in groups for p in g['plans']),30)
        self.assertEqual(context['housing_plan_groups'][0]['shortage'],20)

    def test_missing_plan_is_not_zero_and_deficit_never_negative(self):
        from .housing_plan import position_headers
        from .rotation import rotation_rows
        context=monthly_plan(self.hr,self.a,self.day)
        source=context['housing_plan_groups'][0]
        source['rows'][0]['values']=[None]*5
        source['columns']=[{**c,'available':False} for c in source['columns']]
        groups=position_headers(self.company,rotation_rows(self.company,[self.a],self.day)[0],context)
        self.assertIsNone(source['peak_total']);self.assertIsNone(source['shortage']);self.assertIsNone(groups[0]['plans'][0]['shortage'])
        source['rows'][0]['values']=[Decimal(0)]*5
        source['columns']=[{**c,'available':True} for c in source['columns']]
        groups=position_headers(self.company,rotation_rows(self.company,[self.a],self.day)[0],context)
        self.assertEqual(source['peak_total'],0);self.assertEqual(source['shortage'],0);self.assertEqual(groups[0]['plans'][0]['shortage'],0)

    def test_other_camp_employee_does_not_cover_this_source_shortage(self):
        from .housing_plan import position_headers
        from .rotation import rotation_rows
        from .models import Camp,CampStay
        outside=Camp.objects.create(company=self.company,name='Другой городок')
        CampStay.objects.create(company=self.company,camp=outside,person=self.second_person,arrived_on=self.day)
        rows,_,_=rotation_rows(self.company,[self.a,outside],self.day)
        context=monthly_plan(self.hr,self.a,self.day)
        groups=position_headers(self.company,rows,context)
        self.assertEqual(groups[0]['on_shift'],2)
        self.assertEqual(groups[0]['plans'][0]['on_shift'],1)
        self.assertEqual(groups[0]['plans'][0]['shortage'],9)
        self.assertEqual(context['housing_plan_groups'][0]['on_shift'],1)

    def test_parent_plan_uses_all_selected_descendant_camps_even_with_child_smg(self):
        from .housing_plan import position_headers
        from .rotation import rotation_rows
        from .models import CampStay,CampObjectLink
        CampObjectLink.objects.filter(camp=self.a,construction_object=self.obj2).delete()
        CampStay.objects.create(company=self.company,camp=self.b,person=self.second_person,arrived_on=self.day)
        smg=DailySchedule.objects.create(company=self.company,construction_object=self.obj,month=self.month,status='APPLIED',revision=1,created_by=self.hr)
        DailyScheduleEntry.objects.create(schedule=smg,key='labor:scope',kind='labor',label='Монтажник',rows=[{'date':str(self.month+timedelta(days=n)),'brigade_id':self.brigade,'planned_count':'3'} for n in range(monthrange(self.month.year,self.month.month)[1])])
        context=monthly_plan(self.hr,self.a,self.day,camps=[self.a,self.b])
        groups=position_headers(self.company,rotation_rows(self.company,[self.a,self.b],self.day)[0],context)
        plans={plan['kind']:plan for plan in groups[0]['plans']}
        self.assertEqual(plans['СМГ']['on_shift'],1);self.assertEqual(plans['СМГ']['shortage'],2)
        self.assertEqual(plans['ГПР']['on_shift'],2);self.assertEqual(plans['ГПР']['shortage'],8)

    def forecast_with_history(self, history):
        from apps.planning.models import PlanningWorkspace
        from django.utils import timezone
        target=add_month(self.month,2)
        workspace=PlanningWorkspace.objects.create(company=self.company,construction_object=self.parent,name='ГПР КТП',start_date=self.month,end_date=self.gpr.end_date)
        future=[{'date':str(add_month(self.month,n)+timedelta(days=d)),'brigade_id':self.brigade,'label':'Монтажник','planned_workers':str(count)} for n,count in [(2,69),(3,143),(4,125)] for d in range(monthrange(add_month(self.month,n).year,add_month(self.month,n).month)[1])]
        return GlobalPlanVersion.objects.create(company=self.company,construction_object=self.parent,workspace=workspace,version_kind='FORECAST',planning_month=target,scenario='ENTERED',start_date=self.month,end_date=self.gpr.end_date,version_number=6,status='COMPLETED',approved_at=timezone.now(),snapshot={'version_kind':'FORECAST','planning_month':str(target),'scenario':'ENTERED','works':[],'resources':{'labor':history+future}})

    def test_completed_forecast_keeps_explicit_november_35_equal_to_gpr_report(self):
        from .housing_plan import position_headers
        from apps.accounts.models import User,Role
        from apps.planning.report_services import build_matrix
        from apps.planning.pivot_sources import sources
        november=add_month(self.month,1)
        version=self.forecast_with_history([{'date':str(november+timedelta(days=n)),'brigade_id':self.brigade,'label':'Монтажник','planned_workers':'35'} for n in range(monthrange(november.year,november.month)[1])])
        actor=User.objects.create_user(username='roster-report-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        filters={'start':november,'end':add_month(november,1)-timedelta(days=1),'mode':'current','sections':['labor'],'objects':ConstructionObject.objects.filter(pk=self.parent.pk),'plan_type':'gpr','selected_plan':version}
        report=build_matrix(actor,filters,source_overrides=sources(actor,filters),plan_only=True)
        def leaves(nodes):
            return [row for node in nodes for row in node['rows']+leaves(node['children'])]
        report_value=leaves(report['objects'][0]['sections'][0]['groups'])[0]['cells'][0]['plan']
        self.assertEqual(report_value,35)
        context=monthly_plan(self.hr,self.a,self.day)
        source=context['housing_plan_groups'][0]
        self.assertEqual(source['rows'][0]['values'],[None,report_value,Decimal(69),Decimal(143),Decimal(125)])
        groups=position_headers(self.company,[],context)
        self.assertEqual(source['totals'][1]['value'],report_value)
        self.assertEqual(groups[0]['plans'][0]['months'][1]['value'],report_value)
        self.assertEqual(source['totals'][1]['versions'],'6')
        self.assertIsNone(source['totals'][0]['value'])
        from django.urls import reverse
        self.client.force_login(self.hr)
        page=self.client.get(reverse('personnel:housing'),{'date':str(self.day),'camps':[self.a.pk]})
        self.assertEqual(page.status_code,200)
        self.assertEqual(page.context['housing_plan_groups'][0]['totals'][1]['value'],35)
        self.assertContains(page,'data-plan-month-value="35"')

    def test_historical_partial_days_average_known_values_per_position(self):
        from apps.resources.models import Brigade
        other=Brigade.objects.create(company=self.company,name='Инженер с частичной историей')
        month=add_month(self.month,1)
        self.forecast_with_history([
            {'date':str(month),'brigade_id':self.brigade,'planned_workers':None},
            {'date':str(month+timedelta(days=1)),'brigade_id':self.brigade,'planned_workers':'35'},
            {'date':str(month+timedelta(days=1)),'brigade_id':other.pk,'planned_workers':'10'},
            {'date':str(month+timedelta(days=2)),'brigade_id':other.pk,'planned_workers':'20'},
        ])
        group=monthly_plan(self.hr,self.a,self.day)['housing_plan_groups'][0]
        rows={row['id']:row for row in group['rows']}
        self.assertEqual(rows[self.brigade]['values'][1],35)
        self.assertEqual(rows[other.pk]['values'][1],15)
        self.assertFalse(group['columns'][0]['available'])

    def test_explicit_zero_in_history_is_zero_and_absent_history_stays_missing(self):
        month=add_month(self.month,1)
        self.forecast_with_history([{'date':str(month),'brigade_id':self.brigade,'planned_count':'0'}])
        group=monthly_plan(self.hr,self.a,self.day)['housing_plan_groups'][0]
        self.assertIsNone(group['rows'][0]['values'][0]);self.assertFalse(group['columns'][0]['available'])
        self.assertEqual(group['rows'][0]['values'][1],0);self.assertTrue(group['columns'][1]['available'])
