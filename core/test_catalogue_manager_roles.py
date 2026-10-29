from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from apps.accounts.models import Company, Role, User
from apps.accounts.forms import CompanyUserAccessForm
from apps.resources.models import Brigade, BrigadeGroup, Employee, Equipment, EquipmentCategory, EquipmentType
from apps.resources.position_catalog import position_for
from core.permissions import check_route, PERSONNEL_CATALOG_MANAGER, EQUIPMENT_CATALOG_MANAGER


class CatalogueManagerRoleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Catalogue managers')
        cls.other = Company.objects.create(name='Foreign catalogue')
        cls.users = {code: User.objects.create_user(username=code.lower(), company=cls.company,
                     role=Role.objects.get(code=code)) for code in [PERSONNEL_CATALOG_MANAGER, EQUIPMENT_CATALOG_MANAGER]}
        cls.position = Brigade.objects.create(company=cls.company, name='Инженер', staffing_current=True)
        cls.employee = Employee.objects.create(company=cls.company, last_name='Каталогов', first_name='Иван',
                                               position=position_for(cls.position))
        cls.synced = Employee.objects.create(company=cls.company, last_name='ЗУП', first_name='Иван', zup_ref='a' * 32)
        cls.equipment = EquipmentType.objects.create(company=cls.company, name='Экскаватор')
        cls.unit = Equipment.objects.create(company=cls.company, type=cls.equipment, name='Машина', plate_number='CAT-01')
        cls.foreign_employee = Employee.objects.create(company=cls.other, first_name='Чужой', last_name='Сотрудник')
        cls.foreign_type = EquipmentType.objects.create(company=cls.other, name='Чужая техника')
        cls.foreign_unit = Equipment.objects.create(company=cls.other, type=cls.foreign_type, name='Чужая машина')
        cls.foreign_position = Brigade.objects.create(company=cls.other, name='Чужая должность', staffing_current=True)

    def login(self, role):
        self.client.force_login(self.users[role])

    def test_roles_are_seeded_and_available_for_admin_assignment(self):
        self.assertEqual(Role.objects.get(code=PERSONNEL_CATALOG_MANAGER).name,
                         'Ответственный за справочники сотрудников и должностей')
        from apps.accounts.access_groups import GroupForm
        for form in [CompanyUserAccessForm(company=self.company, instance=self.users[PERSONNEL_CATALOG_MANAGER]),
                     GroupForm(company=self.company)]:
            codes = set(form.fields['role'].queryset.values_list('code', flat=True))
            self.assertTrue({PERSONNEL_CATALOG_MANAGER, EQUIPMENT_CATALOG_MANAGER} <= codes)

    def test_landing_and_navigation_are_limited_to_own_catalogues(self):
        for role, route, visible, hidden in [
            (PERSONNEL_CATALOG_MANAGER, 'resources:employee_list', 'resources:brigade_list', 'resources:equipment_type_list'),
            (EQUIPMENT_CATALOG_MANAGER, 'resources:equipment_type_list', 'resources:equipment_unit_list', 'resources:employee_list'),
        ]:
            self.login(role)
            with patch('apps.planning.task_services.generate_daily', side_effect=AssertionError('No planning tasks for catalogue roles')):
                self.assertRedirects(self.client.get(reverse('dashboard:index')), reverse(route))
                page = self.client.get(reverse(route))
            self.assertContains(page, reverse(visible))
            self.assertNotContains(page, 'href="' + reverse(hidden) + '"')
            self.assertFalse(page.context.get('task_inbox_access', False))
            self.assertNotContains(page, 'href="' + reverse('accounts:user_list') + '"')

    def test_cross_domain_admin_planning_and_fact_routes_reject_get_and_post(self):
        common = [('accounts:user_list', {}), ('accounts:one_c_jobs', {}), ('planning:workspace_list', {}),
                  ('production:fact_day_workspace', {}), ('production:transport_board', {}),
                  ('personnel:housing', {}), ('works:work_list', {}), ('resources:catalogue_purge', {'kind':'equipment'})]
        for role, forbidden in [(PERSONNEL_CATALOG_MANAGER, 'resources:equipment_type_create'),
                                (EQUIPMENT_CATALOG_MANAGER, 'resources:employee_create')]:
            self.login(role)
            for route, kwargs in common + [(forbidden, {})]:
                for method in [self.client.get, self.client.post]:
                    with self.subTest(role=role, route=route, method=method.__name__):
                        self.assertEqual(method(reverse(route, kwargs=kwargs)).status_code, 403)
            self.assertEqual(self.client.get(reverse('accounts:settings')).status_code, 200)
            self.assertEqual(self.client.get(reverse('accounts:user_detail', args=[self.users[role].pk])).status_code, 200)

    def test_personnel_manager_creates_and_edits_positions_employees_and_groups(self):
        self.login(PERSONNEL_CATALOG_MANAGER)
        response = self.client.post(reverse('resources:brigade_create'), {'name':'Новая должность','unit':'чел.','is_active':'on'})
        self.assertEqual(response.status_code, 302)
        position = Brigade.objects.get(company=self.company, name='Новая должность')
        group_response = self.client.post(reverse('resources:brigade_group_create'), {'name':'Новая группа'})
        self.assertEqual(group_response.status_code, 201)
        response = self.client.post(reverse('resources:brigade_update', args=[position.pk]),
            {'name':'Уточнённая должность','unit':'чел.','group':group_response.json()['value'],'is_active':'on'})
        self.assertEqual(response.status_code, 302)
        data = {'first_name':'Иван','last_name':'Созданный','is_active':'on','position':position_for(position).pk}
        response = self.client.post(reverse('resources:employee_create'), data)
        self.assertEqual(response.status_code, 302)
        employee = Employee.objects.get(company=self.company, last_name='Созданный')
        detail = self.client.get(reverse('resources:employee_detail', args=[employee.pk]))
        self.assertTrue(detail.context['can_edit'])
        response = self.client.post(reverse('resources:employee_update', args=[employee.pk]), dict(data, last_name='Исправленный'))
        self.assertEqual(response.status_code, 302)
        employee.refresh_from_db()
        self.assertEqual(employee.last_name, 'Исправленный')
        self.assertTrue(employee.is_active)

    def test_personnel_source_restrictions_and_company_boundaries_stay_in_place(self):
        self.login(PERSONNEL_CATALOG_MANAGER)
        self.assertEqual(self.client.get(reverse('resources:employee_update', args=[self.synced.pk])).status_code, 403)
        for route, pk in [('employee_update', self.foreign_employee.pk), ('employee_detail', self.foreign_employee.pk),
                          ('brigade_update', self.foreign_position.pk)]:
            self.assertEqual(self.client.get(reverse('resources:'+route, args=[pk])).status_code, 404)
        payload = {'first_name':'Подмена','last_name':'Должность','position':position_for(self.foreign_position).pk}
        response = self.client.post(reverse('resources:employee_create'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn('position', response.context['form'].errors)

    def test_equipment_manager_edits_type_machine_and_crew(self):
        self.login(EQUIPMENT_CATALOG_MANAGER)
        payload = {'name':'Погрузчик','is_active':'on','crew-TOTAL_FORMS':'0','crew-INITIAL_FORMS':'0'}
        response = self.client.post(reverse('resources:equipment_type_create'), payload)
        self.assertEqual(response.status_code, 302)
        kind = EquipmentType.objects.get(company=self.company, name='Погрузчик')
        response = self.client.post(reverse('resources:equipment_unit_create'),
            {'name':'Новая машина','type':kind.pk,'plate_number':'CAT-02','is_active':'on'})
        self.assertEqual(response.status_code, 302)
        unit = Equipment.objects.get(company=self.company, plate_number='CAT-02')
        page = self.client.get(reverse('resources:equipment_unit_detail', args=[unit.pk]))
        self.assertTrue(page.context['editable'])
        response = self.client.post(reverse('resources:equipment_crew', args=[kind.pk]),
            {'form-TOTAL_FORMS':'1','form-INITIAL_FORMS':'0','form-0-position':position_for(self.position).pk,'form-0-count':'1'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(kind.crew_positions.get().position_id, position_for(self.position).pk)
        self.assertEqual(self.client.post(reverse('resources:equipment_category_create'), {'name':'Группа машин'}).status_code, 201)

    def test_equipment_manager_cannot_edit_foreign_records_or_create_foreign_group_links(self):
        self.login(EQUIPMENT_CATALOG_MANAGER)
        for route, pk in [('equipment_type_update', self.foreign_type.pk), ('equipment_unit_update', self.foreign_unit.pk),
                          ('equipment_crew', self.foreign_type.pk)]:
            self.assertEqual(self.client.get(reverse('resources:'+route, args=[pk])).status_code, 404)
        category = EquipmentCategory.objects.create(company=self.other, name='Чужая группа')
        response = self.client.post(reverse('resources:equipment_type_quick_create'),
                                    {'name':'Подмена группы','category':category.pk,'unit':'ед.'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(EquipmentType.objects.filter(company=self.company, name='Подмена группы').exists())

    def test_import_forms_exports_and_templates_use_domain_permissions(self):
        for role, routes, export in [
            (PERSONNEL_CATALOG_MANAGER, ['brigade_import','brigade_import_template','brigade_merge','brigade_group_list'], 'brigade_export'),
            (EQUIPMENT_CATALOG_MANAGER, ['equipment_type_import','equipment_import_template','equipment_group_import','equipment_type_merge'], 'equipment_type_export'),
        ]:
            self.login(role)
            for route in routes:
                self.assertEqual(self.client.get(reverse('resources:'+route)).status_code, 200, route)
            response = self.client.get(reverse('resources:'+export))
            self.assertEqual(response.status_code, 200)
            book = load_workbook(BytesIO(response.content))
            self.assertNotIn('Чужая', str(list(book.active.values)))
            book.close()
        self.login(PERSONNEL_CATALOG_MANAGER)
        self.assertEqual(self.client.get(reverse('resources:employee_list'), {'export':'xlsx'}).status_code, 200)
        self.login(EQUIPMENT_CATALOG_MANAGER)
        self.assertEqual(self.client.get(reverse('resources:equipment_unit_list'), {'export':'xlsx'}).status_code, 200)

    def test_no_administrative_deletion_or_unknown_routes_are_granted(self):
        for user in self.users.values():
            for route, kwargs in [('bulk_delete', {'kind':'employees'}), ('bulk_delete', {'kind':'equipment'}),
                                  ('brigade_delete', {'pk':self.position.pk}), ('fleet_import', {}),
                                  ('employee_zup_import', {}), ('future_unclassified_route', {})]:
                with self.assertRaises(PermissionDenied):
                    check_route(user, SimpleNamespace(namespace='resources', url_name=route, kwargs=kwargs), 'POST')
