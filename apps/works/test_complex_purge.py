from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from apps.accounts.models import Role, User
from apps.projects.models import ConstructionObject,Section
from .models import ComplexObject,ProjectWork
from .test_copying import CatalogueCopyTests

class ComplexPurgeTests(TestCase):
    def setUp(self):
        CatalogueCopyTests.setUp(self)
        self.url=reverse('works:complex_purge')
        self.other=ComplexObject.objects.create(company=self.company,construction_object=self.obj,name='Сохранить')

    def preview(self):
        return self.client.post(reverse('works:bulk_delete',args=['works']),{'selected_complex':[self.obj_facility.pk]})

    def confirm(self,response):
        return self.client.post(self.url,{'confirm':'1','confirm_related':'on','selection':response.context['selection']})

    def test_selecting_object_purges_entire_branch_and_keeps_neighbours(self):
        extra=ProjectWork.objects.create(company=self.company,section=self.section,complex_object=self.obj_facility,kind='COMPOSITE',name='Скрытый фильтром',unit='шт')
        response=self.preview()
        self.assertContains(response,'Скрытый фильтром')
        self.assertTrue(ComplexObject.objects.filter(pk=self.obj_facility.pk).exists())
        self.assertEqual(self.confirm(response).status_code,302)
        self.assertFalse(ComplexObject.objects.filter(pk=self.obj_facility.pk).exists())
        self.assertFalse(ProjectWork.objects.filter(pk__in=[self.work.pk,extra.pk]).exists())
        self.assertTrue(ComplexObject.objects.filter(pk=self.other.pk).exists())
        self.assertTrue(ConstructionObject.objects.filter(pk=self.obj.pk).exists())
        self.assertTrue(Section.objects.filter(pk=self.section.pk).exists())

    def test_new_work_requires_new_confirmation(self):
        response=self.preview()
        ProjectWork.objects.create(company=self.company,section=self.section,complex_object=self.obj_facility,kind='COMPOSITE',name='Новая работа',unit='шт')
        self.assertEqual(self.confirm(response).status_code,400)
        self.assertTrue(ComplexObject.objects.filter(pk=self.obj_facility.pk).exists())

    def test_empty_object_and_mixed_selection(self):
        response=self.client.post(self.url,{'selected_complex':[self.other.pk],'selected':[self.work.pk]})
        self.assertEqual(self.confirm(response).status_code,302)
        self.assertFalse(ComplexObject.objects.filter(pk=self.other.pk).exists())
        self.assertFalse(ProjectWork.objects.filter(pk=self.work.pk).exists())
        self.assertTrue(ComplexObject.objects.filter(pk=self.obj_facility.pk).exists())

    def test_rollback_and_admin_only(self):
        response=self.preview()
        with patch('apps.works.complex_purge.AuditLog.objects.create',side_effect=ValidationError('Ошибка аудита')):
            self.assertEqual(self.confirm(response).status_code,400)
        self.assertTrue(ProjectWork.objects.filter(pk=self.work.pk).exists())
        user=User.objects.create_user(username='complex-planner',company=self.company,role=Role.objects.get(code='PLANNER'))
        user.assigned_objects.add(self.obj);self.client.force_login(user)
        self.assertEqual(self.client.post(self.url,{'selected_complex':[self.obj_facility.pk]}).status_code,403)
        self.assertEqual(self.preview().status_code,403)
