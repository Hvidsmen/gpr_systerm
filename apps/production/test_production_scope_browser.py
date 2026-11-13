import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('PRODUCTION_SCOPE_BROWSER_TESTS')=='1','Optional full-scope production UX scenario')
class ProductionScopeBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_user_actions_cover_rows_beyond_first_page(self):
        from .test_employee_search_pagination import ProductionEmployeeSearchPaginationTests as Source
        Source.setUpTestData.__func__(type(self))
        from apps.works.models import ProjectWork
        from .models import DailyFact
        far_work=ProjectWork.objects.get(code='MASS-203')
        DailyFact.objects.create(company=self.company,date=self.day,project_work=far_work,actual_quantity=3,reported_by=self.users['DISPATCHER'])
        from apps.resources.models import Employee
        far_person=Employee.objects.get(company=self.company,employee_number='T-204')
        self.client.force_login(self.users['DISPATCHER'])
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':self.client.cookies['sessionid'].value,
            'path':f'{self.url}?construction_object={self.obj.pk}&date={self.day}&section=works','work':'Массовая работа 203','person':far_person.display_name,
            'people_total':205}
        script=Path(__file__).resolve().parents[2]/'tests/browser/production_scope.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env={**os.environ,'PRODUCTION_SCOPE_CONFIG':json.dumps(config)},timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        print(result.stdout.strip())
