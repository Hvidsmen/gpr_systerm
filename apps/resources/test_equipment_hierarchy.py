from io import BytesIO
from openpyxl import load_workbook
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company,Role,User
from .models import EquipmentType,EquipmentCategory
from .forms import EquipmentTypeForm


class EquipmentHierarchyTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Equipment hierarchy')
        self.user=User.objects.create_user(username='equipment-hierarchy',company=self.company,role=Role.objects.get(code='PLANNER'))
        self.client.force_login(self.user)
        self.root=EquipmentType.objects.create(company=self.company,name='Экскаваторы')
        self.child=EquipmentType.objects.create(company=self.company,name='JCB 3CX',parent=self.root)
        self.other=EquipmentType.objects.create(company=self.company,name='Краны')

    def payload(self,**values):
        return {'name':'CAT 320','parent':self.root.pk,'is_active':'on','crew-TOTAL_FORMS':'0','crew-INITIAL_FORMS':'0',**values}

    def test_form_exposes_type_and_create_child_preserves_relation(self):
        url=reverse('resources:equipment_type_create')
        response=self.client.get(url,{'parent':self.root.pk})
        self.assertEqual(response.context['form']['parent'].value(),self.root.pk)
        self.assertContains(response,'Вид техники')
        self.assertEqual(self.client.post(url,self.payload()).status_code,302)
        child=EquipmentType.objects.get(name='CAT 320')
        self.assertEqual(child.parent,self.root)
        response=self.client.get(reverse('resources:equipment_type_update',args=[child.pk]))
        self.assertEqual(response.context['form']['parent'].value(),self.root.pk)

    def test_tree_contains_each_root_and_its_children_once(self):
        response=self.client.get(reverse('resources:equipment_type_list'))
        nodes={node['root'].pk:node for node in response.context['equipment_hierarchy']}
        self.assertEqual([r.pk for r in nodes[self.root.pk]['children']],[self.child.pk])
        self.assertEqual(nodes[self.other.pk]['children'],[])
        self.assertContains(response,f'data-equipment-type="{self.root.pk}"')
        self.assertContains(response,'Добавить технику')
        self.assertNotContains(response,'Модель для СМГ')

    def test_child_search_keeps_parent_context_without_selecting_unmatched_parent(self):
        response=self.client.get(reverse('resources:equipment_type_list'),{'name':'JCB'})
        nodes=response.context['equipment_hierarchy']
        self.assertEqual(len(nodes),1);self.assertEqual(nodes[0]['root'],self.root)
        self.assertFalse(nodes[0]['root_matches']);self.assertEqual(nodes[0]['children'],[self.child])
        filtered=self.client.get(reverse('resources:equipment_type_list'),{'parent':self.root.pk})
        self.assertEqual(set(filtered.context['equipment_types'].values_list('pk',flat=True)),{self.root.pk,self.child.pk})

    def test_foreign_nested_and_self_types_are_rejected_without_changes(self):
        foreign=EquipmentType.objects.create(company=Company.objects.create(name='Foreign equipment hierarchy'),name='Чужой вид')
        url=reverse('resources:equipment_type_update',args=[self.root.pk])
        for parent in [foreign.pk,self.child.pk,self.root.pk,self.other.pk]:
            response=self.client.post(url,self.payload(name=self.root.name,parent=parent))
            self.assertEqual(response.status_code,200)
            self.assertIn('parent',response.context['form'].errors)
        self.root.refresh_from_db();self.assertIsNone(self.root.parent)
        self.assertEqual(self.client.get(reverse('resources:equipment_type_list'),{'parent':foreign.pk}).context['equipment_types'].count(),0)

    def test_table_and_excel_expose_type(self):
        response=self.client.get(reverse('resources:equipment_type_list'),{'view':'table'})
        self.assertContains(response,'<th>Вид техники</th>',html=True)
        response=self.client.get(reverse('resources:equipment_type_export'),{'parent':self.root.pk})
        self.assertEqual(response.status_code,200)
        book=load_workbook(BytesIO(response.content));rows=list(book.active.values);book.close()
        index=rows[0].index('Вид техники')
        self.assertEqual(next(row[index] for row in rows[1:] if row[0]=='JCB 3CX'),'Экскаваторы')

    def test_categories_inherit_for_unclassified_models_and_status_filter_is_scoped(self):
        category=EquipmentCategory.objects.create(company=self.company,name='Землеройная техника')
        self.root.category=category;self.root.save()
        self.child.is_active=False;self.child.save()
        url=reverse('resources:equipment_type_list')
        response=self.client.get(url,{'category':category.pk})
        groups=response.context['equipment_category_groups']
        self.assertEqual([g['label'] for g in groups],[category.name])
        self.assertEqual(groups[0]['count'],2)
        self.assertEqual(groups[0]['nodes'][0]['children'],[self.child])
        response=self.client.get(url,{'is_active':'False','view':'table'})
        self.assertEqual(list(response.context['equipment_types']),[self.child])
        self.assertContains(response,'name="view" value="table"')
        self.assertNotContains(response,'onclick="return confirm(')

    def test_model_with_own_category_keeps_category_and_has_one_bulk_checkbox(self):
        parent_category=EquipmentCategory.objects.create(company=self.company,name='Первая категория')
        own=EquipmentCategory.objects.create(company=self.company,name='Вторая категория')
        self.root.category=parent_category;self.root.save()
        self.child.category=own;self.child.save()
        response=self.client.get(reverse('resources:equipment_type_list'))
        groups={g['label']:g for g in response.context['equipment_category_groups']}
        self.assertEqual(groups[own.name]['nodes'][0]['children'],[self.child])
        self.assertFalse(groups[own.name]['nodes'][0]['root_matches'])
        self.assertEqual(response.content.decode().count(f'name="selected" value="{self.root.pk}"'),1)
        self.assertEqual(response.content.decode().count(f'name="selected" value="{self.child.pk}"'),1)
