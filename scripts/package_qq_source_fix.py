"""Build the small, data-free, offline 0.5.1 custom-path repair package."""
import hashlib
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def build():
    output=ROOT/'release/Tulpa-0.5.1-QQ读取修复包-v2.zip'
    files={
        '修复QQ并导入.cmd':(ROOT/'scripts/fix_qq_source.cmd').read_bytes().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'),
        'qq_path_fix/fix.py':(ROOT/'scripts/fix_qq_source.py').read_bytes(),
        'qq_path_fix/reader_patches.py':(ROOT/'scripts/reader_patches.py').read_bytes(),
        'qq_path_fix/qq_passive_keys.py':(ROOT/'scripts/qq_passive_keys.py').read_bytes(),
        '使用说明.txt':b'\xef\xbb\xbf'+(ROOT/'doc/QQ_PATH_FIX.md').read_bytes(),
    }
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,data in files.items():archive.writestr(name,data)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None and set(archive.namelist())==set(files)
        for name,data in files.items():assert archive.read(name)==data
    print(str(output))
    print('bytes='+str(output.stat().st_size))
    print('sha256='+hashlib.sha256(output.read_bytes()).hexdigest())


if __name__=='__main__':build()
