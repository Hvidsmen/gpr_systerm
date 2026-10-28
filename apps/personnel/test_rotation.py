from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from apps.resources.models import Employee
from apps.projects.models import ConstructionObject
from .test_housing import HousingTests
from .models import StaffingSlot,Person,CampStay,CampObjectLink
from .rotation import RotationFilter,rotation_rows


class RotationTests(TestCase):
    @classmethod
    def setUpTestData(cls):HousingTests.setUpTestData.__func__(cls)
    def setUp(self):self.client.force_login(self.hr)
    def test_rest_and_vacancies_visible_even_without_smg_and_do_not_grant_production_access(self):
        StaffingSlot.objects.create(company=self.company,group=self.group,person=self.second_person,housing_camp=self.a,status='МО')
        vacancy=StaffingSlot.objects.create(company=self.company,group=self.group,housing_camp=self.a)
        rows,days,stats=rotation_rows(self.company,[self.a],self.day)
        self.assertEqual(stats['people'],2);self.assertEqual(stats['present'],1);self.assertEqual(stats['rest'],1);self.assertEqual(stats['vacancies'],1)
        self.assertEqual(len(rows),3)
        self.assertNotIn(self.second_employee.pk,set(__import__('apps.personnel.housing',fromlist=['available_employee_ids']).available_employee_ids(self.obj,self.day).values_list('person__employee_id',flat=True)))
        response=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':str(self.day)})
        self.assertContains(response,'Таблица перевахты');self.assertContains(response,'Межвахтовый отдых');self.assertContains(response,'Открытая вакансия')
    def test_macro_object_selects_all_linked_camps_of_its_children(self):
        parent=ConstructionObject.objects.create(company=self.company,project=self.project,name='Общий КТП')
        self.obj.parent=parent;self.obj.save();self.obj3.parent=parent;self.obj3.save()
        form=RotationFilter({'construction_object':parent.pk,'date':str(self.day)},company=self.company)
        self.assertTrue(form.is_valid());self.assertEqual(set(form.selected_camps(self.company).values_list('pk',flat=True)),{self.a.pk,self.b.pk})
        form=RotationFilter({'construction_object':parent.pk,'date':str(self.day),'camps':[self.b.pk]},company=self.company)
        self.assertTrue(form.is_valid());self.assertEqual(list(form.selected_camps(self.company)),[self.b])
    def test_closed_stay_is_rest_and_transferred_employee_is_not_duplicated_in_previous_camp(self):
        self.stay.departed_on=self.day;self.stay.save()
        rows,_,stats=rotation_rows(self.company,[self.a],self.day)
        self.assertEqual(rows[0]['state'],'MO');self.assertEqual(stats['present'],0)
        CampStay.objects.create(company=self.company,person=self.person,camp=self.b,arrived_on=self.day)
        rows,_,stats=rotation_rows(self.company,[self.a],self.day);self.assertEqual(len(rows),0)
        rows,_,stats=rotation_rows(self.company,[self.a,self.b],self.day);self.assertEqual(len(rows),1)
    def test_multiple_camps_and_rotation_export_are_scoped(self):
        StaffingSlot.objects.create(company=self.company,group=self.group,person=self.second_person,housing_camp=self.b,status='МО')
        data={'camps':[self.a.pk,self.b.pk],'date':str(self.day)}
        page=self.client.get(reverse('personnel:housing'),data);self.assertEqual(page.status_code,200);self.assertEqual(page.context['rotation_stats']['people'],2)
        response=self.client.get(reverse('personnel:housing_export'),data);self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get(reverse('personnel:housing'),{'camp':self.foreign_camp.pk}).status_code,404)
