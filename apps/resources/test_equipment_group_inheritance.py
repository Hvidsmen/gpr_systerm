from io import BytesIO
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.urls import reverse
from openpyxl import load_workbook
from apps.accounts.models import Company, User, Role
from .models import EquipmentType, Equipment, EquipmentCategory
from .forms import EquipmentTypeForm
from .equipment_units import UnitForm
from .equipment_unit_catalogue import unit_queryset, UnitFilterForm, filter_units


class EquipmentGroupInheritanceTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Наследование техники')
        self.user=User.objects.create_user(username='equipment-inheritance',company=self.company,role=Role.objects.get(code='PLANNER'))
        self.a=EquipmentCategory.objects.create(company=self.company,name='Группа А')
        self.b=EquipmentCategory.objects.create(company=self.company,name='Группа Б')
        self.root=EquipmentType.objects.create(company=self.company,name='Экскаватор',category=self.a)
        self.model=EquipmentType.objects.create(company=self.company,name='CAT 320',parent=self.root,category=self.b)
        self.machine=Equipment.objects.create(company=self.company,type=self.model,plate_number='A001',catalogue_group=self.b)
        self.direct=Equipment.objects.create(company=self.company,type=self.root,plate_number='A002')
        self.other=EquipmentType.objects.create(company=self.company,name='Кран',category=self.b)
        self.other_machine=Equipment.objects.create(company=self.company,type=self.other,plate_number='B001')
        self.client.force_login(self.user)

    def test_children_cannot_override_and_root_change_updates_all_descendants(self):
        self.assertEqual(self.model.category_id,self.a.pk)
        self.assertEqual(self.machine.catalogue_group_id,self.a.pk)
        self.root.category=self.b;self.root.save(update_fields=['category','updated_at'])
        for child,field in [(self.model,'category_id'),(self.machine,'catalogue_group_id'),(self.direct,'catalogue_group_id')]:
            child.refresh_from_db();self.assertEqual(getattr(child,field),self.b.pk)
        self.assertEqual(self.model.parent_id,self.root.pk)
        self.assertEqual(self.machine.type_id,self.model.pk)
        self.root.category=None;self.root.save()
        self.model.refresh_from_db();self.machine.refresh_from_db();self.direct.refresh_from_db()
        self.assertIsNone(self.model.category_id);self.assertIsNone(self.machine.catalogue_group_id);self.assertIsNone(self.direct.catalogue_group_id)
        self.other_machine.refresh_from_db();self.assertEqual(self.other_machine.catalogue_group_id,self.b.pk)

    def test_reparenting_model_or_machine_inherits_new_root(self):
        self.model.parent=self.other;self.model.save(update_fields=['parent'])
        self.machine.refresh_from_db();self.model.refresh_from_db()
        self.assertEqual(self.model.category_id,self.b.pk);self.assertEqual(self.machine.catalogue_group_id,self.b.pk)
        self.direct.type=self.model;self.direct.save(update_fields=['type'])
        self.assertEqual(self.direct.catalogue_group_id,self.b.pk)

    def test_partial_root_save_cannot_propagate_an_unsaved_category(self):
        self.root.category=self.b;self.root.name='Новое название';self.root.save(update_fields=['name'])
        self.model.refresh_from_db();self.machine.refresh_from_db()
        self.assertEqual(self.model.category_id,self.a.pk);self.assertEqual(self.machine.catalogue_group_id,self.a.pk)

    def test_model_and_machine_forms_ignore_forged_group(self):
        form=EquipmentTypeForm({'name':self.model.name,'parent':self.root.pk,'category':self.b.pk,'unit':'ед.','is_active':'on'},instance=self.model)
        self.assertTrue(form.is_valid(),form.errors);self.assertTrue(form.fields['category'].disabled)
        saved=form.save();self.assertEqual(saved.category_id,self.a.pk)
        form=UnitForm({'type':self.root.pk,'model':self.model.pk,'catalogue_group':self.b.pk,'name':'Машина','plate_number':'A001','is_active':'on'},instance=self.machine,company=self.company)
        self.assertTrue(form.is_valid(),form.errors);self.assertTrue(form.fields['catalogue_group'].disabled)
        self.assertEqual(form.save().catalogue_group_id,self.a.pk)

    def test_legacy_overrides_are_ignored_in_filters_grouping_and_export(self):
        EquipmentType.objects.filter(pk=self.model.pk).update(category=self.b)
        Equipment.objects.filter(pk=self.machine.pk).update(catalogue_group=self.b)
        url=reverse('resources:equipment_type_list')
        response=self.client.get(url,{'category':self.a.pk})
        self.assertEqual({e.pk for e in response.context['equipment_types']},{self.root.pk,self.model.pk})
        form=UnitFilterForm({'category':self.a.pk},company=self.company);self.assertTrue(form.is_valid())
        self.assertEqual(set(filter_units(unit_queryset(self.company),form).values_list('pk',flat=True)),{self.machine.pk,self.direct.pk})
        self.assertEqual(unit_queryset(self.company).get(pk=self.machine.pk).group_name,self.a.name)
        response=self.client.get(reverse('resources:equipment_type_export'),{'parent':self.root.pk})
        rows=list(load_workbook(BytesIO(response.content)).active.values)
        self.assertTrue(all(row[2]==self.a.name for row in rows[1:]))

    def test_foreign_parent_or_nested_model_cannot_be_saved(self):
        company=Company.objects.create(name='Чужая компания')
        parent=EquipmentType.objects.create(company=company,name='Чужой тип')
        for invalid in [parent,self.model]:
            with self.assertRaises(ValidationError):
                EquipmentType.objects.create(company=self.company,name='Неверная модель',parent=invalid)
