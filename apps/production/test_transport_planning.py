from datetime import timedelta
from uuid import uuid4
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from apps.resources.models import EquipmentType,EquipmentCategory,Equipment
from apps.planning.models import DailySchedule,DailyScheduleEntry
from . import transport
from .transport_inline import save_cell
from .transport_models import EquipmentDayRecord


def setup_data(cls):
    from .test_transport import TransportTests
    TransportTests.setUpTestData.__func__(cls)
    cls.category=EquipmentCategory.objects.create(company=cls.company,name='Специализированная техника')
    cls.type.category=cls.category;cls.type.save()
    cls.aux_category=EquipmentCategory.objects.create(company=cls.company,name='Вспомогательная техника')
    cls.bus_type=EquipmentType.objects.create(company=cls.company,name='Автобус вахтовый',category=cls.aux_category)
    cls.bus=Equipment.objects.create(company=cls.company,type=cls.bus_type,name='Автобус',plate_number='BUS-01')
    cls.planned_only=EquipmentType.objects.create(company=cls.company,name='Экскаватор',category=cls.category)
    for kind,count in [(cls.type,Decimal('2.1')),(cls.planned_only,Decimal(1))]:
        DailyScheduleEntry.objects.create(schedule=cls.schedule,key='equipment:'+str(kind.pk),kind='equipment',label=kind.name,rows=[{'date':str(cls.day),'equipment_type_id':kind.pk,'planned_count':str(count)}])


