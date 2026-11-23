from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase,override_settings
from django.urls import reverse

from apps.planning.models import WorkTask
from apps.personnel.models import Camp
from apps.projects.models import ConstructionObject,Project
from .approval_assignments import assignee,authorized,replace
from .approval_mail import send_notice
from .invitation_models import CompanyMailSettings
from .models import ApprovalAssignment,ApprovalAssignmentChange,ApprovalNotice,ApprovalObserver,Company,Role,User


class ApprovalAssignmentTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Approval settings')
        self.admin=User.objects.create_user(username='approval-settings-admin',company=self.company,
            role=Role.objects.get(code='ADMIN'),email='admin@example.org')
        self.first=User.objects.create_user(username='approval-hr-first',company=self.company,
            role=Role.objects.get(code='HR_HEAD'),email='first@example.org')
        self.second=User.objects.create_user(username='approval-hr-second',company=self.company,
            role=Role.objects.get(code='HR_HEAD'),email='second@example.org')
        self.third=User.objects.create_user(username='approval-hr-third',company=self.company,
            role=Role.objects.get(code='HR_HEAD'),email='third@example.org')
        manager_role=Role.objects.get(code='MANAGER')
        self.manager_first=User.objects.create_user(username='approval-manager-first',company=self.company,role=manager_role)
        self.manager_second=User.objects.create_user(username='approval-manager-second',company=self.company,role=manager_role)
        self.manager_third=User.objects.create_user(username='approval-manager-third',company=self.company,role=manager_role)
        self.project=Project.objects.create(company=self.company,name='Проект условий')
        self.parent=ConstructionObject.objects.create(company=self.company,project=self.project,name='Объект')
        self.child=ConstructionObject.objects.create(company=self.company,project=self.project,parent=self.parent,name='Участок')
        self.other=ConstructionObject.objects.create(company=self.company,project=self.project,name='Другой объект')
        self.camp=Camp.objects.create(company=self.company,name='ВЖГ условий')

    def test_single_owner_and_audited_manual_replacement(self):
        row=replace(company=self.company,process='GPR',stage_code='HR',user=self.first,actor=self.admin)
        self.assertTrue(authorized(self.first,'GPR','HR','HR_HEAD'))
        self.assertFalse(authorized(self.second,'GPR','HR','HR_HEAD'))
        with self.assertRaisesMessage(ValidationError,'причину'):
            replace(company=self.company,process='GPR',stage_code='HR',user=self.second,actor=self.admin)
        replace(company=self.company,process='GPR',stage_code='HR',user=self.second,actor=self.admin,reason='Отпуск')
        row.refresh_from_db();self.assertEqual(row.user,self.second)
        change=ApprovalAssignmentChange.objects.first()
        self.assertEqual(change.previous_user,self.first);self.assertEqual(change.new_user,self.second);self.assertEqual(change.reason,'Отпуск')

    def test_admin_page_assigns_and_requires_reason_for_replacement(self):
        self.client.force_login(self.admin);url=reverse('accounts:approval_settings')
        self.assertContains(self.client.get(url),'Настройки согласующих')
        response=self.client.post(url,{'process':'GPR','stage_code':'HR','user':self.first.pk})
        self.assertRedirects(response,url)
        response=self.client.post(url,{'process':'GPR','stage_code':'HR','user':self.second.pk})
        self.assertContains(response,'При замене согласующего укажите причину.')
        self.assertEqual(ApprovalAssignment.objects.get(process='GPR',stage_code='HR').user,self.first)

    def test_scope_conditions_resolve_from_most_specific_to_global(self):
        replace(company=self.company,process='MOVEMENT',stage_code='MANAGER',user=self.manager_first,actor=self.admin)
        replace(company=self.company,process='MOVEMENT',stage_code='MANAGER',user=self.manager_second,actor=self.admin,
            construction_object=self.parent)
        replace(company=self.company,process='MOVEMENT',stage_code='MANAGER',user=self.manager_third,actor=self.admin,camp=self.camp)
        replace(company=self.company,process='MOVEMENT',stage_code='MANAGER',user=self.manager_first,actor=self.admin,
            construction_object=self.parent,camp=self.camp)
        self.assertEqual(assignee(self.company,'MOVEMENT','MANAGER','MANAGER'),self.manager_first)
        self.assertEqual(assignee(self.company,'MOVEMENT','MANAGER','MANAGER',construction_object=self.child),self.manager_second)
        self.assertEqual(assignee(self.company,'MOVEMENT','MANAGER','MANAGER',construction_object=self.other,camp=self.camp),self.manager_third)
        self.assertEqual(assignee(self.company,'MOVEMENT','MANAGER','MANAGER',construction_object=self.child,camp=self.camp),self.manager_first)
        self.assertTrue(authorized(self.manager_first,'MOVEMENT','MANAGER','MANAGER',construction_object=self.child,camp=self.camp))
        self.assertFalse(authorized(self.manager_second,'MOVEMENT','MANAGER','MANAGER',construction_object=self.child,camp=self.camp))

    def test_admin_page_saves_object_and_location_condition(self):
        self.client.force_login(self.admin);url=reverse('accounts:approval_settings')
        response=self.client.post(url,{'process':'MOVEMENT','stage_code':'MANAGER','user':self.admin.pk,
            'construction_object':self.parent.pk,'camp':self.camp.pk})
        self.assertRedirects(response,url)
        row=ApprovalAssignment.objects.get(process='MOVEMENT',stage_code='MANAGER')
        self.assertEqual(row.construction_object,self.parent);self.assertEqual(row.camp,self.camp)
        page=self.client.get(url)
        self.assertContains(page,'Приоритет условий')
        self.assertContains(page,'Все строительные объекты')
        self.assertContains(page,'Все местонахождения')

    def test_admin_can_add_and_remove_company_wide_observer(self):
        self.client.force_login(self.admin);url=reverse('accounts:approval_settings')
        response=self.client.post(url,{'action':'add_observer','observer':self.second.pk})
        self.assertRedirects(response,url)
        observer=ApprovalObserver.objects.get(company=self.company,user=self.second)
        self.assertContains(self.client.get(url),'Общие наблюдатели согласований')
        response=self.client.post(url,{'action':'remove_observer','observer_id':observer.pk})
        self.assertRedirects(response,url)
        self.assertFalse(ApprovalObserver.objects.filter(pk=observer.pk).exists())

    @override_settings(STAGING_MODE=False,EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_notice_goes_only_to_administrators_in_main_and_has_actions_first(self):
        CompanyMailSettings.objects.create(company=self.company,host='smtp.example.org',port=25,security='NONE',
            from_email='portal@example.org',portal_url='http://portal.example.org/')
        task=WorkTask.objects.create(company=self.company,key='approval:test:HR',kind='APPROVAL',title='Согласовать ГПР: должности',
            description='Должность: Электромонтёр\nКоличество: 5',target_role='HR_HEAD',assigned_to=self.first,created_by=self.admin)
        notice=ApprovalNotice.objects.create(company=self.company,process='GPR',stage_code='HR',task_id=task.pk,
            intended_user=self.first,intended_email=self.first.email)
        send_notice(notice.pk);notice.refresh_from_db()
        self.assertEqual(notice.status,'SENT');self.assertEqual(notice.delivered_to,[self.admin.email])
        self.assertEqual(mail.outbox[0].to,[self.admin.email]);self.assertNotIn(self.first.email,mail.outbox[0].to)
        html=mail.outbox[0].alternatives[0].content
        self.assertLess(html.index('Согласовать</button>'),html.index('Что согласовывается'))
        self.assertIn('Открыть согласование в портале',html);self.assertIn('Назначенный согласующий',html)

    @override_settings(STAGING_MODE=False,EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_observer_receives_information_without_decision_actions(self):
        ApprovalObserver.objects.create(company=self.company,user=self.second,added_by=self.admin)
        CompanyMailSettings.objects.create(company=self.company,host='smtp.example.org',port=25,security='NONE',
            from_email='portal@example.org',portal_url='http://portal.example.org/')
        task=WorkTask.objects.create(company=self.company,key='approval:observer:HR',kind='APPROVAL',
            title='Согласовать ГПР: должности',description='Должность: Электромонтёр\nКоличество: 5',
            target_role='HR_HEAD',assigned_to=self.first,created_by=self.admin)
        notice=ApprovalNotice.objects.create(company=self.company,process='GPR',stage_code='HR',task_id=task.pk,
            intended_user=self.first,intended_email=self.first.email)
        send_notice(notice.pk);notice.refresh_from_db()
        self.assertEqual(notice.status,'SENT')
        self.assertEqual({message.to[0] for message in mail.outbox},{self.admin.email,self.second.email})
        observer_message=next(message for message in mail.outbox if message.to==[self.second.email])
        observer_html=observer_message.alternatives[0].content
        self.assertIn('Информационное письмо общему наблюдателю',observer_html)
        self.assertIn('Действие от вас не требуется',observer_html)
        self.assertNotIn('Согласовать</button>',observer_html)
        self.assertNotIn('/approval/action/',observer_html)
