import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from . import test_price_review_batch as fixtures
from apps.works.models import WorkPrice


@skipUnless(os.environ.get('PRICE_REVIEW_BROWSER_TESTS')=='1','Optional batch pricing without reload browser flow')
class PriceReviewBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    create=fixtures.PriceReviewBatchTests.create
    prepare=fixtures.PriceReviewBatchTests.prepare

    def test_batch_errors_save_and_approve_without_navigation(self):
        fixtures.PriceReviewBatchTests.setUpTestData.__func__(type(self));fixtures.prepare_future(self)
        self.obj.name='КТП-1 Нефтепровод';self.obj.save()
        self.client.force_login(self.estimator)
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':self.client.cookies['sessionid'].value,
            'path':reverse('planning:estimate_review_detail',args=[self.task.pk]),'date':str(self.day),'first':self.simple.pk,'second':self.second.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/price_review_batch.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,PRICE_REVIEW_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.task.refresh_from_db();self.assertEqual(self.task.status,'APPROVED')
        self.assertEqual(WorkPrice.objects.filter(work__in=[self.simple,self.second],effective_from=self.day).count(),3)
        print(result.stdout.strip())
