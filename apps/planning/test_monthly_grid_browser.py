import json,os,shutil,subprocess
from pathlib import Path
from datetime import date
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse


@skipUnless(os.environ.get('MONTHLY_GRID_BROWSER_TESTS')=='1','Optional GPR month matrix Chromium scenario')
class MonthlyGridBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_month_columns_excel_paste_composites_crews_and_sticky_header(self):
        from . import test_workspace as fixtures
        from .workspace_services import WorkspaceService
        from .models import WorkMonthAllocation,ResourceMonthAllocation
        from apps.resources.models import Employee,EquipmentCrewPosition
        from apps.resources.position_catalog import position_for
        fixtures.WorkspaceTests.setUpTestData.__func__(type(self))
        workspace=WorkspaceService.create(self.planner,self.obj,'ГПР · по месяцам',date(2026,11,1),date(2027,12,31));version=workspace.baseline_version
        row=WorkMonthAllocation.objects.create(company=self.company,version=version,month=date(2026,11,1),work=self.simple,quantity=5)
        position=position_for(self.brigade);Employee.objects.create(company=self.company,first_name='Тест',last_name='Сотрудник',position=position)
        EquipmentCrewPosition.objects.create(company=self.company,equipment_type=self.equipment,position=position,count=2)
        equipment=ResourceMonthAllocation.objects.create(company=self.company,version=version,month=date(2026,11,1),kind='equipment',equipment_type=self.equipment,count=0)
        empty=WorkspaceService.create(self.planner,self.obj,'ГПР · пустые разделы',date(2026,11,1),date(2027,12,31)).baseline_version
        self.client.force_login(self.planner)
        config={'base':self.live_server_url,'url':reverse('planning:workspace_edit',args=[version.pk]),'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),'empty_single':reverse('planning:workspace_edit',args=[empty.pk]),'empty_months':reverse('planning:workspace_months',args=[empty.pk]),'source':row.pk,'equipment':equipment.pk,'composite':self.composite.pk,'item_a':self.a.pk,'item_b':self.b.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/monthly_grid.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,MONTHLY_GRID_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertEqual(version.work_allocations.get(work=self.simple,month=date(2026,12,1)).quantity,12.5)
        self.assertEqual(version.work_allocations.get(work=self.composite,month=date(2027,2,1)).quantity,15)
        self.assertEqual(version.resource_allocations.get(kind='labor',month=date(2026,12,1)).count,6)
        print(result.stdout.strip())