class TransportPlanningTests(TestCase):
    @classmethod
    def setUpTestData(cls):setup_data(cls)
    def setUp(self):
        for machine in [self.machine,self.second,self.bus]:
            transport.move(self.operator,self.camp,self.day,[machine.pk],'ARRIVAL',condition='READY',signed=transport.snapshot(self.operator,self.camp,self.day))
        self.client.force_login(self.operator)
    def page(self,**values):return self.client.get(self.board,{'camp':self.camp.pk,'date':str(self.day),**values})
    def assign(self,machine=None,kind=None,**extra):
        machine=machine or self.machine
        return self.client.post(reverse('production:transport_cell'),{'camp':self.camp.pk,'date':str(self.day),'equipment':machine.pk,'field':'assign','value':(kind or self.type).pk,'signed':transport.snapshot(self.operator,self.camp,self.day),'session':str(uuid4()),**extra},content_type='application/json')
    def test_default_hierarchy_includes_plan_only_and_available_without_creating_facts(self):
        response=self.page();self.assertEqual(response.status_code,200)
        items={row['id']:row for row in response.context['planning_types']}
        self.assertEqual([row['group'] for row in response.context['planning_types']],['Специализированная техника','Специализированная техника'])
        self.assertEqual(items[self.type.pk]['plan'],3);self.assertEqual(items[self.type.pk]['remaining'],3)
        self.assertEqual(len(items[self.type.pk]['choices']),2);self.assertEqual(items[self.type.pk]['rows'],[])
        self.assertEqual(items[self.planned_only.pk]['available'],0);self.assertNotIn(self.bus_type.pk,items)
        self.assertEqual([item['id'] for item in response.context['outside_choices']],[self.bus_type.pk])
        self.assertTrue(items[self.type.pk]['choices'][0]['label'].startswith(self.type.name+' · '))
        self.assertEqual(EquipmentDayRecord.objects.count(),0)
        self.assertContains(response,'data-type-add');self.assertNotContains(response,'data-transport-row=')
        self.assertContains(self.page(view='list'),'data-transport-row=',count=3)
    def test_assign_adds_only_daily_work_and_keeps_independent_condition_and_counts(self):
        transport.set_condition(self.operator,self.camp,self.day,[self.machine.pk],'REPAIR',signed=transport.snapshot(self.operator,self.camp,self.day))
        self.assertEqual(self.assign().status_code,200)
        record=EquipmentDayRecord.objects.get();self.assertEqual(record.status,'WORKED');self.assertIsNone(record.work_id)
        self.assertEqual(transport.condition_on(self.machine,self.day),'REPAIR')
        row=next(row for row in self.page().context['planning_types'] if row['id']==self.type.pk)
        self.assertEqual((row['worked'],row['remaining'],len(row['rows']),len(row['choices'])),(1,2,1,1))
        save_cell(self.operator,self.camp,self.day,self.machine,'status','IDLE',transport.snapshot(self.operator,self.camp,self.day),str(uuid4()))
        row=next(row for row in self.page().context['planning_types'] if row['id']==self.type.pk)
        self.assertEqual((row['worked'],row['idle'],row['remaining']),(0,1,3))
        save_cell(self.operator,self.camp,self.day,self.machine,'status','CLEAR',transport.snapshot(self.operator,self.camp,self.day),str(uuid4()))
        row=next(row for row in self.page().context['planning_types'] if row['id']==self.type.pk)
        self.assertEqual(len(row['choices']),2);self.assertEqual(row['rows'],[])
    def test_other_type_and_other_camp_are_allowed_but_duplicate_and_stale_are_rejected(self):
        self.assertEqual(self.assign(self.bus,self.type).status_code,200)
        record=self.bus.transport_days.get(date=self.day);self.assertEqual(record.planning_type,self.type)
        row=next(row for row in self.page().context['planning_types'] if row['id']==self.type.pk)
        self.assertEqual(row['worked'],1)
        stale=transport.snapshot(self.operator,self.camp,self.day)
        self.assertEqual(self.assign().status_code,200);self.assertEqual(self.assign().status_code,409)
        save_cell(self.operator,self.camp,self.day,self.second,'comment','Уточнение',transport.snapshot(self.operator,self.camp,self.day),str(uuid4()))
        self.assertEqual(self.assign(self.second,signed=stale).status_code,409)
        self.assertEqual(EquipmentDayRecord.objects.count(),2)

    def test_type_pagination_does_not_drop_available_machines_and_search_scope_is_preserved(self):
        from .transport_models import EquipmentCampStay
        machines=Equipment.objects.bulk_create([Equipment(company=self.company,type=self.type,name='Crane extra '+str(i),inventory_number='EXTRA-'+str(i)) for i in range(60)])
        EquipmentCampStay.objects.bulk_create([EquipmentCampStay(company=self.company,equipment=machine,camp=self.camp,arrived_on=self.day) for machine in machines])
        row=next(row for row in self.page().context['planning_types'] if row['id']==self.type.pk)
        self.assertEqual(len(row['choices']),62)
        searched=self.page(q='EXTRA-59');self.assertEqual(len(searched.context['planning_types']),1)
        self.assertEqual(searched.context['planning_types'][0]['choices'][0]['id'],machines[-1].pk)
    def test_readonly_period_has_no_add_and_state_does_not_build_plans(self):
        with patch('core.fact_edit_window.timezone.localdate',return_value=self.day+timedelta(days=3)):
            response=self.page();self.assertFalse(response.context['editable']);self.assertNotContains(response,'data-type-add')
            self.assertEqual(self.assign().status_code,409)
        with patch('apps.production.dispatch.plans',side_effect=AssertionError('Technical view must not calculate daily plans')):
            self.assertEqual(self.page(tab='state').status_code,200)

    def test_model_is_counted_under_parent_and_inaccessible_object_plan_is_ignored(self):
        from apps.personnel.models import CampObjectLink
        model=EquipmentType.objects.create(company=self.company,name='Модель крана',parent=self.type,brand_model='Модель крана')
        self.machine.type=model;self.machine.save()
        CampObjectLink.objects.create(company=self.company,camp=self.camp,construction_object=self.other)
        other_schedule=DailySchedule.objects.create(company=self.company,construction_object=self.other,month=self.day.replace(day=1),status='APPLIED',revision=1,created_by=self.admin)
        DailyScheduleEntry.objects.create(schedule=other_schedule,key='equipment:other',kind='equipment',label=self.type.name,rows=[{'date':str(self.day),'equipment_type_id':self.type.pk,'planned_count':'100'}])
        row=next(row for row in self.page().context['planning_types'] if row['id']==self.type.pk)
        self.assertEqual(row['plan'],3);self.assertEqual(len(row['choices']),2)
        self.assertEqual(self.assign().status_code,200)

    def test_zero_and_missing_plan_are_hidden_until_explicit_outside_action_and_saved_fact_is_retained(self):
        DailyScheduleEntry.objects.create(schedule=self.schedule,key='equipment:zero',kind='equipment',label=self.bus_type.name,rows=[{'date':str(self.day),'equipment_type_id':self.bus_type.pk,'planned_count':'0'}])
        page=self.page();self.assertNotIn(self.bus_type.pk,{item['id'] for item in page.context['planning_types']})
        self.assertNotContains(page,'data-transport-type="'+str(self.bus_type.pk)+'"')
        self.assertContains(page,'data-add-outside')
        self.assertEqual(self.assign(self.bus,self.bus_type).status_code,200)
        page=self.page();self.assertEqual([item['id'] for item in page.context['outside_fact_types']],[self.bus_type.pk])
        self.assertEqual(page.context['outside_fact_count'],1)
        self.assertContains(page,'data-outside-facts>')
        self.assertEqual(self.bus.transport_days.get(date=self.day).status,'WORKED')
        self.schedule.entries.filter(kind='equipment').delete()
        page=self.page();self.assertEqual(page.context['planning_types'],[])
        self.assertContains(page,'В СМГ на выбранный день техника не запланирована')
        self.assertEqual(page.context['outside_fact_count'],1)

    def test_outside_facts_remain_accessible_when_planned_types_are_paginated(self):
        for i in range(26):
            kind=EquipmentType.objects.create(company=self.company,name='Плановый тип '+str(i),category=self.category)
            DailyScheduleEntry.objects.create(schedule=self.schedule,key='equipment:paged:'+str(i),kind='equipment',label=kind.name,rows=[{'date':str(self.day),'equipment_type_id':kind.pk,'planned_count':'1'}])
        self.assertEqual(self.assign(self.bus,self.bus_type).status_code,200)
        for number in [1,2]:
            page=self.page(page=number)
            self.assertEqual(page.context['page_obj'].paginator.count,28)
            self.assertEqual(page.context['outside_fact_count'],1)
            self.assertEqual([item['id'] for item in page.context['outside_fact_types']],[self.bus_type.pk])
            self.assertNotIn(self.bus_type.pk,{item['id'] for item in page.context['planning_types']})
