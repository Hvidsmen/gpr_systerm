from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.accounts.models import User,Role
from apps.planning.models import WorkTask
from apps.planning.task_services import generate_daily,validate_fact,invalidate_fact
from . import test_dispatch as fixtures
from .models import DailyFact,EmployeeActivity,EquipmentActivity,FuelFact,FactSectionDay

ROLE='ALL_FACT_DISPATCHER'

class AllFactDispatcherTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.DispatcherTests.setUpTestData.__func__(cls)
        user=User.objects.create_user(username='all-fact',company=cls.company,role=Role.objects.get(code=ROLE))
        user.assigned_objects.add(cls.obj);cls.users[ROLE]=user
    payload=fixtures.DispatcherTests.payload

    def confirm(self,section):
        data=self.payload(ROLE,section);data['action']='confirm'
        if section=='works':data['works-0-actual_quantity']='5'
        elif section=='labor':data.update({'labor-0-employee':self.employee.pk,'labor-0-general':'on'})
        elif section=='mechanization':data.update({'equipment-0-equipment':self.machine.pk,'equipment-0-state':'REPAIR','equipment-0-activity':'Ремонт насоса'})
        else:data.update({'fuel-0-opening_balance':'100','fuel-0-received_liters':'0','fuel-0-actual_liters':'5','fuel-0-actual_balance':'95'})
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,302)

    def test_all_four_directions_can_be_written(self):
        for section in ['works','labor','mechanization','fuel']:self.confirm(section)
        self.assertEqual(DailyFact.objects.get().actual_quantity,5)
        self.assertEqual(EmployeeActivity.objects.get().employee,self.employee)
        self.assertEqual(EquipmentActivity.objects.get().state,'REPAIR')
        self.assertEqual(FuelFact.objects.get().actual_liters,5)
        self.assertEqual(FactSectionDay.objects.filter(confirmed_at__isnull=False).count(),4)

    def test_assignment_scope_and_planning_permissions_remain_restricted(self):
        self.client.force_login(self.users[ROLE])
        response=self.client.get(self.url,{'construction_object':self.other.pk,'date':str(self.day)})
        self.assertFalse(response.context['selector'].is_valid())
        self.assertEqual(self.client.post(reverse('production:equipment_create'),{}).status_code,403)
        self.assertEqual(self.client.get(reverse('works:work_create')).status_code,403)
        response=self.client.get(reverse('dashboard:index'))
        self.assertRedirects(response,reverse('planning:task_list'),fetch_redirect_response=False)

    def test_deviation_reason_is_still_required(self):
        data=self.payload(ROLE,'works');data['works-0-actual_quantity']='3'
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        self.assertFalse(DailyFact.objects.exists())

    def test_daily_task_completes_only_after_all_sections_and_reopens(self):
        user=self.users[ROLE]
        self.assertEqual(generate_daily(user,today=self.day+timedelta(days=1)),1)
        task=WorkTask.objects.get(assigned_to=user,kind='FACT',fact_date=self.day)
        for section in ['works','labor']:
            self.confirm(section);task.refresh_from_db();self.assertEqual(task.status,'OPEN')
            with self.assertRaises(ValidationError):validate_fact(task)
        self.confirm('mechanization');task.refresh_from_db();self.assertNotEqual(task.status,'DONE')
        self.confirm('fuel');task.refresh_from_db();self.assertEqual(task.status,'DONE')
        validate_fact(task)
        invalidate_fact(self.company.pk,self.obj.pk,self.day,'labor')
        task.refresh_from_db();self.assertEqual(task.status,'OPEN')
        with self.assertRaises(ValidationError):validate_fact(task)

    def test_late_generated_task_uses_all_confirmations(self):
        for section in ['works','labor','mechanization','fuel']:self.confirm(section)
        # Login middleware may already have moved the cursor past the fixed fixture date.
        from apps.planning.models import DailyTaskCursor
        DailyTaskCursor.objects.filter(user=self.users[ROLE],construction_object=self.obj).update(last_date=self.day-timedelta(days=1))
        generate_daily(self.users[ROLE],today=self.day+timedelta(days=1))
        task=WorkTask.objects.get(assigned_to=self.users[ROLE],kind='FACT',fact_date=self.day)
        self.assertEqual(task.status,'DONE')

    def test_role_can_be_selected_and_task_shows_all_required_sections(self):
        from apps.accounts.forms import CompanyUserAccessForm
        form=CompanyUserAccessForm(company=self.company,instance=self.users[ROLE])
        self.assertTrue(form.fields['role'].queryset.filter(code=ROLE).exists())
        generate_daily(self.users[ROLE],today=self.day+timedelta(days=1))
        task=WorkTask.objects.get(assigned_to=self.users[ROLE],kind='FACT',fact_date=self.day)
        self.client.force_login(self.users[ROLE])
        response=self.client.get(reverse('planning:task_detail',args=[task.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual({key for key,label in response.context['fact_sections']},{'works','labor','mechanization','fuel'})

    def test_admin_task_generation_includes_the_new_role(self):
        self.client.force_login(self.users['ADMIN'])
        self.assertEqual(self.client.get(reverse('planning:task_list')).status_code,200)
        self.assertTrue(WorkTask.objects.filter(assigned_to=self.users[ROLE],kind='FACT').exists())
