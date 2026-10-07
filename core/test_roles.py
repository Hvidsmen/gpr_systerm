from datetime import date
from types import SimpleNamespace
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.db import transaction
from django.urls import reverse
from apps.accounts.models import Company, Role, User
from apps.projects.models import Project, ConstructionObject, Section
from apps.works.models import ProjectWork
from apps.resources.models import Brigade, EquipmentType
from apps.production.models import DailyFact, LaborFact, EquipmentFact, FuelFact, LaborPlan
from apps.planning.models import GlobalPlanVersion
from apps.planning.global_services import GlobalPlanService
from core.permissions import check_route


class RoleAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Role company')
        cls.users = {code: User.objects.create_user(username=code.lower(), company=cls.company, role=Role.objects.get(code=code))
                     for code in ('ADMIN', 'PLANNER', 'MANAGER', 'FOREMAN')}
        cls.no_role = User.objects.create_user(username='no-role', company=cls.company)
        cls.day = date(2026, 10, 6)
        project = Project.objects.create(company=cls.company, name='Project', code='p')
        cls.objects = [ConstructionObject.objects.create(company=cls.company, project=project, name='Object ' + str(i), code=str(i)) for i in range(2)]
        cls.users['FOREMAN'].assigned_objects.add(cls.objects[0])
        cls.brigade = Brigade.objects.create(company=cls.company, code='b', name='Brigade')
        cls.equipment = EquipmentType.objects.create(company=cls.company, name='Machine')
        cls.works = []
        cls.facts = []
        cls.resource_facts = []
        for i, obj in enumerate(cls.objects):
            section = Section.objects.create(company=cls.company, construction_object=obj, name='Section', code='s')
            work = ProjectWork.objects.create(company=cls.company, section=section, name='Work ' + str(i), code='w', unit='m', unit_price=10)
            cls.works.append(work)
            cls.facts.append(DailyFact.objects.create(company=cls.company, project_work=work, date=cls.day, actual_quantity=i+1))
            cls.resource_facts.append([
                LaborFact.objects.create(company=cls.company, construction_object=obj, date=cls.day, brigade=cls.brigade, actual_workers=i+1),
                EquipmentFact.objects.create(company=cls.company, construction_object=obj, date=cls.day, equipment_type=cls.equipment, actual_count=i+1),
                FuelFact.objects.create(company=cls.company, construction_object=obj, date=cls.day, fuel_type='DIESEL', actual_liters=i+1),
            ])
        other = Company.objects.create(name='Foreign')
        foreign_project = Project.objects.create(company=other, name='Foreign', code='p')
        cls.foreign_obj = ConstructionObject.objects.create(company=other, project=foreign_project, name='Foreign object', code='o')
        cls.foreign_user = User.objects.create_user(username='foreign-user', company=other, role=Role.objects.get(code='FOREMAN'))

    def login(self, role):
        self.client.force_login(self.users[role])

    def test_roles_enforce_page_and_post_permissions(self):
        cases = [
            ('projects:project_list', {}, {'ADMIN','PLANNER','MANAGER'}),
            ('projects:project_create', {}, {'ADMIN','PLANNER'}),
            ('planning:workspace_list', {}, {'ADMIN','PLANNER','MANAGER'}),
            ('planning:workspace_create', {}, {'ADMIN','PLANNER'}),
            ('production:fact_create', {}, {'ADMIN','FOREMAN'}),
            ('production:labor_fact_create', {}, {'ADMIN','FOREMAN'}),
            ('production:labor_plan_create', {}, {'ADMIN','PLANNER'}),
            ('accounts:user_list', {}, {'ADMIN'}),
            ('accounts:user_create', {}, {'ADMIN'}),
        ]
        for role in self.users:
            self.login(role)
            for name, kwargs, allowed in cases:
                with self.subTest(role=role, route=name):
                    url=reverse(name, kwargs=kwargs)
                    self.assertEqual(self.client.get(url).status_code, 200 if role in allowed else 403)
                    if role not in allowed:
                        self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_approval_and_edit_actions_have_distinct_roles(self):
        version = GlobalPlanVersion.objects.create(company=self.company, construction_object=self.objects[0], start_date=self.day, end_date=self.day, version_number=1, status='SUBMITTED')
        for role in ('PLANNER','FOREMAN'):
            self.login(role)
            for action in ('approve','reject','complete'):
                self.assertEqual(self.client.post(reverse('planning:global_action', args=[version.pk, action])).status_code, 403)
            with self.assertRaises(PermissionDenied):
                GlobalPlanService.transition(version, self.users[role], 'approve')
        self.login('MANAGER')
        self.assertEqual(self.client.post(reverse('planning:global_action', args=[version.pk, 'submit'])).status_code, 403)
        self.assertEqual(self.client.post(reverse('planning:global_action', args=[version.pk, 'approve'])).status_code, 403)
        version.refresh_from_db()
        self.assertEqual(version.status, 'SUBMITTED')
        with self.assertRaises(PermissionDenied):
            GlobalPlanService.create(self.users['MANAGER'], self.objects[0], self.day, self.day)

    def test_master_only_sees_assigned_work_and_resource_facts(self):
        self.login('FOREMAN')
        response=self.client.get(reverse('production:fact_list'))
        self.assertEqual(list(response.context['facts']), [self.facts[0]])
        for index, kind in enumerate(('labor','equipment','fuel')):
            response=self.client.get(reverse('production:'+kind+'_fact_list'))
            self.assertEqual(list(response.context['records']), [self.resource_facts[0][index]])
            self.assertEqual(list(response.context['objects']), [self.objects[0]])
            self.assertEqual(self.client.get(reverse('production:'+kind+'_fact_update', args=[self.resource_facts[1][index].pk])).status_code, 404)
            self.assertEqual(self.client.post(reverse('production:'+kind+'_fact_delete', args=[self.resource_facts[1][index].pk])).status_code, 404)
        response=self.client.get(reverse('production:fact_input'))
        self.assertEqual([row['work'] for row in response.context['works_data']], [self.works[0]])
        self.assertEqual(self.client.get(reverse('production:fact_update', args=[self.facts[1].pk])).status_code, 404)
        response=self.client.get(reverse('production:fact_create'))
        self.assertEqual(list(response.context['form'].fields['project_work'].queryset), [self.works[0]])

    def test_master_cannot_change_unassigned_object_through_any_input(self):
        self.login('FOREMAN')
        for obj in (self.objects[1], self.foreign_obj):
            data={'construction_object': obj.pk, 'date': self.day.isoformat()}
            self.assertEqual(self.client.post(reverse('production:fact_day_workspace'), data).status_code, 400)
            for kind in ('labor','equipment','fuel'):
                response=self.client.post(reverse('production:'+kind+'_fact_daily_input'), data)
                self.assertFalse(response.context['form'].is_valid())
        self.assertEqual(self.client.post(reverse('production:fact_create'), {'project_work':self.works[1].pk,'date':self.day,'actual_quantity':99}).status_code, 200)
        self.assertEqual(DailyFact.objects.count(), 2)
        response=self.client.post(reverse('production:labor_fact_inline_update'), {'id':self.resource_facts[1][0].pk,'field':'actual_workers','value':99}, content_type='application/json')
        self.assertEqual(response.status_code, 404)
        self.resource_facts[1][0].refresh_from_db()
        self.assertEqual(self.resource_facts[1][0].actual_workers, 2)

    def test_master_can_edit_own_fact_and_bulk_delete_only_own_objects(self):
        self.login('FOREMAN')
        response=self.client.post(reverse('production:fact_update', args=[self.facts[0].pk]), {'project_work': self.works[0].pk,'date':self.day,'actual_quantity':7})
        self.assertEqual(response.status_code, 302)
        self.facts[0].refresh_from_db()
        self.assertEqual(self.facts[0].actual_quantity, 7)
        self.assertEqual(self.client.post(reverse('production:labor_fact_delete_by_date', args=[self.day.isoformat()])).status_code, 302)
        self.assertFalse(LaborFact.objects.filter(construction_object=self.objects[0]).exists())
        self.assertTrue(LaborFact.objects.filter(construction_object=self.objects[1]).exists())

    def test_revoked_assignment_takes_effect_immediately(self):
        self.login('FOREMAN')
        self.users['FOREMAN'].assigned_objects.clear()
        response=self.client.get(reverse('production:fact_list'))
        self.assertFalse(response.context['facts'])
        self.assertEqual(self.client.get(reverse('production:fact_update', args=[self.facts[0].pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('production:fact_day_workspace'), {'construction_object':self.objects[0].pk,'date':self.day}).status_code, 400)

    def test_admin_assigns_role_and_objects_only_within_company(self):
        self.login('ADMIN')
        url=reverse('accounts:user_access', args=[self.no_role.pk])
        data={'role':Role.objects.get(code='FOREMAN').pk,'assigned_objects':[self.objects[0].pk],'is_active':'on'}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.no_role.refresh_from_db()
        self.assertEqual(self.no_role.role.code, 'FOREMAN')
        self.assertEqual(list(self.no_role.assigned_objects.all()), [self.objects[0]])
        self.assertEqual(self.client.post(url, {**data,'assigned_objects':[self.foreign_obj.pk]}).status_code, 200)
        self.assertEqual(self.client.get(reverse('accounts:user_access', args=[self.foreign_user.pk])).status_code, 404)
        with self.assertRaises(ValidationError), transaction.atomic():
            self.no_role.assigned_objects.add(self.foreign_obj)
        self.no_role.company = self.foreign_user.company
        with self.assertRaises(ValidationError):
            self.no_role.save()

    def test_admin_creates_company_user_without_privilege_flags(self):
        self.login('ADMIN')
        response=self.client.post(reverse('accounts:user_create'), {'username':'new-master','password1':'Long-New-Master-Password-57','password2':'Long-New-Master-Password-57','role':Role.objects.get(code='FOREMAN').pk,'assigned_objects':[self.objects[0].pk],'is_active':'on','is_superuser':'on','is_staff':'on','company':self.foreign_user.company_id})
        self.assertEqual(response.status_code, 302)
        user=User.objects.get(username='new-master')
        self.assertEqual(user.company, self.company)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        self.assertTrue(user.check_password('Long-New-Master-Password-57'))
        self.assertEqual(list(user.assigned_objects.all()), [self.objects[0]])

    def test_self_escalation_and_admin_self_lockout_are_blocked(self):
        for role in ('PLANNER','MANAGER','FOREMAN'):
            self.login(role)
            self.assertEqual(self.client.post(reverse('accounts:user_access', args=[self.users[role].pk]), {'role':Role.objects.get(code='ADMIN').pk}).status_code, 403)
        self.login('ADMIN')
        response=self.client.post(reverse('accounts:user_access', args=[self.users['ADMIN'].pk]), {'role':Role.objects.get(code='FOREMAN').pk,'is_active':'on'})
        self.assertEqual(response.status_code, 200)
        self.users['ADMIN'].refresh_from_db()
        self.assertEqual(self.users['ADMIN'].role.code, 'ADMIN')

    def test_no_role_only_has_own_profile_and_master_dashboard_redirects(self):
        self.client.force_login(self.no_role)
        self.assertEqual(self.client.get(reverse('production:fact_list')).status_code, 403)
        self.assertEqual(self.client.get(reverse('accounts:settings')).status_code, 200)
        self.assertEqual(self.client.get(reverse('accounts:user_detail', args=[self.no_role.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('accounts:user_detail', args=[self.users['ADMIN'].pk])).status_code, 403)
        self.login('FOREMAN')
        self.assertRedirects(self.client.get('/dashboard/'), reverse('production:fact_day_workspace'))

    def test_manager_and_planner_can_read_but_cannot_post_facts(self):
        for role in ('MANAGER','PLANNER'):
            self.login(role)
            self.assertEqual(self.client.get(reverse('production:fact_list')).status_code, 200)
            self.assertEqual(self.client.post(reverse('production:fact_day_workspace'), {'construction_object':self.objects[0].pk,'date':self.day}).status_code, 403)
            response=self.client.get(reverse('production:labor_fact_list'))
            self.assertNotContains(response, 'href="'+reverse('production:labor_fact_create')+'"')
            self.assertNotContains(response, 'href="'+reverse('production:labor_fact_update', args=[self.resource_facts[0][0].pk])+'"')
