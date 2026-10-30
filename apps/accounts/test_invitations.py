from uuid import uuid4
from datetime import timedelta
from unittest.mock import patch,MagicMock
import smtplib,socket
from django.test import TestCase,override_settings
from django.urls import reverse
from django.core import mail
from django.core.exceptions import ValidationError,PermissionDenied
from django.utils import timezone
from .models import Company,Role,User
from .invitation_models import CompanyMailSettings,InvitationBatch,UserInvitation
from . import invitations,invitation_mail

@override_settings(LDAP_ENABLED=True,INVITATION_ASYNC_ENABLED=False,EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',EMAIL_HOST='smtp.example.org',EMAIL_HOST_USER='sender@example.org',EMAIL_HOST_PASSWORD='synthetic-mail-secret',DEFAULT_FROM_EMAIL='sender@example.org',PORTAL_PUBLIC_URL='http://192.168.70.9:8001/')
class InvitationTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Invitation tests')
        self.admin=User.objects.create_user(username='invite-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.user=self.person('ivan',role=Role.objects.get(code='MECHANIZATION_DISPATCHER'))
        self.client.force_login(self.admin)
    def person(self,login,**kwargs):
        defaults=dict(company=self.company,role=Role.objects.get(code='DISPATCHER'),auth_source='LDAP',ldap_guid=uuid4(),ldap_login=login,email=login+'@example.org',first_name='Иван',last_name='Иванов')
        defaults.update(kwargs)
        return User.objects.create_user(username='ad:'+uuid4().hex,**defaults)
    def batch(self,*users):return invitations.prepare(self.admin,[u.pk for u in users or [self.user]])
    def confirm(self,batch):
        invitations.confirm(self.admin,batch);batch.refresh_from_db();return batch
    def test_review_has_real_login_role_instructions_and_never_sends(self):
        batch=self.batch();item=batch.items.get()
        self.assertEqual(item.status,'DRAFT');self.assertFalse(getattr(mail,'outbox',[]))
        self.assertIn('ivan',item.body);self.assertIn(self.user.role.name,item.body)
        self.assertIn('http://192.168.70.9:8001/',item.body);self.assertIn('Windows',item.body)
        self.assertIn('/instructions/',item.body);self.assertNotIn('ad:',item.body)
        self.assertNotIn('synthetic-mail-secret',item.body+item.html_body)
    def test_bulk_sends_individual_messages_once_and_keeps_review_snapshot(self):
        second=self.person('petr',role=Role.objects.get(code='PERSONNEL_DISPATCHER'))
        batch=self.confirm(self.batch(self.user,second));self.confirm(batch)
        self.assertEqual(invitations.drain(),2);self.assertEqual(invitations.drain(),0)
        self.assertEqual(len(mail.outbox),2)
        self.assertEqual({tuple(m.to) for m in mail.outbox},{(self.user.email,),(second.email,)})
        self.assertTrue(all(not m.cc and not m.bcc for m in mail.outbox))
        for item in batch.items.all():
            message=next(m for m in mail.outbox if m.to==[item.recipient_email])
            self.assertEqual(message.body,item.body);self.assertEqual(message.alternatives[0].content,item.html_body)
        self.assertEqual(invitations.counts(batch)['sent'],2)
    def test_invalid_recipients_are_explained_and_excluded(self):
        local=User.objects.create_user(username='local-invite',company=self.company,role=self.user.role)
        no_mail=self.person('empty',email='');no_mail.ldap_guid=None;no_mail.save()
        no_role=self.person('norole',role=None);disabled=self.person('disabled',is_active=False)
        batch=self.batch(self.user,local,no_mail,no_role,disabled)
        self.assertEqual(invitations.counts(batch)['blocked'],4)
        self.confirm(batch);self.assertEqual(invitations.drain(),1)
    @patch('apps.accounts.directory_admin.invitation_identities')
    def test_legacy_guid_account_gets_verified_sam_login(self,lookup):
        self.user.ldap_login='';self.user.save()
        lookup.return_value={str(self.user.ldap_guid):{'login':'verified.sam','email':'new@example.org'}}
        batch=self.batch();self.user.refresh_from_db()
        self.assertEqual(self.user.ldap_login,'verified.sam');self.assertEqual(batch.items.get().windows_login,'verified.sam')
        self.assertEqual(self.user.email,'ivan@example.org')
    def test_changed_email_role_or_portal_requires_new_review(self):
        batch=self.batch();User.objects.filter(pk=self.user.pk).update(email='changed@example.org')
        with self.assertRaises(ValidationError):self.confirm(batch)
        self.user.refresh_from_db();batch=self.batch()
        with override_settings(PORTAL_PUBLIC_URL='http://example.org/'):
            with self.assertRaises(ValidationError):self.confirm(batch)
        self.assertFalse(batch.items.filter(status='QUEUED').exists())
    def test_changes_after_confirmation_cancel_before_delivery(self):
        batch=self.confirm(self.batch());User.objects.filter(pk=self.user.pk).update(role=Role.objects.get(code='OBSERVER'))
        self.assertEqual(invitations.drain(),0);self.assertEqual(batch.items.get().status,'CANCELLED')
    def test_scope_and_actor_are_enforced(self):
        foreign=self.person('foreign',company=Company.objects.create(name='Foreign invites'))
        with self.assertRaises(PermissionDenied):self.batch(self.user,foreign)
        batch=self.batch();other=User.objects.create_user(username='second-admin',company=self.company,role=self.admin.role)
        with self.assertRaises(PermissionDenied):invitations.confirm(other,batch)
        self.client.force_login(foreign)
        self.assertEqual(self.client.get(reverse('accounts:invitation_detail',args=[batch.pk])).status_code,403)
    def test_single_post_preview_and_get_cannot_send(self):
        url=reverse('accounts:user_invite',args=[self.user.pk]);self.assertEqual(self.client.get(url).status_code,405)
        response=self.client.post(url);self.assertEqual(response.status_code,302)
        batch=InvitationBatch.objects.get();detail=self.client.get(response.url)
        self.assertContains(detail,'Проверьте приглашения');self.assertContains(detail,self.user.ldap_login)
        self.assertIsNone(batch.confirmed_at);self.assertEqual(batch.items.get().status,'DRAFT')
        self.assertContains(self.client.get(reverse('accounts:user_list')),'Отправить приглашения')
        self.assertContains(self.client.get(reverse('accounts:user_detail',args=[self.user.pk])),'Отправить приглашение')
    def test_all_filtered_spans_pages_and_does_not_include_other_roles(self):
        for n in range(22):self.person('bulk'+str(n))
        response=self.client.post(reverse('accounts:user_invite_bulk')+'?role='+str(Role.objects.get(code='DISPATCHER').pk),{'all_filtered':'yes'})
        self.assertEqual(response.status_code,302);batch=InvitationBatch.objects.get()
        self.assertEqual(batch.items.count(),22);self.assertFalse(batch.items.filter(recipient=self.user).exists())
    def test_another_company_admin_cannot_read_batch_or_status(self):
        batch=self.batch();other=User.objects.create_user(username='foreign-admin',company=Company.objects.create(name='Other admin'),role=self.admin.role)
        self.client.force_login(other)
        for name in ['invitation_detail','invitation_status']:
            self.assertEqual(self.client.get(reverse('accounts:'+name,args=[batch.pk])).status_code,404)
    @patch('apps.accounts.invitation_mail.connection')
    def test_auth_failure_pauses_queue_and_redacts_server_text(self,connection):
        connection.return_value.open.side_effect=smtplib.SMTPAuthenticationError(535,b'private-password')
        batch=self.confirm(self.batch());self.assertEqual(invitations.drain(),0);batch.refresh_from_db()
        self.assertTrue(batch.paused);self.assertNotIn('private-password',batch.last_error)
        self.assertEqual(batch.items.get().status,'QUEUED');invitations.drain();self.assertEqual(connection.call_count,1)
    @patch('apps.accounts.invitation_mail.connection')
    def test_connection_failure_backs_off_without_claim_or_loss(self,connection):
        connection.return_value.open.side_effect=OSError('secret-network-detail')
        batch=self.confirm(self.batch());invitations.drain();batch.refresh_from_db()
        self.assertGreater(batch.retry_after,timezone.now());self.assertFalse(batch.paused)
        self.assertEqual(batch.items.get().attempts,0);invitations.drain();self.assertEqual(connection.call_count,1)
    @patch('apps.accounts.invitations.EmailMultiAlternatives.send',side_effect=socket.timeout('private-value'))
    def test_uncertain_delivery_is_not_silently_retried(self,send):
        batch=self.confirm(self.batch());invitations.drain();invitations.drain()
        item=batch.items.get();self.assertEqual(item.status,'UNKNOWN');self.assertEqual(send.call_count,1)
        self.assertNotIn('private-value',item.error)
    @patch('apps.accounts.invitations.EmailMultiAlternatives.send',side_effect=smtplib.SMTPRecipientsRefused({'ivan@example.org':(550,b'private-detail')}))
    def test_rejected_recipient_fails_without_exposing_response(self,send):
        batch=self.confirm(self.batch());invitations.drain();item=batch.items.get()
        self.assertEqual(item.status,'FAILED');self.assertNotIn('private-detail',item.error)
    def test_interrupted_worker_is_marked_unknown(self):
        batch=self.confirm(self.batch());batch.items.update(status='SENDING',started_at=timezone.now()-timedelta(minutes=11))
        invitations.drain();self.assertEqual(batch.items.get().status,'UNKNOWN')
    def test_missing_smtp_prevents_queueing(self):
        batch=self.batch()
        with override_settings(EMAIL_HOST=''):
            with self.assertRaisesMessage(ValidationError,'SMTP'):self.confirm(batch)
        self.assertEqual(batch.items.get().status,'DRAFT')
    def test_html_escapes_user_names(self):
        self.user.first_name='<img src=x onerror=alert(1)>';self.user.save()
        item=self.batch().items.get();self.assertIn('&lt;img',item.html_body);self.assertNotIn('<img',item.html_body)
    def test_settings_encrypt_preserve_and_never_echo_secret(self):
        url=reverse('accounts:invitation_settings')
        payload=dict(host='smtp.example.org',port=465,security='SSL',username='sender@example.org',from_email='sender@example.org',portal_url='http://192.168.70.9:8001/',password='synthetic-new-secret')
        self.assertEqual(self.client.post(url,payload).status_code,302)
        row=CompanyMailSettings.objects.get(company=self.company)
        self.assertNotIn('synthetic-new-secret',row.password_encrypted)
        self.assertEqual(invitation_mail.configuration(self.company)['password'],'synthetic-new-secret')
        self.assertNotContains(self.client.get(url),'synthetic-new-secret')
        payload['password']='';self.client.post(url,payload)
        self.assertEqual(invitation_mail.configuration(self.company)['password'],'synthetic-new-secret')
        from apps.audit.models import AuditLog
        self.assertNotIn('synthetic-new-secret',str(list(AuditLog.objects.values())))
    @patch('apps.accounts.invitation_mail.connection')
    def test_smtp_test_only_connects_and_does_not_send(self,connection):
        self.client.post(reverse('accounts:invitation_settings'),dict(host='smtp.example.org',port=587,security='STARTTLS',username='',from_email='sender@example.org',portal_url='http://192.168.70.9:8001/',action='test'))
        connection.return_value.open.assert_called_once();connection.return_value.close.assert_called_once()
        self.assertFalse(getattr(mail,'outbox',[]));self.assertFalse(InvitationBatch.objects.exists())
    def test_settings_validate_host_port_and_portal_url(self):
        for change in [dict(host='https://smtp.example.org'),dict(port=70000),dict(portal_url='http://user:secret@example.org/')]:
            payload=dict(host='smtp.example.org',port=465,security='SSL',username='',from_email='sender@example.org',portal_url='http://192.168.70.9:8001/');payload.update(change)
            form=invitation_mail.MailSettingsForm(payload,company=self.company)
            self.assertFalse(form.is_valid())
    def test_password_encryption_is_company_bound(self):
        encrypted=invitation_mail.encrypt_password(self.company.pk,'synthetic-secret')
        with self.assertRaises(ValidationError):invitation_mail.decrypt_password(self.company.pk+1,encrypted)

    def test_windows_login_search_and_nonadmin_routes(self):
        self.user.ldap_login='login.only';self.user.save()
        response=self.client.get(reverse('accounts:user_list'),{'q':'login.only'})
        self.assertEqual(list(response.context['users']),[self.user])
        self.client.force_login(self.user)
        for name in ['invitation_list','invitation_settings']:
            self.assertEqual(self.client.get(reverse('accounts:'+name)).status_code,403)
        self.assertEqual(self.client.post(reverse('accounts:user_invite',args=[self.admin.pk])).status_code,403)
        self.assertFalse(InvitationBatch.objects.exists())
    @patch('apps.accounts.backends.directory_identity')
    def test_verified_login_is_cached_by_authentication(self,lookup):
        from django.test import RequestFactory
        from .backends import DirectoryBackend
        lookup.return_value=dict(guid=self.user.ldap_guid,login='verified.windows',first_name='Иван',last_name='Иванов',email=self.user.email,group_dns=[])
        request=RequestFactory().post('/accounts/login/',{'auth_source':'ldap'})
        with override_settings(LDAP_BIND_PASSWORD='synthetic-bind-secret'):
            user=DirectoryBackend().authenticate(request,username='verified.windows',password='synthetic-windows-secret')
        self.assertEqual(user.ldap_login,'verified.windows');self.assertFalse(user.has_usable_password())

    @override_settings(EMAIL_HOST='',EMAIL_PORT=465,EMAIL_USE_SSL=True,EMAIL_USE_TLS=False,EMAIL_HOST_USER='',EMAIL_HOST_PASSWORD='',DEFAULT_FROM_EMAIL='site@yamaldorstroy.ru')
    def test_unconfigured_form_proposes_verified_lancloud_without_saving_or_sending(self):
        response=self.client.get(reverse('accounts:invitation_settings'))
        form=response.context['form']
        self.assertEqual(form.initial['host'],'smtp.lancloud.ru')
        self.assertEqual(form.initial['port'],587)
        self.assertEqual(form.initial['security'],'STARTTLS')
        self.assertEqual(form.initial['username'],'site@yamaldorstroy.ru')
        self.assertContains(response,'SMTP Submit Allowed')
        self.assertFalse(CompanyMailSettings.objects.exists())
        self.assertFalse(getattr(mail,'outbox',[]))
        self.assertNotIn('password',form.initial)
    def test_lancloud_defaults_preserve_server_environment_and_saved_company_settings(self):
        url=reverse('accounts:invitation_settings')
        with override_settings(EMAIL_HOST='custom.example.org',EMAIL_PORT=465,EMAIL_USE_SSL=True):
            form=self.client.get(url).context['form']
            self.assertEqual(form.initial['host'],'custom.example.org')
            self.assertEqual(form.initial['port'],465)
            self.assertEqual(form.initial['security'],'SSL')
        CompanyMailSettings.objects.create(company=self.company,host='company.example.org',port=2525,security='STARTTLS',username='company@example.org',from_email='company@example.org')
        form=self.client.get(url).context['form']
        self.assertEqual(form.initial['host'],'company.example.org')
        self.assertEqual(form.initial['port'],2525)
        self.assertEqual(form.initial['username'],'company@example.org')
