from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[('personnel','0065_recruitment_request_closure')]
    operations=[migrations.AlterField(
        model_name='movementreviewrequest',name='employment_kind',
        field=models.CharField(blank=True,choices=[('STAFF','Штат'),('NEW','Новый'),
            ('REHIRE','Повторный приём'),('TRANSFER','Перевод'),('BUSINESS_TRIP','Командировка')],
            max_length=16,verbose_name='Статус сотрудника'),
    )]
