from collections import defaultdict
from datetime import date
from django.db import migrations


def seed(apps,schema_editor):
    db=schema_editor.connection.alias
    Camp=apps.get_model('personnel','Camp');Person=apps.get_model('personnel','Person')
    Slot=apps.get_model('personnel','StaffingSlot');Stay=apps.get_model('personnel','CampStay')
    Employee=apps.get_model('resources','Employee')
    names={};rows_by_person=defaultdict(list)
    for slot in Slot.objects.using(db).order_by('pk').iterator():
        name=' '.join(slot.camp.split())
        if name:
            key=(slot.company_id,name.casefold())
            if key not in names:names[key]=Camp.objects.using(db).create(company_id=slot.company_id,name=name).pk
            slot.housing_camp_id=names[key];slot.save(update_fields=['housing_camp'])
        if slot.person_id:rows_by_person[slot.person_id].append(slot)
    employee_numbers=defaultdict(list);employee_names=defaultdict(list)
    for e in Employee.objects.using(db).all().iterator():
        name=' '.join((e.zup_full_name or ' '.join(filter(None,[e.last_name,e.first_name,e.middle_name]))).split()).casefold()
        if e.employee_number:employee_numbers[e.company_id,e.employee_number].append((e.pk,name))
        if name:employee_names[e.company_id,name].append((e.pk,name))
    person_names=defaultdict(int)
    for company_id,name in Person.objects.using(db).values_list('company_id','full_name'):
        person_names[company_id,' '.join(name.split()).casefold()]+=1
    used=set()
    for person in Person.objects.using(db).order_by('pk').iterator():
        name=' '.join(person.full_name.split()).casefold()
        candidates=employee_numbers[person.company_id,person.employee_number] if person.employee_number else employee_names[person.company_id,name]
        if not person.employee_number and person_names[person.company_id,name]!=1:candidates=[]
        fields=[]
        if len(candidates)==1 and candidates[0][1]==name and candidates[0][0] not in used:
            person.employee_id=candidates[0][0];used.add(person.employee_id);fields.append('employee')
        rows=rows_by_person[person.pk]
        if rows and len({(s.pattern_id,str(sorted(s.calendar.items()))) for s in rows})==1:
            person.schedule_slot_id=rows[-1].pk;fields.append('schedule_slot')
        if fields:person.save(update_fields=fields)
        eligible=[s for s in rows if s.housing_camp_id and s.arrival_date and s.arrival_date<=date.today() and s.status=='МВ']
        if eligible and len({(s.housing_camp_id,s.arrival_date) for s in eligible})==1:
            row=eligible[0];Stay.objects.using(db).create(company_id=person.company_id,person_id=person.pk,camp_id=row.housing_camp_id,arrived_on=row.arrival_date,comment='Перенесено из кадровой таблицы')
        elif not eligible:
            closed=[s for s in rows if s.status=='МО' and s.housing_camp_id and s.arrival_date and s.departure_date and s.arrival_date<s.departure_date<=date.today()]
            if closed and len({(s.housing_camp_id,s.arrival_date,s.departure_date) for s in closed})==1:
                row=closed[0];Stay.objects.using(db).create(company_id=person.company_id,person_id=person.pk,camp_id=row.housing_camp_id,arrived_on=row.arrival_date,departed_on=row.departure_date,comment='Перенесено из кадровой таблицы')


class Migration(migrations.Migration):
    dependencies=[('personnel','0002_person_employee_person_schedule_slot_and_more')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
