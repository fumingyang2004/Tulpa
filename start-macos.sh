#!/bin/sh
set -eu
TULPA_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$TULPA_ROOT"
if [ ! -x .venv/bin/python ]; then sh ./setup-macos.sh; fi
exec ./script/build_and_run.sh "$@"
