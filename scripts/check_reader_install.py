"""Installer-root / traversal regression, no downloads or application data."""
from io import BytesIO
import hashlib
from pathlib import Path
import sys
import tarfile
import tempfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import install_readers as installer


def linux_archive(files):
    buffer=BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
        for relative,data in files.items():
            member=tarfile.TarInfo('chatlog-keeper-'+installer.LINUX_COMMIT+'/'+relative)
            member.size=len(data)
            archive.addfile(member,BytesIO(data))
    return buffer.getvalue()


def check_linux_install(root):
    (root/'.tmp').mkdir()
    files={'LICENSE':b'fixture-license', 'chatlog_keeper/__init__.py':b'',
           'chatlog_keeper/qq_db.py':b'def text(out):\n    return " ".join(out)[:1000]\n',
           'README.md':b'not an installed reader'}
    archive=linux_archive(files)
    with patch.object(installer,'ROOT',root/'nested/..'), \
         patch.object(installer,'LINUX_SHA256',hashlib.sha256(archive).hexdigest()), \
         patch.object(installer,'urlopen',return_value=BytesIO(archive)):
        receipt=installer.install_linux()
    assert len(receipt)==3
    installed=root/'tools/qq-reader/chatlog_keeper/qq_db.py'
    assert b'[:1000]' not in installed.read_bytes()
    assert b'from chatlocal.linux_readers import install_qq' in installed.read_bytes()
    assert not (root/'tools/qq-reader/README.md').exists()
    for row in receipt:
        target=(root/row['file']).resolve()
        assert target.is_relative_to((root/'tools/qq-reader').resolve())
        assert hashlib.sha256(target.read_bytes()).hexdigest()==row['installed_sha256']
    unsafe=linux_archive({'chatlog_keeper/../../escape.py':b'unsafe'})
    for data,digest in ((archive,'0'*64),(unsafe,hashlib.sha256(unsafe).hexdigest())):
        with patch.object(installer,'ROOT',root),patch.object(installer,'LINUX_SHA256',digest), \
             patch.object(installer,'urlopen',return_value=BytesIO(data)):
            try:installer.install_linux()
            except ValueError:pass
            else:raise AssertionError('unsafe Linux archive accepted')
    assert not (root/'tools/escape.py').exists()


def main():
    with tempfile.TemporaryDirectory(prefix='tulpa-reader-install-') as tmp:
        root=Path(tmp)/'checkout';root.mkdir()
        with patch.object(installer,'ROOT',root),patch.object(installer,'urlopen',return_value=BytesIO(b'fixture-license')) as request:
            receipt=installer.download(('fixture/repo','fixed-commit','LICENSE','qq-reader'))
            assert (root/'tools/qq-reader/LICENSE').read_bytes()==b'fixture-license'
            assert receipt['source_sha256']==receipt['installed_sha256']
            assert request.call_args.args[0]=='https://raw.githubusercontent.com/fixture/repo/fixed-commit/LICENSE'
            for directory,relative in [('other','LICENSE'),('qq-reader','../../escape'),('qq-reader',str(root/'outside'))]:
                try:installer.install_target(directory,relative)
                except ValueError:pass
                else:raise AssertionError('escaped installation root')
        (root/'nested').mkdir()
        with patch.object(installer,'ROOT',root/'nested/..'):
            expected=(root/'tools/wechat-reader/wechatauto/db.py').resolve()
            assert installer.install_target('wechat-reader','wechatauto/db.py')==expected
        # Exercise a non-canonical checkout on every platform, without needing
        # symlink privileges on Windows. Receipt targets resolve this alias.
        check_linux_install(root/'nested/..')
    print('PASS isolated checkout and Linux archive install, digest, canonical root and path containment; no network')


if __name__=='__main__':main()
