from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('personnel', '0056_lodging_presence'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='lodgingstay',
            name='lodging_departure_after_arrival',
        ),
    ]
