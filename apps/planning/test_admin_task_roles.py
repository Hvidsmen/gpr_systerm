from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Role,User,Company
from .test_task_queues import TaskQueueTests
from .models import WorkTask
from .task_admin import filtered

class AdminRoleListTests(TestCase):
    setUpTestData=classmethod(TaskQueueTests.setUpTestData.__func__)
    task=TaskQueueTests.task
    def test_admin_defaults_company_list_and_filters_assigned_and_role_queues(self):
        personal=self.task('manager');queue=self.task('queue',assigned_to=None,target_role='ESTIMATOR')
        estimator=User.objects.create_user(username='price-user',company=self.company,role=Role.objects.get(code='ESTIMATOR'))
        named=self.task('named-price',assigned_to=estimator)
        other_company=Company.objects.create(name='Foreign')
        WorkTask.objects.create(company=other_company,key='foreign',kind='MANUAL',title='foreign',target_role='ESTIMATOR')
        self.client.force_login(self.admin);url=reverse('planning:task_list')
        response=self.client.get(url);self.assertEqual(response.context['scope'],'all');self.assertEqual(response.context['status'],'active');self.assertEqual(response.context['page'].paginator.count,3)
        response=self.client.get(url,{'role':'ESTIMATOR'});self.assertEqual({t.pk for t in response.context['page']},{queue.pk,named.pk});self.assertContains(response,'Роль исполнителя')
        self.assertEqual(set(filtered(self.admin,{'role':'ESTIMATOR'}).values_list('pk',flat=True)),{queue.pk,named.pk})
        self.client.force_login(self.worker);self.assertNotContains(self.client.get(url,{'role':'ESTIMATOR'}),'id="task-role"')
