import os
from pathlib import Path
import shutil
from django.conf import settings
from django.db import migrations


CONTROL_FILES = ('CHECK_GPR_MSSQL.cmd', 'ops/check-mssql.ps1')


def publish_tool(apps, schema_editor, platform_name=None):
    # The updater executing this first release still has the previous control
    # file allow-list, so expose the launcher from inside the migration.
    if (platform_name or os.name) != 'nt':
        return
    root=Path(settings.CONFIG_DIR).resolve();code=Path(settings.BASE_DIR).resolve()
    if root==code:return
    for relative in CONTROL_FILES:
        source=code/relative
        if not source.is_file():raise RuntimeError('Release is missing control file: '+relative)
        target=root/relative;target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_name(target.name+'.new')
        shutil.copy2(source,temporary);os.replace(temporary,target)


class Migration(migrations.Migration):
    dependencies=[('accounts','0031_publish_portal_account_tool')]
    operations=[migrations.RunPython(publish_tool,migrations.RunPython.noop)]
