from django.db.models.signals import post_save,post_delete,pre_save
from django.dispatch import receiver
from .models import DailyFact,LaborFact,EquipmentFact,FuelFact,DayActuation


@receiver(pre_save,sender=DailyFact)
@receiver(pre_save,sender=LaborFact)
@receiver(pre_save,sender=EquipmentFact)
@receiver(pre_save,sender=FuelFact)
@receiver(pre_save,sender=DayActuation)
def remember_previous_day(sender,instance,raw=False,**kwargs):
    if raw or not instance.pk:return
    old=sender.objects.filter(pk=instance.pk).first()
    if old:
        obj_id=old.project_work.section.construction_object_id if sender==DailyFact else old.construction_object_id
        instance._task_previous_location=(old.company_id,obj_id,old.date)


@receiver(post_save,sender=DailyFact)
@receiver(post_delete,sender=DailyFact)
@receiver(post_save,sender=LaborFact)
@receiver(post_delete,sender=LaborFact)
@receiver(post_save,sender=EquipmentFact)
@receiver(post_delete,sender=EquipmentFact)
@receiver(post_save,sender=FuelFact)
@receiver(post_delete,sender=FuelFact)
@receiver(post_save,sender=DayActuation)
@receiver(post_delete,sender=DayActuation)
def invalidate_confirmation(sender,instance,raw=False,**kwargs):
    if raw:return
    from apps.planning.task_services import invalidate_fact
    obj_id=instance.project_work.section.construction_object_id if sender==DailyFact else instance.construction_object_id
    invalidate_fact(instance.company_id,obj_id,instance.date)
    old=getattr(instance,'_task_previous_location',None)
    if old and old!=(instance.company_id,obj_id,instance.date):invalidate_fact(*old)
