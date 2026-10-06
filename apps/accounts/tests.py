from django.test import TestCase
from django.urls import reverse

from .models import Company, User


class UserSettingsTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Profile company')
        self.user = User.objects.create_user(username='profile', company=self.company, first_name='Old', last_name='Name')
        self.client.force_login(self.user)

    def test_valid_settings_update_real_fields_and_preserve_permissions(self):
        response = self.client.post(reverse('accounts:settings'), {
            'first_name': 'Иван', 'last_name': 'Иванов', 'email': 'ivan@example.org', 'phone': '+79990000000',
            'company': 999, 'is_superuser': '1', 'is_staff': '1', 'username': 'attacker',
        })
        self.assertRedirects(response, reverse('accounts:settings'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, 'Иванов Иван')
        self.assertEqual(self.user.email, 'ivan@example.org')
        self.assertEqual(self.user.phone, '+79990000000')
        self.assertEqual(self.user.company_id, self.company.pk)
        self.assertEqual(self.user.username, 'profile')
        self.assertFalse(self.user.is_superuser)
        self.assertFalse(self.user.is_staff)

    def test_invalid_email_and_long_name_do_not_save_partial_changes(self):
        response = self.client.post(reverse('accounts:settings'), {
            'first_name': 'x' * 151, 'last_name': 'Changed', 'email': 'invalid-email', 'phone': '123',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('email', response.context['form'].errors)
        self.assertIn('first_name', response.context['form'].errors)
        self.user.refresh_from_db()
        self.assertEqual(self.user.last_name, 'Name')

    def test_form_shows_saved_values(self):
        response = self.client.get(reverse('accounts:settings'))
        self.assertEqual(response.context['form'].initial['first_name'], 'Old')
        self.assertContains(response, 'name="last_name"')
