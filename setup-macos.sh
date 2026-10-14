#!/bin/sh
set -eu
cd "$(dirname "$0")"
export UV_CACHE_DIR="$PWD/.cache/uv"
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements/macos.txt
.venv/bin/python scripts/install_readers.py
if [ ! -e .env ]; then cp .env.example .env; fi
printf '%s\n' 'Ready: run ./start-macos.sh'
