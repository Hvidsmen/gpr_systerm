from django.db import migrations,models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('production','0029_separate_fuel_direction')]
    operations=[
        migrations.AddField(model_name='equipmentdayrecord',name='planning_type',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name='+',to='resources.equipmenttype',verbose_name='Плановая потребность')),
        migrations.AddField(model_name='equipmentdayrecord',name='presence_required',field=models.BooleanField(default=True,editable=False)),
    ]
