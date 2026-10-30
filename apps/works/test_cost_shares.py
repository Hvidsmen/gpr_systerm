from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.planning import test_workspace as fixtures
from apps.planning.models import WorkMonthAllocation, LoadProfile
from apps.planning.workspace_services import WorkspaceService, build_workspace_snapshot
from apps.planning.report_services import build_matrix, Source
from apps.production.models import DailyFact
from apps.accounts.models import Role
from .models import ProjectWorkItem
from .forms import ProjectWorkItemForm
from .cost_shares import revision


class CostSharesTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)

    def setUp(self):
        self.client.force_login(self.planner)
        self.url=reverse('works:work_cost_shares',args=[self.composite.pk])

    def data(self,a='25',b='75'):
        return {'revision':revision(list(self.composite.items.order_by('sequence','pk'))),
                f'share_{self.a.pk}':a,f'share_{self.b.pk}':b}

    def post_confirmed(self,url,data):
        response=self.client.post(url,data)
        if response.status_code==200 and response.context and response.context.get('token'):
            self.assertContains(response,'Подтвердить')
            return self.client.post(reverse('works:work_change_confirm'),{'token':response.context['token']})
        return response

    def test_visible_inline_and_separate_form_and_decimal_share_save(self):
        page=self.client.get(reverse('works:work_detail',args=[self.composite.pk]))
        self.assertContains(page,'Доля от суммы, %')
        self.assertContains(page,f'name="share_{self.a.pk}"')
        self.assertContains(self.client.get(reverse('works:work_item_update',args=[self.a.pk])),'name="weight"')
        response=self.post_confirmed(self.url,self.data('25,25','74,75'))
        self.assertEqual(response.status_code,302)
        self.a.refresh_from_db();self.b.refresh_from_db()
        self.assertEqual((self.a.weight,self.b.weight),(D('25.25'),D('74.75')))

    def test_invalid_sum_bounds_and_stale_form_leave_all_shares_unchanged(self):
        for a,b in [('10','10'),('-1','101'),('NaN','100'),('100.001','0')]:
            self.assertEqual(self.post_confirmed(self.url,self.data(a,b)).status_code,400)
            self.a.refresh_from_db();self.b.refresh_from_db()
            self.assertEqual((self.a.weight,self.b.weight),(40,60))
        stale=self.data()
        self.assertEqual(self.post_confirmed(self.url,self.data('30','70')).status_code,302)
        self.assertEqual(self.post_confirmed(self.url,stale).status_code,400)
        self.a.refresh_from_db();self.assertEqual(self.a.weight,30)

    def test_scope_and_read_only_roles(self):
        self.planner.assigned_objects.clear()
        self.assertEqual(self.post_confirmed(self.url,self.data()).status_code,404)
        self.planner.role=Role.objects.get(code='OBSERVER');self.planner.save()
        self.assertEqual(self.post_confirmed(self.url,self.data()).status_code,403)
        page=self.client.get(reverse('works:work_detail',args=[self.composite.pk]))
        self.assertContains(page,'Доля от суммы, %')
        self.assertNotContains(page,'data-cost-share=""')

    def test_single_form_range_and_omitted_share_preserves_existing(self):
        profile=LoadProfile.objects.create(company=self.company,name='Share profile')
        base=dict(name=self.a.name,unit=self.a.unit,load_profile=profile.pk,quantity_per_unit=self.a.quantity_per_unit)
        for value in ['-0.01','100.01','NaN']:
            form=ProjectWorkItemForm({**base,'weight':value},instance=deepcopy(self.a))
            self.assertFalse(form.is_valid());self.assertIn('weight',form.errors)
        form=ProjectWorkItemForm(base,instance=deepcopy(self.a))
        self.assertTrue(form.is_valid(),form.errors);self.assertEqual(form.cleaned_data['weight'],40)

    def matrix(self,snapshot,version,basis):
        return build_matrix(self.planner,{'start':fixtures.JAN,'end':date(2026,1,31),
            'sections':['works'],'mode':'latest','money_basis':basis},
            source_overrides={self.obj.pk:[Source(version,snapshot,fixtures.JAN,date(2026,1,31))]})

    def package(self,report):
        def walk(nodes):
            for node in nodes:
                for row in node['rows']:
                    if row.get('decomposition',{}).get('package_id')==self.composite.pk:return row
                result=walk(node['children'])
                if result:return result
        return walk(report['objects'][0]['sections'][0]['groups'])

    def test_partial_plan_and_fact_use_shares_without_double_count_or_rewriting_snapshot(self):
        self.assertEqual(self.post_confirmed(self.url,self.data('25','75')).status_code,302)
        version=WorkspaceService.create(self.planner,self.obj,'Share plan',fixtures.JAN,date(2026,1,31)).baseline_version
        WorkMonthAllocation.objects.create(company=self.company,version=version,work=self.composite,month=fixtures.JAN,
            quantity=10,item_quantities={str(self.a.pk):'20',str(self.b.pk):'0'})
        snapshot=build_workspace_snapshot(version)
        DailyFact.objects.create(company=self.company,project_work=self.composite,work_item=self.a,
            date=date(2026,1,5),actual_quantity=1,reported_by=self.planner)
        report=self.matrix(snapshot,version,'estimated');row=self.package(report);child=row['children'][0]
        self.assertTrue(child['has_amount'])
        self.assertEqual((child['total']['amount'],child['total']['fact_amount']),(D('250'),D('12.50')))
        from django.template.loader import render_to_string
        rendered=render_to_string('planning/report_cell.html',{'row':child,'cell':child['total'],'section':{'kind':'works'}})
        self.assertIn('12,50',rendered)
        self.assertEqual(row['total']['amount'],250)
        self.assertEqual(report['objects'][0]['sections'][0]['amount'],250)
        real=self.package(self.matrix(snapshot,version,'real'))
        self.assertEqual(real['total']['amount'],0)
        self.assertTrue(real['children'][0]['show_estimated_amount'])
        self.assertEqual(self.post_confirmed(self.url,self.data('50','50')).status_code,302)
        self.assertEqual(self.package(self.matrix(snapshot,version,'estimated'))['children'][0]['total']['fact_amount'],D('12.50'))

    def test_rounding_does_not_assign_kopecks_to_zero_share(self):
        self.composite.unit_price=D('.01');self.composite.save()
        self.a.weight=self.b.weight=50;self.a.quantity_per_unit=self.b.quantity_per_unit=1
        self.a.save();self.b.save()
        zero=ProjectWorkItem.objects.create(company=self.company,project_work=self.composite,name='Zero share',unit='m',quantity_per_unit=1,weight=0)
        version=WorkspaceService.create(self.planner,self.obj,'Rounding',fixtures.JAN,date(2026,1,31)).baseline_version
        WorkMonthAllocation.objects.create(company=self.company,version=version,work=self.composite,month=fixtures.JAN,quantity=1)
        row=self.package(self.matrix(build_workspace_snapshot(version),version,'estimated'))
        self.assertEqual(sum(c['total']['amount'] for c in row['children']),row['total']['amount'])
        self.assertEqual(next(c for c in row['children'] if c['key'].endswith('-i'+str(zero.pk)))['total']['amount'],0)
