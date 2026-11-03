from datetime import timedelta
from uuid import uuid4
from django.test import TestCase
from django.urls import reverse
from .test_production_collaboration import ProductionCollaborationTests
from .dispatch_models import EmployeeActivity, EmployeeWorkLink
from .models import DailyFact


class WorkClearTests(TestCase):
    setUpTestData=classmethod(ProductionCollaborationTests.setUpTestData.__func__)
    setUp=ProductionCollaborationTests.setUp
    payload=ProductionCollaborationTests.payload
    edge=ProductionCollaborationTests.edge

    def zero(self,**extra):
        data=self.payload('DISPATCHER','works');data.update({'autosave':'1','collaborative':'1',**extra})
        prefix=next(k[:-len('project_work')] for k,v in data.items() if k.endswith('-project_work') and str(v)==str(self.work.pk))
        data[prefix+'actual_quantity']='0'
        return self.client.post(self.url,data)
    def test_zero_removes_only_matching_work_and_retains_other_links(self):
        self.edge();self.edge(fact=self.second_fact.pk)
        self.person.refresh_from_db();before=self.person.links_revision
        self.assertEqual(self.zero().status_code,200)
        self.person.refresh_from_db();self.fact.refresh_from_db()
        self.assertEqual(self.fact.actual_quantity,0);self.assertEqual(self.person.work_id,self.second_work.pk)
        self.assertEqual(set(self.person.work_links.values_list('work_id',flat=True)),{self.second_work.pk})
        self.assertTrue(self.person.assignment_reviewed);self.assertGreater(self.person.links_revision,before)
    def test_zero_last_work_keeps_employee_and_resets_assignment_to_pending(self):
        self.edge();pk=self.person.pk
        from .dispatch import project_resources
        project_resources(self.obj,self.day,'labor')
        self.assertEqual(self.zero().status_code,200)
        self.person.refresh_from_db();self.assertEqual(self.person.pk,pk);self.assertEqual(self.person.employee_id,self.employee.pk)
        self.assertIsNone(self.person.work_id);self.assertIsNone(self.person.work_item_id);self.assertFalse(self.person.general);self.assertFalse(self.person.assignment_reviewed)
        from .models import LaborFact
        self.assertEqual(LaborFact.objects.get().actual_workers,1)
    def test_zero_removes_all_employee_edges_but_preserves_other_day(self):
        self.edge();other=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=self.cook,position=self.cook.position,work=self.work,entry_direction='works')
        EmployeeWorkLink.objects.create(company=self.company,activity=other,work=self.work)
        old=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day-timedelta(days=1),employee=self.employee,work=self.work,entry_direction='works')
        old_link=EmployeeWorkLink.objects.create(company=self.company,activity=old,work=self.work)
        self.assertEqual(self.zero().status_code,200)
        self.assertFalse(EmployeeWorkLink.objects.filter(activity__date=self.day,work=self.work).exists());self.assertTrue(EmployeeWorkLink.objects.filter(pk=old_link.pk).exists())
        self.assertEqual(EmployeeActivity.objects.filter(date=self.day).count(),2)
    def test_legacy_primary_assignment_without_link_is_cleared(self):
        self.person.work=self.work;self.person.assignment_reviewed=True;self.person.save()
        self.assertFalse(self.person.work_links.exists());self.assertEqual(self.zero().status_code,200)
        self.person.refresh_from_db();self.assertIsNone(self.person.work_id);self.assertFalse(self.person.assignment_reviewed)
    def test_late_add_and_delta_to_zero_are_rejected(self):
        self.assertEqual(self.zero().status_code,200)
        for action,data in [('add',{}),('delta',{'revision':self.person.links_revision,'edges':[{'action':'add','fact':self.fact.pk}]})]:
            self.assertEqual(self.edge(action,**data).status_code,409)
        self.assertFalse(self.person.work_links.exists())
    def test_nonzero_after_clear_does_not_restore_edges_automatically(self):
        self.edge();self.assertEqual(self.zero().status_code,200)
        data=self.payload('DISPATCHER','works');data['works-0-actual_quantity']='5';data.update(autosave='1',collaborative='1')
        self.assertEqual(self.client.post(self.url,data).status_code,200);self.assertFalse(self.person.work_links.exists())
        self.assertEqual(self.edge().status_code,200);self.assertTrue(self.person.work_links.exists())
    def test_stale_person_in_same_full_post_cannot_recreate_cleared_edges(self):
        self.edge();data=self.payload('DISPATCHER','works')
        prefix=next(k[:-len('project_work')] for k,v in data.items() if k.endswith('-project_work') and str(v)==str(self.work.pk))
        data[prefix+'actual_quantity']='0';data['labor-0-activity']='Уточнение'
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.person.refresh_from_db();self.assertFalse(self.person.work_links.exists());self.assertIsNone(self.person.work_id)
        self.assertEqual(self.person.activity,'Уточнение')
    def test_validation_failure_does_not_partially_zero_or_detach(self):
        self.edge();response=self.zero(**{'works-1-actual_quantity':'не число'})
        self.assertEqual(response.status_code,400);self.fact.refresh_from_db();self.assertEqual(self.fact.actual_quantity,5);self.assertTrue(self.person.work_links.exists())
    def test_other_dispatcher_work_reservation_blocks_zero_without_partial_cleanup(self):
        self.edge();self.client.force_login(self.second)
        r=self.client.post(reverse('production:work_claims'),{**self.scope,'session':str(uuid4()),'action':'take','key':f'work:{self.work.pk}'},content_type='application/json')
        self.assertEqual(r.status_code,200);response=self.zero();self.assertEqual(response.status_code,400)
        self.fact.refresh_from_db();self.assertEqual(self.fact.actual_quantity,5);self.assertTrue(self.person.work_links.exists())
    def test_clear_button_available_for_planned_work_and_no_fact_delete(self):
        self.edge();page=self.client.get(self.url,{'construction_object':self.obj.pk,'date':str(self.day),'section':'works'})
        self.assertContains(page,'data-work-clear');self.assertContains(page,'Очистить работу')
        self.assertEqual(self.zero().status_code,200);self.assertTrue(DailyFact.objects.filter(pk=self.fact.pk).exists())
    def test_composite_item_clear_keeps_other_item_links(self):
        from .test_work_entry_ui import hierarchy_fixture
        _,package,first,second,_,_=hierarchy_fixture(self)
        one=DailyFact.objects.create(company=self.company,date=self.day,project_work=package,work_item=first,actual_quantity=2)
        two=DailyFact.objects.create(company=self.company,date=self.day,project_work=package,work_item=second,actual_quantity=3)
        self.edge(fact=one.pk);self.edge(fact=two.pk)
        data=self.payload('DISPATCHER','works');data.update(autosave='1',collaborative='1')
        prefix=next(k[:-len('work_item')] for k,v in data.items() if k.endswith('-work_item') and str(v)==str(first.pk))
        data[prefix+'actual_quantity']='0';self.assertEqual(self.client.post(self.url,data).status_code,200)
        self.assertFalse(self.person.work_links.filter(work_item=first).exists());self.assertTrue(self.person.work_links.filter(work_item=second).exists())
        self.person.refresh_from_db();self.assertEqual(self.person.work_item_id,second.pk)
    def test_copied_links_skip_work_with_zero_fact(self):
        from .copy_previous import production_copy
        old=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day-timedelta(days=1),employee=self.employee,work=self.work,entry_direction='works')
        EmployeeWorkLink.objects.create(company=self.company,activity=old,work=self.work)
        self.fact.actual_quantity=0;self.fact.save()
        result=production_copy(self.user,self.obj,self.day,'links',session=self.session,reset_epoch=0,revisions={str(self.person.pk):self.person.links_revision})
        self.assertEqual(result['added'],0);self.assertFalse(self.person.work_links.exists())

    def test_direct_fact_save_zero_also_clears_edges(self):
        self.edge();self.fact.actual_quantity=0;self.fact.save()
        self.person.refresh_from_db();self.assertFalse(self.person.work_links.exists());self.assertIsNone(self.person.work_id)
    def test_metadata_only_save_does_not_clear_a_positive_database_fact(self):
        self.edge();self.fact.actual_quantity=0;self.fact.comment='Комментарий'
        self.fact.save(update_fields=['comment'])
        self.fact.refresh_from_db();self.assertEqual(self.fact.actual_quantity,5);self.assertTrue(self.person.work_links.exists())
