from datetime import date, timedelta
from decimal import Decimal as D
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User, Role, Company
from apps.projects.models import Project, ConstructionObject, Section
from apps.works.models import ProjectWork, ProjectWorkItem
from apps.works.progress import WorkProgressService, work_specification
from .models import DailyFact, WorkOpeningBalance
from .history_models import FactRevision
from .work_opening import save, revision
from . import test_dispatch as fixtures


class WorkOpeningTests(TestCase):
    setUpTestData = classmethod(fixtures.DispatcherTests.setUpTestData.__func__)

    def setUp(self):
        self.admin = self.users['ADMIN']
        self.client.force_login(self.admin)
        self.url = reverse('production:work_opening_edit', args=[self.work.pk])

    def put(self, quantities=None, work=None, **kwargs):
        work = work or self.work
        return save(self.admin, work.pk, quantities or {None: (self.day, D(100))}, kwargs.get('expected', revision(work)), kwargs.get('reason', 'Начальный учёт'))

    def payload(self, **changes):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        data = {field.html_name: field.value() for field in form}
        data.update({'date_work': str(self.day), 'quantity_work': '100.125', 'reason': 'Начальный учёт'}, **changes)
        return data

    def test_admin_form_saves_stock_separately_with_history_and_no_daily_revenue(self):
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 302)
        row = WorkOpeningBalance.objects.get()
        self.assertEqual(row.quantity, D('100.125'))
        self.assertEqual(DailyFact.objects.count(), 0)
        history = FactRevision.objects.get(entity_type='production.workopeningbalance')
        self.assertEqual(history.actor, self.admin)
        self.assertEqual(history.reason, 'Начальный учёт')
        self.assertEqual(history.object_id, self.obj.pk)
        daily = DailyFact.objects.create(company=self.company, project_work=self.work, date=self.day, actual_quantity=5)
        self.assertEqual(daily.actual_value, 50)
        self.assertEqual(WorkProgressService.completed(self.work), D('105.125'))
        series = WorkProgressService.facts(self.work)
        self.assertEqual(series[self.day]['daily'], 5)
        self.assertEqual(series[self.day - timedelta(days=1)]['daily'], 0)
        self.assertContains(self.client.get(self.url), '100,125')

    def test_all_non_admin_roles_cannot_open_or_post_or_call_service(self):
        for code in ['PLANNER', 'MANAGER', 'FOREMAN', 'DISPATCHER', 'PERSONNEL_DISPATCHER', 'HR_HEAD', 'EQUIPMENT_CATALOG_MANAGER', 'PERSONNEL_CATALOG_MANAGER']:
            user = User.objects.create_user(username='opening-' + code, company=self.company, role=Role.objects.get(code=code))
            user.assigned_objects.add(self.obj)
            self.client.force_login(user)
            for method in ['get', 'post']:
                self.assertEqual(getattr(self.client, method)(self.url).status_code, 403, code)
            with self.assertRaises(PermissionDenied):
                save(user, self.work.pk, {None: (self.day, D(1))}, revision(self.work), 'test')
        self.assertFalse(WorkOpeningBalance.objects.exists())

    def test_foreign_company_and_unrelated_work_cannot_be_changed(self):
        company = Company.objects.create(name='Foreign opening')
        project = Project.objects.create(company=company, name='Foreign')
        obj = ConstructionObject.objects.create(company=company, project=project, name='Foreign')
        section = Section.objects.create(company=company, construction_object=obj, name='Foreign')
        foreign = ProjectWork.objects.create(company=company, section=section, name='Secret work', unit='m')
        self.assertEqual(self.client.get(reverse('production:work_opening_edit', args=[foreign.pk])).status_code, 404)
        with self.assertRaises(ProjectWork.DoesNotExist):
            self.put(work=foreign)
        response = self.client.get(reverse('production:work_opening_list'), {'construction_object': self.obj.pk})
        self.assertContains(response, self.work.name)
        self.assertNotContains(response, 'Secret work')

    def test_stale_form_and_missing_reason_do_not_overwrite(self):
        payload = self.payload()
        self.put()
        self.assertContains(self.client.post(self.url, payload), 'изменились', status_code=400)
        self.assertEqual(WorkOpeningBalance.objects.get().quantity, 100)
        with self.assertRaises(ValidationError):
            self.put(reason=' ')

    def test_invalid_numbers_and_blank_input_do_not_silently_clear_balance(self):
        self.put()
        for value in ['', '-1', 'NaN', 'Infinity', '1.0001']:
            response = self.client.post(self.url, self.payload(quantity_work=value))
            self.assertEqual(response.status_code, 400, value)
            self.assertEqual(WorkOpeningBalance.objects.get().quantity, 100)

    def test_zero_clears_stock_with_audit_and_repeated_save_is_idempotent(self):
        self.put()
        self.put({None: (self.day, D(0))}, reason='Исправление')
        self.assertEqual(WorkProgressService.completed(self.work), 0)
        revisions = FactRevision.objects.filter(entity_type='production.workopeningbalance')
        self.assertEqual(revisions.count(), 2)
        self.put({None: (self.day, D(0))})
        self.assertEqual(revisions.count(), 2)

    def test_no_double_counting_with_earlier_fact_in_both_directions(self):
        DailyFact.objects.create(company=self.company, project_work=self.work, date=self.day - timedelta(days=1), actual_quantity=4)
        with self.assertRaisesMessage(ValidationError, 'уже есть дневной факт'):
            self.put()
        self.assertFalse(WorkOpeningBalance.objects.exists())
        self.put({None: (self.day - timedelta(days=1), D(10))})
        self.assertEqual(WorkProgressService.completed(self.work), 14)
        with self.assertRaisesMessage(ValidationError, 'раньше начала учёта'):
            DailyFact.objects.create(company=self.company, project_work=self.work, date=self.day - timedelta(days=2), actual_quantity=1)

    def test_dated_stock_and_daily_card_previous_quantity(self):
        self.put()
        self.assertEqual(WorkProgressService.completed(self.work, self.day - timedelta(days=2)), 0)
        self.assertEqual(WorkProgressService.completed(self.work, self.day - timedelta(days=1)), 100)
        self.client.force_login(self.users['DISPATCHER'])
        page = self.client.get(fixtures.reverse('production:fact_day_workspace'), {'construction_object': self.obj.pk, 'date': str(self.day), 'section': 'works'})
        forms = page.context['groups'][0]['formset']
        row = next(f for f in forms if f.work_display and f.work_display['title'] == self.work.name)
        self.assertEqual(row.work_display['previous'], 100)
        self.assertNotContains(page, 'Входящие остатки работ</a>')

    def test_composite_stock_uses_norms_and_whole_unit_rounding(self):
        work = ProjectWork.objects.create(company=self.company, section=self.work.section, kind='COMPOSITE', name='Assembly', unit='шт', allow_fractional=False)
        a = ProjectWorkItem.objects.create(company=self.company, project_work=work, name='A', unit='m', quantity_per_unit=2, weight=50)
        b = ProjectWorkItem.objects.create(company=self.company, project_work=work, name='B', unit='m3', quantity_per_unit=3, weight=50)
        self.put({a.pk: (self.day, D(3)), b.pk: (self.day, D('4.5'))}, work=work)
        self.assertEqual(WorkProgressService.completed(work), 1)
        DailyFact.objects.create(company=self.company, project_work=work, work_item=a, date=self.day, actual_quantity=1)
        DailyFact.objects.create(company=self.company, project_work=work, work_item=b, date=self.day, actual_quantity=D('1.5'))
        values = WorkProgressService.facts(work)[self.day]
        self.assertEqual(values, {'daily': D(1), 'cumulative': D(2)})
        a.quantity_per_unit = 4
        with self.assertRaises(ValidationError):
            a.full_clean()

    def test_invalid_composite_row_rolls_back_entire_edit(self):
        work = ProjectWork.objects.create(company=self.company, section=self.work.section, kind='COMPOSITE', name='Assembly', unit='шт')
        a = ProjectWorkItem.objects.create(company=self.company, project_work=work, name='A', unit='m')
        b = ProjectWorkItem.objects.create(company=self.company, project_work=work, name='B', unit='m')
        DailyFact.objects.create(company=self.company, project_work=work, work_item=b, date=self.day - timedelta(days=1), actual_quantity=1)
        with self.assertRaises(ValidationError):
            self.put({a.pk: (self.day, D(4)), b.pk: (self.day, D(5))}, work=work)
        self.assertFalse(WorkOpeningBalance.objects.exists())
        self.assertFalse(FactRevision.objects.filter(entity_type='production.workopeningbalance').exists())


