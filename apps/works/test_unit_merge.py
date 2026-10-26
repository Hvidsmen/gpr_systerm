from unittest.mock import patch
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User,Role
from apps.resources.models import Brigade,EquipmentType,FuelType
from . import test_catalogs as fixtures
from .models import MeasurementUnit,MeasurementUnitAlias,ProjectWork,ProjectWorkItem,WorkTemplate,WorkTemplateVersion,WorkTemplateItem


class UnitMergeTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkCatalogTests.setUpTestData.__func__)
    def setUp(self):
        self.client.force_login(self.user)
        self.source=MeasurementUnit.objects.create(company=self.company,symbol='м.п',name='Погонный метр')
        self.target=MeasurementUnit.objects.create(company=self.company,symbol='м.п.',name='Погонный метр основной')
        self.url=reverse('works:unit_merge')

    def preview(self,units=None,target=None):
        response=self.client.post(self.url,{'stage':'preview','units':units or [self.source.pk,self.target.pk],'target':target or self.target.pk,'equivalent':'on'})
        self.assertEqual(response.status_code,200,response.content)
        self.assertIsNotNone(response.context.get('selection'),response.context['form'].errors)
        return response.context['selection']

    def confirm(self,token=None):return self.client.post(self.url,{'confirm':'1','selection':token or self.preview()})

    def test_merges_all_catalogue_references_without_changing_numbers(self):
        work=ProjectWork.objects.create(company=self.company,section=self.section,name='Работа',unit=self.source.symbol,unit_price=Decimal('123.45'),kind='COMPOSITE')
        item=ProjectWorkItem.objects.create(company=self.company,project_work=work,name='Состав',unit=self.source.symbol,quantity_per_unit=12,weight=100)
        template=WorkTemplate.objects.create(company=self.company,name='Шаблон',unit=self.source.symbol)
        version=WorkTemplateVersion.objects.create(company=self.company,template=template,version_number=1)
        part=WorkTemplateItem.objects.create(company=self.company,version=version,name='Часть',unit=self.source.symbol,quantity_per_unit=12)
        brigade=Brigade.objects.create(company=self.company,name='Должность',unit=self.source.symbol)
        gear=EquipmentType.objects.create(company=self.company,name='Техника',unit=self.source.symbol)
        fuel=FuelType.objects.create(company=self.company,name='Топливо',unit=self.source.symbol)
        foreign=ProjectWork.objects.create(company=self.foreign,section=fixtures.Section.objects.create(company=self.foreign,construction_object=fixtures.ConstructionObject.objects.create(company=self.foreign,project=fixtures.Project.objects.create(company=self.foreign,name='Other'),name='Other'),name='Other'),name='Other',unit=self.source.symbol)
        response=self.confirm();self.assertRedirects(response,reverse('works:unit_list'))
        for row in [work,item,template,part,brigade,gear,fuel]:row.refresh_from_db();self.assertEqual(row.unit,self.target.symbol)
        foreign.refresh_from_db();self.assertEqual(foreign.unit,self.source.symbol)
        self.assertEqual(work.unit_price,Decimal('123.45'));self.assertEqual(item.quantity_per_unit,12)
        self.assertFalse(MeasurementUnit.objects.filter(pk=self.source.pk).exists())

    def test_aliases_survive_repeated_merge_and_prevent_recreation(self):
        self.confirm();unit,created=MeasurementUnit.objects.get_or_create(company=self.company,symbol=self.source.symbol,defaults={'name':'Старое'})
        self.assertEqual(unit,self.target);self.assertFalse(created)
        final=MeasurementUnit.objects.get(company=self.company,symbol='м')
        self.confirm(self.preview([self.target.pk,final.pk],final.pk))
        self.assertEqual(set(MeasurementUnitAlias.objects.filter(company=self.company).values_list('target_id',flat=True)),{final.pk})
        work=ProjectWork.objects.create(company=self.company,section=self.section,name='Новая',unit=self.source.symbol)
        self.assertEqual(work.unit,final.symbol)
        response=self.client.post(reverse('works:unit_create'),{'symbol':self.source.symbol,'name':'Повтор'})
        self.assertEqual(response.status_code,400)

    def test_company_and_role_scope_and_stale_preview(self):
        foreign=MeasurementUnit.objects.get(company=self.foreign,symbol='м')
        response=self.client.post(self.url,{'stage':'preview','units':[foreign.pk,self.source.pk],'target':self.target.pk,'equivalent':'on'})
        self.assertTrue(response.context['form'].errors)
        token=self.preview();ProjectWork.objects.create(company=self.company,section=self.section,name='Поздняя',unit=self.source.symbol)
        self.assertEqual(self.confirm(token).status_code,400)
        self.assertTrue(MeasurementUnit.objects.filter(pk=self.source.pk).exists())
        manager=User.objects.create_user(username='unit-merge-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        self.client.force_login(manager);self.assertEqual(self.client.post(self.url,{'confirm':'1','selection':token}).status_code,403)

    def test_requires_equivalence_and_protects_against_self_merge(self):
        for data in [{'units':[self.source.pk],'target':self.source.pk,'equivalent':'on'},{'units':[self.source.pk],'target':self.target.pk}]:
            response=self.client.post(self.url,{'stage':'preview',**data});self.assertTrue(response.context['form'].errors)
        self.assertEqual(self.confirm('tampered').status_code,400)

    def test_rollback_on_audit_failure(self):
        work=ProjectWork.objects.create(company=self.company,section=self.section,name='Работа',unit=self.source.symbol)
        token=self.preview()
        with patch('apps.audit.models.AuditLog.objects.create',side_effect=RuntimeError('Audit failed')):
            with self.assertRaises(RuntimeError):self.confirm(token)
        work.refresh_from_db();self.assertEqual(work.unit,self.source.symbol)
        self.assertFalse(MeasurementUnitAlias.objects.filter(company=self.company).exists())

    def test_import_matching_uses_canonical_unit(self):
        from apps.planning.meeting_import import resolve_sheet
        work=ProjectWork.objects.create(company=self.company,section=self.section,name='Работа',unit=self.source.symbol)
        self.confirm()
        sheet={'name':self.obj.name,'entries':[{'kind':'work','name':'Работа','unit':self.source.symbol,'section':self.section.name,'row':1,'month':'2026-01-01','quantity':'1'}]}
        _,resolved=resolve_sheet(self.company,self.obj.project,sheet,target_object=self.obj)
        self.assertEqual(resolved[0]['target_id'],work.pk)

    def test_selection_toolbar_and_get_are_non_mutating(self):
        response=self.client.get(reverse('works:unit_list'));self.assertContains(response,'Объединить выбранные единицы')
        response=self.client.post(self.url,{'stage':'select','selected':[self.source.pk,self.target.pk]})
        self.assertEqual(response.status_code,200);self.assertTrue(MeasurementUnit.objects.filter(pk=self.source.pk).exists())
