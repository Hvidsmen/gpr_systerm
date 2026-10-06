from decimal import Decimal
from types import SimpleNamespace

from django import forms
from django.test import TestCase
from django.urls import URLResolver, get_resolver, reverse
from django.utils import timezone

from apps.accounts.models import Company, Role, User
from apps.planning.models import (
    CalendarDay, DailyPlan, LoadProfile, LoadProfileItem, MonthlyPlan,
    PlanVersion, ProductionCalendar,
)
from apps.production.models import (
    DailyFact, DeviationReason, EquipmentFact, EquipmentPlan, FuelFact,
    FuelPlan, LaborFact, LaborPlan,
)
from apps.projects.models import ConstructionObject, Project, Section
from apps.resources.models import Brigade, Employee, EquipmentType, Position
from apps.works.models import ProjectWork, ProjectWorkItem, WorkTemplate


class CompanyAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        role = Role.objects.get(code="ADMIN")
        cls.tenants = []
        for label in ('own', 'foreign'):
            company = Company.objects.create(name=label, inn=label)
            user = User.objects.create_user(
                username=label, password='Access-test-password-123', company=company, role=role,
            )
            project = Project.objects.create(company=company, code=label, name=label)
            obj = ConstructionObject.objects.create(company=company, project=project, code=label, name=label)
            section = Section.objects.create(company=company, construction_object=obj, code=label, name=label)
            template = WorkTemplate.objects.create(company=company, code=label, name=label, unit='m')
            work = ProjectWork.objects.create(kind="COMPOSITE", company=company, section=section, code=label, name=label, unit='m', unit_price=10)
            profile = LoadProfile.objects.create(company=company, code=label, name=label)
            profile_item = LoadProfileItem.objects.create(company=company, profile=profile, workday_number=1, percentage=100)
            item = ProjectWorkItem.objects.create(company=company, project_work=work, name=label, unit='m', load_profile=profile, weight=100)
            calendar = ProductionCalendar.objects.create(company=company, code=label, name=label, year=cls.today.year, is_default=True)
            day = CalendarDay.objects.create(company=company, calendar=calendar, date=cls.today)
            monthly = MonthlyPlan.objects.create(
                company=company, project_work=work, year=cls.today.year, month=cls.today.month,
                start_date=cls.today, end_date=cls.today, planned_quantity=10,
            )
            version = PlanVersion.objects.create(company=company, monthly_plan=monthly, version_number=1, status='APPROVED', created_by=user)
            draft = PlanVersion.objects.create(company=company, monthly_plan=monthly, version_number=2, created_by=user)
            daily = DailyPlan.objects.create(company=company, plan_version=version, work_item=item, date=cls.today, workday_number=1, planned_quantity=10)
            draft_daily = DailyPlan.objects.create(company=company, plan_version=draft, work_item=item, date=cls.today, workday_number=1, planned_quantity=10)
            reason = DeviationReason.objects.create(company=company, code=label, name=label)
            fact = DailyFact.objects.create(company=company, project_work=work, work_item=item, date=cls.today, actual_quantity=3, deviation_reason=reason)
            position = Position.objects.create(company=company, code=label, name=label)
            employee = Employee.objects.create(company=company, first_name=label, last_name=label, position=position)
            brigade = Brigade.objects.create(company=company, code=label, name=label)
            equipment = EquipmentType.objects.create(company=company, name=label)
            labor_plan = LaborPlan.objects.create(company=company, construction_object=obj, brigade=brigade, date=cls.today, planned_workers=2)
            labor_fact = LaborFact.objects.create(company=company, construction_object=obj, brigade=brigade, date=cls.today, actual_workers=2)
            equipment_plan = EquipmentPlan.objects.create(company=company, construction_object=obj, equipment_type=equipment, date=cls.today, planned_count=2)
            equipment_fact = EquipmentFact.objects.create(company=company, construction_object=obj, equipment_type=equipment, date=cls.today, actual_count=2)
            fuel_plan = FuelPlan.objects.create(company=company, construction_object=obj, date=cls.today, planned_liters=2)
            fuel_fact = FuelFact.objects.create(company=company, construction_object=obj, date=cls.today, actual_liters=2)
            cls.tenants.append(SimpleNamespace(
                company=company, user=user, project=project, obj=obj, section=section,
                template=template, work=work, profile=profile, profile_item=profile_item,
                item=item, calendar=calendar, day=day, monthly=monthly, version=version,
                draft=draft, daily=daily, draft_daily=draft_daily, reason=reason, fact=fact,
                position=position, employee=employee, brigade=brigade, equipment=equipment,
                labor_plan=labor_plan, labor_fact=labor_fact,
                equipment_plan=equipment_plan, equipment_fact=equipment_fact,
                fuel_plan=fuel_plan, fuel_fact=fuel_fact,
            ))
        cls.own, cls.foreign = cls.tenants
        cls.unassigned = User.objects.create_user(username='unassigned')
        cls.staff = User.objects.create_user(username='staff', company=cls.own.company, is_staff=True)
        cls.superuser = User.objects.create_superuser(username='global-admin', password='Access-test-password-123')

    def setUp(self):
        self.client.force_login(self.own.user)

    def app_routes(self):
        for resolver in get_resolver().url_patterns:
            if isinstance(resolver, URLResolver) and resolver.namespace in {
                'accounts', 'projects', 'works', 'planning', 'production', 'resources', 'dashboard',
            }:
                for pattern in resolver.url_patterns:
                    yield f'{resolver.namespace}:{pattern.name}', pattern

    def test_anonymous_access_requires_login_for_every_application_route(self):
        self.client.logout()
        for name, pattern in self.app_routes():
            if name in {'accounts:login', 'accounts:register'}:
                continue
            kwargs = {
                key: '2026-01-01' if key == 'date_str' else 'DIESEL' if key == 'fuel_type' else 1
                for key in pattern.pattern.converters
            }
            url = reverse(name, kwargs=kwargs)
            for method in ('get', 'post'):
                with self.subTest(route=name, method=method):
                    response = getattr(self.client, method)(url)
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(response.url.startswith('/accounts/login/?next='))

    def test_public_login_and_registration_are_accessible(self):
        self.client.logout()
        for name in ('accounts:login', 'accounts:register'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_own_detail_pages_remain_accessible(self):
        for name, obj in (
            ('accounts:user_detail', self.own.user),
            ('projects:project_detail', self.own.project),
            ('works:work_detail', self.own.work),
            ('planning:plan_detail', self.own.monthly),
            ('planning:version_detail', self.own.version),
            ('planning:profile_detail', self.own.profile),
            ('planning:calendar_detail', self.own.calendar),
        ):
            with self.subTest(route=name):
                self.assertEqual(self.client.get(reverse(name, kwargs={'pk': obj.pk})).status_code, 200)

    def test_unassigned_users_cannot_access_or_create_company_data(self):
        self.client.force_login(self.unassigned)
        before = Company.objects.count()
        for url in ('/projects/', '/works/', '/planning/', '/production/', '/dashboard/', '/accounts/users/'):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post('/projects/create/', {'code': 'new', 'name': 'new'}).status_code, 403)
        self.assertContains(self.client.get('/projects/'), 'назначить вам компанию', status_code=403)
        self.assertEqual(Company.objects.count(), before)
        self.unassigned.refresh_from_db()
        self.assertIsNone(self.unassigned.company_id)
        self.assertEqual(self.client.get('/accounts/settings/').status_code, 200)

    def test_admin_is_global_and_superuser_only(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get('/admin/').status_code, 403)
        self.assertEqual(self.client.get('/admin/projects/project/').status_code, 403)
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertEqual(self.client.get('/admin/projects/project/').status_code, 200)

    def test_lists_only_include_own_company(self):
        for name in (
            'accounts:user_list', 'projects:project_list', 'works:work_list',
            'planning:plan_list', 'planning:profile_list', 'planning:calendar_list',
            'resources:employee_list', 'resources:brigade_list', 'resources:equipment_type_list',
            'production:fact_list', 'production:labor_fact_list', 'production:equipment_fact_list',
            'production:fuel_fact_list', 'production:labor_plan_list', 'production:equipment_plan_list',
            'production:fuel_plan_list',
        ):
            with self.subTest(route=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                objects = list(response.context['object_list'])
                self.assertTrue(objects)
                self.assertEqual({obj.company_id for obj in objects}, {self.own.company.pk})

    def test_foreign_generic_objects_cannot_be_read_updated_or_deleted(self):
        # Cover every detail/update/delete view, including aliases in production URLs.
        lookup = {type(value): value for value in vars(self.foreign).values() if hasattr(value, '_meta')}
        for name, pattern in self.app_routes():
            view = getattr(pattern.callback, 'view_class', None)
            model = getattr(view, 'model', None)
            if not model or model not in lookup or not any(s in name for s in ('detail', 'update', 'delete', 'edit')):
                continue
            keys = pattern.pattern.converters
            if not keys or not set(keys).issubset({'pk', 'section_pk'}):
                continue
            kwargs = {key: self.foreign.obj.pk if key == 'pk' and 'section_pk' in keys else lookup[model].pk for key in keys}
            url = reverse(name, kwargs=kwargs)
            for method in ('get', 'post'):
                if not hasattr(view, method):
                    continue
                with self.subTest(route=name, method=method):
                    self.assertEqual(getattr(self.client, method)(url).status_code, 404)
            self.assertTrue(model.objects.filter(pk=lookup[model].pk).exists())

    def test_foreign_parent_objects_are_rejected(self):
        for name, kwargs, data in (
            ('projects:object_list', {'project_pk': self.foreign.project.pk}, {}),
            ('projects:object_create', {'project_pk': self.foreign.project.pk}, {'code': 'x', 'name': 'x'}),
            ('projects:section_list', {'pk': self.foreign.obj.pk}, {}),
            ('projects:section_create', {'pk': self.foreign.obj.pk}, {'code': 'x', 'name': 'x'}),
            ('works:work_item_create', {'work_pk': self.foreign.work.pk}, {'name': 'x', 'unit': 'm', 'weight': 100, 'quantity_per_unit': 1}),
            ('planning:profile_item_create', {'profile_pk': self.foreign.profile.pk}, {'workday_number': 2, 'percentage': 50}),
            ('planning:plan_versions', {'plan_pk': self.foreign.monthly.pk}, {}),
            ('planning:version_create', {'plan_pk': self.foreign.monthly.pk}, {}),
        ):
            for method in ('get', 'post'):
                if name == 'projects:object_list' and method == 'post':
                    continue
                with self.subTest(route=name, method=method):
                    url = reverse(name, kwargs=kwargs)
                    response = self.client.get(url) if method == 'get' else self.client.post(url, data)
                    self.assertEqual(response.status_code, 404)

    def test_foreign_version_and_calendar_actions_are_rejected(self):
        for name, pk in (
            *[(f'planning:version_{action}', self.foreign.draft.pk) for action in ('generate', 'regenerate', 'submit', 'approve', 'reject', 'revision')],
            ('planning:set_baseline_version', self.foreign.version.pk),
            ('planning:calendar_autofill', self.foreign.calendar.pk),
            ('planning:calendar_bulk_edit', self.foreign.calendar.pk),
        ):
            with self.subTest(route=name):
                self.assertEqual(self.client.post(reverse(name, kwargs={'pk': pk})).status_code, 404)
        self.foreign.draft.refresh_from_db()
        self.assertEqual(self.foreign.draft.status, 'DRAFT')
        self.assertEqual(self.foreign.calendar.days.count(), 1)

    def test_form_choices_are_company_scoped(self):
        for name in (
            'projects:project_create', 'works:work_create', 'works:section_create',
            'planning:plan_create', 'resources:employee_create', 'production:fact_create',
            'production:labor_fact_create', 'production:equipment_fact_create',
            'production:fuel_fact_create', 'production:labor_plan_create',
            'production:equipment_plan_create', 'production:fuel_plan_create',
            'production:labor_plan_create_range', 'production:equipment_plan_create_range',
            'production:fuel_plan_create_range',
        ):
            with self.subTest(route=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                for field in response.context['form'].fields.values():
                    if isinstance(field, (forms.ModelChoiceField, forms.ModelMultipleChoiceField)) and any(f.name == 'company' for f in field.queryset.model._meta.fields):
                        self.assertTrue(all(obj.company_id == self.own.company.pk for obj in field.queryset))
                        # Validation must reject foreign IDs, not just hide them in HTML.
                        foreign = field.queryset.model.objects.filter(company=self.foreign.company).first()
                        if foreign:
                            from django.core.exceptions import ValidationError
                            with self.assertRaises(ValidationError):
                                field.clean([foreign.pk] if isinstance(field, forms.ModelMultipleChoiceField) else foreign.pk)

    def test_forged_foreign_keys_do_not_create_work_or_facts(self):
        response = self.client.post(reverse('works:work_create'), {
            'code': 'forged', 'name': 'forged', 'unit': 'm', 'unit_price': 1,
            'section': self.foreign.section.pk, 'template': self.foreign.template.pk, 'status': 'PLANNED',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('section', response.context['form'].errors)
        self.assertIn('template', response.context['form'].errors)
        self.assertFalse(ProjectWork.objects.filter(code='forged').exists())
        response = self.client.post(reverse('production:fact_create'), {
            'project_work': self.foreign.work.pk, 'work_item': self.foreign.item.pk,
            'date': self.today, 'actual_quantity': 9, 'deviation_reason': self.foreign.reason.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('project_work', response.context['form'].errors)
        self.foreign.fact.refresh_from_db()
        self.assertEqual(self.foreign.fact.actual_quantity, 3)

    def test_inline_updates_reject_foreign_records_and_allow_own_records(self):
        cases = (
            ('planning:daily_plan_inline_update', 'draft_daily', 'plan_id', 'planned_quantity'),
            ('production:labor_fact_inline_update', 'labor_fact', 'fact_id', 'actual_workers'),
            ('production:equipment_fact_inline_update', 'equipment_fact', 'fact_id', 'actual_count'),
            ('production:fuel_fact_inline_update', 'fuel_fact', 'fact_id', 'actual_liters'),
            ('production:labor_plan_inline_update', 'labor_plan', 'plan_id', 'planned_workers'),
            ('production:equipment_plan_inline_update', 'equipment_plan', 'plan_id', 'planned_count'),
            ('production:fuel_plan_inline_update', 'fuel_plan', 'plan_id', 'planned_liters'),
        )
        for route, attr, id_field, field in cases:
            with self.subTest(route=route):
                foreign = getattr(self.foreign, attr)
                old = getattr(foreign, field)
                response = self.client.post(reverse(route), {id_field: foreign.pk, 'field': field, 'value': 7}, content_type='application/json')
                self.assertEqual(response.status_code, 404)
                foreign.refresh_from_db()
                self.assertEqual(getattr(foreign, field), old)
                own = getattr(self.own, attr)
                response = self.client.post(reverse(route), {id_field: own.pk, 'field': field, 'value': 7}, content_type='application/json')
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['success'])
                own.refresh_from_db()
                self.assertEqual(getattr(own, field), 7)

    def test_analytics_and_matrix_do_not_include_foreign_company(self):
        ProjectWork.objects.filter(pk=self.foreign.work.pk).update(status='IN_PROGRESS')
        response = self.client.get('/dashboard/')
        self.assertEqual(response.context['projects_count'], 1)
        self.assertEqual(response.context['approved_plans_count'], 1)
        self.assertEqual(response.context['in_progress_count'], 0)
        self.assertEqual(response.context['last_7_days'][-1]['plan'], Decimal('10'))
        self.assertEqual(response.context['last_7_days'][-1]['fact'], Decimal('3'))
        response = self.client.get('/planning/matrix/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.foreign.project.name)
        self.assertContains(response, self.own.work.name)

    def test_master_screen_and_fact_input_only_show_own_data(self):
        response = self.client.get(reverse('production:fact_daily'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row['work'].company_id for row in response.context['works_with_plan']}, {self.own.company.pk})
        self.assertEqual({r.company_id for r in response.context['deviation_reasons']}, {self.own.company.pk})
        response = self.client.post(reverse('production:fact_input'), {'select_filters': '1', 'date': self.today})
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row['work'].company_id for row in response.context['works_data']}, {self.own.company.pk})

    def test_forged_daily_fact_plan_or_reason_cannot_change_foreign_data(self):
        for work, item, reason in ((self.foreign.work,self.foreign.item,self.own.reason),(self.own.work,self.own.item,self.foreign.reason)):
            response = self.client.post(reverse('production:fact_input'), {'project_work':work.pk,'work_item':item.pk,'date':self.today,'actual_quantity':9,'deviation_reason':reason.pk})
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.context['form'].errors)
        for tenant in self.tenants:
            tenant.fact.refresh_from_db()
            self.assertEqual(tenant.fact.actual_quantity,3)

    def test_mass_input_rejects_foreign_resource_plans_and_accepts_own(self):
        for resource, attr, field in [('labor','labor_plan','actual_workers'),('equipment','equipment_plan','actual_count'),('fuel','fuel_plan','actual_liters')]:
            own_plan,foreign_plan=getattr(self.own,attr),getattr(self.foreign,attr)
            url=reverse(f'production:{resource}_fact_daily_input')
            response=self.client.post(url,{'construction_object':self.own.obj.pk,'date':self.today,'plan_ids':[own_plan.pk,foreign_plan.pk],f'value_{own_plan.pk}':8,f'value_{foreign_plan.pk}':9})
            self.assertEqual(response.status_code,404)
            own_fact=getattr(self.own,resource+'_fact');foreign_fact=getattr(self.foreign,resource+'_fact')
            own_fact.refresh_from_db();foreign_fact.refresh_from_db()
            self.assertEqual(getattr(own_fact,field),2) # whole request rolls back
            self.assertEqual(getattr(foreign_fact,field),2)
            response=self.client.post(url,{'construction_object':self.own.obj.pk,'date':self.today,'plan_ids':[own_plan.pk],f'value_{own_plan.pk}':8})
            self.assertEqual(response.status_code,302)
            own_fact.refresh_from_db();self.assertEqual(getattr(own_fact,field),8)

    def test_bulk_resource_changes_leave_foreign_records_unchanged(self):
        for resource, attr, field in (
            ('labor', 'labor_fact', 'actual_workers'),
            ('equipment', 'equipment_fact', 'actual_count'),
            ('fuel', 'fuel_fact', 'actual_liters'),
            ('labor', 'labor_plan', 'planned_workers'),
            ('equipment', 'equipment_plan', 'planned_count'),
            ('fuel', 'fuel_plan', 'planned_liters'),
        ):
            kind = 'fact' if 'fact' in attr else 'plan'
            route = f'production:{resource}_{kind}_edit_by_date'
            own = getattr(self.own,attr)
            from apps.production import forms as resource_forms
            form_class=getattr(resource_forms,type(own).__name__+'Form')
            form=form_class(instance=own,user=self.own.user)
            data={str(own.pk)+'-'+name:form[name].value() if form[name].value() is not None else '' for name in form.fields}
            data[str(own.pk)+'-'+field]=8
            response = self.client.post(reverse(route, kwargs={'date_str': self.today.isoformat()}), data)
            self.assertEqual(response.status_code, 302)
            foreign = getattr(self.foreign, attr)
            foreign.refresh_from_db()
            self.assertEqual(getattr(foreign, field), 2)
            own = getattr(self.own, attr)
            own.refresh_from_db()
            self.assertEqual(getattr(own, field), 8)
            route = f'production:{resource}_{kind}_delete_by_date'
            self.assertEqual(self.client.post(reverse(route, kwargs={'date_str': self.today.isoformat()})).status_code, 302)
            self.assertTrue(type(foreign).objects.filter(pk=foreign.pk).exists())
            self.assertFalse(type(own).objects.filter(pk=own.pk).exists())

    def test_own_project_creation_and_nested_section_edit_still_work(self):
        response = self.client.post('/projects/create/', {'code': 'created', 'name': 'Created', 'status': 'ACTIVE'})
        self.assertEqual(response.status_code, 302)
        project = Project.objects.get(code='created')
        self.assertEqual(project.company_id, self.own.company.pk)
        # Parent and child IDs differ: the URL's pk identifies the object, not the section.
        second = Section.objects.create(company=self.own.company, construction_object=self.own.obj, code='second', name='second')
        url = reverse('projects:section_edit', kwargs={'pk': self.own.obj.pk, 'section_pk': second.pk})
        response = self.client.post(url, {'code': 'edited', 'name': 'Edited'})
        self.assertEqual(response.status_code, 302)
        second.refresh_from_db()
        self.assertEqual(second.name, 'Edited')

    def test_login_redirect_cannot_point_to_external_site(self):
        self.client.logout()
        response = self.client.post('/accounts/login/?next=https://example.org/', {'username': 'own', 'password': 'Access-test-password-123'})
        self.assertRedirects(response, '/dashboard/')
        self.client.logout()
        response = self.client.post('/accounts/login/?next=/projects/', {'username': 'own', 'password': 'Access-test-password-123'})
        self.assertRedirects(response, '/projects/')
