"""Install pinned recovery source and its license; never run a client/helper."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_name('recovery-source-manifest.json')
MANIFEST_SHA256 = '34dbed9c49b78a6ebd2df9c5c90dd07e8839397b28dd8890cac77d9a4c937329'


def main():
    raw = MANIFEST.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError('Recovery manifest changed')
    manifest = json.loads(raw)
    destination = ROOT / 'tools/macos-key-reader'
    verified = {}
    for relative, digest in manifest['files'].items():
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Invalid recovery source path')
        url = f"https://raw.githubusercontent.com/labazhou2024/chatlog-keeper/{manifest['commit']}/{relative}"
        with urlopen(url, timeout=45) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('Recovery source hash mismatch')
        verified[path] = data
    # Validate all downloads before modifying an existing installation.
    for relative, data in verified.items():
        target = destination / relative
        if not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError('Linked recovery destination rejected')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (destination / 'manifest.json').write_bytes(raw)
    print(f'Installed {len(verified)} verified source/license files; no client was started')


if __name__ == '__main__':
    main()
