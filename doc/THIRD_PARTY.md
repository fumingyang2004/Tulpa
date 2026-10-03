# Third-party components

The MIT license at the repository root applies to Tulpa's own code. It does not
relicense the dependencies, model weights, browser runtime or external services.
The portable ZIP is an aggregate distribution. Original license and copyright
files are retained in `licenses/`, `runtime/`, Python package `.dist-info/`
directories and the corresponding component directories. Exact package versions
and the source commit are recorded in `build-manifest.json`.

| Component | License / source |
| --- | --- |
| CPython 3.13.9 | [PSF license](https://www.python.org/downloads/release/python-3139/); `runtime/LICENSE.txt` |
| SQLite 3.53.2 / 3.53.4 | [Public domain](https://www.sqlite.org/copyright.html); pinned downloads in `desktop/fetch_runtime.py` |
| APSW | [APSW license](https://github.com/rogerbinns/apsw/blob/master/LICENSE); package notices retained |
| WebView2 SDK 1.0.4191.47 and Fixed Runtime 153.0.4234.48 | [Microsoft distribution terms](https://developer.microsoft.com/en-us/microsoft-edge/webview2/); SDK license in `licenses/`, runtime notices in `_internal/WebView2/` |
| DeepSeek Harness SDK/runtime 0.1.5rc1 | [MIT](https://github.com/deepseek-ai/deepseek-harness/blob/master/LICENSE) |
| MCP Python SDK 1.26.0 | [MIT](https://github.com/modelcontextprotocol/python-sdk/blob/v1.26.0/LICENSE); package license retained |
| QQ reader (`chatlog-keeper`) | [MIT](https://github.com/labazhou2024/chatlog-keeper/blob/b55675779e50edec913fab9d891e4185c8f7c9ac/LICENSE); commit `b55675779e50edec913fab9d891e4185c8f7c9ac` |
| WeChat reader (`wechatauto-replica`) | [Apache-2.0](https://github.com/fanyuantaier/wechatauto-replica/blob/492a8fb70b95865613d6d8d9740323233dbfa197/LICENSE); commit `492a8fb70b95865613d6d8d9740323233dbfa197` |
| whisper.cpp b5130 and Whisper small q5_1 | [whisper.cpp MIT](https://github.com/ggml-org/whisper.cpp), [Whisper MIT](https://github.com/openai/whisper), [model distribution](https://huggingface.co/ggerganov/whisper.cpp); copies in `licenses/` |
| RapidOCR / PP-OCR models | [RapidOCR Apache-2.0](https://github.com/RapidAI/RapidOCR), [PaddleOCR Apache-2.0](https://github.com/PaddlePaddle/PaddleOCR); model hashes pinned by the build |
| ONNXRuntime, OpenCV, Pillow, Docling and other Python packages | Original package licenses retained; see `build-manifest.json` for the installed versions |
| imageio-ffmpeg Python wrapper | [BSD-2-Clause](https://github.com/imageio/imageio-ffmpeg/tree/v0.6.0); the bundled executable has a separate license, below |
| FFmpeg 7.1 essentials executable | **GPL-3.0-or-later**, [Gyan build](https://github.com/GyanD/codexffmpeg/releases/tag/7.1); `licenses/FFmpeg-GPL-3.0.txt` and `licenses/FFmpeg-build.txt` |
| Microsoft Visual C++ runtime | Unchanged app-local DLLs from the pinned Microsoft runtime distribution; Microsoft distribution terms |

## FFmpeg

This software uses an unmodified executable from [FFmpeg](https://ffmpeg.org/),
licensed under GPL version 3 or later. It runs as a separate process to decode
WeChat WXGF / HEVC images. Its license is not the BSD license of the Python wrapper.

The executable's source revision is
[`b08d7969c5`](https://github.com/FFmpeg/FFmpeg/commit/b08d7969c5).
The corresponding FFmpeg source archive is provided alongside the desktop ZIP
on the [Tulpa release page](https://github.com/fumingyang2004/Tulpa/releases/latest).
The build provider's full configuration and external library revisions are
reproduced without changes in `desktop/licenses/FFmpeg-build.txt` (in the portable
package: `licenses/FFmpeg-build.txt`). The original distribution, source links and
build information are available from [Gyan's build page](https://www.gyan.dev/ffmpeg/builds/)
and [FFmpeg's external library documentation](https://ffmpeg.org/general.html#External-libraries).
No restriction in Tulpa's license prevents replacing, modifying or reverse
engineering this component under its own license.

## Reader adaptations

`scripts/install_readers.py` fetches the pinned upstream modules and their licenses;
`scripts/reader_patches.py` contains the reproducible local adaptations. The reader
manifest records both original and installed hashes. QQ adaptations preserve long
text and use the isolated SQLite runtime; the WeChat adaptation adds bounded legacy
key recovery. The modified WeChat file carries a modification notice. Reader
licenses remain in `tools/qq-reader/LICENSE` and `tools/wechat-reader/LICENSE`.

## Not bundled

SnowLuma / NapCat, QQ and WeChat clients are not redistributed. SnowLuma's separate
EULA does not grant general redistribution permission; users install their own
service. No Microsoft / Office fonts are redistributed. User-provided credentials,
chat exports, model answers and local account state are not part of a release.
