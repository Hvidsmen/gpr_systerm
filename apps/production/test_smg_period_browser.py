import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse


@skipUnless(os.environ.get('SMG_PERIOD_BROWSER_TESTS') == '1', 'Optional monthly SMG Chromium scenario')
class SmgPeriodBrowserTests(StaticLiveServerTestCase):
    serialized_rollback = True

    def test_navigation_autosave_refresh_excel_and_mobile(self):
        from .test_smg_period_report import SmgPeriodReportTests
        SmgPeriodReportTests.setUpTestData.__func__(type(self))
        from .models import DailyFact
        from .camp_fuel_models import CampFuelDay
        from apps.planning.models import DailyScheduleEntry
        from .models import EquipmentActivity, EmployeeActivity
        from apps.resources.models import EquipmentCategory,EquipmentType,BrigadeGroup,Brigade
        from apps.works.models import ComplexObject,ProjectWork,ProjectWorkItem
        category=EquipmentCategory.objects.create(company=self.company,name='Специализированная техника',sort_order=1)
        EquipmentType.objects.filter(pk=self.type.pk).update(category=category)
        staff=BrigadeGroup.objects.create(company=self.company,name='Водительский состав',sort_order=1)
        Brigade.objects.filter(pk=self.brigade.pk).update(group=staff)
        for n in [1,2]:
            obj=ComplexObject.objects.create(company=self.company,section=self.work.section,construction_object=self.obj,name=f'ПК-{n}')
            package=ProjectWork.objects.create(company=self.company,section=self.work.section,complex_object=obj,name=f'Комплекс {n}',kind='COMPOSITE')
            item=ProjectWorkItem.objects.create(company=self.company,project_work=package,name='Установка камеры',unit='шт')
            DailyScheduleEntry.objects.create(schedule=self.schedule,kind='work',key=f'browser:item:{n}',metadata={'id':package.pk,'kind':'COMPOSITE'},
                rows=[{'date':str(self.day),'item_id':item.pk,'quantity':str(n+1)}])
            DailyFact.objects.create(company=self.company,project_work=package,work_item=item,date=self.day,actual_quantity=n)
        DailyFact.objects.create(company=self.company, project_work=self.work, date=self.start if self.day!=self.start else self.later, actual_quantity=2)
        CampFuelDay.objects.create(company=self.company, camp=self.housing_camp, date=self.start,
            fuel_type='TOTAL', received_liters=2, actual_liters=1, actual_balance=1)
        for kind, value in [('equipment', {'equipment_type_id': self.type.pk, 'planned_count': '2'}),
                ('labor', {'brigade_id': self.brigade.pk, 'planned_count': '2'}),
                ('fuel', {'fuel_type': 'DIESEL', 'planned_liters': '1000'})]:
            DailyScheduleEntry.objects.create(schedule=self.schedule, kind=kind, key='browser:'+kind,
                label=kind, rows=[{'date': str(self.start), **value}])
        EquipmentActivity.objects.create(company=self.company, construction_object=self.obj,
            equipment=self.machine, date=self.start, state='WORKING', shift=1)
        EmployeeActivity.objects.create(company=self.company, construction_object=self.obj,
            employee=self.employee, position=self.employee.position, date=self.start, shift=1)
        if self.day!=self.start:
            EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,employee=self.employee,position=self.employee.position,date=self.day,shift=1)
        self.client.force_login(self.users['DISPATCHER'])
        config = dict(base=self.live_server_url, cookie=self.client.cookies['sessionid'].value,
            chromium=shutil.which('chromium'), work=self.work.pk,group=staff.name,category=category.name,
            path=f'{self.url}?construction_object={self.obj.pk}&date={self.day}&section=works',
            standalone_path=f'{reverse("production:equipment_report")}?construction_object={self.obj.pk}&date={self.day}&scope=period',
            readonly_path=f'{self.url}?construction_object={self.obj.pk}&date={self.start}&section=works&entry_step=5')
        script = Path(__file__).resolve().parents[2] / 'tests/browser/smg_period_report.js'
        result = subprocess.run(['node', str(script)], capture_output=True, text=True,
            env=dict(os.environ, SMG_PERIOD_CONFIG=json.dumps(config)), timeout=100)
        self.assertEqual(result.returncode, 0, result.stdout + '\n' + result.stderr)
        print(result.stdout.strip())
