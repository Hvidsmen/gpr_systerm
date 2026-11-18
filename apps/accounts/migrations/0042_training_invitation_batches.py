from django.db import migrations,models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('accounts','0041_republish_mssql_cutover_resume_fix')]
    operations=[
        migrations.AddField(model_name='invitationbatch',name='kind',
            field=models.CharField(choices=[('PORTAL','Основной портал'),('TRAINING','Учебная копия')],default='PORTAL',max_length=12)),
        migrations.AddField(model_name='invitationbatch',name='training_copy',
            field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='invitation_batches',to='accounts.trainingcopy')),
    ]
