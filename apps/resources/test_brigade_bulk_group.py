from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, Role
from apps.audit.models import AuditLog
from .models import Brigade, BrigadeGroup, BrigadeMacroGroup
from .test_brigade_catalogs import BrigadeCatalogTests


class BulkPositionGroupTests(TestCase):
    def setUp(self):
        BrigadeCatalogTests.setUp(self)
        self.a = Brigade.objects.create(company=self.company, name='Электромонтажник 4 разряда', macro_group=self.macro)
        self.b = Brigade.objects.create(company=self.company, name='Электромонтажник 5 разряда')
        self.other = Brigade.objects.create(company=self.company, name='Сварщик')
        self.url = reverse('resources:brigade_bulk_group')

    def post(self, **kwargs):
        return self.client.post(self.url, {'selected': [self.a.pk, self.b.pk], **kwargs})

    def test_assigns_selected_only_and_preserves_unspecified_fields(self):
        response = self.post(**{'assignment-group': self.group.pk, 'return_query': 'name=Электромонтажник'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('name=', response.url)
        self.a.refresh_from_db(); self.b.refresh_from_db(); self.other.refresh_from_db()
        self.assertEqual(self.a.group, self.group); self.assertEqual(self.b.group, self.group)
        self.assertEqual(self.a.macro_group, self.macro); self.assertIsNone(self.b.macro_group)
        self.assertIsNone(self.other.group)
        logs=AuditLog.objects.filter(entity_type='resources.Brigade', action='UPDATE')
        self.assertEqual(logs.count(), 2)
        self.assertEqual(logs.get(entity_id=str(self.a.pk)).old_value, {'group_id': None})
        self.assertEqual(self.post(**{'assignment-group': self.group.pk}).status_code, 302)
        self.assertEqual(logs.count(), 2, 'Unchanged assignments should not create duplicate audit entries')

    def test_macro_group_is_not_editable_and_group_can_be_cleared(self):
        self.post(**{'assignment-group':self.group.pk,'assignment-macro_group':self.macro.pk})
        self.a.refresh_from_db();self.b.refresh_from_db()
        self.assertEqual(self.b.group,self.group);self.assertIsNone(self.b.macro_group)
        self.assertEqual(self.a.macro_group,self.macro)
        self.post(**{'assignment-group':'clear'});self.b.refresh_from_db();self.assertIsNone(self.b.group)

    def test_empty_or_invalid_values_keep_selection_and_do_not_change_records(self):
        for values in [{}, {'assignment-group': 'invalid'}]:
            response=self.post(**values)
            self.assertEqual(response.status_code, 400)
            self.assertEqual([row.pk for row in response.context['rows']], [self.a.pk, self.b.pk])
            self.assertContains(response, f'name="selected" value="{self.a.pk}"', status_code=400)
        self.assertEqual(self.client.post(self.url, {'assignment-group': self.group.pk}).status_code, 302)
        self.assertEqual(self.client.post(self.url, {'selected':['invalid'], 'assignment-group':self.group.pk}).status_code,404)
        self.a.refresh_from_db(); self.assertIsNone(self.a.group)
        self.assertFalse(AuditLog.objects.exists())

    def test_rejects_foreign_targets_or_selected_rows_without_partial_updates(self):
        foreign=Company.objects.create(name='Foreign assignment')
        group=BrigadeGroup.objects.create(company=foreign,name='Чужая группа')
        macro=BrigadeMacroGroup.objects.create(company=foreign,name='Чужая макрогруппа')
        row=Brigade.objects.create(company=foreign,name='Чужая должность')
        for values in [{'assignment-group':group.pk},{'assignment-macro_group':macro.pk}]:
            self.assertEqual(self.post(**values).status_code,400)
        self.assertEqual(self.post(selected=[self.a.pk,row.pk],**{'assignment-group':self.group.pk}).status_code,404)
        self.a.refresh_from_db(); self.assertIsNone(self.a.group)
        self.assertFalse(AuditLog.objects.exists())
        response=self.client.get(reverse('resources:brigade_list'))
        self.assertNotContains(response,'Чужая группа'); self.assertNotContains(response,'Чужая макрогруппа')

    def test_planner_can_select_and_assign_without_delete_permission(self):
        response=self.client.get(reverse('resources:brigade_list'))
        self.assertContains(response,'data-bulk-group-open')
        self.assertContains(response,'data-bulk-row')
        self.assertNotContains(response,'Удалить выбранные')
        self.assertEqual(self.client.get(self.url).status_code,405)
        for code in ['MANAGER','FOREMAN','OBSERVER']:
            self.user.role=Role.objects.get(code=code); self.user.save()
            self.assertEqual(self.post(**{'assignment-group':self.group.pk}).status_code,403)
            if code!='FOREMAN':
                self.assertNotContains(self.client.get(reverse('resources:brigade_list')),'data-bulk-group-open')
        self.a.refresh_from_db(); self.assertIsNone(self.a.group)

    def test_zup_refresh_keeps_assigned_groups(self):
        from .zup_positions import sync_names
        self.post(**{'assignment-group':self.group.pk,'assignment-macro_group':self.macro.pk})
        sync_names(self.company.pk,[self.a.name,self.b.name])
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertEqual((self.a.group,self.a.macro_group),(self.group,self.macro))
        self.assertEqual(self.b.group,self.group);self.assertIsNone(self.b.macro_group)


import json, os, shutil, subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('BULK_GROUP_BROWSER_TESTS') == '1', 'Optional group-assignment browser scenario')
class BulkPositionGroupBrowserTests(StaticLiveServerTestCase):
    serialized_rollback = True

    def test_selected_positions_can_be_grouped_from_catalogue(self):
        BrigadeCatalogTests.setUp(self)
        a=Brigade.objects.create(company=self.company,name='Электромонтажник 4 разряда')
        b=Brigade.objects.create(company=self.company,name='Электромонтажник 5 разряда')
        other=Brigade.objects.create(company=self.company,name='Сварщик')
        config={'base':self.live_server_url,'list':reverse('resources:brigade_list'),
                'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium')}
        script=Path(__file__).resolve().parents[2]/'tests/browser/brigade_bulk_group.js'
        result=subprocess.run(['node',str(script)],cwd=script.parents[2],text=True,capture_output=True,
                              env=dict(os.environ,BULK_GROUP_BROWSER_CONFIG=json.dumps(config)),timeout=100)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        a.refresh_from_db();b.refresh_from_db();other.refresh_from_db()
        self.assertIsNone(a.group);self.assertIsNone(a.macro_group)
        self.assertIsNone(b.group);self.assertIsNone(b.macro_group)
        self.assertIsNone(other.group);self.assertIsNone(other.macro_group)
        print(result.stdout.strip())
