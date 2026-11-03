import json, os, shutil, subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from django.test import Client


@skipUnless(os.environ.get('RECRUITMENT_BROWSER_TESTS')=='1','Optional recruitment browser journey')
class RecruitmentBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_dispatcher_recruiter_handoff_and_actual_arrival(self):
        from .test_recruitment import RecruitmentTests
        from .models import ArrivalPreparation, CampStay, RecruitmentRequest, RecruitmentCandidate
        from apps.resources.models import Employee
        from .recruitment import progress
        RecruitmentTests.setUpTestData.__func__(type(self))
        before=Employee.objects.count()
        self.client.force_login(self.dispatch);dispatcher_cookie=self.client.cookies['sessionid'].value
        recruit_client=Client();recruit_client.force_login(self.recruiter);recruiter_cookie=recruit_client.cookies['sessionid'].value
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'dispatch_cookie':dispatcher_cookie,'recruit_cookie':recruiter_cookie,
            'object':self.obj.pk,'camp':self.camp.pk,'employee':self.employee.pk,'day':str(self.day)}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/recruitment.js')],capture_output=True,text=True,env=dict(os.environ,RECRUITMENT_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertEqual(Employee.objects.count(),before)
        self.assertEqual(RecruitmentCandidate.objects.count(),1);self.assertEqual(CampStay.objects.filter(cancelled_at__isnull=True).count(),1)
        self.assertTrue(ArrivalPreparation.objects.get().actually_arrived);self.assertEqual(ArrivalPreparation.objects.get().actual_stay.arrived_on,self.day);self.assertEqual(progress(RecruitmentRequest.objects.get())['arrived'],1)
        print(result.stdout.strip())
