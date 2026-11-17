import os
from pathlib import Path
import shutil

from django.conf import settings
from django.db import migrations


CONTROL_FILES=('SWITCH_GPR_TO_MSSQL.cmd','ops/switch-to-mssql.ps1')


def publish_tool(apps,schema_editor,platform_name=None):
    if (platform_name or os.name)!='nt':return
    root=Path(settings.CONFIG_DIR).resolve();code=Path(settings.BASE_DIR).resolve()
    if root==code:return
    for relative in CONTROL_FILES:
        source=code/relative
        if not source.is_file():raise RuntimeError('Release is missing control file: '+relative)
        target=root/relative;target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_name(target.name+'.new')
        shutil.copy2(source,temporary);os.replace(temporary,target)


class Migration(migrations.Migration):
    dependencies=[('accounts','0035_republish_mssql_copy_file_sharing_fix')]
    operations=[migrations.RunPython(publish_tool,migrations.RunPython.noop)]
