from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, User
from apps.projects.models import Project, ConstructionObject, Section
from apps.resources.models import Brigade, EquipmentType
from apps.works.models import ProjectWork, ProjectWorkItem
from apps.production.models import DailyFact, LaborFact, LaborPlan, EquipmentFact, FuelFact
from apps.planning.models import PlanningWorkspace, GlobalPlanVersion


class FactDayWorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Day entry')
        cls.user = User.objects.create_user(username='day-entry', company=cls.company)
        project = Project.objects.create(company=cls.company, code='p', name='Project')
        cls.obj = ConstructionObject.objects.create(company=cls.company, project=project, code='o', name='Object')
        cls.other_obj = ConstructionObject.objects.create(company=cls.company, project=project, code='o2', name='Other object')
        section = Section.objects.create(company=cls.company, construction_object=cls.obj, code='s', name='Section')
        other_section = Section.objects.create(company=cls.company, construction_object=cls.other_obj, code='s', name='Other section')
        cls.work = ProjectWork.objects.create(company=cls.company, section=section, code='w', name='Work', unit='m', unit_price=10)
        cls.other_work = ProjectWork.objects.create(company=cls.company, section=other_section, code='w', name='Other work', unit='m', unit_price=10)
        cls.composite = ProjectWork.objects.create(company=cls.company, section=section, code='c', name='Composite', unit='unit', unit_price=100, kind='COMPOSITE')
        cls.item = ProjectWorkItem.objects.create(company=cls.company, project_work=cls.composite, name='Part', unit='m', quantity_per_unit=2, weight=100)
        cls.brigade = Brigade.objects.create(company=cls.company, code='b', name='Brigade')
        cls.equipment = EquipmentType.objects.create(company=cls.company, name='Machine')
        cls.foreign = Company.objects.create(name='Foreign')
        foreign_project = Project.objects.create(company=cls.foreign, code='p', name='Foreign')
        cls.foreign_obj = ConstructionObject.objects.create(company=cls.foreign, project=foreign_project, code='o', name='Foreign')
        cls.foreign_brigade = Brigade.objects.create(company=cls.foreign, code='b', name='Foreign brigade')
        cls.day = date(2026, 10, 6)
        cls.url = reverse('production:fact_day_workspace')

    def setUp(self):
        self.client.force_login(self.user)

    def screen(self):
        return self.client.get(self.url, {'construction_object': self.obj.pk, 'date': self.day.isoformat()})

    def payload(self):
        response = self.screen()
        self.assertEqual(response.status_code, 200)
        data = {'construction_object': str(self.obj.pk), 'date': self.day.isoformat()}
        for group in response.context['groups']:
            fs = group['formset']
            data.update({field.html_name: field.value() for field in fs.management_form})
            for form in fs:
                for field in form:
                    if field.name == 'DELETE':
                        continue
                    value = field.value()
                    data[field.html_name] = str(value) if value is not None else ''
        return data

    def work_row(self, data, work):
        return next(key[:-len('project_work')] for key, value in data.items() if key.startswith('works-') and key.endswith('-project_work') and value == str(work.pk))

    def fill(self, data):
        data[self.work_row(data, self.work) + 'actual_quantity'] = '7'
        data[self.work_row(data, self.composite) + 'actual_quantity'] = '4'
        data.update({'labor-0-brigade': str(self.brigade.pk), 'labor-0-actual_workers': '5', 'labor-0-actual_hours': '40',
                     'equipment-0-equipment_type': str(self.equipment.pk), 'equipment-0-actual_count': '2', 'equipment-0-machine_hours': '12',
                     'fuel-0-fuel_type': 'DIESEL', 'fuel-0-actual_liters': '30'})
        return data

    def test_create_all_categories_and_repeat_without_duplicates(self):
        response = self.client.post(self.url, self.fill(self.payload()))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DailyFact.objects.count(), 2)
        self.assertEqual(DailyFact.objects.get(project_work=self.work).actual_value, Decimal('70'))
        self.assertEqual(DailyFact.objects.get(project_work=self.work).reported_by, self.user)
        self.assertEqual(LaborFact.objects.get().actual_hours, Decimal('40'))
        self.assertEqual(EquipmentFact.objects.get().machine_hours, Decimal('12'))
        self.assertEqual(FuelFact.objects.get().actual_liters, Decimal('30'))
        data = self.payload()
        data[self.work_row(data, self.work) + 'actual_quantity'] = '9'
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(DailyFact.objects.count(), 2)
        self.assertEqual(DailyFact.objects.get(project_work=self.work).actual_quantity, Decimal('9'))
        self.assertEqual(FuelFact.objects.count(), 1)

    def test_empty_rows_do_not_create_zero_facts_but_explicit_zero_does(self):
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 302)
        self.assertFalse(DailyFact.objects.exists())
        self.assertFalse(FuelFact.objects.exists())
        data = self.payload()
        data[self.work_row(data, self.work) + 'actual_quantity'] = '0'
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(DailyFact.objects.get().actual_quantity, 0)

    def test_invalid_category_prevents_all_saves_and_preserves_input(self):
        data = self.fill(self.payload())
        data['fuel-0-actual_liters'] = '-1'
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'value="40"', status_code=400)
        for model in (DailyFact, LaborFact, EquipmentFact, FuelFact):
            self.assertFalse(model.objects.exists())

    def test_plan_initials_are_not_facts_and_rates_are_copied(self):
        LaborPlan.objects.create(company=self.company, construction_object=self.obj, date=self.day, brigade=self.brigade, planned_workers=5, hourly_rate=350)
        data = self.payload()
        self.assertEqual(data['labor-0-brigade'], str(self.brigade.pk))
        self.assertEqual(data['labor-0-actual_workers'], '')
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertFalse(LaborFact.objects.exists())
        data['labor-0-actual_workers'] = '4'
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(LaborFact.objects.get().hourly_rate, 350)

    def test_approved_workspace_resource_plan_overrides_legacy(self):
        workspace = PlanningWorkspace.objects.create(company=self.company, construction_object=self.obj, name='Period', start_date=self.day, end_date=self.day)
        GlobalPlanVersion.objects.create(company=self.company, construction_object=self.obj, workspace=workspace, version_number=1, start_date=self.day, end_date=self.day, status='APPROVED', snapshot={'resources': {'labor': [{'date': self.day.isoformat(), 'brigade_id': self.brigade.pk, 'planned_workers': 5, 'hourly_rate': '420'}]}})
        self.assertEqual(self.payload()['labor-0-hourly_rate'], '420')

    def test_foreign_objects_resources_and_other_object_works_rejected(self):
        self.assertEqual(self.client.get(self.url, {'construction_object': self.foreign_obj.pk, 'date': self.day.isoformat()}).context['selector'].is_valid(), False)
        data = self.fill(self.payload())
        data['labor-0-brigade'] = str(self.foreign_brigade.pk)
        self.assertEqual(self.client.post(self.url, data).status_code, 400)
        data = self.fill(self.payload())
        data[self.work_row(data, self.work) + 'project_work'] = str(self.other_work.pk)
        self.assertEqual(self.client.post(self.url, data).status_code, 400)
        self.assertFalse(DailyFact.objects.exists())

    def test_foreign_hidden_id_is_rejected(self):
        fact = LaborFact.objects.create(company=self.foreign, construction_object=self.foreign_obj, date=self.day, brigade=self.foreign_brigade, actual_workers=6)
        data = self.fill(self.payload())
        data['labor-0-id'] = str(fact.pk)
        self.assertEqual(self.client.post(self.url, data).status_code, 400)
        fact.refresh_from_db()
        self.assertEqual(fact.actual_workers, 6)

    def test_duplicate_rows_rollback_earlier_categories(self):
        data = self.fill(self.payload())
        data.update({'labor-TOTAL_FORMS': '2', 'labor-1-brigade': str(self.brigade.pk), 'labor-1-actual_workers': '3'})
        self.assertEqual(self.client.post(self.url, data).status_code, 400)
        self.assertFalse(DailyFact.objects.exists())
        self.assertFalse(LaborFact.objects.exists())

    def test_delete_and_validation_failure_are_atomic(self):
        self.client.post(self.url, self.fill(self.payload()))
        data = self.payload()
        data['labor-0-DELETE'] = 'on'
        data['fuel-0-actual_liters'] = '-1'
        self.assertEqual(self.client.post(self.url, data).status_code, 400)
        self.assertEqual(LaborFact.objects.count(), 1)
        data['fuel-0-actual_liters'] = '25'
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(LaborFact.objects.count(), 0)
