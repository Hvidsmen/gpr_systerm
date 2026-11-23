from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0053_approval_assignment_soft_delete'),
    ]

    operations = [
        migrations.AddField(
            model_name='approvalnotice',
            name='delivery_backend',
            field=models.CharField(blank=True, max_length=12),
        ),
    ]
