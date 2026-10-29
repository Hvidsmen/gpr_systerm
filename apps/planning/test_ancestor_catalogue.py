from django.test import TestCase
from django.core.exceptions import ValidationError
from django.urls import reverse
from apps.projects.models import Section,ConstructionObject
from apps.works.models import ProjectWork
from . import test_subobject_smg as fixtures
from .independent_smg import create,reference
from .daily_schedule_table import add_work_branch,add_entry
from .estimate_catalog import alternatives
from .work_selection_views import ScheduleBranchForm


class AncestorCatalogueTests(TestCase):
    setUpTestData=classmethod(fixtures.SubobjectSMGTests.setUpTestData.__func__)
    setUp=fixtures.SubobjectSMGTests.setUp

    def test_child_without_reference_finds_parent_package_and_copies_local_zero_plan(self):
        schedule=create(self.planner,self.child1,fixtures.JAN)
        form=ScheduleBranchForm(schedule=schedule,user=self.planner)
        self.assertTrue(alternatives(schedule).filter(pk=self.composite.pk).exists())
        self.assertTrue(any(o['value']=='package:'+str(self.composite.pk) for o in form.catalogue_options))
        entry=add_work_branch(self.planner,schedule,'item:'+str(self.a.pk))[0]
        local=ProjectWork.objects.get(pk=entry.metadata['id'])
        self.assertEqual(local.section.construction_object,self.child1)
        self.assertEqual(local.gpr_origin.source_work,self.composite)
        self.assertEqual(entry.gpr_work,self.composite)
        self.assertTrue(all(str(r['quantity']) in {'0','0.000000'} for r in entry.rows))
        schedule.refresh_from_db();self.assertIsNone(schedule.source_id)
        self.source.refresh_from_db();self.assertEqual(self.source.snapshot,self.snapshot)
        # A later reference can use the captured origin without a name match.
        reference(self.planner,schedule,self.source)

    def test_sibling_and_unrelated_catalogues_cannot_be_added(self):
        schedule=create(self.planner,self.child1,fixtures.JAN)
        section=Section.objects.create(company=self.company,construction_object=self.child2,name='Сосед')
        work=ProjectWork.objects.create(company=self.company,section=section,name='Работа соседа',unit='м')
        self.assertFalse(alternatives(schedule).filter(pk=work.pk).exists())
        with self.assertRaises(ValidationError):add_entry(self.planner,schedule,{'kind':'work','work':work})
        self.planner.assigned_objects.set([self.child1])
        self.assertTrue(alternatives(schedule).filter(pk=self.simple.pk).exists())
        entry=add_entry(self.planner,schedule,{'kind':'work','work':self.simple})
        self.assertNotEqual(entry.metadata['id'],self.simple.pk)
