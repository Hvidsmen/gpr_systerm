"""Compact polling: cheap stamps and a scoped cache for the signed form baseline."""
import hashlib
import json
from django.core import signing
from django.core.cache import cache
from django.db.models import Count, Max
from .models import DailyFact
from .dispatch_models import EmployeeActivity, EmployeeWorkLink
from .actuation_models import ActuationReason


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()

def state_stamp(obj,day):
    works=list(DailyFact.objects.filter(company=obj.company,date=day,project_work__section__construction_object=obj).order_by('pk').values_list('pk','updated_at','operational_key','operational_name','operational_unit','actual_quantity','actuation_type','actuation_reason','actuation_reason_ids','comment','reported_by_id'))
    people=list(EmployeeActivity.objects.filter(company=obj.company,date=day,construction_object=obj).order_by('pk').values_list('pk','updated_at','links_revision','employee_id','shift','planning_position_id','position_id','work_id','work_item_id','equipment_id','general','assignment_reviewed','activity'))
    edges=EmployeeWorkLink.objects.filter(company=obj.company,activity__construction_object=obj,activity__date=day).order_by('pk').values_list('pk','activity_id','work_id','work_item_id','fact_id','assigned_individually','crew_source_id')
    reasons=list(ActuationReason.objects.filter(company=obj.company).order_by('pk').values_list('pk','revision','updated_at'))
    return digest([works,people,list(edges),reasons])


def snapshot_key(user,obj,day,session,token):
    return f'production-snapshot-v1:{user.pk}:{obj.company_id}:{obj.pk}:{day}:{session}:{token}'


def remember(user,obj,day,session,signed):
    from .entry_concurrency import SALT
    original=signing.loads(signed,salt=SALT,max_age=12*3600)
    if any(original.get(k)!=v for k,v in {'user':user.pk,'company':obj.company_id,'object':obj.pk,'day':day.isoformat()}.items()):
        from django.core.exceptions import ValidationError
        raise ValidationError('Форма другого пользователя, объекта или дня.')
    token=signed.rsplit(':',1)[-1]
    cache.set(snapshot_key(user,obj,day,session,token),signed,12*3600)
    return token


def response_data(user,obj,day,session,data,users,claims):
    from .collaboration import report_data,canonical_rows,rebase
    signed=data.get('production_snapshot')
    if signed:token=remember(user,obj,day,session,signed)
    else:
        token=str(data.get('snapshot_key',''))[:128]
        signed=cache.get(snapshot_key(user,obj,day,session,token))
    result={}
    users_digest=digest(users);claims_digest=digest([{k:v for k,v in c.items() if k!='expires_at'} for c in claims])
    result.update(users_digest=users_digest,claims_digest=claims_digest)
    if data.get('users_digest')!=users_digest:result['users']=users
    if data.get('claims_digest')!=claims_digest:result['claims']=claims
    if not signed:
        result['need_snapshot']=True
        return result
    stamp=state_stamp(obj,day)
    result.update(digest=stamp,snapshot_key=token)
    if data.get('digest')!=stamp:
        report=report_data(obj,day);rows=canonical_rows(obj,day,report)
        fresh=rebase(user,obj,day,signed,rows,set(data.get('keep',[])))
        result.update(report=report,rows=rows,production_snapshot=fresh,snapshot_key=remember(user,obj,day,session,fresh))
        from .actuation_models import ActuationReason
        result['reason_options']=[{'id':r.pk,'label':r.label,'active':r.status in {'PENDING','APPROVED'}} for r in ActuationReason.objects.filter(company=obj.company)]
    return result
