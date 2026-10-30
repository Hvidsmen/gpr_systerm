from unittest.mock import patch
from django.test import TestCase,RequestFactory
from django.urls import reverse
from core.context_processors import planning_tasks
from apps.accounts.models import Company,User,Role
from .models import WorkTask


class InboxBadgeTests(TestCase):
    def test_badge_counts_only_own_active_incoming_and_link_matches(self):
        company=Company.objects.create(name='Счётчик заданий')
        user=User.objects.create_user(username='badge',company=company,role=Role.objects.get(code='ADMIN'))
        other=User.objects.create_user(username='badge-other',company=company,role=Role.objects.get(code='ADMIN'))
        for index,(status,assignee,author,role) in enumerate([('OPEN',user,other,''),('IN_REVIEW',user,other,''),('OPEN',None,other,'ADMIN'),('DONE',user,other,''),('CLOSED_ADMIN',user,other,''),('SUPERSEDED',user,other,''),('OPEN',other,user,''),('OPEN',None,user,'MANAGER')]):
            WorkTask.objects.create(company=company,key=str(index),kind='MANUAL',title='Задание',status=status,assigned_to=assignee,created_by=author,target_role=role)
        request=RequestFactory().get('/');request.user=user
        with patch('apps.planning.task_services.generate_daily'):
            self.assertEqual(planning_tasks(request)['task_inbox_count'],3)
        self.client.force_login(user)
        response=self.client.get(reverse('planning:task_list'),{'status':'active','scope':'incoming'})
        self.assertEqual(response.context['page'].paginator.count,3)
        self.assertContains(response,'?status=active&amp;scope=incoming')
