from unittest.mock import patch
from django.db import OperationalError
from .test_monthly_grid import MonthlyGridTests, JAN
from .models import ResourceMonthAllocation


class MonthlyGridBusyTests(MonthlyGridTests):
    def test_json_busy_response_preserves_database_and_returns_retryable_error(self):
        changes=[self.patch(self.simple_row,JAN,'quantity',19)]
        with patch('apps.planning.monthly_grid.save',side_effect=OperationalError('database is locked')):
            response=self.send(changes)
        self.assertEqual(response.status_code,503)
        self.assertTrue(response.json()['retryable'])
        self.assertIn('введённые значения остались',response.json()['error'])
        self.assertEqual(response['Retry-After'],'3')
        self.simple_row.refresh_from_db();self.assertEqual(self.simple_row.quantity,5)
        self.assertEqual(self.send(changes).status_code,200)
        self.simple_row.refresh_from_db();self.assertEqual(self.simple_row.quantity,19)

    def test_busy_resource_add_keeps_bound_form_instead_of_server_error(self):
        payload={'action':'add_resource','kind':'equipment','equipment':self.equipment.pk,'rate':'123.45',
            'months':[str(JAN)],'add_crew':'on'}
        with patch('apps.planning.monthly_grid_views.WorkspaceMonths.add_rows',side_effect=OperationalError('database is locked')):
            response=self.client.post(self.url,payload)
        self.assertEqual(response.status_code,503)
        form=response.context['add_resource_form']
        self.assertEqual(str(form['equipment'].value()),str(self.equipment.pk))
        self.assertEqual(form['rate'].value(),'123.45')
        self.assertTrue(form['add_crew'].value())
        self.assertContains(response,'База занята',status_code=503)
        self.assertFalse(ResourceMonthAllocation.objects.filter(version=self.version,kind='equipment').exists())
        self.assertEqual(self.client.post(self.url,payload).status_code,302)
        self.assertEqual(ResourceMonthAllocation.objects.filter(version=self.version,kind='equipment').count(),1)

    def test_unrelated_database_errors_still_reach_error_logging(self):
        with patch('apps.planning.monthly_grid.save',side_effect=OperationalError('no such table: broken_schema')):
            with self.assertRaises(OperationalError):self.send([self.patch(self.simple_row,JAN,'quantity',7)])
