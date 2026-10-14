"""Package the transparent, rounded Tulpa artwork as a macOS app icon.

Preserve the artwork's alpha channel at every icon size. AppKit does not mask
runtime application icons, so an opaque square must never be substituted here.
No application processes are restarted.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile

from PIL import Image

ASSETS = Path(__file__).resolve().parent / "assets"
ICON_NAME = "Tulpa.icns"


def icon_artwork(source: Path) -> Image.Image:
    with Image.open(source) as supplied:
        artwork = supplied.convert("RGBA")
    if artwork.width != artwork.height:
        raise ValueError("The icon canvas must be square")
    alpha = artwork.getchannel("A")
    corners = ((0, 0), (artwork.width - 1, 0),
               (0, artwork.height - 1), (artwork.width - 1, artwork.height - 1))
    if any(alpha.getpixel(point) != 0 for point in corners):
        raise ValueError("The rounded icon must have transparent canvas corners")
    if alpha.getextrema() != (0, 255):
        raise ValueError("The icon needs both transparent margins and opaque artwork")
    return artwork.resize((1024, 1024), Image.Resampling.LANCZOS)


def build_icon(source: Path, output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    artwork = icon_artwork(source)
    artwork.save(output / "Tulpa-icon.png")
    with tempfile.TemporaryDirectory(prefix="tulpa-icon-") as temporary:
        iconset = Path(temporary) / "Tulpa.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for retina in (False, True):
                pixels = size * (2 if retina else 1)
                name = f"icon_{size}x{size}{'@2x' if retina else ''}.png"
                artwork.resize((pixels, pixels), Image.Resampling.LANCZOS).convert("RGBA").save(iconset / name)
        subprocess.run(
            ["/usr/bin/iconutil", "-c", "icns", str(iconset), "-o", str(output / ICON_NAME)],
            check=True,
        )
    return output / ICON_NAME


def install_icon(icon: Path, application: Path) -> None:
    contents = application / "Contents"
    info_file = contents / "Info.plist"
    info = plistlib.loads(info_file.read_bytes())
    if info.get("CFBundleIdentifier") != "local.tulpa.macos":
        raise ValueError("The target application is not the Tulpa Mac launcher")
    resources = contents / "Resources"
    resources.mkdir(exist_ok=True)
    shutil.copyfile(icon, resources / ICON_NAME)
    info["CFBundleIconFile"] = ICON_NAME
    info_file.write_bytes(plistlib.dumps(info))
    os.utime(application, None)
    # Refresh Launch Services metadata without terminating Tulpa or the Dock.
    registrar = Path("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister")
    if registrar.exists():
        subprocess.run([str(registrar), "-f", str(application)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ASSETS / "Tulpa-icon-rounded.png")
    parser.add_argument("--output", type=Path, default=ASSETS)
    parser.add_argument("--install-app", type=Path, action="append", default=[])
    args = parser.parse_args()
    icon = build_icon(args.source.resolve(), args.output.resolve())
    for application in args.install_app:
        install_icon(icon, application.resolve())
    print(icon)


if __name__ == "__main__":
    main()
