import json, os, shutil, subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from apps.accounts.models import User, Role
from apps.resources.models import CatalogueReviewRequest
from . import test_preparation as fixtures
from .independent_smg import create


@skipUnless(os.environ.get('CATALOGUE_REVIEW_BROWSER_TESTS') == '1', 'Optional catalogue review Chromium workflow')
class CatalogueReviewBrowserTests(StaticLiveServerTestCase):
    serialized_rollback = True
    handoff = fixtures.PreparationTests.handoff
    checked = fixtures.PreparationTests.checked
    def test_plan_creation_responsible_review_replacement_rejection_and_submission(self):
        fixtures.PreparationTests.setUpTestData.__func__(type(self))
        fixtures.PreparationTests.setUp(self)
        hr=User.objects.create_user(username='review-hr-browser',company=self.company,role=Role.objects.get(code='PERSONNEL_CATALOG_MANAGER'))
        tech=User.objects.create_user(username='review-tech-browser',company=self.company,role=Role.objects.get(code='EQUIPMENT_CATALOG_MANAGER'))
        self.handoff();self.checked()
        from .models import ResourceMonthAllocation
        ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,kind='labor',brigade=self.brigade,count=2,hours=16)
        schedule=create(self.planner,self.obj,fixtures.JAN)
        def cookie(user):
            from django.test import Client
            client=Client();client.force_login(user)
            return client.cookies['sessionid'].value
        config={'base':self.live_server_url,'gpr':reverse('planning:workspace_edit',args=[self.version.pk]),
                'smg':reverse('planning:daily_schedule_detail',args=[schedule.pk]),
                'queue':reverse('resources:catalogue_review_list'),'target':self.brigade.pk,
                'cookies':{'planner':cookie(self.planner),'manager':cookie(self.manager),'hr':cookie(hr),'tech':cookie(tech)},
                'chromium':shutil.which('chromium')}
        script=Path(__file__).resolve().parents[2]/'tests/browser/catalogue_review.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,
            env=dict(os.environ,CATALOGUE_REVIEW_CONFIG=json.dumps(config)),timeout=140)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.version.refresh_from_db();self.assertEqual(self.version.status,'SUBMITTED')
        self.assertEqual(self.version.resource_allocations.get(kind='labor').count,5)
        self.assertEqual(self.version.resource_allocations.get(kind='equipment').count,2)
        self.assertEqual(set(CatalogueReviewRequest.objects.values_list('status',flat=True)),{'CONFIRMED','REPLACED','REJECTED'})
        print(result.stdout.strip())
