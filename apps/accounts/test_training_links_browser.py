import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from urllib.parse import urljoin


@skipUnless(os.environ.get('TRAINING_LINK_BROWSER_TESTS')=='1','Optional training link clipboard scenario')
class TrainingLinksBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_admin_can_copy_stable_participant_link_with_http_and_manual_fallback(self):
        from .test_training import TrainingTests
        TrainingTests.setUpTestData.__func__(type(self))
        from .models import TrainingCopy
        TrainingCopy.objects.create(company=self.other_company,name='Другая группа',port=8102)
        self.client.force_login(self.admin)
        config={'base':self.live_server_url,'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),
            'list':reverse('accounts:training_list'),'edit':reverse('accounts:training_edit',args=[self.copy.pk]),
            'url':urljoin(settings.PORTAL_PUBLIC_URL,reverse('accounts:training_enter',args=[self.copy.pk]))}
        script=Path(__file__).resolve().parents[2]/'tests/browser/training_links.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,
            env=dict(os.environ,TRAINING_LINK_CONFIG=json.dumps(config)),timeout=75)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        print(result.stdout.strip())
