"""Explicit optional download of pinned local CPU ASR (no chat data leaves here)."""
import hashlib
from pathlib import Path
import sys
import urllib.request
import zipfile
import os
import shutil
import subprocess
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]
ASSETS=[
    ('https://github.com/ggml-org/whisper.cpp/releases/download/b5130/whisper-bin-x64.zip',
     ROOT/'tools/whispercpp/b5130.zip',8573270,'f9ec6c52a2e949b62ab51fa21d0d497958f9e41c3010c157c4e42932d5316f3c'),
    ('https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin',
     ROOT/'.cache/voice-models/ggml-small-q5_1.bin',190085487,'ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb'),
]
LINUX_SOURCE=('https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/927cfce34f31707e17f2bff35c349632fb9e2c3a',
    ROOT/'.cache/whispercpp-927cfce.tar.gz',9357755,'41b664fee09e79176ac277b5237debec34f8d74af3c7d71f333f1ec67989ecde')


def linux_build():
    target=ROOT/'tools/whispercpp/bin/whisper-cli'
    if target.is_file() and os.access(target,os.X_OK):return
    env=dict(os.environ)
    env['PATH']=str(Path(sys.executable).parent)+os.pathsep+env.get('PATH','')
    compiler_missing=not all(shutil.which(p,path=env['PATH']) for p in ('cc','c++'))
    cmake_options=[]
    if compiler_missing or not shutil.which('cmake',path=env['PATH']) or not any(shutil.which(p,path=env['PATH']) for p in ('ninja','make')):
        # An explicit voice installation can use a user-local compiler. No
        # system package manager, sudo or global PATH/configuration change.
        from install_linux_component import pip_install
        pip_install('requirements/linux-build.txt')
    if compiler_missing:
        import shlex
        folder=ROOT/'.tmp/voice-compiler';folder.mkdir(parents=True,exist_ok=True)
        for name,language in (('cc','cc'),('c++','c++'),('ar','ar'),('ranlib','ranlib')):
            path=folder/name
            path.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' -m ziglang '+language+' "$@"\n')
            path.chmod(0o700)
        # CMake treats CC/CXX environment variables as a command line. Explicit
        # arguments preserve compiler paths containing spaces as one filename.
        cmake_options=[f'-DCMAKE_C_COMPILER={folder / "cc"}',f'-DCMAKE_CXX_COMPILER={folder / "c++"}',
                       f'-DCMAKE_AR={folder / "ar"}',f'-DCMAKE_RANLIB={folder / "ranlib"}']
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='whisper-build-') as tmp:
        with tarfile.open(LINUX_SOURCE[1]) as tf:tf.extractall(tmp,filter='data')
        source=next(Path(tmp).glob('whisper.cpp-*'));build=Path(tmp)/'build'
        generator='Ninja' if shutil.which('ninja',path=env['PATH']) else 'Unix Makefiles'
        command=['cmake','-G',generator,'-S',str(source),'-B',str(build),*cmake_options,'-DCMAKE_BUILD_TYPE=Release',
            '-DBUILD_SHARED_LIBS=OFF','-DGGML_NATIVE=OFF','-DGGML_OPENMP=OFF',
            '-DWHISPER_BUILD_TESTS=OFF','-DWHISPER_BUILD_SERVER=OFF','-DWHISPER_CURL=OFF']
        if sys.platform == 'darwin':
            command += ['-DGGML_METAL=OFF']
        subprocess.run(command,env=env,check=True)
        subprocess.run(['cmake','--build',str(build),'--target','whisper-cli','--parallel','2'],env=env,check=True)
        target.parent.mkdir(parents=True,exist_ok=True)
        staged=target.with_suffix('.new');shutil.copy2(build/'bin/whisper-cli',staged);staged.chmod(0o755);staged.replace(target)
        shutil.copy2(source/'LICENSE',target.parent.parent/'LICENSE')

def main():
    assets=ASSETS if os.name=='nt' else [LINUX_SOURCE,ASSETS[1]]
    for url,path,size,sha in assets:
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
            print(f'Download {path.name}: {size:,} bytes',flush=True)
            temp=path.with_suffix('.part')
            try:
                with urllib.request.urlopen(url,timeout=60) as src,temp.open('wb') as dst:
                    count=0
                    while block:=src.read(1024*1024):
                        count+=len(block)
                        if count>size:raise ValueError('Download exceeded expected size')
                        dst.write(block)
                if temp.stat().st_size!=size or hashlib.sha256(temp.read_bytes()).hexdigest()!=sha:
                    raise ValueError('Download hash mismatch')
                temp.replace(path)
            finally:temp.unlink(missing_ok=True)
    if os.name!='nt':
        linux_build()
        print('Local CPU voice runtime and model ready.')
        return
    archive=ASSETS[0][1]
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            if not (archive.parent/item.filename).resolve().is_relative_to(archive.parent.resolve()):raise ValueError('Unsafe archive')
        z.extractall(archive.parent)
    print('Local voice runtime ready; use requirements/voice.txt for SILK decoder.')

if __name__=='__main__':main()
