import json
import os
from pathlib import Path
import shutil
import subprocess
from datetime import timedelta
from uuid import uuid4
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.utils import timezone
from . import test_production_entry as fixtures


@skipUnless(os.environ.get('PRODUCTION_ENTRY_BROWSER_TESTS')=='1','Optional production people Chromium scenario')
class ProductionPeopleBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_select_multiple_people_with_shift_position_and_existing_reservations(self):
        fixtures.ProductionEntryTests.setUpTestData.__func__(type(self))
        from apps.accounts.models import Company
        from apps.resources.models import Employee,Brigade
        from apps.resources.position_catalog import position_for
        from apps.planning.models import DailyScheduleEntry
        from .dispatch_models import EmployeeActivity,EmployeeFactClaim
        def employee(last,first,number,**extra):
            return Employee.objects.create(company=self.company,last_name=last,first_name=first,employee_number=number,position=self.employee.position,**extra)
        free=employee('Петров','Алексей','BULK-002')
        saved=employee('Ранее','Внесён','BULK-003')
        busy=employee('Занят','Резервом','BULK-004')
        elsewhere=employee('На другом','Объекте','BULK-005')
        cancel=employee('Отмена','Выбора','BULK-006')
        inactive=employee('Неактивный','Сотрудник','BULK-007',is_active=False)
        foreign=Employee.objects.create(company=Company.objects.create(name='Foreign employee company'),last_name='Чужой',first_name='Сотрудник')
        for person,obj in [(saved,self.obj),(elsewhere,self.other)]:
            EmployeeActivity.objects.create(company=self.company,construction_object=obj,date=self.day,employee=person,position=person.position,shift=1,entry_direction='works',assignment_reviewed=False,reported_by=self.users['DISPATCHER'])
        EmployeeFactClaim.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=busy,shift=1,dispatcher=self.second,session_key=uuid4(),expires_at=timezone.now()+timedelta(minutes=5))
        target=position_for(Brigade.objects.create(company=self.company,name='Должность для массового назначения'))
        DailyScheduleEntry.objects.create(schedule=self.schedule,key='labor:bulk',kind='labor',label=target.name,rows=[{'date':str(self.day),'brigade_id':target.planning_mapping.brigade_id,'planned_count':'3'}])
        self.client.force_login(self.users['DISPATCHER'])
        config={'base':self.live_server_url,'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),
            'path':f'{self.url}?construction_object={self.obj.pk}&date={self.day}&section=works','first':self.employee.pk,'second':free.pk,'saved':saved.pk,'busy':busy.pk,'elsewhere':elsewhere.pk,'cancel':cancel.pk,'inactive':inactive.pk,'foreign':foreign.pk,'position':target.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/production_people.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,PRODUCTION_PEOPLE_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        added=EmployeeActivity.objects.filter(company=self.company,construction_object=self.obj,date=self.day,employee_id__in=[self.employee.pk,free.pk])
        self.assertEqual(added.count(),3)
        self.assertEqual(set(added.values_list('employee_id','shift')),{(self.employee.pk,1),(free.pk,1),(self.employee.pk,2)})
        self.assertEqual(set(added.values_list('planning_position_id',flat=True)),{target.pk})
        self.assertEqual(set(added.values_list('position_id',flat=True)),{self.employee.position_id})
        self.assertFalse(added.filter(assignment_reviewed=True).exists())
        self.assertFalse(EmployeeActivity.objects.filter(employee_id__in=[busy.pk,cancel.pk,inactive.pk,foreign.pk]).exists())
        self.assertEqual(EmployeeActivity.objects.filter(employee=elsewhere,construction_object=self.other).count(),1)
        from .history_models import FactRevision
        self.assertEqual(FactRevision.objects.filter(entity_type='production.employeeactivity',entity_id__in=[str(pk) for pk in added.values_list('pk',flat=True)],operation='CREATE',actor=self.users['DISPATCHER']).count(),3)
        print(result.stdout.strip())
