"""Exercise the worker's occupancy selection on synthetic employment history.

SQLite executes the actual SQL fragment with only T-SQL syntax adapted (TOP,
COUNT_BIG, binary literals, date cast and correlated outer values). This does
not replace the first real SQL Server run, but checks the selection rules.
"""
import re
import sqlite3
from pathlib import Path
from django.test import SimpleTestCase


class OccupancySelectionTests(SimpleTestCase):
    def setUp(self):
        script = (Path(__file__).resolve().parents[2] / 'ops/diagnose-1c.ps1').read_text(encoding='utf-8-sig')
        fragment = script.split('-- staffing occupancy begin\n', 1)[1].split('-- staffing occupancy end', 1)[0]
        query = fragment.removeprefix('OUTER APPLY (\n').removesuffix(') occ\n')
        query = query.replace('SELECT TOP (1)', 'SELECT', 1).replace('COUNT_BIG(*)', 'COUNT(*)')
        query = query.replace('[dbo].', '').replace('CAST(@storedNow AS date)', 'date(@storedNow)')
        query = re.sub(r'0x([0-9a-fA-F]+)', r"X'\1'", query)
        for column, parameter in [('e.[_IDRRef]', '@employee'), ('e.[_Fld30280]', '@archived'), ('k.[_Fld21751]', '@dismissed')]:
            query = query.replace(column, parameter)
        self.query = query + ' LIMIT 1'
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.create_function('YEAR', 1, lambda v: int(v[:4]) if v else None)
        self.db.execute('''CREATE TABLE _InfoRg40095 (
            _Fld40096RRef BLOB, _Fld40097 TEXT, _Fld40098 TEXT,
            _Fld40101_RRRef BLOB, _Fld40105RRef BLOB, _Fld40107 NUMERIC,
            _Fld40108 TEXT, _Fld40113 BLOB)''')
        self.zero = bytes(16)
        self.employee = bytes.fromhex('11' * 16)
        self.job = bytes.fromhex('22' * 16)

    def row(self, *, employee=None, job=None, begin='2026-01-01 00:00:00',
            end='0001-01-01 00:00:00', valid_until='0001-01-01 00:00:00',
            fte=1, basis=None, empty=False):
        self.db.execute('INSERT INTO _InfoRg40095 VALUES (?,?,?,?,?,?,?,?)', (
            employee or self.employee, end, begin, basis if basis is not None else self.zero,
            job if job is not None else self.job, fte, valid_until, bytes([int(empty)])))

    def select(self, *, archived=False, dismissed=None, offset=0):
        result = self.db.execute(self.query, {'employee': self.employee,
            'archived': bytes([int(archived)]), 'dismissed': dismissed,
            'dateOffset': offset, 'storedNow': f'{2026+offset}-10-26 15:00:00'}).fetchone()
        return dict(result) if result else None

    def test_exact_employment_not_same_person_other_contract(self):
        self.row(employee=bytes.fromhex('33' * 16), job=bytes.fromhex('44' * 16))
        self.row(fte=0.5)
        result = self.select()
        self.assertEqual((result['staffing_ref'], result['fte'], result['candidates']), (self.job, 0.5, 1))

    def test_future_expired_zero_empty_and_additional_work_do_not_supply_job(self):
        for kwargs in [
            {'begin': '2026-11-01 00:00:00'}, {'end': '2026-10-25 00:00:00'},
            {'valid_until': '2026-10-25 00:00:00'}, {'fte': 0}, {'empty': True},
            {'job': self.zero}, {'basis': bytes.fromhex('55' * 16)},
        ]:
            with self.subTest(kwargs=kwargs):
                self.db.execute('DELETE FROM _InfoRg40095')
                self.row(**kwargs)
                self.assertIsNone(self.select())

    def test_end_and_valid_until_include_last_day(self):
        self.row(end='2026-10-26 00:00:00', valid_until='2026-10-26 00:00:00')
        self.assertEqual(self.select()['candidates'], 1)

    def test_equivalent_yearly_segments_are_not_conflicts(self):
        self.row(begin='2025-01-01 00:00:00')
        self.row(begin='2026-01-01 00:00:00')
        self.row(begin='2026-01-01 00:00:00')
        result = self.select()
        self.assertEqual(result['candidates'], 1)
        self.assertEqual(result['since'], '2026-01-01 00:00:00')

    def test_concurrent_positions_are_ambiguous_even_with_different_start_dates(self):
        self.row()
        self.row(job=bytes.fromhex('66' * 16), begin='2026-09-01 00:00:00')
        self.assertEqual(self.select()['candidates'], 2)

    def test_conflicting_fte_on_same_position_is_ambiguous(self):
        self.row(fte=1)
        self.row(fte=0.5)
        self.assertEqual(self.select()['candidates'], 2)

    def test_archived_keeps_last_job_before_dismissal_but_not_future(self):
        self.row(begin='2025-01-01 00:00:00', end='2025-12-31 00:00:00')
        last_job = bytes.fromhex('77' * 16)
        self.row(job=last_job, begin='2026-01-01 00:00:00', end='2026-08-31 00:00:00')
        self.row(job=bytes.fromhex('88' * 16), begin='2026-09-01 00:00:00')
        self.row(begin='2026-12-01 00:00:00')
        result = self.select(archived=True, dismissed='2026-08-31 00:00:00')
        self.assertEqual((result['staffing_ref'], result['candidates']), (last_job, 1))
        self.assertEqual(self.select(archived=True)['since'], '2026-09-01 00:00:00')

    def test_archived_conflicting_latest_jobs_are_ambiguous(self):
        self.row()
        self.row(job=bytes.fromhex('99' * 16))
        self.assertEqual(self.select(archived=True)['candidates'], 2)

    def test_year_offset_2000_does_not_drop_valid_intervals_or_accept_future(self):
        self.row(begin='4026-01-01 00:00:00', end='2001-01-01 00:00:00', valid_until='2001-01-01 00:00:00')
        self.row(begin='4026-11-01 00:00:00', job=bytes.fromhex('aa' * 16))
        self.assertEqual(self.select(offset=2000)['candidates'], 1)
        self.db.execute('UPDATE _InfoRg40095 SET _Fld40097=?', ('4026-10-25 00:00:00',))
        self.assertIsNone(self.select(offset=2000))
