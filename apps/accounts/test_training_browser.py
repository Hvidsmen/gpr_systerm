import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from django.conf import settings


@skipUnless(os.environ.get('TRAINING_BROWSER_TESTS')=='1','Optional isolated multi-process training scenario')
class TrainingBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_real_copy_login_shared_database_isolation_reset_and_revocation(self):
        from .test_training import TrainingTests
        TrainingTests.setUpTestData.__func__(type(self))
        from . import training
        from .training_runtime import Manager
        from .models import TrainingCopy, OneCConnection, CompanyMailSettings
        from .models import User, Role
        from ops.windows_runtime import read_json
        from apps.projects.models import Project
        source=Project.objects.create(company=self.company,name='Основной проект')
        second=User.objects.create_user(username='training-peer',company=self.company,role=Role.objects.get(code='PERSONNEL_DISPATCHER'))
        self.copy.members.add(second)
        free_ports=[]
        for port in range(8101,8200):
            with socket.socket() as candidate:
                try:candidate.bind(('127.0.0.1',port))
                except OSError:continue
                free_ports.append(port)
                if len(free_ports)==2:break
        self.assertEqual(len(free_ports),2)
        self.copy.port=free_ports[0];self.copy.save(update_fields=['port'])
        other_copy=TrainingCopy.objects.create(company=self.company,name='Группа 2',port=free_ports[1],created_by=self.admin)
        other_copy.members.add(self.member)
        temporary=TemporaryDirectory();self.addCleanup(temporary.cleanup)
        base=Path(temporary.name);media=base/'live-media';media.mkdir();(media/'example.txt').write_text('original')
        private=base/'live-private';private.mkdir()
        configured=override_settings(TRAINING_ROOT=base/'copies',MEDIA_ROOT=media,ACTUATION_FILES_ROOT=private,
            ALLOWED_HOSTS=['testserver','localhost','127.0.0.1'],SESSION_COOKIE_SECURE=False,STATIC_ROOT=base/'static-collected')
        configured.enable();self.addCleanup(configured.disable)
        from django.core.management import call_command
        call_command('collectstatic',interactive=False,verbosity=0)
        OneCConnection.objects.create(company=self.company)
        CompanyMailSettings.objects.create(company=self.company,host='test-smtp.invalid',password_encrypted='fake-test-only')
        training.queue(self.copy)
        manager=Manager();self.addCleanup(manager.close)
        manager.reset(self.copy,timezone.now());self.copy.refresh_from_db()
        preparation=training.folder(self.copy)/'prepare.log'
        self.assertEqual(self.copy.state,'READY',self.copy.error+'\n'+(preparation.read_text() if preparation.exists() else ''))
        self.assertEqual(self.copy.runs.get().status,'DONE')
        training.queue(other_copy);manager.reset(other_copy,timezone.now())
        other_copy.refresh_from_db();self.assertEqual(other_copy.state,'READY',other_copy.error)
        current=read_json(training.folder(self.copy)/'current.json')
        generation=training.folder(self.copy)/current['generation']
        with sqlite3.connect(generation/'db.sqlite3') as copy:
            self.assertEqual(copy.execute('select count(*) from accounts_onecconnection').fetchone()[0],0)
            self.assertEqual(copy.execute('select password_encrypted from accounts_companymailsettings').fetchone()[0],'')
        self.assertNotIn(b'fake-test-only',(generation/'db.sqlite3').read_bytes())
        self.client.force_login(self.member);cookie=self.client.cookies['sessionid'].value
        from django.test import Client
        peer=Client();peer.force_login(second)
        admin_client=Client();admin_client.force_login(self.admin)
        config={'base':self.live_server_url,'cookie':cookie,'port':self.copy.port,'chromium':shutil.which('chromium'),
            'peer_cookie':peer.cookies['sessionid'].value,
            'admin_cookie':admin_client.cookies['sessionid'].value,'admin_path':reverse('accounts:training_edit',args=[self.copy.pk]),
            'tasks':reverse('planning:task_list'),'other_entry':reverse('accounts:training_enter',args=[other_copy.pk]),'other_port':other_copy.port,
            'entry':reverse('accounts:training_enter',args=[self.copy.pk]),'create':reverse('projects:project_create'),
            'projects':reverse('projects:project_list')}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/training_portals.js')],
            capture_output=True,text=True,env=dict(os.environ,TRAINING_CONFIG=json.dumps(config)),timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertFalse(Project.objects.filter(name='Учебный проект').exists())
        self.member.refresh_from_db();self.assertEqual(self.member.role.code,'DISPATCHER')
        self.assertTrue(OneCConnection.objects.filter(company=self.company).exists())
        (generation/'media'/'example.txt').write_text('exercise')
        self.assertEqual((media/'example.txt').read_text(),'original')
        with sqlite3.connect(generation/'db.sqlite3') as db:
            self.assertEqual(db.execute("select count(*) from projects_project where name='Учебный проект'").fetchone()[0],1)
        # A lost parent pipe terminates the child; the manager restarts the same DB.
        process=manager.workers[self.copy.pk][0]
        process.stdin.close();process.wait(timeout=10)
        manager.tick()
        self.assertNotEqual(manager.workers[self.copy.pk][0].pid,process.pid)
        self.assertEqual(self.copy.runs.count(),1)
        # A fresh snapshot removes exercises and restores files without touching live data.
        manager.reset(self.copy,timezone.now());self.copy.refresh_from_db()
        current2=read_json(training.folder(self.copy)/'current.json')
        self.assertNotEqual(current2['generation'],current['generation'])
        generation2=training.folder(self.copy)/current2['generation']
        with sqlite3.connect(generation2/'db.sqlite3') as db:
            self.assertEqual(db.execute("select count(*) from projects_project where name='Учебный проект'").fetchone()[0],0)
        self.assertEqual((generation2/'media'/'example.txt').read_text(),'original')
        self.assertEqual(Project.objects.get(pk=source.pk).name,'Основной проект')
        self.assertEqual(self.copy.runs.filter(status='DONE').count(),2)
        print(result.stdout.strip())
