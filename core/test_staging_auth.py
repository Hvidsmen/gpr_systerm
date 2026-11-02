from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import uuid

from django.test import SimpleTestCase
from ops import staging_auth
from ops.windows_runtime import atomic_json
from apps.accounts.staging_directory import verify


class StagingDirectoryConfigTests(SimpleTestCase):
    def test_only_directory_auth_options_are_read_from_validated_source_config(self):
        with TemporaryDirectory() as temp:
            base=Path(temp);root=base/'test';root.mkdir();source=base/'production';(source/'.runtime').mkdir(parents=True)
            database=base/'separate-database.sqlite3';database.touch()
            atomic_json(source/'.runtime/installed.json',{'database':str(database)})
            (source/'.env').write_text('LDAP_ENABLED=1\nLDAP_SERVERS=dc.example.local\nLDAP_DOMAIN=example.local\nLDAP_BIND_PASSWORD=synthetic-service-secret\nEMAIL_HOST_PASSWORD=synthetic-mail-secret\n')
            state={'source_config_dir':str(source),'source':{'database':str(database),'media':str(source/'media')}}
            values=staging_auth.directory_settings(root,state)
            self.assertTrue(values['enabled']);self.assertEqual(values['domain'],'example.local')
            self.assertNotIn('synthetic-service-secret',str(values));self.assertNotIn('synthetic-mail-secret',str(values))

    def test_old_manifest_discovers_matching_source_by_media_parent(self):
        with TemporaryDirectory() as temp:
            base=Path(temp);root=base/'test';root.mkdir();source=base/'production';(source/'.runtime').mkdir(parents=True)
            database=base/'db.sqlite3';atomic_json(source/'.runtime/installed.json',{'database':str(database)})
            state={'source':{'database':str(database),'media':str(source/'media')}}
            self.assertEqual(staging_auth.production_root(root,state),source)

    def test_directory_bind_is_read_only_and_checks_guid_without_group_mapping(self):
        identity=uuid.uuid4();connection=Mock()
        connection.entries=[{'objectGUID':SimpleNamespace(raw_values=[identity.bytes_le])}]
        config=dict(enabled=True,servers=['dc.example.local'],port=389,domain='example.local',base_dn='DC=example,DC=local',timeout=2)
        person={'ldap_login':'windows.admin','ldap_guid':identity.hex}
        with patch('ldap3.Server'),patch('ldap3.Connection',return_value=connection) as factory:
            self.assertTrue(verify(person,'synthetic-password',config))
            self.assertTrue(factory.call_args.kwargs['read_only'])
            self.assertFalse(factory.call_args.kwargs['auto_referrals'])
            self.assertEqual(factory.call_args.kwargs['user'],'windows.admin@example.local')
            connection.unbind.assert_called_once()
        person['ldap_guid']=uuid.uuid4().hex
        with patch('ldap3.Server'),patch('ldap3.Connection',return_value=connection):self.assertFalse(verify(person,'synthetic-password',config))
