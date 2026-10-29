from datetime import date
from django.test import TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from apps.resources.models import Brigade,EquipmentType
from apps.works.models import ProjectWork,ProjectWorkItem
from . import test_workspace as fixtures
from .models import WorkMonthAllocation,ResourceMonthAllocation
from .workspace_services import WorkspaceService


class GprEditorPerformanceTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)

    def test_catalogue_queries_are_bounded_for_forty_works_and_forty_five_resources(self):
        version=WorkspaceService.create(self.planner,self.obj,'Large GPR',date(2026,1,1),date(2026,12,31)).baseline_version
        for index in range(40):
            work=ProjectWork.objects.create(company=self.company,section=self.simple.section,name='Complex '+str(index),unit='шт',kind='COMPOSITE')
            for item in range(2):ProjectWorkItem.objects.create(company=self.company,project_work=work,name='Item '+str(item),unit='м',quantity_per_unit=2)
            WorkMonthAllocation.objects.create(company=self.company,version=version,work=work,month=date(2026,1,1),quantity=2)
        for kind,count,Model in [('labor',20,Brigade),('equipment',25,EquipmentType)]:
            for index in range(count):
                kwargs={'staffing_current':True} if kind=='labor' else {}
                resource=Model.objects.create(company=self.company,name=kind+str(index),**kwargs)
                ResourceMonthAllocation.objects.create(company=self.company,version=version,month=date(2026,1,1),kind=kind,count=2,**{'brigade' if kind=='labor' else 'equipment_type':resource})
        self.client.force_login(self.planner)
        for route,bound in [('planning:workspace_edit',180),('planning:workspace_months',100)]:
            with CaptureQueriesContext(connection) as queries:
                page=self.client.get(reverse(route,args=[version.pk]))
            self.assertEqual(page.status_code,200)
            self.assertLessEqual(len(queries),bound)
            self.assertLess(len(page.content),950000)
            if route=='planning:workspace_edit':
                self.assertEqual(sum(len(list(form.fields['work'].choices)) for form in page.context['work_forms']),80)
