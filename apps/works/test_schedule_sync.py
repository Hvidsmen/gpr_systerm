from io import BytesIO
from decimal import Decimal
from copy import deepcopy
from unittest.mock import patch
from django.test import TestCase,SimpleTestCase
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import Workbook
from apps.projects.models import ConstructionObject,Section
from apps.production.models import DailyFact
from apps.planning.models import SubobjectWorkOrigin
from .models import ProjectWork,ProjectWorkItem
from .schedule_import import HEADERS,parse_schedule
from .schedule_sync import preview_catalogue,apply_catalogue
from .test_schedule_import import ScheduleImportTests
from .test_schedule_value_import import DAY


def section_book():
    book=Workbook();s=book.active;s['L2']=DAY
    s.append(['Раздел']+HEADERS+['Факт с начала строительства','Остаток работ','План /факт']+list(range(1,31)))
    # A populated L2 moves append to row 3.
    s.append(['УЗА','УЗА 26','Видеонаблюдение','210-СВН','3.6','Кабель','м.п.',290,'=H4',0,'План'])
    s.append(['УЗА','УЗА 26','Видеонаблюдение',None,None,None,None,None,'=3+SUM(L5:AO5)',0,'Факт',2])
    for col in range(4,9):s.merge_cells(start_row=4,start_column=col,end_row=5,end_column=col)
    out=BytesIO();book.save(out);out.seek(0);return out


class SectionParserTests(SimpleTestCase):
    def test_shifted_norm_volume_and_opening_columns(self):
        for metric,field,value in [('catalogue','quantity','290.000'),('plan','volume','290.000000'),('opening','volume','3.000')]:
            rows,ignored=parse_schedule(section_book(),metric=metric)
            self.assertEqual(len(rows),1);self.assertEqual(ignored,1)
            self.assertEqual(rows[0]['section'],'УЗА');self.assertEqual(rows[0][field],value)
            if metric!='catalogue':self.assertEqual(rows[0]['file_month'],str(DAY))


