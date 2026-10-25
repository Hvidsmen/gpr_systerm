import os,json,subprocess,shutil
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from datetime import date
from apps.planning import test_workspace as fixtures
from apps.planning.workspace_services import WorkspaceService
from apps.planning.models import WorkMonthAllocation,WorkResourceAssignment
from apps.accounts.models import User,Role
from apps.resources.models import Position,EquipmentCrewPosition
from apps.planning import preparation,work_resources

@skipUnless(os.environ.get('GPR_BROWSER_TESTS')=='1','Optional browser test: requires Node Playwright and Chromium')
class InlineResourceBrowserTests(StaticLiveServerTestCase):
    def test_inline_resources(self):
        fixtures.WorkspaceTests.setUpTestData.__func__(type(self))
        from apps.works.models import ComplexObject,ProjectWork,ProjectWorkItem
        facility=ComplexObject.objects.create(company=self.company,construction_object=self.obj,name='Filter object one')
        self.composite.complex_object=facility;self.composite.save()
        second=ComplexObject.objects.create(company=self.company,construction_object=self.obj,name='Filter object two')
        other=ProjectWork.objects.create(company=self.company,section=self.composite.section,complex_object=second,name='Composite',kind='COMPOSITE',unit='шт')
        other_item=ProjectWorkItem.objects.create(company=self.company,project_work=other,name=self.a.name,unit=self.a.unit,quantity_per_unit=1,weight=100)
        admin=User.objects.create_user(username='browser-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        estimator=User.objects.create_user(username='browser-estimator',company=self.company,role=Role.objects.get(code='ESTIMATOR'))
        version=WorkspaceService.create(self.planner,self.obj,'Browser resource work',date(2026,1,1),date(2027,2,28)).baseline_version
        WorkMonthAllocation.objects.create(company=self.company,version=version,work=self.composite,quantity=1,month=date(2026,1,1))
        preparation.handoff(self.planner,version,self.manager,estimator)
        position=Position.objects.create(company=self.company,name='Машинист браузерной проверки')
        work_resources.brigade_for(position)
        EquipmentCrewPosition.objects.create(company=self.company,equipment_type=self.equipment,position=position,count=1)
        work_resources.save(self.manager,version,WorkResourceAssignment(work=self.composite,work_item=self.a,equipment_type=self.equipment,start_date=date(2026,1,1),end_date=date(2026,1,31),count=2,shifts_per_day=1,hours_per_shift=8))
        from apps.planning.models import ObjectWorkPlan,ResourceMonthAllocation,GlobalPlanVersion
        from apps.planning.object_plans import add_branch
        from apps.planning.workspace_services import build_workspace_snapshot
        from apps.planning.independent_smg import create as create_smg
        objplan=ObjectWorkPlan.objects.create(company=self.company,construction_object=self.obj,created_by=self.planner,name='Browser grouped plan')
        add_branch(self.planner,objplan,f'package:{self.composite.pk}',1)
        copy_source=WorkspaceService.create(self.planner,self.obj,'Browser approved copy',date(2026,1,1),date(2026,1,31)).baseline_version
        WorkMonthAllocation.objects.create(company=self.company,version=copy_source,work=self.composite,quantity=1,month=date(2026,1,1))
        for kind,fields in [('labor',dict(brigade=self.brigade,count=1,hours=248)),('equipment',dict(equipment_type=self.equipment,count=1,hours=248)),('fuel',dict(fuel_type='DIESEL',liters=31))]:ResourceMonthAllocation.objects.create(company=self.company,version=copy_source,month=date(2026,1,1),kind=kind,**fields)
        GlobalPlanVersion.objects.filter(pk=copy_source.pk).update(status='APPROVED',snapshot=build_workspace_snapshot(copy_source))
        copy_source.refresh_from_db()
        smg=create_smg(self.planner,self.obj,date(2026,1,1),copy_source)
        def cookie(user):
            client=Client();client.force_login(user);return client.cookies['sessionid'].value
        config=dict(base=self.live_server_url,monthly=reverse('planning:workspace_edit',args=[version.pk]),detail=reverse('planning:global_detail',args=[version.pk]),item=self.a.pk,item_unit=self.a.unit,equipment=self.equipment.name,position=position.name,position_id=position.pk,admin_cookie=cookie(admin),manager_cookie=cookie(self.manager),planner_cookie=cookie(self.planner))
        config.update(filter_object=str(facility.pk),filter_other_object=str(second.pk),filter_package=str(self.composite.pk),filter_other_package=str(other.pk),filter_other_item=str(other_item.pk),filter_item_name=self.a.name)
        config['object_plan']=reverse('planning:object_plan_detail',args=[objplan.pk])
        config['smg']=reverse('planning:daily_schedule_detail',args=[smg.pk])
        config['chromium']=os.environ.get('GPR_CHROMIUM_EXECUTABLE') or shutil.which('chromium') or shutil.which('chromium-browser')
        script=Path(__file__).resolve().parents[2]/'tests'/'browser'/'inline_resources.js'
        env=dict(os.environ,INLINE_BROWSER_CONFIG=json.dumps(config))
        result=subprocess.run(['node',str(script)],cwd=str(script.parents[2]),env=env,text=True,capture_output=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        print(result.stdout.strip())

