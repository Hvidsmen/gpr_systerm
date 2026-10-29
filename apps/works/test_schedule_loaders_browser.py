import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from . import test_schedule_value_import as fixtures


@skipUnless(os.environ.get('SCHEDULE_LOADER_BROWSER_TESTS') == '1', 'Optional three schedule loaders Chromium scenario')
class ScheduleLoaderBrowserTests(StaticLiveServerTestCase):
    serialized_rollback = True

    def test_three_separate_uploaders_and_visible_opening_tab(self):
        fixtures.ValueImportTests.setUp(self)
        self.work.delete()
        config = {'base': self.live_server_url, 'cookie': self.client.cookies['sessionid'].value,
            'chromium': shutil.which('chromium'), 'object': self.obj.pk, 'section': self.section.pk, 'profile': self.profile.pk,
            'works': reverse('works:work_list'), 'catalogue': reverse('works:schedule_import'),
            'plans': reverse('planning:object_plan_list'), 'plan_upload': reverse('planning:object_plan_import'),
            'opening': reverse('production:work_opening_list'), 'opening_upload': reverse('production:work_opening_import'),
            'workbook': base64.b64encode(fixtures.source_book().read()).decode()}
        script = Path(__file__).resolve().parents[2] / 'tests/browser/schedule_loaders.js'
        result = subprocess.run(['node', str(script)], capture_output=True, text=True,
            env={**os.environ, 'SCHEDULE_LOADER_CONFIG': json.dumps(config)}, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + '\n' + result.stderr)
        from apps.planning.models import ObjectWorkPlan, ObjectWorkPlanRow
        from apps.production.models import WorkOpeningBalance, DailyFact
        self.assertEqual(ObjectWorkPlan.objects.count(), 1)
        self.assertEqual(ObjectWorkPlanRow.objects.get(work_item__name='Кабель').quantity, 145)
        self.assertEqual(WorkOpeningBalance.objects.get().quantity, 3)
        self.assertFalse(DailyFact.objects.exists())
        print(result.stdout.strip())