class CatalogueSyncTests(TestCase):
    setUp=ScheduleImportTests.setUp

    def rows_with_sections(self):
        return [{**r,'section':'УЗА'} for r in self.rows]

    def child(self,name='Субобъект',parent=None):
        return ConstructionObject.objects.create(company=self.company,project=self.obj.project,parent=parent or self.obj,name=name)

    def test_full_structure_updates_units_norms_and_descendants_marks_absent_without_deleting(self):
        child=self.child();grandchild=self.child('Вложенный',child);other=ConstructionObject.objects.create(company=self.company,project=self.obj.project,name='Другой объект')
        othersec=Section.objects.create(company=self.company,construction_object=other,name='УЗА')
        foreign=ProjectWork.objects.create(company=self.company,section=othersec,name='Не трогать',unit='м',load_profile=self.profile)
        rows=self.rows_with_sections();apply_catalogue(self.user,self.obj,self.profile,rows)
        self.assertEqual(ProjectWork.objects.filter(complex_object__name='УЗА 26').count(),3)
        package=ProjectWork.objects.get(section__construction_object=self.obj,complex_object__name='УЗА 26')
        old_id=package.items.get(name='Кабель').pk
        old=ProjectWork.objects.create(company=self.company,section=self.section,name='Старая работа',unit='м',load_profile=self.profile)
        rows=rows[:1];rows[0]['unit']='м';rows[0]['quantity']='500.000'
        preview=preview_catalogue(self.user,self.obj,rows)
        self.assertEqual(len(preview['targets']),3);self.assertGreater(preview['archived_count'],0)
        apply_catalogue(self.user,self.obj,self.profile,rows,expected=preview['revision'])
        item=ProjectWorkItem.objects.get(pk=old_id);self.assertEqual(item.unit,'м');self.assertEqual(item.quantity_per_unit,500)
        old.refresh_from_db();foreign.refresh_from_db();self.assertTrue(old.is_legacy_structure);self.assertFalse(foreign.is_legacy_structure)
        for target in [self.obj,child,grandchild]:
            w=ProjectWork.objects.get(section__construction_object=target,complex_object__name='УЗА 26',is_legacy_structure=False)
            self.assertFalse(w.items.get(name='Кабель').is_legacy_structure);self.assertTrue(w.items.get(name='Оборудование').is_legacy_structure)
        self.assertEqual(SubobjectWorkOrigin.objects.count(),2)
        apply_catalogue(self.user,self.obj,self.profile,self.rows_with_sections())
        self.assertFalse(ProjectWorkItem.objects.get(pk=old_id).is_legacy_structure)
        self.assertFalse(package.items.get(name='Оборудование').is_legacy_structure)

    def test_norm_with_fact_creates_current_revision_and_retains_original_identity_and_fact(self):
        rows=self.rows_with_sections();apply_catalogue(self.user,self.obj,self.profile,rows)
        old=ProjectWork.objects.get(section__construction_object=self.obj)
        item=old.items.get(name='Кабель')
        fact=DailyFact.objects.create(company=self.company,project_work=old,work_item=item,date=DAY,actual_quantity=3)
        rows[0]['quantity']='600.000';rows[0]['unit']='м'
        apply_catalogue(self.user,self.obj,self.profile,rows)
        old.refresh_from_db();item.refresh_from_db();fact.refresh_from_db()
        self.assertTrue(old.is_legacy_structure);self.assertTrue(item.is_legacy_structure)
        self.assertEqual(item.quantity_per_unit,290);self.assertEqual(fact.work_item_id,item.pk);self.assertEqual(fact.actual_quantity,3)
        current=ProjectWork.objects.get(section__construction_object=self.obj,is_legacy_structure=False)
        self.assertNotEqual(current.pk,old.pk);self.assertEqual(current.items.get(name='Кабель').quantity_per_unit,600)
        apply_catalogue(self.user,self.obj,self.profile,rows)
        self.assertEqual(ProjectWork.objects.count(),2)

    def test_stale_structure_or_new_descendant_requires_new_preview(self):
        rows=self.rows_with_sections();preview=preview_catalogue(self.user,self.obj,rows)
        self.child()
        with self.assertRaisesMessage(ValidationError,'изменились'):apply_catalogue(self.user,self.obj,self.profile,rows,expected=preview['revision'])
        self.assertFalse(ProjectWork.objects.exists())

    def test_failure_on_descendant_rolls_back_parent_and_scope_is_rechecked(self):
        child=self.child();rows=self.rows_with_sections();original=ProjectWorkItem.full_clean
        def clean(item,*args,**kwargs):
            if item.project_work.section.construction_object_id==child.pk:raise ValidationError('Ошибка субобъекта')
            return original(item,*args,**kwargs)
        with patch.object(ProjectWorkItem,'full_clean',clean),self.assertRaises(ValidationError):apply_catalogue(self.user,self.obj,self.profile,rows)
        self.assertFalse(ProjectWork.objects.exists())
        from apps.accounts.models import Role
        self.user.role=Role.objects.get(code='PLANNER');self.user.save();self.user.assigned_objects.add(self.obj)
        with self.assertRaisesMessage(ValidationError,'все его субобъекты'):preview_catalogue(self.user,self.obj,rows)

    def test_preview_without_section_is_readonly_and_confirm_is_idempotent(self):
        child=self.child()
        response=self.client.post(self.url,{'construction_object':self.obj.pk,'load_profile':self.profile.pk,'file':SimpleUploadedFile('new.xlsx',section_book().read())})
        self.assertContains(response,'Предпросмотр');self.assertFalse(ProjectWork.objects.exists())
        token=response.context['token']
        self.assertEqual(self.client.post(self.url,{'action':'confirm','token':token}).status_code,302)
        self.assertEqual(ProjectWork.objects.count(),2)
        self.assertEqual(self.client.post(self.url,{'action':'confirm','token':token}).status_code,302)
        self.assertEqual(ProjectWork.objects.count(),2)

    def test_catalogue_default_hides_old_and_checkbox_selects_only_old(self):
        rows=self.rows_with_sections();apply_catalogue(self.user,self.obj,self.profile,rows)
        package=ProjectWork.objects.get(section__construction_object=self.obj)
        olditem=package.items.get(name='Оборудование');olditem.is_legacy_structure=True;olditem._allow_legacy_structure_change=True;olditem.save()
        old=ProjectWork.objects.create(company=self.company,section=self.section,name='Полностью старый комплекс',unit='м',load_profile=self.profile,is_legacy_structure=True)
        response=self.client.get(reverse('works:work_list'))
        self.assertNotContains(response,old.name);self.assertNotContains(response,'Оборудование');self.assertContains(response,'Кабель')
        response=self.client.get(reverse('works:work_list'),{'legacy_only':'on'})
        self.assertContains(response,old.name);self.assertContains(response,'Оборудование');self.assertNotContains(response,'Кабель')
