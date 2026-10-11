"""Prepare bounded report projections outside the web request."""
from datetime import date, timedelta
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from apps.planning.models import GlobalPlanVersion, ProjectPlanMember
from apps.planning.report_projection import period_snapshot


class Command(BaseCommand):
    help = 'Подготовить данные отчётов за период без загрузки полного дневного плана в Python.'

    def add_arguments(self, parser):
        parser.add_argument('--start', required=True)
        parser.add_argument('--end', required=True)

    def handle(self, *args, **options):
        try:
            start, end = date.fromisoformat(options['start']), date.fromisoformat(options['end'])
        except ValueError as exc:
            raise CommandError('Укажите даты YYYY-MM-DD.') from exc
        if start > end:
            raise CommandError('Начало периода позже окончания.')
        if connection.vendor != 'sqlite':
            raise CommandError('Подготовка выборок пока поддерживается для SQLite.')
        versions = GlobalPlanVersion.objects.filter(status__in=['APPROVED', 'COMPLETED'],
                     start_date__lte=end, end_date__gte=start).defer('snapshot').order_by('construction_object_id', '-approved_at', '-pk')
        remaining = {}
        dates = {start + timedelta(days=i) for i in range((end-start).days+1)}
        for version in versions.iterator(chunk_size=20):
            open_days = remaining.setdefault(version.construction_object_id, set(dates))
            covered = {day for day in open_days if version.start_date <= day <= version.end_date}
            if not covered:
                continue
            open_days.difference_update(covered)
            period_snapshot(version, start, end)
            self.stdout.write(f'v{version.pk}: {start} — {end} готово')
        members = ProjectPlanMember.objects.filter(consolidated_version__status='FIXED',
                    consolidated_version__start_date__lte=end, consolidated_version__end_date__gte=start).select_related(
                    'version', 'consolidated_version').defer('snapshot', 'version__snapshot')
        for member in members.iterator(chunk_size=20):
            period_snapshot(member.version, start, end, member=member,
                            source_key=f"member-{member.pk}-{member.review_id}-{member.consolidated_version.fixed_at}")
            self.stdout.write(f'Сводная версия: объект {member.pk} готово')
