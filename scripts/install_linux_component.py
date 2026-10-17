"""Install one explicitly selected runtime component in this checkout."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def pip_install(requirement):
    uv=shutil.which('uv')
    if not uv and (ROOT/'.tmp/bootstrap/bin/uv').is_file():uv=str(ROOT/'.tmp/bootstrap/bin/uv')
    env=dict(os.environ,UV_CACHE_DIR=str(ROOT/'.cache/uv'))
    if uv:command=[uv,'pip','install','--python',sys.executable,'--excludes',str(ROOT/'requirements/linux-excludes.txt')]
    else:
        subprocess.run([sys.executable,'-m','ensurepip','--upgrade'],check=True)
        command=[sys.executable,'-m','pip','install']
    subprocess.run(command+['-r',str(ROOT/requirement)],cwd=ROOT,env=env,check=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('component',choices=['readers','voice','ocr','office','full']);args=p.parse_args()
    choice=args.component
    if choice=='full':pip_install('requirements/macos.txt' if sys.platform == 'darwin' else 'requirements/linux-full.txt')
    if choice in ('readers','full'):
        pip_install('requirements/macos.txt' if sys.platform == 'darwin' else 'requirements/linux-readers.txt')
        subprocess.run([sys.executable,str(ROOT/'scripts/install_readers.py')],cwd=ROOT,check=True)
    if choice=='ocr' or (choice=='full' and sys.platform == 'darwin'):pip_install('requirements/linux-ocr.txt')
    if choice in ('ocr','full'):
        subprocess.run([sys.executable,str(ROOT/'scripts/setup_linux_ocr.py')],cwd=ROOT,check=True)
    if choice=='office' or (choice=='full' and sys.platform == 'darwin'):pip_install('requirements/artifacts.txt')
    if choice in ('voice','full'):
        pip_install('requirements/voice.txt')
        subprocess.run([sys.executable,str(ROOT/'scripts/setup_voice.py')],cwd=ROOT,check=True)


if __name__=='__main__':main()