class WorkOpeningPlanningTests(TestCase):
    from apps.planning.test_workspace import WorkspaceTests as F
    setUpTestData = classmethod(F.setUpTestData.__func__)
    create = F.create
    approve = F.approve
    from apps.planning.test_report_matrix import MatrixReportTests as M
    data = M.data
    section = M.section
    row = M.row

    def opening(self, work, item=None, quantity=100, day=date(2026, 1, 1)):
        return WorkOpeningBalance.objects.create(company=self.company, project_work=work, work_item=item, date=day, quantity=quantity)

    def test_report_and_comparison_keep_daily_money_separate_and_show_stock(self):
        from apps.planning.report_services import build_matrix
        from apps.planning.global_services import comparison
        version = self.create([self.simple]).baseline_version
        version = self.approve(version)
        frozen = version.snapshot
        self.opening(self.simple)
        DailyFact.objects.create(company=self.company, project_work=self.simple, date=date(2026, 1, 5), actual_quantity=5)
        report = build_matrix(self.planner, self.data())
        row = self.row(report, 'works', self.simple.name)
        self.assertEqual(row['total']['fact'], 5)
        self.assertEqual(row['total']['fact_amount'], 50)
        self.assertEqual(row['incoming_quantity'], 100)
        self.assertEqual(row['completed_quantity'], 105)
        self.assertEqual(comparison(version)['works'][0]['fact'], 5)
        version.refresh_from_db()
        self.assertEqual(version.snapshot, frozen)

    def test_composite_report_gets_fraction_from_opening_not_daily_sum(self):
        from apps.planning.report_services import build_matrix
        self.composite.allow_fractional = False
        self.composite.save()
        self.opening(self.composite, self.a, 3)
        self.opening(self.composite, self.b, D('4.5'))
        for item, quantity in [(self.a, 1), (self.b, D('1.5'))]:
            DailyFact.objects.create(company=self.company, project_work=self.composite, work_item=item, date=date(2026, 1, 5), actual_quantity=quantity)
        row = self.row(build_matrix(self.planner, self.data()), 'works', self.composite.name)
        self.assertEqual(row['total']['fact'], 1)
        self.assertEqual(row['total']['fact_amount'], 100)
        self.assertEqual(row['completed_quantity'], 2)

    def test_forecast_does_not_deduct_opening_from_remaining_plan_or_emit_it_as_fact(self):
        from apps.planning.workspace_services import WorkspaceService, build_workspace_snapshot
        baseline = self.create([self.simple]).baseline_version
        baseline = self.approve(baseline)
        self.opening(self.simple, quantity=1000)
        DailyFact.objects.create(company=self.company, project_work=self.simple, date=date(2026, 1, 5), actual_quantity=10)
        version = WorkspaceService.forecast(baseline.workspace, self.planner, date(2026, 2, 1), 'REMAINING')
        snapshot = build_workspace_snapshot(version)
        work = snapshot['works'][0]
        self.assertEqual(sum(D(r['quantity']) for r in work['daily']), 1000)
        self.assertEqual(D(work['daily'][-1]['cumulative']), 2000)
        self.assertTrue(any(r['origin'] == 'OPENING' for r in work['context_plans']))
        self.assertFalse(any(r.get('origin') == 'OPENING' for r in work['plans']))

    def test_fresh_baseline_captures_stock_without_daily_production(self):
        from apps.planning.workspace_services import build_workspace_snapshot
        self.opening(self.simple, quantity=250)
        baseline = self.create([self.simple]).baseline_version
        spec = build_workspace_snapshot(baseline)['works'][0]
        self.assertEqual(sum(D(r['quantity']) for r in spec['daily']), 1000)
        self.assertEqual(D(spec['daily'][-1]['cumulative']), 1250)
        self.assertTrue(any(r['origin'] == 'OPENING' for r in spec['context_plans']))
        self.assertFalse(any(r.get('origin') == 'OPENING' for r in spec['plans']))

    def test_merge_moves_stock_without_losing_source_history(self):
        from apps.works.merge_service import apply_merge, fingerprint, preview
        from apps.planning.models import LoadProfile, LoadProfileItem
        profile = LoadProfile.objects.create(company=self.company, name='Uniform')
        LoadProfileItem.objects.create(company=self.company, profile=profile, workday_number=1, percentage=100)
        other = ProjectWork.objects.create(company=self.company, section=self.simple.section, name='Other', unit='m')
        self.opening(self.simple, quantity=4)
        self.opening(other, quantity=6)
        values = {'name': 'Merged opening', 'section': self.simple.section, 'unit': 'шт', 'work_group': None, 'allow_fractional': True,
            f'norm_{self.simple.pk}': D(2), f'norm_{other.pk}': D(3), f'profile_{self.simple.pk}': profile, f'profile_{other.pk}': profile}
        works = [self.simple, other]
        self.assertEqual(preview(self.planner, works, values)['fact_quantity'], 2)
        target, _ = apply_merge(self.planner, [w.pk for w in works], values, fingerprint(works))
        self.assertEqual(WorkProgressService.completed(target), 2)
        self.assertEqual(WorkProgressService.completed(self.simple), 4)
        self.assertEqual(WorkOpeningBalance.objects.filter(project_work=target).count(), 2)


