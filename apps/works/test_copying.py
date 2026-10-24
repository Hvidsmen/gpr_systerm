from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.urls import reverse
from apps.accounts.models import Role
from . import test_schedule_import as fixtures
from .schedule_import import apply_rows
from .copying import copy_catalogue
from .models import ComplexObject, ProjectWork, ProjectWorkItem

class CatalogueCopyTests(TestCase):
    def setUp(self):
        fixtures.ScheduleImportTests.setUp(self)
        apply_rows(self.user,self.section,self.profile,self.rows)
        self.work=ProjectWork.objects.get(section=self.section)
        self.obj_facility=self.work.complex_object
        self.item=self.work.items.get(name='Кабель')
        self.item.weight=Decimal(40);self.item.planned_quantity=99;self.item.excel_aliases=['Кабель из Excel'];self.item.save()
        self.child=ProjectWorkItem.objects.create(company=self.company,project_work=self.work,parent=self.item,name='Подготовка',unit='м.п.',quantity_per_unit=2,load_profile=self.profile,sequence=3)
        self.work.excel_aliases=['Комплекс из Excel'];self.work.unit_price=123;self.work.save()

    def test_package_copy_is_independent_and_has_no_plans_or_fact(self):
        clone=copy_catalogue(self.user,'package',self.work.pk,'Новая система')
        self.assertNotEqual(clone.pk,self.work.pk)
        self.assertEqual(clone.complex_object,self.obj_facility)
        self.assertEqual(clone.rd_section,'210-СВН')
        self.assertEqual(clone.unit_price,Decimal(123))
        self.assertEqual(clone.excel_aliases,[])
        copied=clone.items.get(name='Кабель')
        self.assertEqual(copied.quantity_per_unit,290)
        self.assertEqual(copied.weight,40);self.assertEqual(copied.planned_quantity,0)
        self.assertEqual(copied.excel_aliases,[])
        self.assertEqual(clone.items.get(name='Подготовка').parent,copied)
        self.assertFalse(clone.daily_facts.exists());self.assertFalse(clone.monthly_plans.exists())
        self.assertEqual(clone.price_history.count(),1)
        self.assertEqual(self.work.items.count(),3)

    def test_complex_object_copy_deep_copies_its_packages(self):
        clone=copy_catalogue(self.user,'object',self.obj_facility.pk,'УЗА 27')
        self.assertEqual(clone.construction_object,self.obj)
        self.assertEqual(clone.quantity,1)
        self.assertEqual(clone.work_packages.count(),1)
        work=clone.work_packages.get()
        self.assertNotEqual(work.pk,self.work.pk)
        self.assertEqual(work.name,self.work.name)
        self.assertEqual(work.items.count(),3)
        self.assertEqual(work.items.get(name='Подготовка').parent.project_work_id,work.pk)

    def test_item_copy_keeps_descendants_and_sibling_location(self):
        clone=copy_catalogue(self.user,'item',self.item.pk,'Кабель второй очереди')
        self.assertEqual(clone.project_work,self.work)
        self.assertIsNone(clone.parent_id)
        self.assertEqual(clone.children.get().name,'Подготовка')
        self.assertEqual(clone.planned_quantity,0)
        child_copy=copy_catalogue(self.user,'item',self.child.pk,'Подготовка новая')
        self.assertEqual(child_copy.parent,self.item)
        self.assertEqual(self.item.children.count(),2)

    def test_duplicate_cyrillic_name_is_rejected_without_partial_copy(self):
        with self.assertRaises(ValidationError):copy_catalogue(self.user,'object',self.obj_facility.pk,'уза 26')
        with self.assertRaises(ValidationError):copy_catalogue(self.user,'package',self.work.pk,self.work.name.lower())
        self.assertEqual(ComplexObject.objects.count(),1)
        self.assertEqual(ProjectWork.objects.count(),1)

    def test_cycle_rolls_back_all_new_records(self):
        ProjectWorkItem.objects.filter(pk=self.item.pk).update(parent=self.child)
        before=ProjectWorkItem.objects.count()
        with self.assertRaises(ValidationError):copy_catalogue(self.user,'package',self.work.pk,'Копия с циклом')
        self.assertEqual(ProjectWork.objects.count(),1)
        self.assertEqual(ProjectWorkItem.objects.count(),before)

    def test_get_has_no_side_effect_and_post_renames_copy(self):
        url=reverse('works:catalogue_copy',args=['package',self.work.pk])
        response=self.client.get(url)
        self.assertContains(response,'Новое название')
        self.assertEqual(ProjectWork.objects.count(),1)
        response=self.client.post(url,{'name':'Система следующего участка'})
        self.assertEqual(response.status_code,302)
        self.assertTrue(ProjectWork.objects.filter(name='Система следующего участка').exists())
        self.assertContains(self.client.get(reverse('works:work_detail',args=[self.work.pk])),'Копировать')

    def test_standalone_work_can_be_copied(self):
        source=ProjectWork.objects.create(company=self.company,section=self.section,name='Самостоятельная',unit='шт',kind='SIMPLE',load_profile=self.profile,unit_price=50)
        clone=copy_catalogue(self.user,'package',source.pk,'Самостоятельная новая')
        self.assertEqual(clone.kind,'SIMPLE')
        self.assertEqual(clone.load_profile,self.profile)
        self.assertFalse(clone.items.exists())
        self.assertEqual(clone.unit_price,50)

    def test_scope_and_role_are_checked(self):
        self.user.role=Role.objects.get(code='PLANNER');self.user.save()
        url=reverse('works:catalogue_copy',args=['object',self.obj_facility.pk])
        self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.post(url,{'name':'Новый'}).status_code,404)
        self.user.assigned_objects.add(self.obj)
        self.assertEqual(self.client.get(url).status_code,200)
        self.user.role=Role.objects.get(code='OBSERVER');self.user.save()
        self.assertEqual(self.client.get(url).status_code,403)

    def test_locked_package_blocks_item_copy_but_can_itself_be_copied(self):
        from apps.production.models import DailyFact
        from datetime import date
        DailyFact.objects.create(company=self.company,project_work=self.work,work_item=self.item,date=date(2026,11,1),actual_quantity=1,reported_by=self.user)
        before=ProjectWorkItem.objects.count()
        with self.assertRaises(ValidationError):copy_catalogue(self.user,'item',self.item.pk,'Новая работа')
        self.assertEqual(ProjectWorkItem.objects.count(),before)
        clone=copy_catalogue(self.user,'package',self.work.pk,'Новый комплекс')
        self.assertFalse(clone.daily_facts.exists())
        self.assertEqual(clone.items.count(),3)
