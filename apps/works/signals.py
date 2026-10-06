from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.accounts.models import Company
from .models import MeasurementUnit
from .catalogs import DEFAULT_UNITS


@receiver(post_save, sender=Company)
def seed_company_units(sender, instance, created, **kwargs):
    if created:
        MeasurementUnit.objects.using(kwargs.get("using", "default")).bulk_create([
            MeasurementUnit(company=instance, symbol=symbol, name=name)
            for symbol, name in DEFAULT_UNITS
        ], ignore_conflicts=True)
