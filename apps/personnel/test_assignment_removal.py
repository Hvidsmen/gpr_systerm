"""Removing a plan connection preserves actual movements, schedules and its audit."""
import html
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from django.test import TestCase, Client
from django.urls import reverse
from apps.accounts.models import Company, Role, User
from apps.resources.models import Brigade
from apps.production.dispatch_models import EmployeeActivity
from .models import StaffingSlot, CampStay, HousingEvent, Person
from .plan_assignments import assignment_history
from .rotation import rotation_rows
from .forms import PersonScheduleForm
from . import housing


class AssignmentRemovalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_housing import HousingTests
        HousingTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.client.force_login(self.hr)
        self.role=Brigade.objects.create(company=self.company,name='Начальник участка')
        self.create=reverse('personnel:plan_assignment_create')
        self.data={'employee':self.second_employee.pk,'planning_position':self.role.pk,'construction_object':self.obj.pk,'housing_camp':self.a.pk,'assigned_from':str(self.day-timedelta(days=5)),'pattern':self.pattern.pk}
        self.assertEqual(self.client.post(self.create,self.data).status_code,302)
        self.row=StaffingSlot.objects.get(planning_position=self.role)
        self.url=reverse('personnel:plan_assignment_remove',args=[self.row.pk])

    def payload(self):return {'version':self.row.updated_at.isoformat(),'reason':'Ошибочно выбрана должность'}

    def test_add_button_remains_without_current_plan_and_allows_second_employee(self):
        response=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':self.day})
        group=next(g for g in response.context['position_groups'] if g['name']==self.role.name)
        self.assertEqual(group['people'],1);self.assertEqual(group['plans'],[])
        links=re.findall(r'href="([^"]+)">Назначить сотрудника</a>',response.content.decode())
        params=[parse_qs(urlsplit(html.unescape(link)).query) for link in links]
        self.assertTrue(any(p.get('planning_position')==[str(self.role.pk)] and p.get('construction_object')==[str(self.obj.pk)] for p in params))
        self.assertEqual(self.client.post(self.create,{**self.data,'employee':self.employee.pk}).status_code,302)
        response=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':self.day})
        group=next(g for g in response.context['position_groups'] if g['name']==self.role.name)
        self.assertEqual(group['people'],2);self.assertContains(response,'Назначить сотрудника')

    def test_general_add_button_available_after_last_connection_is_removed(self):
        self.client.post(self.url,self.payload())
        response=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':self.day})
        self.assertContains(response,'Назначить сотрудника')

    def test_roster_has_one_assignment_action_and_edit_page_contains_change_and_remove(self):
        response=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk})
        content=response.content.decode()
        self.assertEqual(content.count(f'href="{reverse("personnel:plan_assignment_edit",args=[self.row.pk])}">Назначение</a>'),1)
        self.assertNotIn(reverse('personnel:plan_assignment_change',args=[self.row.pk]),content)
        self.assertNotIn(reverse('personnel:plan_assignment_remove',args=[self.row.pk]),content)
        edit=self.client.get(reverse('personnel:plan_assignment_edit',args=[self.row.pk]))
        self.assertContains(edit,'Сменить назначение с выбранной даты')
        self.assertContains(edit,'Удалить назначение')

    def test_add_uses_assignment_camp_when_actual_camp_differs_in_multi_camp_view(self):
        CampStay.objects.create(company=self.company,person=self.row.person,camp=self.b,arrived_on=self.day)
        response=self.client.get(reverse('personnel:housing'),{'camps':[self.a.pk,self.b.pk],'date':self.day})
        group=next(g for g in response.context['position_groups'] if g['name']==self.role.name)
        self.assertEqual(group['assignment_initial']['housing_camp'],self.a.pk)

    def test_removal_reopens_plan_shortage_for_both_smg_and_gpr_without_deleting_presence(self):
        from .housing_plan import position_headers
        from .test_plan_assignments import PlanAssignmentTests
        CampStay.objects.create(company=self.company,person=self.row.person,camp=self.a,arrived_on=self.day)
        def header():
            rows=rotation_rows(self.company,[self.a],self.day)[0]
            return next(g for g in position_headers(self.company,rows,PlanAssignmentTests.context(self)) if g['name']==self.role.name)
        self.assertEqual([p['shortage'] for p in header()['plans']],[0,0])
        self.client.post(self.url,self.payload())
        group=header();self.assertEqual(group['people'],0)
        self.assertEqual([p['shortage'] for p in group['plans']],[1,1])
        self.assertEqual([p['on_shift'] for p in group['plans']],[0,0])
        self.assertTrue(housing.present_stays(self.company,self.day).filter(person_id=self.row.person_id).exists())

    def test_get_only_confirms_post_retains_record_and_audits_actor(self):
        self.assertContains(self.client.get(self.url),'Удалить назначение')
        self.row.refresh_from_db();self.assertIsNone(self.row.cancelled_at)
        self.assertEqual(self.client.post(self.url,self.payload()).status_code,302)
        self.row.refresh_from_db();self.assertIsNotNone(self.row.cancelled_at)
        self.assertEqual(self.row.cancelled_by,self.hr);self.assertEqual(self.row.cancellation_reason,self.payload()['reason'])
        self.assertEqual(assignment_history(self.row.person)[0].pk,self.row.pk)
        response=self.client.get(reverse('personnel:housing_history',args=[self.row.person_id]))
        self.assertContains(response,'Удалено');self.assertContains(response,self.hr.username);self.assertContains(response,'Ошибочно выбрана должность')
        self.assertEqual(self.client.put(self.url).status_code,405)

    def test_removal_returns_to_current_roster_without_report_date(self):
        chosen=self.day-timedelta(days=1)
        response=self.client.post(self.url,{**self.payload(),'date':str(chosen)})
        self.assertNotIn('date',parse_qs(urlsplit(response.url).query))

    def test_removed_assignment_does_not_count_on_past_current_or_future_dates_and_can_be_reassigned(self):
        self.client.post(self.url,self.payload())
        for day in (self.row.assigned_from,self.day,self.day+timedelta(days=30)):
            rows=rotation_rows(self.company,[self.a],day)[0]
            self.assertFalse(any(r.get('planning_position_id')==self.role.pk for r in rows))
        self.assertEqual(self.client.post(self.create,self.data).status_code,302)
        self.assertEqual(StaffingSlot.objects.filter(planning_position=self.role,cancelled_at=None).count(),1)

    def test_mo_movements_employee_production_fact_and_schedule_are_preserved(self):
        # First input has already happened; deleting the connection must not undo it.
        HousingEvent.objects.create(company=self.company,person=self.row.person,date=self.day-timedelta(days=6),kind='DEPARTURE',from_camp=self.a,departure_destination='REST',recorded_by=self.hr)
        fact=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=self.second_employee,general=True)
        self.row.calendar={str(self.day):'7'};self.row.save();self.row.refresh_from_db()
        before=(list(CampStay.objects.values()),list(HousingEvent.objects.values()),list(EmployeeActivity.objects.values()))
        self.assertEqual(self.client.post(self.url,self.payload()).status_code,302)
        self.assertEqual((list(CampStay.objects.values()),list(HousingEvent.objects.values()),list(EmployeeActivity.objects.values())),before)
        person=Person.objects.get(pk=self.row.person_id);schedule=housing.schedule_slot(person)
        self.assertNotEqual(schedule.pk,self.row.pk);self.assertIsNone(schedule.planning_position_id)
        self.assertEqual(schedule.pattern_id,self.row.pattern_id);self.assertEqual(schedule.calendar,self.row.calendar)
        self.assertIsNone(schedule.housing_camp_id)
        self.second_employee.refresh_from_db();self.assertEqual(self.second_employee.position_id,self.position.pk)
        self.assertTrue(EmployeeActivity.objects.filter(pk=fact.pk).exists())
        row=next(r for r in rotation_rows(self.company,[self.a],self.day)[0] if r['person'].pk==person.pk)
        self.assertEqual(row['state'],'MO');self.assertIsNone(row['planning_position_id'])

    def test_duplicate_remove_does_not_make_duplicate_schedules_or_change_new_assignment(self):
        payload=self.payload();self.client.post(self.url,payload)
        self.client.post(self.create,self.data)
        person=Person.objects.get(pk=self.row.person_id);selected=person.schedule_slot_id;count=StaffingSlot.objects.count()
        self.assertEqual(self.client.post(self.url,payload).status_code,302)
        self.assertEqual(StaffingSlot.objects.count(),count)
        person.refresh_from_db();self.assertEqual(person.schedule_slot_id,selected)

    def test_stale_form_refuses_delete_without_changes(self):
        payload=self.payload();self.row.assigned_until=self.day;self.row.save()
        self.assertContains(self.client.post(self.url,payload),'изменено другим пользователем')
        self.row.refresh_from_db();self.assertIsNone(self.row.cancelled_at)
        self.assertEqual(Person.objects.get(pk=self.row.person_id).schedule_slot_id,self.row.pk)

    def test_deleted_record_cannot_be_edited_changed_or_selected_as_schedule(self):
        self.client.post(self.url,self.payload());self.row.refresh_from_db()
        response=self.client.get(reverse('personnel:plan_assignment_edit',args=[self.row.pk]))
        self.assertContains(response,'Удалённое назначение');self.assertNotContains(response,'Сохранить назначение')
        self.assertContains(self.client.post(reverse('personnel:plan_assignment_edit',args=[self.row.pk]),{**self.data,'version':self.row.updated_at.isoformat()}),'Назначение удалено')
        other=Brigade.objects.create(company=self.company,name='Мастер')
        response=self.client.post(reverse('personnel:plan_assignment_change',args=[self.row.pk]),{**self.data,'planning_position':other.pk,'version':self.row.updated_at.isoformat()})
        self.assertContains(response,'Назначение удалено')
        self.assertFalse(StaffingSlot.objects.filter(previous_assignment=self.row).exists())
        form=PersonScheduleForm(person=Person.objects.get(pk=self.row.person_id))
        self.assertFalse(form.fields['slot'].queryset.filter(pk=self.row.pk).exists())
        self.assertEqual(self.client.get(reverse('personnel:slot_edit',args=[self.row.group_id,self.row.pk])).status_code,404)

    def test_remove_changed_assignment_keeps_chain_and_does_not_reopen_predecessor(self):
        other=Brigade.objects.create(company=self.company,name='Мастер')
        self.client.post(reverse('personnel:plan_assignment_change',args=[self.row.pk]),{**self.data,'planning_position':other.pk,'assigned_from':str(self.day),'version':self.row.updated_at.isoformat()})
        child=StaffingSlot.objects.get(previous_assignment=self.row)
        self.client.post(reverse('personnel:plan_assignment_remove',args=[child.pk]),{'version':child.updated_at.isoformat()})
        self.row.refresh_from_db();self.assertEqual(self.row.replaced_on,self.day)
        rows=rotation_rows(self.company,[self.a],self.day)[0]
        self.assertFalse(any(r.get('planning_position_id') in (self.role.pk,other.pk) for r in rows))
        previous=rotation_rows(self.company,[self.a],self.day-timedelta(days=1))[0]
        self.assertTrue(any(r.get('planning_position_id')==self.role.pk for r in previous))
        self.assertEqual(len(assignment_history(self.row.person)),2)

    def test_remove_predecessor_preserves_current_assignment_and_schedule(self):
        other=Brigade.objects.create(company=self.company,name='Мастер')
        self.client.post(reverse('personnel:plan_assignment_change',args=[self.row.pk]),{**self.data,'planning_position':other.pk,'assigned_from':str(self.day),'version':self.row.updated_at.isoformat()})
        child=StaffingSlot.objects.get(previous_assignment=self.row);self.row.refresh_from_db()
        self.assertEqual(self.client.post(self.url,self.payload()).status_code,302)
        self.assertEqual(Person.objects.get(pk=self.row.person_id).schedule_slot_id,child.pk)
        rows=rotation_rows(self.company,[self.a],self.day)[0]
        self.assertTrue(any(r.get('planning_position_id')==other.pk for r in rows))

    def test_permissions_csrf_and_tenant_boundary(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.hr)
        self.assertEqual(client.post(self.url,self.payload()).status_code,403)
        commandant=User.objects.create_user(username='remove-commandant',company=self.company,role=Role.objects.get(code='COMMANDANT'))
        for user in (self.dispatcher,commandant):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code,403);self.assertEqual(self.client.post(self.url,self.payload()).status_code,403)
        foreign=User.objects.create_user(username='remove-foreign',company=self.other,role=Role.objects.get(code='PERSONNEL_DISPATCHER'))
        self.client.force_login(foreign)
        self.assertEqual(self.client.get(self.url).status_code,404);self.assertEqual(self.client.post(self.url,self.payload()).status_code,404)
        self.row.refresh_from_db();self.assertIsNone(self.row.cancelled_at)
