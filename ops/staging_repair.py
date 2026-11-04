"""Offline recovery for a separately verified TEST source checkout."""
import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import logging
from pathlib import Path
import sys
from urllib.request import build_opener, ProxyHandler
import uuid
import zipfile

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from ops import staging, staging_updates
from ops.windows_runtime import atomic_json, log_setup, read_json


def check(root, revision):
    root = Path(root).resolve()
    if not staging_updates.valid_sha(revision):
        raise ValueError('An exact TEST commit SHA is required.')
    current = staging.manifest(root)
    if (root/'.runtime/installed.json').exists() or (root/'.env').exists():
        raise ValueError('This directory is not a separate TEST installation.')
    for marker in ['staging-recovery.json', 'staging-refresh-transaction.json']:
        if (root/'.runtime'/marker).exists():
            raise ValueError('TEST has a pending database recovery. Automatic repair is refused.')
    if read_json(root/'.runtime/staging-update.json', {}).get('state') in staging_updates.BUSY:
        raise ValueError('TEST already has an active update. Wait for its result before repair.')
    return {'revision':current['revision'], 'port':current['port'], 'target':revision}


def pack(root, revision):
    check(root, revision)
    root = Path(root).resolve()
    expected = staging.private_path(root, '.runtime/repair-code/'+revision)
    if CODE_ROOT != expected or not (expected/'ops/staging_repair.py').is_file():
        raise ValueError('Use the verified repair checkout inside this TEST installation.')
    target = staging.private_path(root, '.runtime/staging-repair-'+uuid.uuid4().hex+'.zip')
    try:
        with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as output:
            for path in sorted(expected.rglob('*')):
                relative = path.relative_to(expected)
                if any(p in {'.git','.venv','__pycache__','.runtime'} for p in relative.parts):continue
                if path.is_symlink() or getattr(path,'is_junction',lambda:False)():
                    raise ValueError('Linked files are not allowed in the repair checkout.')
                if not path.is_file():continue
                if relative.parts[0] in {'data','media','private_uploads','staticfiles','staging.json','secret.key','.env'}:
                    raise ValueError('Private runtime files were found in the repair checkout.')
                output.write(path, 'gpr_systerm-'+revision+'/'+relative.as_posix())
        return {'archive':str(target)}
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def apply(root, revision, archive, expected_revision):
    root = Path(root).resolve();before = check(root, revision)
    if before['revision'] != expected_revision:
        raise ValueError('TEST revision changed during preparation; nothing was installed.')
    if not (root/'.runtime/server.stop').is_file():
        raise ValueError('Stop the separate TEST task before applying a repair.')
    archive = Path(archive).resolve()
    if archive.parent != root/'.runtime' or not archive.name.startswith('staging-repair-'):
        raise ValueError('The repair archive must belong to this TEST runtime.')
    # update holds the TEST HTTP/prepare locks and performs the normal DB backup/rollback.
    # Incomplete releases remain for inspection; never delete an existing immutable release.
    staging.update(root, archive, revision)
    job={'id':uuid.uuid4().hex,'state':'starting','manual_repair':True,'revision':revision,
         'from_revision':expected_revision,'created_at':staging_updates.now(),'branch':staging_updates.BRANCH}
    atomic_json(root/'.runtime/staging-update.json',job)
    return {'revision':revision, 'job_id':job['id'], 'port':before['port']}


def complete(root, revision, job_id):
    root = Path(root).resolve();current=staging.manifest(root)
    job=read_json(root/'.runtime/staging-update.json',{})
    if (current['revision']!=revision or job.get('revision')!=revision or job.get('id')!=job_id
            or not job.get('manual_repair')):
        raise ValueError('The TEST repair state changed. No status was overwritten.')
    opener=build_opener(ProxyHandler({}))  # The health probe is local, never an external download.
    with opener.open(f"http://127.0.0.1:{current['port']}/accounts/login/",timeout=3) as response:
        if (response.status!=200 or response.headers.get('X-Portal-Environment')!='staging'
                or response.headers.get('X-Portal-Version')!=revision[:12]):
            raise ValueError('The selected TEST version did not confirm a healthy start.')
    staging_updates.record_progress(root,job,'done')
    return {'revision':revision,'state':'done','port':current['port']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['check','pack','apply','complete'])
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--expected-revision',default='')
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--job-id',default='')
    args=parser.parse_args()
    try:
        # Validate the private TEST root before opening its log; no production log is touched.
        staging.manifest(args.root)
        if (args.root/'.runtime/installed.json').exists() or (args.root/'.env').exists():
            raise ValueError('This directory is not a separate TEST installation.')
        log_setup(args.root/'.runtime','repair.log')
        with redirect_stdout(StringIO()) as messages:
            result=(check(args.root,args.revision) if args.command=='check' else pack(args.root,args.revision) if args.command=='pack'
                    else apply(args.root,args.revision,args.archive,args.expected_revision) if args.command=='apply'
                    else complete(args.root,args.revision,args.job_id))
        if messages.getvalue():logging.info('%s',messages.getvalue().strip())
        print(json.dumps(result));return 0
    except Exception as exc:
        logging.exception('Separate TEST repair failed')
        print('TEST repair failed: '+str(exc));return 1


if __name__=='__main__':raise SystemExit(main())
