#!/bin/sh
set -eu
TULPA_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export PATH="$TULPA_ROOT/.runtime/container/bin:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
case "${1:-status}" in
    start) exec colima start tulpa --vm-type vz --cpus 2 --memory 3 --disk 25 --mount none --activate=false --ssh-config=false --binfmt=false ;;
    stop) exec colima stop tulpa ;;
    status) exec colima status tulpa ;;
    *) printf '%s\n' '用法：./macos/colima.sh start|stop|status' >&2; exit 2 ;;
esac
