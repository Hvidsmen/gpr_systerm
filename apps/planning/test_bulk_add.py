from datetime import date
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, User, Role
from apps.projects.models import Project, ConstructionObject, Section
from apps.resources.models import Brigade, EquipmentType
from apps.works.models import ProjectWork
from .models import PlanningWorkspace, GlobalPlanVersion, WorkMonthAllocation, ResourceMonthAllocation, LoadProfile


class BulkAddTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Bulk company')
        self.user = User.objects.create_user(username='bulk-planner', company=self.company, role=Role.objects.get(code='PLANNER'))
        project = Project.objects.create(company=self.company, name='Project')
        self.obj = ConstructionObject.objects.create(company=self.company, project=project, name='Object')
        section = Section.objects.create(company=self.company, construction_object=self.obj, name='Section')
        profile = LoadProfile.objects.create(company=self.company, name='Profile')
        self.works = [ProjectWork.objects.create(company=self.company, section=section, name='Work '+str(i), unit='м', load_profile=profile) for i in range(2)]
        self.brigades = [Brigade.objects.create(company=self.company, name='Brigade '+str(i)) for i in range(2)]
        self.equipment = [EquipmentType.objects.create(company=self.company, name='Type '+str(i)) for i in range(2)]
        self.workspace = PlanningWorkspace.objects.create(company=self.company, construction_object=self.obj, name='Plan', start_date=date(2026,1,1), end_date=date(2026,3,31))
        self.version = GlobalPlanVersion.objects.create(company=self.company, construction_object=self.obj, workspace=self.workspace, version_kind='BASELINE', version_number=1, start_date=self.workspace.start_date, end_date=self.workspace.end_date)
        self.workspace.baseline_version = self.version
        self.workspace.save()
        self.client.force_login(self.user)

    def url(self, kind):
        return reverse('planning:workspace_bulk_add', args=[self.version.pk, kind])

    def test_all_kinds_add_multiple_items_across_period_idempotently(self):
        items = {'works':self.works, 'labor':self.brigades, 'equipment':self.equipment, 'fuel':['DIESEL','PETROL_95']}
        for kind, rows in items.items():
            response = self.client.get(self.url(kind))
            self.assertContains(response, 'bulk-search')
            self.assertContains(response, 'type="checkbox"')
            data = {'items':[getattr(row,'pk',row) for row in rows], 'months':['2026-01-01','2026-02-01','2026-03-01']}
            self.assertEqual(self.client.post(self.url(kind), data).status_code, 302)
            query = self.version.work_allocations.all() if kind == 'works' else self.version.resource_allocations.filter(kind=kind)
            self.assertEqual(query.count(), 6)
            row = query.first()
            if kind == 'works':
                row.quantity = 17
            else:
                row.rate = 25
            row.save()
            self.assertEqual(self.client.post(self.url(kind), data).status_code, 302)
            self.assertEqual(query.count(), 6)
            row.refresh_from_db()
            self.assertEqual(row.quantity if kind=='works' else row.rate, 17 if kind=='works' else 25)

    def test_current_month_only_and_snapshot_cleared(self):
        self.version.snapshot = {'old':'preview'}
        self.version.save()
        data = {'items':[row.pk for row in self.equipment], 'months':['2026-02-01'],'equipment_number':'A1'}
        response = self.client.post(self.url('equipment'), data)
        self.assertEqual(response.status_code, 302)
        rows = self.version.resource_allocations.all()
        self.assertEqual(rows.count(), 2)
        self.assertTrue(all(row.month==date(2026,2,1) and row.equipment_number=='A1' for row in rows))
        self.version.refresh_from_db()
        self.assertEqual(self.version.snapshot, {})

    def test_foreign_choices_wrong_object_and_bad_month_are_atomic(self):
        foreign = Company.objects.create(name='Foreign bulk')
        brigade = Brigade.objects.create(company=foreign, name='Foreign brigade')
        data = {'items':[self.brigades[0].pk,brigade.pk], 'months':['2026-01-01','2026-02-01','2026-03-01']}
        self.assertEqual(self.client.post(self.url('labor'), data).status_code, 400)
        self.assertFalse(ResourceMonthAllocation.objects.exists())
        other_obj = ConstructionObject.objects.create(company=self.company, project=self.obj.project, name='Other object')
        section = Section.objects.create(company=self.company, construction_object=other_obj, name='Other section')
        work = ProjectWork.objects.create(company=self.company, section=section, name='Other work', unit='м')
        self.assertNotContains(self.client.get(self.url('works')), 'Other work')
        self.assertEqual(self.client.post(self.url('works'), {'items':[self.works[0].pk,work.pk], 'months':['2026-01-01','2026-02-01','2026-03-01']}).status_code, 400)
        self.assertFalse(WorkMonthAllocation.objects.exists())
        self.assertEqual(self.client.post(self.url('fuel'), {'items':['DIESEL'],'months':['2025-12-01']}).status_code, 400)
        self.assertFalse(ResourceMonthAllocation.objects.exists())

    def test_forecast_scope_and_roles(self):
        GlobalPlanVersion.objects.filter(pk=self.version.pk).update(version_kind='FORECAST', planning_month=date(2026,2,1), scenario='BASELINE')
        data = {'items':['DIESEL'],'months':['2026-01-01','2026-02-01']}
        self.assertEqual(self.client.post(self.url('fuel'), data).status_code, 400)
        data['months']=['2026-02-01','2026-03-01']
        self.assertEqual(self.client.post(self.url('fuel'), data).status_code, 400)
        data['months']=['2026-02-01']
        self.assertEqual(self.client.post(self.url('fuel'), data).status_code, 302)
        for code in ('MANAGER','FOREMAN'):
            user = User.objects.create_user(username='bulk-'+code, company=self.company, role=Role.objects.get(code=code))
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url('fuel')).status_code, 403)
            self.assertEqual(self.client.post(self.url('fuel'), data).status_code, 403)
        self.client.force_login(self.user)
        GlobalPlanVersion.objects.filter(pk=self.version.pk).update(status='APPROVED')
        self.assertEqual(self.client.post(self.url('fuel'), data).status_code, 403)

    def test_nonconsecutive_months_and_no_month_validation(self):
        data = {'items':[self.equipment[0].pk], 'months':['2026-01-01','2026-03-01']}
        self.assertEqual(self.client.post(self.url('equipment'), data).status_code, 302)
        self.assertEqual(set(self.version.resource_allocations.values_list('month', flat=True)), {date(2026,1,1),date(2026,3,1)})
        response = self.client.post(self.url('fuel'), {'items':['DIESEL']})
        self.assertEqual(response.status_code, 400)
        self.assertIn('months', response.context['form'].errors)
        self.assertFalse(self.version.resource_allocations.filter(kind='fuel').exists())
        response = self.client.get(self.url('equipment')+'?month=2026-03-01')
        self.assertEqual(response.context['form']['months'].value(), ['2026-03-01'])
        self.assertContains(response, 'data-bulk-month')
