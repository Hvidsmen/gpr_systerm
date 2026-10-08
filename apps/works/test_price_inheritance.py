from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .tests import WorkLocationTests
from .models import ProjectWork
from .prices import change_price, price_on


class PriceInheritanceTests(TestCase):
    setUpTestData = classmethod(WorkLocationTests.setUpTestData.__func__)

    def setUp(self):
        self.client.force_login(self.user)
        self.target = self.make_work(self.sections[0], 'Испытание свай', 'm', 10)
        self.source = self.make_work(self.sections[1], 'Испытание свай', 'm', 125)
        self.foreign_work = self.make_work(self.foreign_section, 'Испытание свай', 'm', 999)
        self.url = reverse('works:price_change', args=[self.target.pk])

    def make_work(self, section, name, unit, price):
        return ProjectWork.objects.create(company=section.company, section=section, name=name, unit=unit,
            unit_price=price, load_profile=self.profile if section.company == self.company else None)

    def data(self, **overrides):
        values = dict(price_source=self.source.pk, price='', effective_from=timezone.localdate().isoformat(), reason='')
        values.update(overrides)
        return values

    def test_auto_search_matches_name_and_unit_across_sections_and_scopes_company(self):
        duplicate = self.make_work(self.sections[0], ' ИСПЫТАНИЕ   СВАЙ ', 'm', 150)
        self.make_work(self.sections[1], 'Испытание свай', 'km', 777)
        response = self.client.get(reverse('works:price_sources'), dict(name='испытание свай', unit='m', exclude=self.target.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row['id'] for row in response.json()['results']}, {self.source.pk, duplicate.pk})
        row = next(row for row in response.json()['results'] if row['id'] == self.source.pk)
        self.assertEqual(row['price'], '125.00')
        self.assertIn(self.sections[1].name, row['label'])

    def test_copy_uses_server_price_and_records_source_without_later_link(self):
        response = self.client.post(self.url, self.data(price='9999'))
        self.assertRedirects(response, reverse('works:work_detail', args=[self.target.pk]))
        self.target.refresh_from_db()
        self.assertEqual(self.target.unit_price, Decimal(125))
        self.assertIn(f'№{self.source.pk}', self.target.price_history.last().reason)
        change_price(self.user, self.source.pk, Decimal(200), timezone.localdate(), 'Новая цена')
        self.assertEqual(price_on(self.target), Decimal(125))

    def test_scheduled_price_is_copied_for_chosen_effective_date(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        change_price(self.user, self.source.pk, Decimal(250), tomorrow, 'Следующая цена')
        response = self.client.post(self.url, self.data(effective_from=tomorrow.isoformat()))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.target.price_history.last().price, Decimal(250))
        self.assertEqual(price_on(self.target), Decimal(10))
        response = self.client.get(reverse('works:price_sources'), dict(source=self.source.pk, unit='m', date=tomorrow.isoformat()))
        self.assertEqual(response.json()['results'][0]['price'], '250.00')

    def test_invalid_sources_cannot_change_price(self):
        wrong = self.make_work(self.sections[1], 'Испытание свай', 'km', 777)
        before = self.target.price_history.count()
        for source in [self.foreign_work, self.target, wrong]:
            response = self.client.post(self.url, self.data(price_source=source.pk))
            self.assertEqual(response.status_code, 200)
            self.assertIn('price_source', response.context['form'].errors)
        self.assertEqual(self.target.price_history.count(), before)
        self.assertEqual(self.client.get(reverse('works:price_sources'), dict(source=self.foreign_work.pk, unit='m')).json()['results'], [])

    def test_creation_inherits_price_without_javascript_and_keeps_audit(self):
        values = WorkLocationTests.data(self)
        values.update(name='Испытание свай', unit_price='', price_source=self.source.pk)
        response = self.client.post(reverse('works:work_create'), values)
        self.assertEqual(response.status_code, 302)
        work = ProjectWork.objects.exclude(pk__in=[self.target.pk, self.source.pk, self.foreign_work.pk]).get()
        self.assertEqual(work.unit_price, Decimal(125))
        self.assertIn(f'№{self.source.pk}', work.price_history.get().reason)

    def test_manual_price_and_existing_history_correction_remain_available(self):
        response = self.client.post(self.url, self.data(price_source='', price='35'))
        self.assertEqual(response.status_code, 302)
        initial = self.target.price_history.first()
        response = self.client.post(self.url, self.data(corrects=initial.pk, effective_from='', reason='Исправлена исходная цена'))
        self.assertEqual(response.status_code, 302)
        # A correction retains the old effective date; chronological last may be the later manual price.
        correction = self.target.price_history.get(corrects=initial)
        self.assertEqual(correction.effective_from, initial.effective_from)
        self.assertEqual(correction.price, Decimal(125))
        self.assertIn('Исправлена исходная цена', correction.reason)

    def test_lookup_validation_and_role_permissions(self):
        url = reverse('works:price_sources')
        self.assertEqual(self.client.get(url, dict(unit='m', date='bad', name='test')).status_code, 400)
        self.assertEqual(self.client.get(url, dict(name='test')).status_code, 400)
        from apps.accounts.models import Role
        self.user.role = Role.objects.get(code='FOREMAN')
        self.user.save(update_fields=['role'])
        self.assertEqual(self.client.get(url, dict(unit='m', name='test')).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.data()).status_code, 403)