class WorkOpeningHierarchyTests(TestCase):
    from apps.planning.test_hierarchy import HierarchyTests as H
    setUpTestData = classmethod(H.setUpTestData.__func__)
    create = H.create
    approve = H.approve

    def test_subobject_stock_seeds_parent_completion_without_counting_as_daily_fact(self):
        self.planner.assigned_objects.add(self.child, self.child2)
        from apps.planning.hierarchy_services import create_smg
        from apps.planning.report_services import build_matrix
        from apps.planning.test_report_matrix import rows_in
        version = self.approve(self.create([self.composite]).baseline_version)
        workspace = create_smg(self.planner, version, self.child, date(2026, 1, 1), {self.composite.pk: D(10)})
        link = workspace.gpr_work_mappings.get()
        # item_mapping maps the SMG item to its GPR item.
        items = {int(target): int(source) for source, target in link.item_mapping.items()}
        for source, qty in [(self.a, D(3)), (self.b, D('4.5'))]:
            WorkOpeningBalance.objects.create(company=self.company, project_work=link.smg_work, work_item_id=items[source.pk], date=date(2026,1,1), quantity=qty)
        for source, qty in [(self.a, D(1)), (self.b, D('1.5'))]:
            DailyFact.objects.create(company=self.company, project_work=link.smg_work, work_item_id=items[source.pk], date=date(2026,1,5), actual_quantity=qty)
        report = build_matrix(self.planner, {'start': date(2026,1,1), 'end': date(2026,1,31), 'mode': 'latest', 'sections': ['works']})
        obj = next(o for o in report['objects'] if o['object'].pk == self.obj.pk)
        row = next(r for r in rows_in(obj['sections'][0]['groups']) if r['label'] == self.composite.name)
        self.assertEqual(row['incoming_quantity'], D('1.5'))
        self.assertEqual(row['total']['fact'], D('.5'))
        self.assertEqual(row['completed_quantity'], 2)
