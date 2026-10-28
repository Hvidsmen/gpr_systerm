from django.test import TestCase
from django.core.exceptions import ValidationError
from django.urls import reverse
from apps.resources.models import Brigade, BrigadeGroup, Employee
from apps.resources.position_catalog import planning_brigades, position_for
from . import test_one_c_positions as fixtures
from .one_c_positions import apply_names


class StaffingStatusSyncTests(TestCase):
    setUp = fixtures.PositionImportTests.setUp
    run_record = fixtures.PositionImportTests.run_record

    def report(self, specs):
        rows=[dict(ref=f'{i+1:032x}',name=name,marked='0x01' if deleted else '0x00',excluded='0x01' if excluded else '0x00') for i,(name,excluded,deleted) in enumerate(specs)]
        return {'positions_snapshot_version':1,'position_records':rows,
            'position_names':[{'name':r['name']} for r in rows if r['marked']=='0x00' and r['excluded']=='0x00'],
            'position_source':{'total':len(rows),'not_marked':sum(r['marked']=='0x00' for r in rows),
                'excluded':sum(r['marked']=='0x00' and r['excluded']=='0x01' for r in rows),
                'eligible':sum(r['marked']=='0x00' and r['excluded']=='0x00' for r in rows)}}

    def test_current_vacancy_is_plannable_without_employees(self):
        apply_names(self.run_record(),self.report([('Новая вакансия',False,False)]))
        row=Brigade.objects.get(name='Новая вакансия');self.assertTrue(row.staffing_current)
        self.assertFalse(Employee.objects.exists());self.assertIn(row,planning_brigades(self.company))
        self.assertIsNotNone(row.staffing_synced_at)

    def test_excluded_job_is_unavailable_even_with_active_employee_and_groups_survive(self):
        group=BrigadeGroup.objects.create(company=self.company,name='ИТР')
        row=Brigade.objects.create(company=self.company,name='Инженер',group=group,description='Сохранить')
        Employee.objects.create(company=self.company,last_name='Иванов',first_name='Иван',position=position_for(row))
        apply_names(self.run_record(),self.report([('Инженер',True,False)]))
        row.refresh_from_db();self.assertFalse(row.staffing_current);self.assertEqual(row.group_id,group.pk)
        self.assertEqual(row.description,'Сохранить');self.assertTrue(row.is_active)
        self.assertNotIn(row,planning_brigades(self.company))

    def test_duplicate_source_names_use_any_current_record_and_deleted_is_not_current(self):
        row=Brigade.objects.create(company=self.company,name='Водитель')
        apply_names(self.run_record(),self.report([('Водитель',True,False),('водитель',False,False)]))
        row.refresh_from_db();self.assertTrue(row.staffing_current)
        apply_names(self.run_record(),self.report([('Водитель',False,True)]))
        row.refresh_from_db();self.assertFalse(row.staffing_current)

    def test_partial_or_unknown_flags_roll_back_and_legacy_payload_does_not_retire_missing(self):
        apply_names(self.run_record(),self.report([('Повар',False,False)]))
        row=Brigade.objects.get(name='Повар')
        report=self.report([('Монтажник',False,False)]);report['position_source']['total']=2
        with self.assertRaises(ValidationError):apply_names(self.run_record(),report)
        self.assertFalse(Brigade.objects.filter(name='Монтажник').exists())
        report=self.report([('Монтажник',False,False)]);report['position_records'][0]['closed' if report.get('positions_snapshot_version')==2 else 'excluded']='unknown'
        with self.assertRaises(ValidationError):apply_names(self.run_record(),report)
        apply_names(self.run_record(),{'position_names':[{'name':'Вакансия'}]})
        row.refresh_from_db();self.assertTrue(row.staffing_current)
        apply_names(self.run_record(),self.report([('Вакансия',False,False)]))
        row.refresh_from_db();self.assertFalse(row.staffing_current)

    def test_merged_source_updates_retained_catalogue_record(self):
        from apps.resources.models import BrigadeMerge
        source=Brigade.objects.create(company=self.company,name='Импортное имя')
        target=Brigade.objects.create(company=self.company,name='Объединённая должность')
        BrigadeMerge.objects.create(company=self.company,source=source,target=target,created_by=self.admin,audit={'test':True})
        apply_names(self.run_record(),self.report([('Импортное имя',False,False)]))
        target.refresh_from_db();self.assertTrue(target.staffing_current)
        self.assertIn(target,planning_brigades(self.company))

    def test_filter_and_excel_expose_staffing_status_separately_from_occupancy(self):
        apply_names(self.run_record(),self.report([('Вакансия',False,False)]))
        response=self.client.get(reverse('resources:brigade_list'),{'staffing_current':'yes','active_employees':'no'})
        self.assertContains(response,'Вакансия');self.assertContains(response,'Актуальна')
        response=self.client.get(reverse('resources:brigade_export'),{'staffing_current':'yes'})
        from openpyxl import load_workbook
        from io import BytesIO
        sheet=load_workbook(BytesIO(response.content)).active
        self.assertIn('Штатное расписание ЗУП',list(next(sheet.values)))


