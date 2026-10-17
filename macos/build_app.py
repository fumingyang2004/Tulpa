"""Build an owned .app launcher referencing this checkout's isolated runtime."""
import hashlib
from pathlib import Path
import plistlib
import shlex
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chatlocal.version import VERSION


def build(target, *, root=ROOT):
    root, target = Path(root).resolve(), Path(target).expanduser().absolute()
    if target.is_symlink():
        raise ValueError('应用目标是符号链接，未覆盖。')
    if target.exists():
        try:
            info = plistlib.loads((target/'Contents/Info.plist').read_bytes())
        except (OSError, plistlib.InvalidFileException):
            raise ValueError('目标不是本项目创建的应用，未覆盖。') from None
        if info.get('TulpaManagedRoot') != str(root):
            raise ValueError('已有同名应用属于另一目录，未覆盖。')
    contents = target/'Contents'
    (contents/'MacOS').mkdir(parents=True, exist_ok=True)
    (contents/'Resources').mkdir(exist_ok=True)
    bundle = 'io.tulpa.desktop.'+hashlib.sha256(str(root).encode()).hexdigest()[:16]
    info = dict(CFBundleName='Tulpa', CFBundleDisplayName='Tulpa',
        CFBundleIdentifier=bundle, CFBundleExecutable='Tulpa',
        CFBundlePackageType='APPL', CFBundleVersion=VERSION,
        CFBundleShortVersionString=VERSION, LSMinimumSystemVersion='13.0',
        NSHighResolutionCapable=True, CFBundleIconFile='Tulpa.icns', TulpaManagedRoot=str(root))
    (contents/'Info.plist').write_bytes(plistlib.dumps(info))
    shutil.copyfile(root/'macos/assets/Tulpa.icns', contents/'Resources/Tulpa.icns')
    launcher = contents/'MacOS/Tulpa'
    launcher.write_text('#!/bin/sh\nset -eu\numask 077\ncd '+shlex.quote(str(root))+'\n'+
        'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"\n'+
        'mkdir -p .tmp\nexec '+shlex.quote(str(root/'.venv/bin/python'))+
        ' -u '+shlex.quote(str(root/'macos/launch.py'))+' >> .tmp/macos-launch.log 2>&1\n')
    launcher.chmod(0o755)
    return target


if __name__ == '__main__':
    print(build(sys.argv[1] if len(sys.argv) > 1 else Path.home()/'Applications/Tulpa.app'))
