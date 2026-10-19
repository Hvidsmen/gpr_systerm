from django.test import Client, TestCase
from django.urls import reverse
from .models import Company, Role, User


class PasswordChangeTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Passwords')
        self.user = User.objects.create_user(username='password-user', password='Old-Password-654!', company=self.company)
        self.other = User.objects.create_user(username='other-user', password='Other-Password-987!', company=self.company)
        self.url = reverse('accounts:password_change')
        self.client.force_login(self.user)

    def data(self, **changes):
        return dict(old_password='Old-Password-654!', new_password1='New-Secret-7531!', new_password2='New-Secret-7531!', **changes)

    def test_changes_only_own_password_and_preserves_current_session(self):
        other_session = Client()
        other_session.force_login(self.user)
        response = self.client.post(self.url, {**self.data(), 'user_id': self.other.pk})
        self.assertRedirects(response, self.url)
        self.user.refresh_from_db()
        self.other.refresh_from_db()
        self.assertTrue(self.user.check_password('New-Secret-7531!'))
        self.assertFalse(self.user.check_password('Old-Password-654!'))
        self.assertTrue(self.other.check_password('Other-Password-987!'))
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(other_session.get(self.url).status_code, 302)

    def test_invalid_passwords_do_not_change_credentials(self):
        for changes, field in [({'old_password': 'wrong'}, 'old_password'), ({'new_password2': 'different'}, 'new_password2'), ({'new_password1': 'short', 'new_password2': 'short'}, 'new_password2')]:
            response = self.client.post(self.url, {**self.data(), **changes})
            self.assertEqual(response.status_code, 200)
            self.assertIn(field, response.context['form'].errors)
            self.user.refresh_from_db()
            self.assertTrue(self.user.check_password('Old-Password-654!'))

    def test_all_roles_and_unassigned_users_can_change_own_password(self):
        for role in [None, *Role.objects.all()]:
            self.user.role = role
            self.user.company = None
            self.user.set_password('Old-Password-654!')
            self.user.save()
            self.client.force_login(self.user)
            self.assertEqual(self.client.get(self.url).status_code, 200)
            self.assertRedirects(self.client.post(self.url, self.data()), self.url)

    def test_requires_login_and_csrf(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.url, self.data()).status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url, self.data()).status_code, 403)

    def test_get_does_not_change_password_and_menu_links_to_form(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'name="old_password"')
        self.assertContains(response, 'autocomplete="new-password"')
        self.assertContains(response, 'Сменить пароль')
        self.assertIn('no-store', response['Cache-Control'])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Old-Password-654!'))
