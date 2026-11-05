from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from .progress import quantity_from_totals


class EmptyCompositeProgressTests(SimpleTestCase):
    def test_empty_operational_composite_has_zero_progress(self):
        spec = {'kind': 'COMPOSITE', 'name': 'ЭМР. Прочее', 'items': [], 'allow_fractional': True}
        self.assertEqual(quantity_from_totals(spec, {}), Decimal('0'))

    def test_nonpositive_item_norm_still_reports_named_complex(self):
        spec = {
            'kind': 'COMPOSITE', 'name': 'Повреждённый комплекс', 'allow_fractional': True,
            'items': [{'id': 1, 'norm': '0'}],
        }
        with self.assertRaisesMessage(ValidationError, 'Повреждённый комплекс'):
            quantity_from_totals(spec, {})
