"""Build a local .app launcher for this checkout's isolated runtime."""
from pathlib import Path
import plistlib
import shlex
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chatlocal.version import VERSION

target = Path(sys.argv[1]).resolve()
if target.exists():
    raise SystemExit('Target already exists; choose a new app path')
contents = target / 'Contents'
(contents / 'MacOS').mkdir(parents=True)
(contents / 'Resources').mkdir()
info = dict(CFBundleName='Tulpa', CFBundleDisplayName='Tulpa — Mac',
    CFBundleIdentifier='local.tulpa.macos', CFBundleExecutable='Tulpa',
    CFBundlePackageType='APPL', CFBundleVersion=VERSION,
    CFBundleShortVersionString=VERSION+'-mac-preview', LSMinimumSystemVersion='12.0',
    NSHighResolutionCapable=True, CFBundleIconFile='Tulpa.icns')
(contents / 'Info.plist').write_bytes(plistlib.dumps(info))
shutil.copyfile(ROOT / 'macos/assets/Tulpa.icns', contents / 'Resources/Tulpa.icns')
launcher = contents / 'MacOS/Tulpa'
launcher.write_text('#!/bin/sh\ncd ' + shlex.quote(str(ROOT)) + '\n' +
    'mkdir -p .tmp\nexec ' + shlex.quote(str(ROOT / '.venv/bin/python')) +
    ' -u ' + shlex.quote(str(ROOT / 'macos/launch.py')) +
    ' >> .tmp/macos-launch.log 2>&1\n')
launcher.chmod(0o755)
print(target)
