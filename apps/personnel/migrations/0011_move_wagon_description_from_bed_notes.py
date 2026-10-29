"""Separate old imported wagon descriptions from individual bed notes."""
import re
from django.db import migrations


def forwards(apps,schema_editor):
    Building=apps.get_model('personnel','LodgingBuilding')
    Bed=apps.get_model('personnel','LodgingBed')
    alias=schema_editor.connection.alias
    for building in Building.objects.using(alias).all().iterator():
        beds=list(Bed.objects.using(alias).filter(company_id=building.company_id,building_id=building.pk))
        description=building.description.strip()
        if not description:
            candidates={bed.note.split(' · ',1)[0].strip() for bed in beds if re.match(r'^вагон[- ]дом\b',bed.note,re.I)}
            if len(candidates)!=1:continue
            description=candidates.pop()
            Building.objects.using(alias).filter(pk=building.pk).update(description=description)
        for bed in beds:
            if bed.note==description:note=''
            elif bed.note.startswith(description+' · '):note=bed.note[len(description)+3:]
            else:continue
            Bed.objects.using(alias).filter(pk=bed.pk).update(note=note)


class Migration(migrations.Migration):
    dependencies=[('personnel','0010_lodgingstay_key_number_and_more')]
    operations=[migrations.RunPython(forwards,migrations.RunPython.noop)]
