from pathlib import Path
import sqlite3
import tempfile
from django.test import TransactionTestCase
from django.db import connection
from django.contrib.auth.models import Group,Permission
from apps.accounts.models import Company,Role,User,AccessGroup
from apps.projects.models import Project,ConstructionObject
from apps.resources.models import EquipmentType
from .database_reset import create_users_database


class NewUsersDatabaseTests(TransactionTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        company=Company.objects.create(name='Тестовая компания')
        role,_=Role.objects.get_or_create(code='ADMIN',defaults={'name':'Администратор'})
        project=Project.objects.create(company=company,name='Проект')
        self.obj=ConstructionObject.objects.create(company=company,project=project,name='Объект')
        planner,_=Role.objects.get_or_create(code='PLANNER',defaults={'name':'Планировщик'})
        self.access=AccessGroup.objects.create(company=company,role=planner,name='Группа AD',ad_group_dn='CN=test,DC=example,DC=local')
        self.access.assigned_objects.add(self.obj)
        self.user=User.objects.create_user(username='retained-admin',password='test-only-password',company=company,role=role,is_superuser=True,is_staff=True)
        self.user.assigned_objects.add(self.obj)
        import uuid
        self.ldap=User.objects.create_user(username='retained-ad',password=None,company=company,role=planner,access_group=self.access,access_group_from_ad=True,auth_source='LDAP',ldap_guid=uuid.uuid4())
        permission=Permission.objects.get(codename='view_user',content_type__app_label='accounts')
        group=Group.objects.create(name='Django group');group.permissions.add(permission)
        self.user.groups.add(group);self.user.user_permissions.add(permission)
        EquipmentType.objects.create(company=company,name='Не переносить')
        self.source=self.root/'source.sqlite3'
        with sqlite3.connect(self.source) as db:connection.connection.backup(db)

    def test_accounts_permissions_passwords_kept_and_business_data_empty(self):
        destination=self.root/'new.sqlite3'
        before=self.source.read_bytes()
        counts=create_users_database(self.source,destination,False)
        self.assertEqual(self.source.read_bytes(),before)
        with sqlite3.connect(destination) as db:
            row=db.execute("SELECT username,password,is_staff,is_superuser,access_group_id FROM accounts_user WHERE username='retained-admin'").fetchone()
            self.assertEqual(row,('retained-admin',self.user.password,1,1,None))
            self.assertEqual(db.execute('SELECT ad_group_dn FROM accounts_accessgroup').fetchone()[0],self.access.ad_group_dn)
            for table in ['projects_project','projects_constructionobject','accounts_user_assigned_objects','accounts_accessgroup_assigned_objects','resources_equipmenttype','planning_planningworkspace','django_session']:
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM accounts_user_user_permissions').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM auth_group_permissions').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM accounts_user_groups').fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            ldap=db.execute("SELECT password,auth_source,ldap_guid,access_group_from_ad,access_group_id FROM accounts_user WHERE username='retained-ad'").fetchone()
            self.assertEqual(ldap,(self.ldap.password,'LDAP',self.ldap.ldap_guid.hex,1,self.access.pk))
        self.assertEqual(counts['accounts_user'],2)

    def test_keep_objects_and_assignments_without_equipment(self):
        destination=self.root/'objects.sqlite3'
        create_users_database(self.source,destination,True)
        with sqlite3.connect(destination) as db:
            self.assertEqual(db.execute('SELECT id FROM projects_constructionobject').fetchone()[0],self.obj.pk)
            self.assertEqual(db.execute('SELECT constructionobject_id FROM accounts_user_assigned_objects').fetchone()[0],self.obj.pk)
            self.assertEqual(db.execute('SELECT constructionobject_id FROM accounts_accessgroup_assigned_objects').fetchone()[0],self.obj.pk)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM resources_equipmenttype').fetchone()[0],0)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_existing_destination_and_source_are_never_overwritten(self):
        destination=self.root/'exists.sqlite3';destination.write_bytes(b'keep')
        with self.assertRaises(ValueError):create_users_database(self.source,destination,False)
        with self.assertRaises(ValueError):create_users_database(self.source,self.source,False)
        self.assertEqual(destination.read_bytes(),b'keep')

    def test_schema_mismatch_does_not_publish_partial_database(self):
        with sqlite3.connect(self.source) as db:
            db.execute("DELETE FROM django_migrations WHERE app='accounts' AND name=(SELECT name FROM django_migrations WHERE app='accounts' ORDER BY name DESC LIMIT 1)")
        destination=self.root/'rejected.sqlite3'
        with self.assertRaisesMessage(ValueError,'Сначала обновите приложение'):
            create_users_database(self.source,destination,False)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob('*.building-*')),[])
