import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from . import test_table_crew_shifts as fixtures


@skipUnless(os.environ.get('TABLE_SHIFTS_BROWSER_TESTS')=='1','Optional two-shift GPR/SMG Chromium scenario')
class TableShiftsBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    prepare_owner=fixtures.TableCrewShiftTests.prepare_owner
    def test_add_two_shift_equipment_and_live_crews(self):
        fixtures.TableCrewShiftTests.setUpTestData.__func__(type(self));fixtures.TableCrewShiftTests.setUp(self)
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':self.client.cookies['sessionid'].value,
            'gear':self.equipment.pk,'gpr':reverse('planning:workspace_edit',args=[self.version.pk]),'smg':reverse('planning:daily_schedule_detail',args=[self.schedule.pk])}
        script=Path(__file__).resolve().parents[2]/'tests/browser/table_shifts.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,TABLE_SHIFTS_CONFIG=json.dumps(config)),timeout=100)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertEqual(set(self.version.resource_allocations.filter(kind='labor').values_list('count',flat=True)),{6})
        for entry in self.schedule.entries.filter(kind='labor'):self.assertEqual(int(entry.rows[0]['planned_count']),6)
        print(result.stdout.strip())
