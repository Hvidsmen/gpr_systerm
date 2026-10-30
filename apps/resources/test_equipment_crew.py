from django.test import TestCase
from .equipment_units import UnitForm
from .models import Position
from apps.production.test_transport import TransportTests

class EquipmentCrewTests(TestCase):
    setUpTestData=classmethod(TransportTests.setUpTestData.__func__)
    def test_multiple_positions_and_clear_and_legacy_submission(self):
        one=Position.objects.create(company=self.company,name='Водитель');two=Position.objects.create(company=self.company,name='Механик')
        from .position_catalog import brigade_for
        brigade_for(one);brigade_for(two)
        data={'type':self.machine.type_id,'name':'Машина','inventory_number':'CREW1','operator_positions':[one.pk,two.pk]}
        form=UnitForm(data,company=self.company);self.assertTrue(form.is_valid(),form.errors);machine=form.save()
        self.assertEqual(set(machine.operator_positions.values_list('pk',flat=True)),{one.pk,two.pk});self.assertIn(machine.operator_position_id,{one.pk,two.pk})
        form=UnitForm({**data,'operator_positions':[]},company=self.company,instance=machine);self.assertTrue(form.is_valid(),form.errors);machine=form.save();self.assertFalse(machine.operator_positions.exists());self.assertIsNone(machine.operator_position_id)
        data.pop('operator_positions');data['operator_position']=one.pk
        form=UnitForm(data,company=self.company,instance=machine);self.assertTrue(form.is_valid(),form.errors);machine=form.save();self.assertEqual(list(machine.operator_positions.all()),[one])
