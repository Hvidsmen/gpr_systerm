from copy import deepcopy
from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError,PermissionDenied
from django.test import TestCase
from django.urls import reverse
from apps.projects.models import ConstructionObject,Section
from apps.works.models import ProjectWork,ProjectWorkItem
from apps.resources.models import EquipmentType
from . import test_workspace as fixtures,independent_smg as independent
from .models import GlobalPlanVersion,WorkMonthAllocation,ResourceMonthAllocation,DailySchedule,SubobjectWorkOrigin,WorkResourceAssignment
from .workspace_services import WorkspaceService,build_workspace_snapshot
from .daily_schedule_table import add_entry,add_work_branch,save_table
from .daily_schedule_table_forms import AddEntryForm
from .daily_schedule import identities
from .subobject_smg import comparison,reference_choices,map_entry

D=Decimal
JAN=fixtures.JAN


class SubobjectSMGTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)

    def setUp(self):
        self.obj.name='КТП';self.obj.save()
        self.child1=ConstructionObject.objects.create(company=self.company,project=self.obj.project,parent=self.obj,name='КТП-1')
        self.child2=ConstructionObject.objects.create(company=self.company,project=self.obj.project,parent=self.obj,name='КТП-2')
        self.planner.assigned_objects.set([self.obj,self.child1,self.child2])
        self.source=WorkspaceService.create(self.planner,self.obj,'Общий план КТП',JAN,date(2026,1,31)).baseline_version
        for work,amount in [(self.simple,100),(self.composite,10)]:WorkMonthAllocation.objects.create(company=self.company,version=self.source,month=JAN,work=work,quantity=amount)
        ResourceMonthAllocation.objects.create(company=self.company,version=self.source,month=JAN,kind='equipment',equipment_type=self.equipment,count=3,hours=744)
        snapshot=build_workspace_snapshot(self.source)
        GlobalPlanVersion.objects.filter(pk=self.source.pk).update(status='APPROVED',snapshot=snapshot)
        self.source.refresh_from_db();self.snapshot=deepcopy(self.source.snapshot)
        self.client.force_login(self.planner)

    def schedule(self,obj=None,copy=True):
        return independent.create(self.planner,obj or self.child1,JAN,self.source,copy)

    def entry(self,schedule,work):return schedule.entries.get(gpr_work=work)

    def set_values(self,entry,quantity,source_item=None,field='quantity'):
        local_item=entry.metadata.get('gpr_item_mapping',{}).get(str(source_item.pk)) if source_item else None
        save_table(self.planner,entry.schedule,[{'id':entry.pk,'revision':entry.revision,'changes':[dict(date='2026-01-05',item_id=local_item,field=field,value=str(quantity))]}])
        entry.refresh_from_db()

    def test_copy_parent_only_structure_uses_local_ids_and_preserves_reference(self):
        schedule=self.schedule()
        self.assertEqual(schedule.entries.count(),2)
        self.assertFalse(schedule.resource_assignments.exists())
        self.assertFalse(schedule.entries.exclude(kind='work').exists())
        for entry in schedule.entries.all():
            work=ProjectWork.objects.get(pk=entry.metadata['id'])
            self.assertEqual(work.section.construction_object,self.child1)
            self.assertNotEqual(work.pk,entry.gpr_work_id)
            self.assertTrue(all(D(row['quantity'])==0 for row in entry.rows))
            self.assertEqual(entry.original,[])
        complex_entry=self.entry(schedule,self.composite)
        self.assertEqual(len(complex_entry.metadata['gpr_item_mapping']),2)
        self.assertTrue(all(item.pk in complex_entry.metadata['gpr_item_mapping'].values() for item in ProjectWork.objects.get(pk=complex_entry.metadata['id']).items.all()))
        self.source.refresh_from_db();self.assertEqual(self.source.snapshot,self.snapshot)

    def test_compare_parent_with_sum_and_own_contribution(self):
        first=self.schedule();second=self.schedule(self.child2)
        self.set_values(self.entry(first,self.simple),60);self.set_values(self.entry(second,self.simple),40)
        report=comparison(first)
        row=next(row for row in report['rows'] if row['name']==self.simple.name)
        self.assertEqual((row['gpr'],row['own'],row['total'],row['delta']),(100,60,100,0))
        self.assertEqual(row['cells'],[D(60),D(40)])
        response=self.client.get(reverse('planning:daily_schedule_detail',args=[first.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Σ СМГ');self.assertContains(response,'КТП-2')
        self.assertFalse(response.context['has_direct_reference'])
        self.source.refresh_from_db();self.assertEqual(self.source.snapshot,self.snapshot)

    def test_complex_completion_is_calculated_per_child_not_from_fragments(self):
        first=self.schedule();second=self.schedule(self.child2)
        self.set_values(self.entry(first,self.composite),20,self.a)
        self.set_values(self.entry(second,self.composite),30,self.b)
        row=next(row for row in comparison(first)['rows'] if 'готовые комплексы' in row['name'])
        self.assertEqual(row['total'],0)
        self.set_values(self.entry(first,self.composite),12,self.b)
        row=next(row for row in comparison(first)['rows'] if 'готовые комплексы' in row['name'])
        self.assertEqual(row['own'],4);self.assertEqual(row['total'],4)

    def test_partial_parent_item_addition_preserves_values_and_expands_explicitly(self):
        schedule=self.schedule(copy=False)
        entry=add_work_branch(self.planner,schedule,f'item:{self.a.pk}')[0]
        self.assertEqual(len(entry.metadata['items']),1)
        self.set_values(entry,20,self.a)
        entry=add_work_branch(self.planner,schedule,f'item:{self.b.pk}')[0]
        self.assertEqual(len(entry.metadata['items']),2)
        self.assertEqual(sum(D(row['quantity']) for row in entry.rows),20)
        self.assertEqual(sum(r['total'] for r in comparison(schedule)['rows'] if 'готовые комплексы' in r['name']),0)
        self.set_values(entry,30,self.b)
        self.assertEqual(next(r['total'] for r in comparison(schedule)['rows'] if 'готовые комплексы' in r['name']),10)

    def test_parent_copy_keeps_only_selected_monthly_subworks(self):
        GlobalPlanVersion.objects.filter(pk=self.source.pk).update(status='DRAFT');self.source.status='DRAFT'
        WorkMonthAllocation.objects.filter(version=self.source,work=self.composite).update(quantity=0,selected_item_ids=[self.a.pk],item_quantities={str(self.a.pk):'20'})
        snapshot=build_workspace_snapshot(self.source)
        GlobalPlanVersion.objects.filter(pk=self.source.pk).update(snapshot=snapshot,status='APPROVED')
        self.source.refresh_from_db()
        schedule=self.schedule();entry=self.entry(schedule,self.composite)
        self.assertEqual([item['name'] for item in entry.metadata['items']],[self.a.name])
        self.assertEqual(len(entry.metadata['valuation_items']),2)
        self.set_values(entry,20,self.a)
        self.assertEqual(next(r['total'] for r in comparison(schedule)['rows'] if 'готовые комплексы' in r['name']),0)

    def test_parent_catalog_available_to_child_planner_without_root_write_access(self):
        self.planner.assigned_objects.set([self.child1])
        self.assertIn(self.source,reference_choices(self.planner))
        schedule=self.schedule(copy=False)
        form=AddEntryForm(schedule=schedule)
        self.assertIn(self.simple,form.fields['work'].queryset)
        self.assertTrue(any('КТП' in label for _,group in form.fields['selection'].choices if isinstance(group,list) for token,label in group))
        entry=add_work_branch(self.planner,schedule,f'package:{self.simple.pk}')[0]
        self.assertEqual(entry.gpr_work_id,self.simple.pk)
        self.assertEqual(self.client.get(reverse('planning:workspace_edit',args=[self.source.pk])).status_code,404)
        other=ConstructionObject.objects.create(company=self.company,project=self.obj.project,name='Чужой объект')
        with self.assertRaises((ValidationError,PermissionDenied)):independent.create(self.planner,other,JAN,self.source)

    def test_same_names_never_silently_match_and_manual_mapping_preserves_values(self):
        schedule=self.schedule(copy=False)
        section=Section.objects.create(company=self.company,construction_object=self.child1,name='Местный раздел')
        work=ProjectWork.objects.create(company=self.company,section=section,name=self.simple.name,unit=self.simple.unit)
        entry=add_entry(self.planner,schedule,{'kind':'work','work':work})
        self.set_values(entry,25)
        report=comparison(schedule)
        self.assertEqual(len(report['unmapped']),1)
        self.assertEqual(next(r['total'] for r in report['rows'] if r['name']==self.simple.name),0)
        before=deepcopy(entry.rows)
        entry=map_entry(self.planner,schedule,entry.pk,self.simple.pk)
        self.assertEqual(entry.rows,before)
        self.assertEqual(next(r['total'] for r in comparison(schedule)['rows'] if r['name']==self.simple.name),25)
        self.assertEqual(comparison(schedule)['unmapped'],[])

    def test_wrong_parent_reference_rejected_and_mapping_checks_units(self):
        schedule=self.schedule(copy=False)
        other=ConstructionObject.objects.create(company=self.company,project=self.obj.project,name='Другой КТП')
        self.planner.assigned_objects.add(other)
        source=WorkspaceService.create(self.planner,other,'Другой',JAN,date(2026,1,31)).baseline_version
        GlobalPlanVersion.objects.filter(pk=source.pk).update(status='APPROVED')
        source.refresh_from_db()
        with self.assertRaises(ValidationError):independent.reference(self.planner,schedule,source)
        section=Section.objects.create(company=self.company,construction_object=self.child1,name='Участок')
        work=ProjectWork.objects.create(company=self.company,section=section,name='Иная единица',unit='м3')
        entry=add_entry(self.planner,schedule,{'kind':'work','work':work})
        with self.assertRaises(ValidationError):map_entry(self.planner,schedule,entry.pk,self.simple.pk)
        self.assertFalse(SubobjectWorkOrigin.objects.filter(local_work=work).exists())

    def test_mapping_survives_draft_deletion_and_reuses_local_catalogue_work(self):
        schedule=self.schedule();local_id=self.entry(schedule,self.simple).metadata['id']
        schedule.delete()
        self.assertTrue(SubobjectWorkOrigin.objects.filter(local_work_id=local_id).exists())
        next_schedule=self.schedule()
        self.assertEqual(self.entry(next_schedule,self.simple).metadata['id'],local_id)

    def test_latest_revision_once_and_open_old_revision_shows_its_own_values(self):
        first=self.schedule();second=self.schedule(self.child2)
        self.set_values(self.entry(first,self.simple),10);self.set_values(self.entry(second,self.simple),20)
        DailySchedule.objects.filter(pk=second.pk).update(status='APPLIED');second.refresh_from_db()
        revised=independent.revise(self.planner,second)
        self.set_values(self.entry(revised,self.simple),30)
        self.assertEqual(next(r['total'] for r in comparison(first)['rows'] if r['name']==self.simple.name),40)
        self.assertEqual(next(r['own'] for r in comparison(second)['rows'] if r['name']==self.simple.name),20)
        self.assertEqual(self.entry(revised,self.simple).gpr_work_id,self.simple.pk)

    def test_equipment_types_in_both_forms_and_no_number_is_stored(self):
        schedule=self.schedule(copy=False)
        variant=EquipmentType.objects.create(company=self.company,parent=self.equipment,name='Экскаватор №101')
        form=AddEntryForm(schedule=schedule)
        self.assertIn(self.equipment,form.fields['equipment'].queryset)
        self.assertNotIn(variant,form.fields['equipment'].queryset)
        self.assertTrue(form.fields['reference'].widget.is_hidden)
        from .work_resource_views import AssignmentForm
        assignment=AssignmentForm(owner=schedule)
        self.assertIn(self.equipment,assignment.fields['equipment_type'].queryset)
        self.assertNotIn(variant,assignment.fields['equipment_type'].queryset)
        entry=add_entry(self.planner,schedule,{'kind':'equipment','equipment':self.equipment,'reference':'ignored-number','rate':10})
        self.set_values(entry,3,field='planned_count')
        self.assertTrue(all(row['equipment_number']=='' for row in entry.rows))
        with self.assertRaises(ValidationError):add_entry(self.planner,schedule,{'kind':'equipment','equipment':variant,'rate':10})
        row=next(r for r in comparison(schedule)['rows'] if r['kind']=='equipment' and 'количество' in r['name'])
        self.assertEqual(row['own'],D(3)/31);self.assertEqual(row['gpr'],3)

    def test_planner_resources_still_need_manager_confirmation_and_closed_days_are_protected(self):
        schedule=self.schedule(copy=False)
        entry=add_entry(self.planner,schedule,{'kind':'labor','brigade':self.brigade,'rate':10})
        self.set_values(entry,2,field='planned_count')
        from . import preparation
        with self.assertRaises(PermissionDenied):preparation.ready(self.planner,schedule)
        DailySchedule.objects.filter(pk=schedule.pk).update(locked_before=date(2026,1,10))
        with self.assertRaises(ValidationError):self.set_values(entry,3,field='planned_count')
        entry.refresh_from_db();self.assertEqual(D(next(r for r in entry.rows if r['date']=='2026-01-05')['planned_count']),2)

    def test_list_exposes_delete_and_graph_only_delete_get_is_preview(self):
        schedule=self.schedule()
        response=self.client.get(reverse('planning:daily_schedule_list'))
        self.assertContains(response,reverse('planning:smg_delete',args=[schedule.pk]))
        self.assertContains(response,reverse('planning:work_resources_graph_smg',args=[schedule.pk]))
        response=self.client.get(reverse('planning:smg_delete',args=[schedule.pk]))
        self.assertEqual(response.status_code,200);self.assertTrue(DailySchedule.objects.filter(pk=schedule.pk).exists())

    def test_child_operative_report_contains_only_child_plan_and_approval_still_required(self):
        from .test_daily_schedule import activate
        from .daily_schedule_approval import requires_approval
        from .report_services import select_sources,build_matrix
        schedule=self.schedule();self.set_values(self.entry(schedule,self.simple),37)
        self.assertTrue(requires_approval(schedule))
        schedule=activate(self.planner,schedule)
        self.assertEqual(schedule.status,'APPLIED')
        filters={'start':JAN,'end':date(2026,1,31),'mode':'current','objects':ConstructionObject.objects.filter(pk=self.child1.pk),'sections':['works']}
        selected=select_sources(self.planner,filters,[self.child1])[self.child1.pk][0]
        self.assertEqual(selected.version.construction_object_id,self.child1.pk)
        self.assertTrue(all(w['id'] not in {self.simple.pk,self.composite.pk} for w in selected.snapshot['works']))
        self.assertEqual(sum(D(r['quantity']) for w in selected.snapshot['works'] if w['kind']=='SIMPLE' for r in w['plans']),37)
        report=build_matrix(self.planner,filters)
        self.assertEqual(report['objects'][0]['object'].pk,self.child1.pk)
        self.source.refresh_from_db();self.assertEqual(self.source.snapshot,self.snapshot)

    def test_resource_confirmation_hash_does_not_change_for_nullable_origin_field(self):
        from unittest.mock import patch
        from . import preparation
        schedule=self.schedule(copy=False)
        add_entry(self.planner,schedule,{'kind':'labor','brigade':self.brigade,'rate':10})
        current=preparation.resource_signature(schedule)
        original=type(schedule.entries.all()).values
        def old_values(query,*args,**kwargs):
            result=original(query,*args,**kwargs)
            if not args and query.model.__name__=='DailyScheduleEntry':
                return [{k:v for k,v in row.items() if k!='gpr_work_id'} for row in result]
            return result
        with patch('django.db.models.query.QuerySet.values',old_values):self.assertEqual(preparation.resource_signature(schedule),current)
