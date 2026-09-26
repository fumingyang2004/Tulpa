"""Build a CRT-free x64 extension from pinned public-domain SQLite headers."""
import hashlib
import os
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
HEADER_ARCHIVE = 'sqlite-amalgamation-3530400.zip'
HEADER_SHA256 = '1e71ddf93849c6a6ecf58b827c0692073d2dd7ee40196158068f7b29f422e87d'


def build():
    archive = ROOT / '.cache/desktop-downloads' / HEADER_ARCHIVE
    if hashlib.sha256(archive.read_bytes()).hexdigest() != HEADER_SHA256:
        raise RuntimeError('SQLite header checksum mismatch')
    out = ROOT / 'build/qq-live'
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for name in ('sqlite3.h', 'sqlite3ext.h'):
            (out / name).write_bytes(z.read('sqlite-amalgamation-3530400/' + name))
    vswhere = Path(os.environ['ProgramFiles(x86)']) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    vs = subprocess.check_output([str(vswhere), '-latest', '-products', '*', '-requires',
        'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'], text=True).strip()
    compiler = sorted((Path(vs) / 'VC/Tools/MSVC').glob('*/bin/Hostx64/x64/cl.exe'))[-1]
    dll = out / 'qq-live-lock.dll'
    subprocess.run([str(compiler), '/nologo', '/O2', '/GS-', '/LD', '/I' + str(out),
        '/I' + str(compiler.parents[3] / 'include'),
        '/Fo' + str(out / 'qq_live_lock.obj'), str(ROOT / 'desktop/qq_live_lock.c'),
        '/link', '/NOENTRY', '/NODEFAULTLIB', '/MACHINE:X64', '/OUT:' + str(dll),
        '/IMPLIB:' + str(out / 'qq_live_lock.lib')], check=True)
    return dll


if __name__ == '__main__':
    print(build())
