from django.db.models.signals import post_save,pre_save
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


def canonical_unit_on_save(sender,instance,raw=False,using='default',**kwargs):
    if raw or not instance.company_id or not instance.unit:return
    from .unit_aliases import canonical
    instance.unit=canonical(instance.company_id,instance.unit,using)


for model in ['works.ProjectWork','works.ProjectWorkItem','works.WorkTemplate','works.WorkTemplateItem','resources.Brigade','resources.EquipmentType','resources.FuelType']:
    pre_save.connect(canonical_unit_on_save,sender=model,dispatch_uid='canonical-unit:'+model,weak=False)
