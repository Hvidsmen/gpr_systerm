"""Run one TEST management command with startup stack diagnostics on stderr."""
import argparse
import faulthandler
import os
from pathlib import Path
import runpy
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code',type=Path,required=True)
    parser.add_argument('--trace-after',type=float,default=30)
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    code=args.code.resolve()
    if not (code/'manage.py').is_file() or not args.command or args.trace_after<=0:
        raise ValueError('A TEST code directory and management command are required.')
    # -I ignores inherited Python paths; only the selected release is imported.
    os.chdir(code);sys.path.insert(0,str(code));sys.argv=[str(code/'manage.py'),*args.command]
    for output in (sys.stdout,sys.stderr):
        if hasattr(output,'reconfigure'):output.reconfigure(encoding='utf-8',errors='backslashreplace')
    faulthandler.enable()
    faulthandler.dump_traceback_later(args.trace_after,repeat=True)
    try:runpy.run_path(str(code/'manage.py'),run_name='__main__')
    finally:faulthandler.cancel_dump_traceback_later()


if __name__=='__main__':main()
