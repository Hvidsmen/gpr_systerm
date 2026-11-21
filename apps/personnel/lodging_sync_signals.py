"""Cheap invalidation of saved lodging reconciliation snapshots."""
from django.db import transaction
from django.db.models.signals import post_delete,post_save
from django.dispatch import receiver
from apps.production.dispatch_models import EmployeeActivity
from .models import Camp,CampObjectLink,CampStay,LodgingResolution,LodgingStay
from .lodging_sync import mark_dirty


def later(company_id,camp_ids):
    ids=tuple(set(camp_ids))
    if ids:transaction.on_commit(lambda:mark_dirty(company_id,ids))


@receiver([post_save,post_delete],sender=LodgingStay,dispatch_uid='lodging_sync_stay_dirty')
def stay_changed(sender,instance,**kwargs):later(instance.company_id,[instance.camp_id])


@receiver([post_save,post_delete],sender=CampStay,dispatch_uid='lodging_sync_camp_stay_dirty')
def camp_stay_changed(sender,instance,**kwargs):later(instance.company_id,[instance.camp_id])


@receiver([post_save,post_delete],sender=LodgingResolution,dispatch_uid='lodging_sync_resolution_dirty')
def resolution_changed(sender,instance,**kwargs):later(instance.company_id,[instance.camp_id])


@receiver([post_save,post_delete],sender=CampObjectLink,dispatch_uid='lodging_sync_link_dirty')
def link_changed(sender,instance,**kwargs):later(instance.company_id,[instance.camp_id])


@receiver([post_save,post_delete],sender=EmployeeActivity,dispatch_uid='lodging_sync_production_dirty')
def production_changed(sender,instance,**kwargs):
    ids=list(CampObjectLink.objects.filter(company_id=instance.company_id,construction_object_id=instance.construction_object_id).values_list('camp_id',flat=True))
    if not ids:ids=list(Camp.objects.filter(company_id=instance.company_id).values_list('pk',flat=True))
    later(instance.company_id,ids)
