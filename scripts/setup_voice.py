"""Explicit optional download of pinned local CPU ASR (no chat data leaves here)."""
import hashlib
from pathlib import Path
import sys
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
ASSETS=[
    ('https://github.com/ggml-org/whisper.cpp/releases/download/b5130/whisper-bin-x64.zip',
     ROOT/'tools/whispercpp/b5130.zip',8573270,'f9ec6c52a2e949b62ab51fa21d0d497958f9e41c3010c157c4e42932d5316f3c'),
    ('https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin',
     ROOT/'.cache/voice-models/ggml-small-q5_1.bin',190085487,'ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb'),
]

def main():
    for url,path,size,sha in ASSETS:
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
    archive=ASSETS[0][1]
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            if not (archive.parent/item.filename).resolve().is_relative_to(archive.parent.resolve()):raise ValueError('Unsafe archive')
        z.extractall(archive.parent)
    print('Local voice runtime ready; use requirements-voice.txt for SILK decoder.')

if __name__=='__main__':main()
