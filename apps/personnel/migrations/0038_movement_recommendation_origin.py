from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[('personnel','0037_talentpoolcandidate_source_data')]
    operations=[
        migrations.AddField(model_name='movementreviewrequest',name='recommendation_planned_on',
            field=models.DateField(blank=True,null=True,verbose_name='Исходная дата рекомендации')),
    ]
