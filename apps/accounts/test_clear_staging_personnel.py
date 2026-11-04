from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from ops import clear_staging_personnel as reset, staging
from ops.windows_runtime import atomic_json, singleton


class ClearStagingPersonnelTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()/'test';(self.root/'data').mkdir(parents=True);(self.root/'.runtime').mkdir()
        self.config=dict(kind=staging.KIND,instance='11111111-1111-4111-8111-111111111111',owner_id=1,company_id=1,owner_login='admin',port=8002,hosts=['127.0.0.1'],revision='a'*40,code=str(self.root),python=sys.executable,static='staticfiles',source={n:str(self.root.parent/'production'/n) for n in ('database','media','actuation')})
        atomic_json(self.root/'staging.json',self.config)
        self.database=self.root/'data/db.sqlite3'
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript('''
                CREATE TABLE personnel_shiftpattern(id INTEGER PRIMARY KEY,company_id INTEGER);
                CREATE TABLE personnel_staffingslot(id INTEGER PRIMARY KEY,company_id INTEGER,pattern_id INTEGER REFERENCES personnel_shiftpattern(id));
                CREATE TABLE personnel_person(id INTEGER PRIMARY KEY,company_id INTEGER,schedule_slot_id INTEGER REFERENCES personnel_staffingslot(id));
                CREATE TABLE personnel_campstay(id INTEGER PRIMARY KEY,company_id INTEGER);
                CREATE TABLE personnel_housingevent(id INTEGER PRIMARY KEY,company_id INTEGER,stay_id INTEGER REFERENCES personnel_campstay(id));
                CREATE TABLE resources_employee(id INTEGER PRIMARY KEY,name TEXT);
                CREATE TABLE production_laborfact(id INTEGER PRIMARY KEY,value INTEGER);
                INSERT INTO resources_employee VALUES(1,'Keep employee');
                INSERT INTO production_laborfact VALUES(1,5);
                INSERT INTO personnel_shiftpattern VALUES(1,1),(2,2);
                INSERT INTO personnel_staffingslot VALUES(1,1,1),(2,2,2);
                INSERT INTO personnel_person VALUES(1,1,1),(2,2,2);
                INSERT INTO personnel_campstay VALUES(1,1),(2,2);
                INSERT INTO personnel_housingevent VALUES(1,1,1),(2,2,2);
            ''')

    def test_preview_is_read_only_and_company_scoped(self):
        before=self.database.read_bytes();result=reset.clear(self.root)
        self.assertEqual(list(result['would_clear'].values()),[1]*4)
        self.assertEqual(self.database.read_bytes(),before);self.assertFalse((self.root/'.runtime/backups').exists())

    def test_real_clear_has_readable_full_backup_and_retains_employee_other_facts_and_company(self):
        (self.root/'.runtime/server.stop').touch();result=reset.clear(self.root,True)
        with closing(sqlite3.connect(self.database)) as db, db:
            for table in reset.TABLES:self.assertEqual(db.execute(f'SELECT company_id FROM {table}').fetchall(),[(2,)])
            self.assertEqual(db.execute('SELECT schedule_slot_id FROM personnel_person ORDER BY id').fetchall(),[(None,),(2,)])
            self.assertEqual(db.execute('SELECT name FROM resources_employee').fetchone(),('Keep employee',))
            self.assertEqual(db.execute('SELECT value FROM production_laborfact').fetchone(),(5,))
        with closing(sqlite3.connect(result['backup'])) as db, db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM personnel_campstay').fetchone(),(2,))
        self.assertEqual(json.loads((Path(result['backup']).parent/'result.json').read_text())['cleared'],result['cleared'])

    def test_live_http_and_missing_stop_marker_cannot_clear(self):
        before=self.database.read_bytes()
        with self.assertRaises(ValueError):reset.clear(self.root,True)
        (self.root/'.runtime/server.stop').touch()
        with singleton(self.root/'.runtime/server.lock'),self.assertRaises(OSError):reset.clear(self.root,True)
        self.assertEqual(self.database.read_bytes(),before)

    def test_wrong_port_production_marker_and_production_source_are_refused(self):
        for change in ({'port':8001},{'source':{**self.config['source'],'database':str(self.database)}}):
            atomic_json(self.root/'staging.json',{**self.config,**change})
            with self.assertRaises(ValueError):reset.clear(self.root,True)
        atomic_json(self.root/'staging.json',self.config)
        for name in ('.env','.runtime/installed.json'):
            p=self.root/name;p.write_text('production')
            with self.assertRaises(ValueError):reset.clear(self.root,True)
            p.unlink()

    def test_active_update_and_recovery_are_refused(self):
        atomic_json(self.root/'.runtime/staging-update.json',{'state':'applying'})
        with self.assertRaises(ValueError):reset.clear(self.root,True)
        (self.root/'.runtime/staging-update.json').unlink()
        (self.root/'.runtime/staging-recovery.json').write_text('{}')
        with self.assertRaises(ValueError):reset.clear(self.root,True)

    def test_recruitment_reference_causes_full_rollback(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript('CREATE TABLE recruitment(id INTEGER PRIMARY KEY,stay_id INTEGER REFERENCES personnel_campstay(id));INSERT INTO recruitment VALUES(1,1);')
        (self.root/'.runtime/server.stop').touch()
        with self.assertRaises(sqlite3.IntegrityError):reset.clear(self.root,True)
        with closing(sqlite3.connect(self.database)) as db, db:
            for table in reset.TABLES:self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone(),(2,))
            self.assertEqual(db.execute('SELECT schedule_slot_id FROM personnel_person WHERE id=1').fetchone(),(1,))
