import json,os,shutil,subprocess
from pathlib import Path
from uuid import uuid4
from unittest import skipUnless
from django.test import override_settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from .models import Company,Role,User
from .invitation_models import InvitationBatch

@skipUnless(os.environ.get('INVITATIONS_BROWSER_TESTS')=='1','Optional invitation browser check')
@override_settings(LDAP_ENABLED=True,INVITATION_ASYNC_ENABLED=False,EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',EMAIL_HOST='smtp.example.org',EMAIL_HOST_USER='',EMAIL_HOST_PASSWORD='',DEFAULT_FROM_EMAIL='sender@example.org')
class InvitationsBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_single_bulk_preview_queue_and_mobile(self):
        company=Company.objects.create(name='Invitation browser')
        admin=User.objects.create_user(username='invite-browser-admin',company=company,role=Role.objects.get(code='ADMIN'))
        ids=[]
        for login,code in [('ivanov.browser','DISPATCHER'),('petrov.browser','MECHANIZATION_DISPATCHER')]:
            user=User.objects.create_user(username='ad:'+uuid4().hex,company=company,auth_source='LDAP',ldap_guid=uuid4(),ldap_login=login,email=login+'@example.org',first_name='Иван' if code=='DISPATCHER' else 'Пётр',last_name='Браузеров',role=Role.objects.get(code=code));ids.append(user.pk)
        self.client.force_login(admin)
        config=dict(base=self.live_server_url,cookie=self.client.cookies['sessionid'].value,chromium=shutil.which('chromium'),users=reverse('accounts:user_list'),settings=reverse('accounts:invitation_settings'),ids=ids)
        script=Path(__file__).resolve().parents[2]/'tests/browser/invitations.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,INVITATIONS_BROWSER_CONFIG=json.dumps(config)),timeout=110)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertEqual(InvitationBatch.objects.filter(confirmed_at__isnull=False).count(),1)
        self.assertEqual(InvitationBatch.objects.get(confirmed_at__isnull=False).items.filter(status='QUEUED').count(),2)
        print(result.stdout.strip())
