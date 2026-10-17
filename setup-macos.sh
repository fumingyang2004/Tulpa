#!/bin/sh
set -eu
TULPA_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$TULPA_ROOT"
[ "$(uname -s)" = Darwin ] || { printf '%s\n' '此脚本需要 macOS。' >&2; exit 1; }
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
umask 077
mkdir -p .tmp data imports .cache .runtime
export UV_CACHE_DIR="$TULPA_ROOT/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$TULPA_ROOT/.runtime/uv/python"
export TMPDIR="$TULPA_ROOT/.tmp"
if [ ! -x .venv/bin/python ]; then
    command -v uv >/dev/null 2>&1 || { printf '%s\n' '请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/' >&2; exit 1; }
    uv venv --python 3.13 .venv
fi
.venv/bin/python -c 'import sys; sys.exit(sys.version_info[:2] != (3,13))' || { printf '%s\n' '请使用 Python 3.13 创建 .venv。' >&2; exit 1; }
if [ "$(uname -m)" = x86_64 ]; then sh macos/bootstrap_crypto.sh; fi
if command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv/bin/python -r requirements/macos.txt
else
    .venv/bin/python -m ensurepip --upgrade
    .venv/bin/python -m pip install -r requirements/macos.txt
fi
.venv/bin/python scripts/install_readers.py
if [ ! -e .env ]; then cp .env.example .env; fi
printf '%s\n' '准备完成：./tulpa 打开终端界面；./start-macos.sh 打开桌面窗口。'
