import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('PRODUCTION_EMPLOYEE_SEARCH_BROWSER_TESTS')=='1','Optional production employee search browser scenario')
class ProductionEmployeeSearchBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_inline_and_bulk_fields_search_the_complete_employee_directory(self):
        from .test_employee_search_pagination import ProductionEmployeeSearchPaginationTests as Source
        Source.setUpTestData.__func__(type(self))
        from apps.resources.models import Employee
        employee=Employee.objects.create(company=self.company,last_name='Иванов',first_name='Данила',middle_name='Владимирович',employee_number='АОЗП-08672',position=self.employee.position)
        self.client.force_login(self.users['DISPATCHER'])
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':self.client.cookies['sessionid'].value,
            'path':f'{self.url}?construction_object={self.obj.pk}&date={self.day}&section=works&entry_step=2','person':employee.display_name}
        script=Path(__file__).resolve().parents[2]/'tests/browser/production_employee_search.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env={**os.environ,'PRODUCTION_EMPLOYEE_SEARCH_CONFIG':json.dumps(config)},timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        print(result.stdout.strip())
