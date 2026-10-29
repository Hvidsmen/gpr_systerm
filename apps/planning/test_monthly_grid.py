from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User,Role,Company
from apps.resources.models import Employee,EquipmentCrewPosition
from apps.resources.position_catalog import position_for
from . import test_workspace as fixtures,monthly_grid
from .models import WorkMonthAllocation,ResourceMonthAllocation,GlobalPlanVersion
from .workspace_services import WorkspaceService

JAN=fixtures.JAN;FEB=fixtures.FEB;MAR=fixtures.MAR


class MonthlyGridTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)
    def setUp(self):
        self.workspace=WorkspaceService.create(self.planner,self.obj,'Помесячный ввод',JAN,date(2026,3,31));self.version=self.workspace.baseline_version
        self.simple_row=WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.simple,month=JAN,quantity=5)
        self.complex_row=WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.composite,month=JAN,quantity=0,item_quantities={str(self.a.pk):'20',str(self.b.pk):'15'})
        self.position=position_for(self.brigade);Employee.objects.create(company=self.company,first_name='Тест',last_name='Сотрудник',position=self.position)
        self.people=ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,kind='labor',brigade=self.brigade,count=2,hours=160,rate=10)
        self.url=reverse('planning:workspace_months',args=[self.version.pk]);self.client.force_login(self.planner)
    def patch(self,row,month,field,value):
        model=type(row);target=model.objects.filter(version=self.version,month=month,**({'work_id':row.work_id} if isinstance(row,WorkMonthAllocation) else {'kind':row.kind,'brigade_id':row.brigade_id,'equipment_type_id':row.equipment_type_id,'equipment_number':row.equipment_number,'fuel_type':row.fuel_type,'equipment_ref':row.equipment_ref})).first()
        return {'kind':'work' if isinstance(row,WorkMonthAllocation) else 'resource','source':row.pk,'key':monthly_grid.identity(row),'month':str(month),'field':field,'value':str(value),'id':target.pk if target else '', 'revision':target.updated_at.isoformat() if target else 'missing'}
    def send(self,patches):return self.client.post(self.url,{'changes':patches},content_type='application/json')
    def test_view_renders_all_months_and_preserves_the_single_month_editor(self):
        page=self.client.get(self.url);self.assertEqual(page.status_code,200)
        self.assertEqual(page.context['months'],[JAN,FEB,MAR]);self.assertContains(page,'data-grid-month="2026-03-01"')
        self.assertContains(page,'Показатель / ед.');self.assertContains(page,'Связи работ и ресурсов')
        self.assertContains(self.client.get(reverse('planning:workspace_edit',args=[self.version.pk])),'По месяцам · весь период')
    def test_atomic_save_of_different_months_creates_only_changed_cells(self):
        response=self.send([self.patch(self.simple_row,JAN,'quantity',8),self.patch(self.simple_row,FEB,'quantity','12,5'),self.patch(self.people,MAR,'count',4)])
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(self.version.work_allocations.get(work=self.simple,month=JAN).quantity,8)
        self.assertEqual(self.version.work_allocations.get(work=self.simple,month=FEB).quantity,Decimal('12.5'))
        self.assertFalse(self.version.work_allocations.filter(work=self.simple,month=MAR).exists())
        self.assertEqual(self.version.resource_allocations.get(kind='labor',month=MAR).count,4)
        self.people.refresh_from_db();self.assertEqual((self.people.hours,self.people.rate),(160,10))
    def test_partial_composite_quantities_preserve_other_components_and_reset_override(self):
        before=self.complex_row.quantity
        response=self.send([self.patch(self.complex_row,JAN,'item:'+str(self.a.pk),'30')]);self.assertEqual(response.status_code,200,response.content)
        self.complex_row.refresh_from_db();self.assertEqual(self.complex_row.item_quantities[str(self.a.pk)],'30');self.assertEqual(Decimal(self.complex_row.item_quantities[str(self.b.pk)]),15)
        self.assertIsNone(self.complex_row.daily_override)
    def test_bad_value_or_stale_cell_rolls_back_entire_batch(self):
        one=self.patch(self.simple_row,JAN,'quantity',9);bad=self.patch(self.people,FEB,'count',-1)
        self.assertEqual(self.send([one,bad]).status_code,400);self.simple_row.refresh_from_db();self.assertEqual(self.simple_row.quantity,5)
        stale=self.patch(self.simple_row,JAN,'quantity',12);self.simple_row.quantity=7;self.simple_row.save()
        self.assertEqual(self.send([self.patch(self.people,FEB,'count',3),stale]).status_code,400)
        self.assertFalse(self.version.resource_allocations.filter(month=FEB).exists())
        self.assertEqual(self.send([self.patch(self.simple_row,JAN,'quantity','')]).status_code,400)
    def test_company_roles_past_months_and_submitted_versions_are_protected(self):
        other=Company.objects.create(name='Чужая компания');user=User.objects.create_user(username='grid-outsider',company=other,role=Role.objects.get(code='ADMIN'))
        payload=self.patch(self.simple_row,JAN,'quantity',8);self.client.force_login(user);self.assertEqual(self.send([payload]).status_code,404)
        self.client.force_login(self.planner);self.version.status='SUBMITTED';GlobalPlanVersion.objects.filter(pk=self.version.pk).update(status='SUBMITTED')
        self.assertEqual(self.send([payload]).status_code,403);self.assertEqual(self.client.get(self.url).status_code,302)
    def test_grid_honors_forecast_months_and_does_not_convert_automatic_months(self):
        self.version.planning_month=FEB;self.version.scenario='BASELINE';self.version.version_kind='FORECAST';self.version.save()
        self.assertEqual(monthly_grid.editable_months(self.version),[FEB])
        self.assertEqual(self.send([self.patch(self.simple_row,JAN,'quantity',8)]).status_code,400)
        self.assertEqual(self.send([self.patch(self.simple_row,MAR,'quantity',8)]).status_code,400)
    def test_adding_a_branch_adds_selected_months_without_overwriting_existing_values(self):
        response=self.client.post(self.url,{'action':'add_work','selection':'package:'+str(self.simple.pk),'months':[str(JAN),str(FEB)]})
        self.assertEqual(response.status_code,302,response.content);self.simple_row.refresh_from_db();self.assertEqual(self.simple_row.quantity,5)
        self.assertTrue(self.version.work_allocations.filter(work=self.simple,month=FEB,quantity=0).exists())
    def test_equipment_count_recalculates_monthly_crew(self):
        EquipmentCrewPosition.objects.create(company=self.company,equipment_type=self.equipment,position=self.position,count=2)
        machine=ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,kind='equipment',equipment_type=self.equipment,count=0)
        response=self.send([self.patch(machine,FEB,'count',3)]);self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(self.version.resource_allocations.get(month=FEB,kind='labor',brigade=self.brigade).count,6)

    def test_manager_cannot_smuggle_work_changes_with_resource_edits(self):
        from .preparation import handoff
        estimator=User.objects.create_user(username='grid-estimator',company=self.company,role=Role.objects.get(code='ESTIMATOR'))
        handoff(self.planner,self.version,self.manager,estimator)
        self.client.force_login(self.manager)
        response=self.send([self.patch(self.people,JAN,'count',4),self.patch(self.simple_row,JAN,'quantity',99)])
        self.assertEqual(response.status_code,403);self.people.refresh_from_db();self.assertEqual(self.people.count,2)
        self.assertEqual(self.send([self.patch(self.people,JAN,'count',4)]).status_code,200)

    def test_saved_values_use_existing_daily_projection(self):
        from .workspace_services import build_workspace_snapshot
        self.assertEqual(self.send([self.patch(self.simple_row,FEB,'quantity','12.5')]).status_code,200)
        snapshot=build_workspace_snapshot(self.version)
        work=next(work for work in snapshot['works'] if work['id']==self.simple.pk)
        self.assertEqual(sum(Decimal(row['quantity']) for row in work['plans'] if row['date'].startswith('2026-02')),Decimal('12.5'))

    def test_subset_and_missing_component_permissions_are_preserved(self):
        self.complex_row.selected_item_ids=[self.a.pk];self.complex_row.item_quantities={str(self.a.pk):'20'};self.complex_row.save()
        self.assertEqual(self.send([self.patch(self.complex_row,JAN,'item:'+str(self.b.pk),3)]).status_code,400)
        self.assertEqual(self.send([self.patch(self.complex_row,FEB,'item:'+str(self.a.pk),6)]).status_code,200)
        row=self.version.work_allocations.get(month=FEB,work=self.composite)
        self.assertEqual(row.selected_item_ids,[self.a.pk]);self.assertEqual(row.quantity,0)
