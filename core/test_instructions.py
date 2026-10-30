from pathlib import Path
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company,Role,User
from apps.projects.models import Project,ConstructionObject
from apps.personnel.models import Camp,CampObjectLink
from .instructions_content import GUIDES

class InstructionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Учебная компания');project=Project.objects.create(company=cls.company,name='Учебный проект');cls.obj=ConstructionObject.objects.create(company=cls.company,project=project,name='Учебный объект');cls.camp=Camp.objects.create(company=cls.company,name='Учебный ВЖГ');CampObjectLink.objects.create(company=cls.company,camp=cls.camp,construction_object=cls.obj)
        cls.users={}
        for code in ['ADMIN','DISPATCHER','PERSONNEL_DISPATCHER','MECHANIZATION_DISPATCHER','COMMANDANT','FUEL_WAREHOUSE','OBSERVER']:
            user=User.objects.create_user(username='help-'+code,company=cls.company,role=Role.objects.get(code=code));user.assigned_objects.add(cls.obj);cls.users[code]=user
    def test_all_requested_roles_can_read_all_ten_guides_and_search_in_russian(self):
        for user in self.users.values():
            self.client.force_login(user);self.assertContains(self.client.get(reverse('instructions:index')),'По ролям')
            for guide in GUIDES:
                page=self.client.get(reverse('instructions:article',args=[guide['slug']]));self.assertContains(page,guide['title']);self.assertContains(page,'Учебный экран') if any(c['image'] for c in guide['chapters']) else None
        page=self.client.get(reverse('instructions:index'),{'q':'заселить'});self.assertGreater(page.context['result_count'],0)
    def test_five_entry_points_and_admin_only_vno_menu_placement(self):
        for code,route,slug in [('DISPATCHER','production:fact_day_workspace','production'),('PERSONNEL_DISPATCHER','personnel:housing','rotation'),('COMMANDANT','personnel:lodging','lodging'),('FUEL_WAREHOUSE','production:camp_fuel','fuel'),('MECHANIZATION_DISPATCHER','production:transport_board','transport')]:
            self.client.force_login(self.users[code]);page=self.client.get(reverse(route));self.assertContains(page,'data-instructions-link');self.assertContains(page,'/instructions/'+slug+'/');self.assertContains(page,'target="_blank"')
        self.client.force_login(self.users['ADMIN']);page=self.client.get(reverse('instructions:index'));html=page.content.decode();self.assertGreater(html.index('Входящие остатки (ВНО)'),html.index('Администрирование'))
    def test_login_read_only_safe_return_and_assets(self):
        self.assertEqual(self.client.get(reverse('instructions:index')).status_code,302)
        self.client.force_login(self.users['FUEL_WAREHOUSE'])
        url=reverse('instructions:article',args=['fuel'])
        for unsafe in ['//example.com','https://example.com','/accounts/users/','/production/transport/']:
            self.assertFalse(self.client.get(url,{'return_to':unsafe}).context['return_to'])
        self.assertEqual(self.client.post(url).status_code,403)
        for guide in GUIDES:
            ids=[c['id'] for c in guide['chapters']];self.assertEqual(len(ids),len(set(ids)))
            for chapter in guide['chapters']:
                if chapter['image']:self.assertTrue((Path(__file__).resolve().parents[1]/'static/instructions'/ (chapter['image']+'.png')).exists())
