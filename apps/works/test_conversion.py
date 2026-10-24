from datetime import date
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from apps.accounts.models import Role,User
from apps.projects.models import Section
from apps.production.models import DailyFact
from . import test_schedule_import as fixtures
from .models import ComplexObject,ProjectWork
from .schedule_import import apply_rows


class GroupConversionTests(TestCase):
    def setUp(self):
        fixtures.ScheduleImportTests.setUp(self)
        apply_rows(self.user,self.section,self.profile,self.rows)
        self.work=ProjectWork.objects.get(section=self.section)
        self.facility=self.work.complex_object
        self.facility.description='Описание сохраняется';self.facility.save()
        self.fact=DailyFact.objects.create(company=self.company,project_work=self.work,work_item=self.work.items.first(),date=date(2026,10,6),actual_quantity=2)

    def submit(self,kind,pk,**data):
        url=reverse('works:group_conversion',args=[kind,pk])
        preview=self.client.get(url)
        self.assertEqual(preview.status_code,200)
        return self.client.post(url,{'token':preview.context['token'],'name':preview.context['source'].name,'confirm':'on',**data})

    def test_roundtrip_preserves_ids_items_fact_and_description(self):
        items=list(self.work.items.values_list('pk','quantity_per_unit'))
        self.assertEqual(self.submit('object',self.facility.pk).status_code,302)
        section=Section.objects.get(name=self.facility.name)
        self.work.refresh_from_db()
        self.assertEqual(self.work.section_id,section.pk)
        self.assertIsNone(self.work.complex_object_id)
        self.assertFalse(ComplexObject.objects.filter(pk=self.facility.pk).exists())
        self.assertEqual(self.submit('section',section.pk,target_section=self.section.pk).status_code,302)
        self.work.refresh_from_db()
        self.assertEqual(self.work.section_id,self.section.pk)
        self.assertEqual(self.work.complex_object.description,'Описание сохраняется')
        self.assertEqual(list(self.work.items.values_list('pk','quantity_per_unit')),items)
        self.assertTrue(DailyFact.objects.filter(pk=self.fact.pk,project_work_id=self.work.pk).exists())
        self.assertFalse(Section.objects.filter(pk=section.pk).exists())

    def test_stale_preview_does_not_apply(self):
        url=reverse('works:group_conversion',args=['object',self.facility.pk])
        response=self.client.get(url)
        ProjectWork.objects.filter(pk=self.work.pk).update(name='Изменено')
        response=self.client.post(url,{'token':response.context['token'],'name':'Новый раздел','confirm':'on'})
        self.assertContains(response,'Состав изменился')
        self.assertTrue(ComplexObject.objects.filter(pk=self.facility.pk).exists())

    def test_conflict_and_audit_error_roll_back(self):
        with patch('apps.works.conversion.AuditLog.objects.create',side_effect=ValidationError('Ошибка аудита')):
            self.assertContains(self.submit('object',self.facility.pk),'Ошибка аудита')
        self.work.refresh_from_db()
        self.assertEqual(self.work.complex_object_id,self.facility.pk)
        self.assertFalse(Section.objects.filter(name=self.facility.name).exists())
        Section.objects.create(company=self.company,construction_object=self.obj,name=self.facility.name)
        self.assertContains(self.submit('object',self.facility.pk),'Раздел с таким названием уже есть')

    def test_section_with_simple_or_existing_facility_rejected(self):
        target=Section.objects.create(company=self.company,construction_object=self.obj,name='Целевой')
        self.assertContains(self.submit('section',self.section.pk,target_section=target.pk),'уже есть работы других комплексных объектов')
        ProjectWork.objects.filter(pk=self.work.pk).update(complex_object=None,kind='SIMPLE')
        self.assertContains(self.submit('section',self.section.pk,target_section=target.pk),'есть самостоятельные работы')

    def test_role_and_assignment_scope(self):
        user=User.objects.create_user(username='conversion-observer',company=self.company,role=Role.objects.get(code='OBSERVER'))
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('works:group_conversion',args=['object',self.facility.pk])).status_code,403)
        user.role=Role.objects.get(code='PLANNER');user.save()
        self.assertEqual(self.client.get(reverse('works:group_conversion',args=['object',self.facility.pk])).status_code,404)
