"""One shared day-picker pool. Presence ranks choices without moving machines."""
from apps.resources.models import Equipment
from .transport_models import EquipmentDayRecord,EquipmentTransportEvent
from .transport import present,conditions_for,CONDITIONS


def prepare(company,camp,day):
    from .models import EquipmentActivity
    from apps.resources.group_order import machine_order
    occupied=set(EquipmentDayRecord.objects.filter(company=company,date=day).values_list('equipment_id',flat=True))
    occupied.update(EquipmentActivity.objects.filter(company=company,date=day).values_list('equipment_id',flat=True))
    disposed=EquipmentTransportEvent.objects.filter(company=company,kind='WRITEOFF',cancelled_at=None,date__lte=day).values('equipment_id')
    machines=list(Equipment.objects.filter(company=company,is_active=True).exclude(pk__in=occupied).exclude(pk__in=disposed).select_related('type__parent__category','type__category','catalogue_group'))
    machines.sort(key=machine_order)
    stays={stay.equipment_id:stay for stay in present(company,day).filter(equipment_id__in=[m.pk for m in machines]).select_related('camp')}
    conditions=conditions_for(company,[m.pk for m in machines],[day])
    result=[]
    for machine in machines:
        root=machine.type.parent if machine.type.parent_id else machine.type
        stay=stays.get(machine.pk);location=0 if stay and stay.camp_id==camp.pk else 1 if stay else 2
        whereabouts='ВЖГ: '+stay.camp.name if stay else 'Нахождение в ВЖГ не оформлено'
        numbers=' · '.join(filter(None,[machine.plate_number,machine.inventory_number,machine.vin]))
        label=' · '.join(filter(None,[root.name,machine.name,numbers,dict(CONDITIONS).get(conditions[machine.pk,day],'Состояние не указано'),whereabouts]))
        result.append({'id':machine.pk,'type':root.pk,'type_name':root.name,'location':location,'whereabouts':whereabouts,'label':label})
    return result

