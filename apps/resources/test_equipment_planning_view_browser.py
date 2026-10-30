import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from .models import EquipmentCategory, EquipmentType


@skipUnless(os.environ.get('EQUIPMENT_PLANNING_VIEW_BROWSER_TESTS') == '1', 'Optional catalogue view Chromium scenario')
class EquipmentPlanningViewBrowserTests(StaticLiveServerTestCase):
    serialized_rollback = True

    def test_views_filter_selection_and_independent_fold_state(self):
        company = Company.objects.create(name='Planning view browser')
        user = User.objects.create_user(username='planning-view-browser', company=company,
                                        role=Role.objects.get(code='PLANNER'))
        category = EquipmentCategory.objects.create(company=company, name='Группа А')
        other = EquipmentCategory.objects.create(company=company, name='Группа Б')
        root = EquipmentType.objects.create(company=company, name='АТЗ', category=category)
        EquipmentType.objects.create(company=company, name='АТЗ / Камаз', parent=root, category=other)
        EquipmentType.objects.create(company=company, name='Экскаватор')
        self.client.force_login(user)
        import tempfile
        from openpyxl import Workbook
        temporary=tempfile.TemporaryDirectory(prefix='equipment-group-preview-');self.addCleanup(temporary.cleanup)
        workbook_path=Path(temporary.name)/'group-update.xlsx'
        book=Workbook();sheet=book.active;sheet.append(['Название','Техника для планирования','Группа техники','Единица измерения','Активна','Категории водителя','Классы водителя'])
        sheet.append([root.name,root.name,'Учебная группа из Excel','ед.','Да','',''])
        sheet.append(['АТЗ / Камаз','Экскаватор','Учебная  группа из Excel','ед.','Да','',''])
        book.save(workbook_path);book.close()
        config = {'base': self.live_server_url, 'cookie': self.client.cookies['sessionid'].value,
                  'chromium': shutil.which('chromium'), 'catalogue': reverse('resources:equipment_type_list'),
                  'root': root.pk,'workbook':str(workbook_path)}
        script = Path(__file__).resolve().parents[2] / 'tests/browser/equipment_planning_view.js'
        result = subprocess.run(['node', str(script)], capture_output=True, text=True,
                                env=dict(os.environ, EQUIPMENT_PLANNING_VIEW_CONFIG=json.dumps(config)), timeout=100)
        self.assertEqual(result.returncode, 0, result.stdout + '\n' + result.stderr)
        root.refresh_from_db();self.assertEqual(root.category.name,'Учебная группа из Excel')
        self.assertEqual(EquipmentType.objects.get(parent=root).category_id,root.category_id)
        print(result.stdout.strip())
