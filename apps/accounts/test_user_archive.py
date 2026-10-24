from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from .models import User,Company,Role
from .user_archive import apply
from apps.audit.models import AuditLog

class ArchiveTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='User archive')
        self.admin=User.objects.create_user(username='archive-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.user=User.objects.create_user(username='archive-one',password='test-pass',company=self.company,role=Role.objects.get(code='OBSERVER'))
        self.inactive=User.objects.create_user(username='archive-inactive',company=self.company,is_active=False)
        self.client.force_login(self.admin)
        self.url=reverse('accounts:user_bulk_archive')

    def confirm(self,ids,action='archive',query=''):
        response=self.client.post(self.url+query,{'action':action,'selected':ids})
        self.assertEqual(response.status_code,200)
        return self.client.post(self.url,{'stage':'confirm','token':response.context['token']})

    def test_review_then_archive_and_restore_preserve_password_and_prior_activity(self):
        password=self.user.password
        response=self.client.post(self.url,{'action':'archive','selected':[self.user.pk,self.inactive.pk]})
        self.assertContains(response,'Выбрано пользователей:')
        self.user.refresh_from_db();self.assertIsNone(self.user.archived_at)
        self.client.post(self.url,{'stage':'confirm','token':response.context['token']})
        self.user.refresh_from_db();self.assertFalse(self.user.is_active);self.assertIsNotNone(self.user.archived_at)
        page=self.client.get(reverse('accounts:user_list'));self.assertNotIn(self.user,list(page.context['users']))
        archived=self.client.get(reverse('accounts:user_list'),{'archive':'yes'});self.assertIn(self.user,list(archived.context['users']))
        self.confirm([self.user.pk,self.inactive.pk],'restore','?archive=yes')
        self.user.refresh_from_db();self.inactive.refresh_from_db()
        self.assertTrue(self.user.is_active);self.assertFalse(self.inactive.is_active)
        self.assertEqual(self.user.password,password)
        self.assertEqual(AuditLog.objects.filter(entity_type='accounts.user',comment__contains='архив').count(),4)

    def test_all_filtered_includes_other_pages_and_skips_admin(self):
        User.objects.bulk_create([User(username=f'archive-many-{i}',company=self.company) for i in range(25)])
        response=self.client.post(self.url+'?q=archive-many',{'action':'archive','all_filtered':'yes'})
        self.assertEqual(response.context['count'],25)
        self.client.post(self.url,{'stage':'confirm','token':response.context['token']})
        self.assertEqual(User.objects.filter(company=self.company,archived_at__isnull=False).count(),25)
        self.admin.refresh_from_db();self.assertTrue(self.admin.is_active)

    def test_no_cross_company_self_admin_or_staff(self):
        foreign=User.objects.create_user(username='archive-foreign',company=Company.objects.create(name='Foreign'))
        staff=User.objects.create_user(username='archive-staff',company=self.company,is_staff=True)
        for target in [foreign,self.admin,staff]:
            self.assertEqual(self.client.post(self.url,{'action':'archive','selected':[self.user.pk,target.pk]}).status_code,403)
        self.user.refresh_from_db();self.assertIsNone(self.user.archived_at)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.url,{'action':'archive','selected':[self.inactive.pk]}).status_code,403)
        self.assertEqual(self.client.get(self.url).status_code,403)

    def test_archived_account_cannot_login_or_be_reactivated_by_access_form(self):
        apply(self.admin,[self.user.pk],'archive')
        from .backends import LocalBackend
        self.assertIsNone(LocalBackend().authenticate(None,username=self.user.username,password='test-pass'))
        self.user.refresh_from_db();self.user.is_active=True;self.user.save(update_fields=['is_active'])
        self.user.refresh_from_db();self.assertFalse(self.user.is_active)
        response=self.client.get(reverse('accounts:user_access',args=[self.user.pk]))
        self.assertTrue(response.context['form'].fields['is_active'].disabled)

    def test_signed_review_cannot_be_tampered_or_reused_by_other_actor(self):
        response=self.client.post(self.url,{'action':'archive','selected':[self.user.pk]})
        token=response.context['token']
        self.assertEqual(self.client.post(self.url,{'stage':'confirm','token':token+'x'}).status_code,403)
        other=User.objects.create_user(username='archive-admin-two',company=self.company,role=self.admin.role)
        self.client.force_login(other)
        self.assertEqual(self.client.post(self.url,{'stage':'confirm','token':token}).status_code,403)
        self.user.refresh_from_db();self.assertIsNone(self.user.archived_at)

    def test_stale_access_edit_does_not_restore_archived_account(self):
        stale=User.objects.get(pk=self.user.pk)
        apply(self.admin,[self.user.pk],'archive')
        stale.first_name='Edited';stale.is_active=True;stale.save()
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.archived_at)
        self.assertFalse(self.user.is_active)
