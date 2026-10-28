import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from . import test_estimate_review as fixtures


@skipUnless(os.environ.get('ESTIMATE_REVIEW_BROWSER_TESTS')=='1','Optional estimator review browser scenario')
class EstimateReviewBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    create=fixtures.EstimateReviewTests.create
    prepare=fixtures.EstimateReviewTests.prepare
    def test_review_selection_filtering_and_completion(self):
        fixtures.EstimateReviewTests.setUpTestData.__func__(type(self));self.prepare();self.client.force_login(self.estimator)
        config={'base':self.live_server_url,'path':reverse('planning:estimate_review_detail',args=[self.task.pk]),'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium')}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/estimate_review.js')],capture_output=True,text=True,env={**os.environ,'ESTIMATE_REVIEW_CONFIG':json.dumps(config)},timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);print(result.stdout.strip())
        self.task.refresh_from_db();self.assertEqual(self.task.status,'APPROVED')
