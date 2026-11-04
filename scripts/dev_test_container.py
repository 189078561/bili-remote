#!/usr/bin/env python3
"""Run Python 3.12 acceptance on an allowlisted source copy, without production mounts."""
import argparse
import shutil
import subprocess
from pathlib import Path

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('group',nargs='?',default='all')
args=parser.parse_args()
copy=root/'.dev'/'py312-src'
copy.mkdir(parents=True,exist_ok=True)
for name in ['bili_remote','tests','scripts','docs']:
    dest=copy/name
    if dest.exists(): shutil.rmtree(dest)
    shutil.copytree(root/name,dest,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
for name in ['pyproject.toml','requirements.txt','requirements-dev.txt']:
    if (root/name).is_file(): shutil.copy2(root/name,copy/name)
(copy/'.dev').mkdir(exist_ok=True)
raise SystemExit(subprocess.call(['docker','run','--rm','--network','none','--shm-size','1g',
    '--mount',f'type=bind,src={copy},dst=/work','bili-remote-v2-test:py312',
    'python','scripts/dev_test.py',args.group,'--python','/usr/local/bin/python']))
