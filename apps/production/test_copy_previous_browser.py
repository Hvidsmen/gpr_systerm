import json,os,shutil,subprocess
from pathlib import Path
from datetime import timedelta
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client


@skipUnless(os.environ.get('COPY_PREVIOUS_BROWSER_TESTS')=='1','Optional day-copy Chromium scenario')
class CopyPreviousBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_people_links_and_transport_copy_without_overwriting_current_day(self):
        from .test_production_entry import ProductionEntryTests
        ProductionEntryTests.setUpTestData.__func__(type(self))
        from .dispatch_models import EmployeeActivity,EmployeeWorkLink
        from .models import DailyFact
        from . import transport
        from .transport_inline import save_cell
        from .transport_details_models import EquipmentDayDetails
        from django.urls import reverse
        previous=self.day-timedelta(days=1);user=self.users['DISPATCHER'];operator=self.users['MECHANIZATION_DISPATCHER'];admin=self.users['ADMIN'];camp=self.housing_camp
        source=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=previous,employee=self.employee,position=self.employee.position,shift=1,entry_direction='works',work=self.work,assignment_reviewed=True,reported_by=user)
        EmployeeWorkLink.objects.create(company=self.company,activity=source,work=self.work)
        DailyFact.objects.create(company=self.company,date=previous,project_work=self.work,actual_quantity=99,reported_by=user)
        DailyFact.objects.create(company=self.company,date=self.day,project_work=self.work,actual_quantity=5,reported_by=user)
        transport.move(admin,camp,previous,[self.machine.pk],'ARRIVAL',condition='READY',signed=transport.snapshot(admin,camp,previous))
        transport.save_day(admin,camp,previous,[self.machine.pk],'WORKED',construction_object=self.obj,work=self.work,signed=transport.snapshot(admin,camp,previous))
        from uuid import uuid4
        save_cell(admin,camp,previous,self.machine,'shift_one_employee',self.employee.pk,transport.snapshot(admin,camp,previous),str(uuid4()))
        transport.set_condition(admin,camp,self.day,[self.machine.pk],'REPAIR',signed=transport.snapshot(admin,camp,self.day))
        self.client.force_login(user);other=Client();other.force_login(operator)
        config={'base':self.live_server_url,'production':self.url,'transport':reverse('production:transport_board'),'cookie':self.client.cookies['sessionid'].value,'operator_cookie':other.cookies['sessionid'].value,'chromium':shutil.which('chromium'),'object':self.obj.pk,'camp':camp.pk,'day':str(self.day),'employee':self.employee.pk,'work':self.work.pk,'machine':self.machine.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/copy_previous.js'
        completed=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,COPY_PREVIOUS_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(completed.returncode,0,completed.stdout+'\n'+completed.stderr)
        person=EmployeeActivity.objects.get(date=self.day,employee=self.employee);self.assertEqual(person.work_links.count(),1)
        self.assertEqual(DailyFact.objects.get(date=self.day,project_work=self.work).actual_quantity,5)
        self.assertEqual(self.machine.transport_days.get(date=self.day).status,'WORKED')
        self.assertEqual(EquipmentDayDetails.objects.get(date=self.day,equipment=self.machine).shift_one_employee_id,self.employee.pk)
        self.assertEqual(transport.condition_on(self.machine,self.day),'REPAIR')
        print(completed.stdout.strip())
