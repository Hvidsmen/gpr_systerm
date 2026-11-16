from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('personnel', '0043_movementreviewrequest_clearance_ready'),
    ]

    operations = [
        migrations.AddField(
            model_name='movementreviewrequest',
            name='ticket_cost',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='Стоимость билета'),
        ),
        migrations.AddField(
            model_name='movementreviewrequest',
            name='ticket_document',
            field=models.FileField(blank=True, upload_to='personnel/tickets/%Y/%m/', verbose_name='Файл билета'),
        ),
        migrations.AddField(
            model_name='movementreviewrequest',
            name='ticket_uploaded_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
