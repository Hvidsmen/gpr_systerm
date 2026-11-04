from datetime import timedelta
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from apps.accounts.models import Company, User, Role
from apps.resources.models import Brigade, Employee
from apps.projects.models import ConstructionObject
from .test_housing import HousingTests
from .models import Person,StaffingSlot,CampStay,HousingEvent,Camp,CampObjectLink
from .plan_assignments import save_assignment
from .rotation import rotation_rows
from .housing_plan import position_headers, monthly_plan


class PlanAssignmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):HousingTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.role=Brigade.objects.create(company=self.company,name='Начальник участка')
        self.client.force_login(self.hr)
        self.url=reverse('personnel:plan_assignment_create')
        self.data={'employee':self.second_employee.pk,'planning_position':self.role.pk,'construction_object':self.obj.pk,'housing_camp':self.a.pk,'assigned_from':str(self.day),'assigned_until':'','pattern':''}

    def assigned(self):return StaffingSlot.objects.get(planning_position=self.role)

    def test_assign_any_employee_without_rewriting_hr_or_creating_arrival_and_repost_is_idempotent(self):
        before=(CampStay.objects.count(),HousingEvent.objects.count())
        original=self.second_employee.position_id
        self.assertEqual(self.client.post(self.url,self.data).status_code,302)
        slot=self.assigned();self.assertEqual(slot.person.employee_id,self.second_employee.pk)
        self.assertEqual(slot.construction_object,self.obj);self.assertEqual(slot.housing_camp,self.a)
        self.second_employee.refresh_from_db();self.assertEqual(self.second_employee.position_id,original)
        self.assertEqual((CampStay.objects.count(),HousingEvent.objects.count()),before)
        self.assertEqual(self.client.post(self.url,self.data).status_code,302)
        self.assertEqual(StaffingSlot.objects.filter(planning_position=self.role).count(),1)
        rows=rotation_rows(self.company,[self.a],self.day)[0]
        row=next(r for r in rows if r['person'].employee_id==self.second_employee.pk)
        self.assertEqual(row['position_label'],self.role.name);self.assertEqual(row['state'],'UNKNOWN');self.assertFalse(row['verified'])
        headers=position_headers(self.company,rows,self.context())
        group=next(g for g in headers if g['name']==self.role.name)
        self.assertEqual(group['people'],1)
        self.assertEqual([p['on_shift'] for p in group['plans']],[0,0])

    def context(self):
        return {'housing_plan_months':[self.day.replace(day=1)],'housing_plan_groups':[
            {'object':self.obj,'kind':kind,'used_for':[],'used_for_ids':[self.obj.pk],'columns':[{'month':self.day.replace(day=1),'available':True,'versions':'1'}],
             'rows':[{'id':self.role.pk,'name':self.role.name,'values':[Decimal(1)]}]} for kind in ('ГПР','СМГ')]}

    def test_actual_arrival_covers_both_sources_once(self):
        self.client.post(self.url,self.data);slot=self.assigned()
        CampStay.objects.create(company=self.company,person=slot.person,camp=self.a,arrived_on=self.day)
        rows=rotation_rows(self.company,[self.a],self.day)[0]
        group=next(g for g in position_headers(self.company,rows,self.context()) if g['name']==self.role.name)
        self.assertEqual(group['people'],1);self.assertEqual(group['on_shift'],1)
        self.assertEqual([p['on_shift'] for p in group['plans']],[1,1])
        self.assertEqual([p['shortage'] for p in group['plans']],[0,0])

    def test_filter_uses_plan_role_even_when_hr_position_differs(self):
        from apps.resources.position_catalog import position_for
        self.client.post(self.url,self.data)
        position=position_for(self.role)
        rows=rotation_rows(self.company,[self.a],self.day,position=position)[0]
        self.assertEqual([r['person'].employee_id for r in rows],[self.second_employee.pk])
        response=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':self.day,'position':position.pk})
        self.assertContains(response,self.second_employee.display_name)
        self.assertEqual(response.context['position_groups'][0]['name'],self.role.name)

    def test_archived_employee_can_have_an_existing_assignment_closed(self):
        self.client.post(self.url,self.data);slot=self.assigned()
        self.second_employee.is_active=False;self.second_employee.save()
        response=self.client.post(reverse('personnel:plan_assignment_edit',args=[slot.pk]),{**self.data,'assigned_until':str(self.day),'version':slot.updated_at.isoformat()})
        self.assertEqual(response.status_code,302);slot.refresh_from_db();self.assertEqual(slot.assigned_until,self.day)

    def test_assignment_is_visible_when_employee_is_actually_in_another_camp(self):
        self.client.post(self.url,self.data);slot=self.assigned()
        CampStay.objects.create(company=self.company,person=slot.person,camp=self.b,arrived_on=self.day)
        rows=rotation_rows(self.company,[self.a],self.day)[0]
        row=next(r for r in rows if r['person'].employee_id==self.second_employee.pk)
        self.assertEqual(row['actual_elsewhere'],self.b);self.assertFalse(row['verified']);self.assertEqual(row['state'],'UNKNOWN')
        group=next(g for g in position_headers(self.company,rows,self.context()) if g['name']==self.role.name)
        self.assertEqual(group['people'],1);self.assertEqual(group['on_shift'],0)

    def test_shared_camp_does_not_cover_another_object_but_child_covers_parent(self):
        parent=ConstructionObject.objects.create(company=self.company,project=self.project,name='Родитель')
        self.obj.parent=parent;self.obj.save()
        self.client.post(self.url,self.data);slot=self.assigned()
        CampStay.objects.create(company=self.company,person=slot.person,camp=self.a,arrived_on=self.day)
        context=self.context();context['housing_plan_groups'][0]['object']=parent
        context['housing_plan_groups'][1]['object']=self.obj2
        group=next(g for g in position_headers(self.company,rotation_rows(self.company,[self.a],self.day)[0],context) if g['name']==self.role.name)
        by_object={p['object'].pk:p['on_shift'] for p in group['plans']}
        self.assertEqual(by_object[parent.pk],1);self.assertEqual(by_object[self.obj2.pk],0)

    def test_effective_dates_and_overlapping_assignment_guard(self):
        self.client.post(self.url,{**self.data,'assigned_from':str(self.day+timedelta(days=1)),'assigned_until':str(self.day+timedelta(days=2))})
        def ids(day):return [r['person'].employee_id for r in rotation_rows(self.company,[self.a],day)[0] if r.get('planning_position_id')]
        self.assertEqual(ids(self.day),[]);self.assertEqual(ids(self.day+timedelta(days=1)),[self.second_employee.pk]);self.assertEqual(ids(self.day+timedelta(days=3)),[])
        response=self.client.post(self.url,{**self.data,'construction_object':self.obj2.pk})
        self.assertContains(response,'уже есть назначение');self.assertEqual(StaffingSlot.objects.filter(planning_position=self.role).count(),1)

    def test_employee_filter_hides_overlapping_assignments_and_restores_available_people(self):
        available=Employee.objects.create(company=self.company,last_name='Свободный',first_name='Сотрудник',is_active=True)
        self.client.post(self.url,self.data)
        response=self.client.get(self.url,{'assigned_from':self.day})
        choices=response.context['form'].fields['employee'].queryset
        self.assertFalse(choices.filter(pk=self.second_employee.pk).exists())
        self.assertTrue(choices.filter(pk=available.pk).exists())
        slot=self.assigned();slot.assigned_from=self.day-timedelta(days=2);slot.assigned_until=self.day-timedelta(days=1);slot.save()
        response=self.client.get(self.url,{'assigned_from':self.day})
        self.assertTrue(response.context['form'].fields['employee'].queryset.filter(pk=self.second_employee.pk).exists())

    def test_bound_form_keeps_stale_selected_employee_for_clear_overlap_error(self):
        self.client.post(self.url,self.data)
        response=self.client.post(self.url,{**self.data,'construction_object':self.obj2.pk})
        self.assertContains(response,'уже есть назначение')
        self.assertEqual(str(response.context['form']['employee'].value()),str(self.second_employee.pk))

    def test_edit_uses_version_and_preserves_employee_identity(self):
        self.client.post(self.url,self.data);slot=self.assigned()
        url=reverse('personnel:plan_assignment_edit',args=[slot.pk])
        version=slot.updated_at.isoformat()
        self.assertEqual(self.client.post(url,{**self.data,'assigned_until':str(self.day+timedelta(days=2)),'version':version,'employee':self.employee.pk}).status_code,302)
        slot.refresh_from_db();self.assertEqual(slot.person.employee_id,self.second_employee.pk)
        response=self.client.post(url,{**self.data,'version':version})
        self.assertContains(response,'изменено другим пользователем')

    def test_foreign_company_archived_employee_unlinked_camp_and_bad_dates_refused(self):
        other=Company.objects.create(name='Other assignment company')
        outsider=Employee.objects.create(company=other,last_name='Other')
        wrong_role=Brigade.objects.create(company=other,name='Other role')
        wrong_camp=Camp.objects.create(company=other,name='Other camp')
        for change in ({'employee':outsider.pk},{'planning_position':wrong_role.pk},{'housing_camp':wrong_camp.pk},{'housing_camp':self.b.pk,'construction_object':self.obj.pk},{'assigned_until':str(self.day-timedelta(days=1))}):
            response=self.client.post(self.url,{**self.data,**change});self.assertEqual(response.status_code,200);self.assertTrue(response.context['form'].errors)
        self.second_employee.is_active=False;self.second_employee.save()
        response=self.client.post(self.url,self.data);self.assertTrue(response.context['form'].errors)
        self.assertFalse(StaffingSlot.objects.filter(planning_position=self.role).exists())

    def test_permissions_csrf_and_foreign_edit(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.hr)
        self.assertEqual(client.post(self.url,self.data).status_code,403)
        for user in (self.dispatcher,User.objects.create_user(username='assign-commandant',company=self.company,role=Role.objects.get(code='COMMANDANT'))):
            self.client.force_login(user);self.assertEqual(self.client.get(self.url).status_code,403);self.assertEqual(self.client.post(self.url,self.data).status_code,403)
        self.client.force_login(self.hr);self.client.post(self.url,self.data);slot=self.assigned()
        other=Company.objects.create(name='Foreign editor')
        foreign=User.objects.create_user(username='assign-foreign',company=other,role=Role.objects.get(code='PERSONNEL_DISPATCHER'))
        self.client.force_login(foreign);self.assertEqual(self.client.get(reverse('personnel:plan_assignment_edit',args=[slot.pk])).status_code,404)

    def test_empty_plan_header_has_assignment_button_with_role_object_and_day(self):
        from apps.planning.models import GlobalPlanVersion
        GlobalPlanVersion.objects.create(company=self.company,construction_object=self.obj,start_date=self.day.replace(day=1),end_date=self.day+timedelta(days=100),version_number=1,status='APPROVED',snapshot={'works':[],'resources':{'labor':[{'date':str(self.day),'brigade_id':self.role.pk,'planned_workers':'1','label':self.role.name}]}})
        response=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':self.day})
        self.assertContains(response,'Назначить сотрудника')
        self.assertContains(response,f'planning_position={self.role.pk}&amp;construction_object={self.obj.pk}')
        response=self.client.get(self.url,{'planning_position':self.role.pk,'construction_object':self.obj.pk,'housing_camp':self.a.pk,'assigned_from':self.day})
        self.assertEqual(response.status_code,200);self.assertEqual(str(response.context['form']['planning_position'].value()),str(self.role.pk))