class StaffingScheduleSourceTests(StaffingStatusSyncTests):
    def report(self,specs):
        rows=[dict(ref=f'{i+1:032x}',name=name,marked='0x01' if deleted else '0x00',approved='0x01',closed='0x01' if closed else '0x00') for i,(name,closed,deleted) in enumerate(specs)]
        return {'positions_snapshot_version':2,'position_records':rows,
            'position_names':[{'name':r['name']} for r in rows if r['marked']=='0x00' and r['approved']=='0x01' and r['closed']=='0x00'],
            'position_source':{'table':'dbo._Reference394','total':len(rows),'not_marked':sum(r['marked']=='0x00' for r in rows),
                'excluded':sum(r['marked']=='0x00' and r['closed']=='0x01' for r in rows),
                'eligible':sum(r['marked']=='0x00' and r['approved']=='0x01' and r['closed']=='0x00' for r in rows)}}

    def test_staffing_labels_split_and_deduplicate_departments(self):
        result=apply_names(self.run_record(),self.report([('Инженер /Участок 1/',False,False),('Инженер /Участок 2/',False,False),('Повар /Столовая/',True,False)]))
        self.assertEqual(result['created'],1);self.assertTrue(Brigade.objects.get(name='Инженер').staffing_current)
        self.assertFalse(Brigade.objects.filter(name__contains='/').exists());self.assertFalse(Brigade.objects.filter(name='Повар').exists())

    def test_source_confirmation_requires_staffing_not_empty_job_dictionary(self):
        from .one_c_positions import source_confirmed,supported
        from .test_one_c_positions import staffing_diagnostic
        run=self.run_record();run.status='DONE';run.report=staffing_diagnostic();run.save()
        self.assertTrue(source_confirmed(self.connection))
        self.assertFalse(supported({'schema':'dbo','table':'_Reference92','name':'Справочник.Должности','columns':[{'column':'_Description'},{'column':'_Marked'},{'column':'_Fld25711','name':'ИсключенаИзШтатногоРасписания'}]}))

    def test_position_sql_reads_staffing_without_job_catalogue_join(self):
        from pathlib import Path
        script=(Path(__file__).resolve().parents[2]/'ops/diagnose-1c.ps1').read_text()
        fragment=script.split('if($PositionsImport){\n            # Only staffing positions',1)[1].split('# All diagnostic statements',1)[0]
        self.assertIn('FROM [dbo].[_Reference394]',fragment);self.assertNotIn('FROM [dbo].[_Reference92]',fragment)
        self.assertIn("$_.approved -eq '0x01'",fragment);self.assertIn("$_.closed -eq '0x00'",fragment)
