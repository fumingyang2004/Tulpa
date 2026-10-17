#!/bin/sh
set -eu
TULPA_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$TULPA_ROOT"
[ "$(uname -s)" = Darwin ] || { printf '%s\n' '此启动入口需要 macOS。' >&2; exit 1; }
case "${1:-}" in ''|--verify) ;; *) printf '%s\n' '用法：./script/build_and_run.sh [--verify]' >&2; exit 2 ;; esac
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
[ -x .venv/bin/python ] || sh ./setup-macos.sh
.venv/bin/python macos/window_control.py stop
.venv/bin/python macos/build_app.py "$HOME/Applications/Tulpa.app"
/usr/bin/open -n "$HOME/Applications/Tulpa.app"
if [ "${1:-}" = --verify ]; then .venv/bin/python macos/window_control.py verify; fi
