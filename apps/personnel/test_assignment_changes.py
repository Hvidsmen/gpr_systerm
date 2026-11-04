from datetime import timedelta
from django.test import TestCase, Client
from django.urls import reverse
from apps.resources.models import Brigade
from apps.accounts.models import Company,User,Role
from .models import StaffingSlot,CampStay,HousingEvent
from .rotation import rotation_rows
from .plan_assignments import assignment_history


class AssignmentChangeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_housing import HousingTests
        HousingTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.old_role=Brigade.objects.create(company=self.company,name='Начальник участка')
        self.new_role=Brigade.objects.create(company=self.company,name='Прораб')
        self.client.force_login(self.hr);self.start=self.day-timedelta(days=5)
        self.base={'employee':self.second_employee.pk,'planning_position':self.old_role.pk,'construction_object':self.obj.pk,'housing_camp':self.a.pk,'assigned_from':str(self.start),'assigned_until':'','pattern':self.pattern.pk}
        self.client.post(reverse('personnel:plan_assignment_create'),{**self.base,'initial-state':'MV','initial-arrival_date':str(self.day-timedelta(days=20))})
        self.source=StaffingSlot.objects.get(planning_position=self.old_role);self.url=reverse('personnel:plan_assignment_change',args=[self.source.pk])

    def payload(self,when=None,**extra):
        return {**self.base,'planning_position':self.new_role.pk,'assigned_from':str(when or self.day),'version':self.source.updated_at.isoformat(),**extra}

    def successor(self):return StaffingSlot.objects.get(previous_assignment=self.source)

    def role_on(self,day,camps=None):
        return next(r for r in rotation_rows(self.company,camps or [self.a],day)[0] if r['person'].employee_id==self.second_employee.pk)

    def test_change_preserves_facts_hr_calendar_and_history_at_exact_date(self):
        self.source.calendar={str(self.day):'8'};self.source.save()
        before=(list(CampStay.objects.values_list('pk','arrived_on','departed_on')),list(HousingEvent.objects.values_list('pk','kind','date')))
        self.assertEqual(self.client.post(self.url,self.payload()).status_code,302)
        child=self.successor();self.source.refresh_from_db()
        self.assertEqual(self.source.replaced_on,self.day);self.assertEqual(self.source.planning_position,self.old_role)
        self.assertEqual(child.assigned_by,self.hr);self.assertEqual(child.person,self.source.person);self.assertEqual(child.calendar,self.source.calendar);self.assertEqual(child.pattern_id,self.source.pattern_id);self.assertIsNone(child.initial_movement_id)
        self.assertEqual(self.role_on(self.day-timedelta(days=1))['planning_position_id'],self.old_role.pk)
        self.assertEqual(self.role_on(self.day)['planning_position_id'],self.new_role.pk);self.assertEqual(self.role_on(self.day)['state_days'],21)
        self.second_employee.refresh_from_db();self.assertEqual(self.second_employee.position_id,self.position.pk)
        self.assertEqual((list(CampStay.objects.values_list('pk','arrived_on','departed_on')),list(HousingEvent.objects.values_list('pk','kind','date'))),before)
        history=assignment_history(child.person);self.assertEqual(len(history),2);self.assertEqual(history[1].effective_end,self.day-timedelta(days=1))

    def test_same_first_day_keeps_source_record_without_overlap(self):
        self.assertEqual(self.client.post(self.url,self.payload(self.start)).status_code,302)
        self.source.refresh_from_db();self.assertEqual(self.source.replaced_on,self.start)
        self.assertEqual(self.role_on(self.start)['slot'].pk,self.successor().pk)
        old=next(r for r in assignment_history(self.source.person) if r.pk==self.source.pk);self.assertTrue(old.replaced_immediately)

    def test_future_change_leaves_current_role_and_status_in_place(self):
        when=self.day+timedelta(days=10)
        self.assertEqual(self.client.post(self.url,self.payload(when)).status_code,302)
        self.assertEqual(self.role_on(self.day)['planning_position_id'],self.old_role.pk)
        self.assertEqual(self.role_on(when)['planning_position_id'],self.new_role.pk)
        self.assertEqual(self.role_on(when)['state'],'MV')

    def test_repost_is_idempotent_and_different_stale_change_is_rejected(self):
        payload=self.payload();self.assertEqual(self.client.post(self.url,payload).status_code,302)
        self.assertEqual(self.client.post(self.url,payload).status_code,302)
        self.assertEqual(StaffingSlot.objects.filter(previous_assignment=self.source).count(),1)
        self.assertContains(self.client.post(self.url,{**payload,'assigned_from':str(self.day+timedelta(days=1))}),'уже оформлена смена')

    def test_stale_version_noop_and_date_before_start_are_refused(self):
        for payload in (self.payload(version='stale'),self.payload(planning_position=self.old_role.pk),self.payload(self.start-timedelta(days=1))):
            self.assertEqual(self.client.post(self.url,payload).status_code,200)
            self.source.refresh_from_db();self.assertIsNone(self.source.replaced_on);self.assertFalse(StaffingSlot.objects.filter(previous_assignment=self.source).exists())

    def test_conflicting_future_assignment_rolls_back_replacement_boundary(self):
        self.source.assigned_until=self.day;self.source.save()
        future=self.day+timedelta(days=1)
        self.client.post(reverse('personnel:plan_assignment_create'),{**self.base,'planning_position':self.new_role.pk,'assigned_from':str(future),'pattern':''})
        self.assertContains(self.client.post(self.url,self.payload()),'уже есть назначение')
        self.source.refresh_from_db();self.assertIsNone(self.source.replaced_on);self.assertFalse(StaffingSlot.objects.filter(previous_assignment=self.source).exists())

    def test_new_object_and_camp_do_not_create_actual_transfer(self):
        before=HousingEvent.objects.count()
        self.assertEqual(self.client.post(self.url,self.payload(construction_object=self.obj2.pk,housing_camp=self.b.pk)).status_code,302)
        self.assertEqual(HousingEvent.objects.count(),before)
        row=self.role_on(self.day,[self.b]);self.assertEqual(row['actual_elsewhere'],self.a);self.assertFalse(row['verified'])
        self.assertEqual(CampStay.objects.get(person=self.source.person,cancelled_at=None).camp,self.a)

    def test_chain_can_change_again_from_new_assignment(self):
        self.client.post(self.url,self.payload());child=self.successor()
        third=Brigade.objects.create(company=self.company,name='Мастер')
        when=self.day+timedelta(days=5)
        response=self.client.post(reverse('personnel:plan_assignment_change',args=[child.pk]),{**self.payload(when),'planning_position':third.pk,'version':child.updated_at.isoformat()})
        self.assertEqual(response.status_code,302);self.assertEqual(self.role_on(when)['planning_position_id'],third.pk)
        self.assertEqual(len(assignment_history(child.person)),3)

    def test_historical_assignment_is_readonly_and_history_shows_actor(self):
        self.client.post(self.url,self.payload())
        url=reverse('personnel:plan_assignment_edit',args=[self.source.pk]);response=self.client.get(url)
        self.assertTrue(response.context['historical']);self.assertNotContains(response,'Сохранить назначение')
        self.source.refresh_from_db();self.assertContains(self.client.post(url,{**self.base,'version':self.source.updated_at.isoformat()}),'Прежнее назначение сохранено в истории')
        response=self.client.get(reverse('personnel:housing_history',args=[self.source.person_id]))
        self.assertContains(response,'История назначений');self.assertContains(response,self.old_role.name);self.assertContains(response,self.new_role.name);self.assertContains(response,self.hr.username)

    def test_correcting_successor_start_updates_both_sides_atomically(self):
        when=self.day+timedelta(days=5);self.client.post(self.url,self.payload(when));child=self.successor()
        corrected=self.day+timedelta(days=2)
        response=self.client.post(reverse('personnel:plan_assignment_edit',args=[child.pk]),{**self.payload(corrected),'version':child.updated_at.isoformat(),'pattern':child.pattern_id})
        self.assertEqual(response.status_code,302);self.source.refresh_from_db();self.assertEqual(self.source.replaced_on,corrected)
        self.assertEqual(self.role_on(corrected-timedelta(days=1))['planning_position_id'],self.old_role.pk)
        self.assertEqual(self.role_on(corrected)['planning_position_id'],self.new_role.pk)

    def test_employee_is_fixed_and_company_role_csrf_boundaries_apply(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.hr);self.assertEqual(client.post(self.url,self.payload()).status_code,403)
        self.client.force_login(self.dispatcher);self.assertEqual(self.client.post(self.url,self.payload()).status_code,403)
        company=Company.objects.create(name='Foreign assignment company');other=User.objects.create_user(username='change-foreign',company=company,role=Role.objects.get(code='PERSONNEL_DISPATCHER'))
        self.client.force_login(other);self.assertEqual(self.client.get(self.url).status_code,404)
        self.client.force_login(self.hr)
        self.assertEqual(self.client.post(self.url,self.payload(employee=self.employee.pk)).status_code,302)
        self.assertEqual(self.successor().person.employee_id,self.second_employee.pk)

    def test_invalid_camp_does_not_close_existing_assignment(self):
        response=self.client.post(self.url,self.payload(housing_camp=self.b.pk))
        self.assertEqual(response.status_code,200);self.assertTrue(response.context['form'].errors)
        self.source.refresh_from_db();self.assertIsNone(self.source.replaced_on)
