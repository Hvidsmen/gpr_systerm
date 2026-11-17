"""Create and verify a trial MSSQL copy without switching production."""
import json
from pathlib import Path
import sqlite3
import traceback
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand,CommandError
from django.db import connections


EXCLUDED_MODELS={'contenttypes.contenttype','auth.permission','sessions.session'}


def model_identity(database,model):
    queryset=model._base_manager.using(database).order_by(model._meta.pk.name)
    return list(queryset.values_list(model._meta.pk.name,flat=True))


def compare_databases(source,target):
    report={};errors=[]
    for model in apps.get_models():
        label=model._meta.label_lower
        if not model._meta.managed or model._meta.proxy or label in EXCLUDED_MODELS:continue
        left=model_identity(source,model);right=model_identity(target,model)
        report[label]={'source':len(left),'target':len(right)}
        if left!=right:errors.append(f'{label}: SQLite {len(left)}, MSSQL {len(right)}')
    return report,errors


def dump_fixture(source,fixture):
    """Serialize through an explicitly UTF-8 stream on localized Windows."""
    with fixture.open('w',encoding='utf-8',newline='') as stream:
        call_command('dumpdata',database=source,exclude=sorted(EXCLUDED_MODELS),
                     use_natural_foreign_keys=True,use_natural_primary_keys=False,
                     use_base_manager=True,stdout=stream,verbosity=1)


def activate_copy_connections(snapshot,target):
    """Make MSSQL default so imperfect legacy data migrations cannot hit SQLite."""
    source_settings=connections['default'].settings_dict.copy()
    source_settings['OPTIONS']=source_settings.get('OPTIONS',{}).copy()
    source_settings['TEST']=source_settings.get('TEST',{}).copy()
    source_settings['NAME']=snapshot
    target_settings=connections[target].settings_dict.copy()
    target_settings['OPTIONS']=target_settings.get('OPTIONS',{}).copy()
    target_settings['TEST']=target_settings.get('TEST',{}).copy()
    connections.close_all()
    connections.settings['source_snapshot']=source_settings
    connections.settings['default']=target_settings
    for alias in ('default','source_snapshot'):
        try:del connections[alias]
        except AttributeError:pass
    return 'source_snapshot','default'


class Command(BaseCommand):
    help='Создать проверочную копию SQLite в пустой MSSQL без переключения портала.'

    def add_arguments(self,parser):
        parser.add_argument('--target',default='mssql')
        parser.add_argument('--output',required=True)

    def handle(self,*args,**options):
        try:
            return self._handle(*args,**options)
        except Exception as error:
            # Windows PowerShell 5 may turn a native stderr stream into a
            # single NativeCommandError record.  Put the complete traceback
            # on stdout as well so the operator's log always contains the
            # actual database error.
            self.stdout.write('\nПОЛНАЯ ОШИБКА ПОДГОТОВКИ MSSQL:')
            traceback.print_exc(file=self.stdout)
            self.stdout.write('Рабочая SQLite-база не изменялась.')
            if isinstance(error,CommandError):
                raise
            raise CommandError('Подготовка копии MSSQL завершилась ошибкой. Полный текст указан выше.') from error

    def _handle(self,*args,**options):
        source='default';target=options['target'];output=Path(options['output']).resolve()
        if target not in connections:raise CommandError(f'Подключение {target} не настроено.')
        if connections[source].vendor!='sqlite':raise CommandError('Исходная рабочая база должна быть SQLite.')
        if connections[target].vendor not in {'microsoft','mssql'}:raise CommandError('Целевая база должна быть Microsoft SQL Server.')
        tables=connections[target].introspection.table_names()
        if tables:raise CommandError('YDS_PORTAL уже содержит таблицы. Подготовка остановлена без удаления данных: '+', '.join(tables[:10]))
        output.mkdir(parents=True,exist_ok=False)
        snapshot=output/'source.sqlite3';fixture=output/'portal-data.json';report_path=output/'verification.json'
        source_path=Path(connections[source].settings_dict['NAME']).resolve()
        self.stdout.write('Создаём согласованный снимок рабочей SQLite…')
        with sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True,timeout=30) as live,sqlite3.connect(snapshot) as copy:
            live.backup(copy)
            if copy.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise CommandError('Снимок SQLite не прошёл проверку целостности.')
        source,target=activate_copy_connections(snapshot,target)
        self.stdout.write('Создаём схему MSSQL…')
        call_command('migrate',database=target,interactive=False,verbosity=1)
        # Remove migration seed data; the fixture contains the authoritative
        # production rows. django_migrations itself is retained by flush.
        call_command('flush',database=target,interactive=False,verbosity=0)
        self.stdout.write('Выгружаем данные из неизменяемого снимка…')
        dump_fixture(source,fixture)
        self.stdout.write('Загружаем данные в MSSQL…')
        call_command('loaddata',str(fixture),database=target,verbosity=1)
        report,errors=compare_databases(source,target)
        report_path.write_text(json.dumps({'source':str(source_path),'snapshot':str(snapshot),'target':target,
                                           'models':report,'errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
        if errors:raise CommandError('Копия создана, но сверка не пройдена: '+'; '.join(errors[:10]))
        self.stdout.write(self.style.SUCCESS(f'ГОТОВО: проверены {len(report)} моделей; расхождений нет.'))
        self.stdout.write(f'Снимок и отчёт: {output}')
        self.stdout.write('Рабочий портал продолжает использовать SQLite.')
