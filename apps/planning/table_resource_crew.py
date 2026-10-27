"""Include catalogue crew positions when adding equipment to the planning table.

These are normal editable resource rows. Adding a machine does not overwrite an
existing headcount, daily distribution, rate or work link for the same position.
"""
from apps.resources.models import Brigade
from apps.resources.position_catalog import brigade_for


def crew_positions(company, equipment):
    rows=equipment.crew_positions.filter(company=company,position__company=company).select_related('position').order_by('position_id')
    for row in rows:
        brigade=Brigade.objects.get(company=company,pk=brigade_for(row.position))
        yield brigade,row.position.default_hourly_rate


def add_monthly_crew(user,version,month,equipment):
    from .plan_workbench import add_month_resource
    added=0
    for brigade,rate in crew_positions(user.company,equipment):
        added+=add_month_resource(user,version,month,{'kind':'labor','brigade':brigade,'rate':rate})
    return added


def add_daily_crew(user,schedule,equipment):
    from .daily_schedule_table import add_entry
    present={row.get('brigade_id') for entry in schedule.entries.filter(kind='labor') for row in entry.rows+entry.original}
    added=0
    for brigade,rate in crew_positions(user.company,equipment):
        if brigade.pk in present:continue
        add_entry(user,schedule,{'kind':'labor','brigade':brigade,'rate':rate})
        present.add(brigade.pk);added+=1
    return added
