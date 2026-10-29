from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company,Role,User
from .models import BrigadeGroup,EquipmentCategory,Brigade,EquipmentType
from .group_order import order_key,ordered,labor_group_order


class ConfigurableGroupOrderTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Configured group order')
        self.user=User.objects.create_user(username='configured-order',company=self.company,role=Role.objects.get(code='PLANNER'))
        self.client.force_login(self.user)
        self.a=BrigadeGroup.objects.create(company=self.company,name='А группа',sort_order=50)
        self.z=BrigadeGroup.objects.create(company=self.company,name='Я группа',sort_order=10)
        for group in [self.a,self.z,None]:Brigade.objects.create(company=self.company,name='Должность '+str(group),group=group,staffing_current=True)

    def post_data(self,rows,priorities):
        data={'order-TOTAL_FORMS':len(rows),'order-INITIAL_FORMS':len(rows)}
        for index,row in enumerate(rows):data.update({f'order-{index}-id':row.pk,f'order-{index}-sort_order':priorities[row.pk]})
        return data

    def test_numbered_screenshot_defaults_and_company_order(self):
        names=['ИТР Состав','Основной ИТР','Основной персонал','Водительский состав','Основной рабочий состав','Вспомогательный ИТР']
        groups=[BrigadeGroup.objects.create(company=self.company,name=name) for name in reversed(names)]
        self.assertEqual([g.name for g in ordered(BrigadeGroup.objects.filter(pk__in=[g.pk for g in groups]))],names)
        self.assertEqual(sorted(reversed(names+['Без группы']),key=labor_group_order),names+['Без группы'])
        self.assertEqual([row.name for row in ordered(BrigadeGroup.objects.filter(pk__in=[self.a.pk,self.z.pk]))],[self.z.name,self.a.name])
        foreign=Company.objects.create(name='Other priorities')
        BrigadeGroup.objects.create(company=foreign,name=self.a.name,sort_order=0)
        BrigadeGroup.objects.create(company=foreign,name=self.z.name,sort_order=90)
        self.assertEqual(sorted([self.a.name,self.z.name,'Без группы'],key=order_key(self.company)),[self.z.name,self.a.name,'Без группы'])
        self.assertEqual(sorted([self.a.name,self.z.name],key=order_key(foreign)),[self.a.name,self.z.name])

    def test_bulk_save_changes_catalogue_and_filter_choice_order(self):
        url=reverse('resources:brigade_group_list')
        self.assertContains(self.client.get(url),'Порядок групп во всех разделах')
        self.assertEqual(self.client.post(url,self.post_data([self.a,self.z],{self.a.pk:0,self.z.pk:90})).status_code,302)
        page=self.client.get(reverse('resources:brigade_list'))
        self.assertEqual([g['name'] for g in page.context['position_groups']],[self.a.name,self.z.name,'Без группы'])
        self.assertEqual(list(page.context['filter_form'].fields['group'].queryset),[self.a,self.z])
        self.a.refresh_from_db();self.assertEqual(self.a.sort_order,0)
        from apps.audit.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(entity_id='order',user=self.user).exists())

    def test_equipment_catalogue_same_configuration_and_role_direction(self):
        a=EquipmentCategory.objects.create(company=self.company,name='А техника',sort_order=50)
        z=EquipmentCategory.objects.create(company=self.company,name='Я техника',sort_order=10)
        for category in [a,z,None]:EquipmentType.objects.create(company=self.company,name='Техника '+str(category),category=category)
        url=reverse('resources:equipment_category_list')
        self.assertEqual(self.client.post(url,self.post_data([a,z],{a.pk:0,z.pk:90})).status_code,302)
        page=self.client.get(reverse('resources:equipment_type_list'))
        self.assertEqual([g['label'] for g in page.context['equipment_category_groups']],[a.name,z.name,'Без группы'])
        for role,allowed,denied in [('PERSONNEL_CATALOG_MANAGER','resources:brigade_group_list','resources:equipment_category_list'),('EQUIPMENT_CATALOG_MANAGER','resources:equipment_category_list','resources:brigade_group_list')]:
            self.user.role=Role.objects.get(code=role);self.user.save(update_fields=['role'])
            self.assertEqual(self.client.get(reverse(allowed)).status_code,200)
            self.assertEqual(self.client.post(reverse(denied),{}).status_code,403)

    def test_invalid_foreign_duplicate_or_stale_batch_rolls_back(self):
        foreign=BrigadeGroup.objects.create(company=Company.objects.create(name='Foreign group order'),name='Foreign')
        url=reverse('resources:brigade_group_list')
        for data in [self.post_data([self.a],{self.a.pk:1}),self.post_data([self.a,foreign],{self.a.pk:1,foreign.pk:2}),
                     self.post_data([self.a,self.a],{self.a.pk:1}),self.post_data([self.a,self.z],{self.a.pk:-1,self.z.pk:2})]:
            self.assertEqual(self.client.post(url,data).status_code,400)
            self.a.refresh_from_db();self.assertEqual(self.a.sort_order,50)
            self.z.refresh_from_db();self.assertEqual(self.z.sort_order,10)
        self.user.role=Role.objects.get(code='OBSERVER');self.user.save(update_fields=['role'])
        self.assertEqual(self.client.post(url,{}).status_code,403)

    def test_individual_form_preserves_omitted_order_and_allows_explicit_edit(self):
        url=reverse('resources:brigade_group_update',args=[self.a.pk])
        self.assertEqual(self.client.post(url,{'name':'Переименована'}).status_code,302)
        self.a.refresh_from_db();self.assertEqual(self.a.sort_order,50)
        self.assertEqual(self.client.post(url,{'name':self.a.name,'sort_order':4}).status_code,302)
        self.a.refresh_from_db();self.assertEqual(self.a.sort_order,4)

    def test_migration_initializes_existing_groups_without_renaming(self):
        row=BrigadeGroup.objects.create(company=self.company,name='Основной ИТР',sort_order=100)
        migration=import_module('apps.resources.migrations.0024_configurable_group_order')
        migration.initial_order(apps,SimpleNamespace(connection=SimpleNamespace(alias='default')))
        row.refresh_from_db();self.assertEqual((row.name,row.sort_order),('Основной ИТР',2))
