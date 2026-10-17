#!/bin/sh
# Official Intel binaries, local to this checkout; no Homebrew or daemon change.
set -eu
TULPA_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$TULPA_ROOT"
[ "$(uname -s)/$(uname -m)" = Darwin/x86_64 ] || { printf '%s\n' '此二进制安装脚本适用于 Intel macOS。其他平台请按 Colima 官方说明安装。' >&2; exit 1; }
umask 077
mkdir -p .runtime/container/bin .cache/container
cd .cache/container
fetch() {
    TULPA_NAME=$1 TULPA_URL=$2 TULPA_DIGEST=$3
    if [ ! -f "$TULPA_NAME" ] || ! printf '%s  %s\n' "$TULPA_DIGEST" "$TULPA_NAME" | shasum -a 256 -c - >/dev/null 2>&1; then
        curl -fL --retry 2 "$TULPA_URL" -o "$TULPA_NAME.part"
        printf '%s  %s\n' "$TULPA_DIGEST" "$TULPA_NAME.part" | shasum -a 256 -c -
        mv "$TULPA_NAME.part" "$TULPA_NAME"
    fi
}
fetch colima https://github.com/abiosoft/colima/releases/download/v0.10.3/colima-Darwin-x86_64 3082737fe8a98afda11cba7d9a20b6e56fe80c6153464beda04bec630758770b
fetch lima.tar.gz https://github.com/lima-vm/lima/releases/download/v2.2.1/lima-2.2.1-Darwin-x86_64.tar.gz 6b776e4bc41f358e6ee01fefcc78e2eb26f11635a0fcff632234af77a566c6c4
fetch docker.tgz https://download.docker.com/mac/static/stable/x86_64/docker-29.9.1.tgz 9fc063bfae58ecf3c46e7528beaad9814968f7e624e9ba04a7f5cba5becad26c
install -m 755 colima "$TULPA_ROOT/.runtime/container/bin/colima"
tar -xzf lima.tar.gz -C "$TULPA_ROOT/.runtime/container"
tar -xzf docker.tgz docker/docker
install -m 755 docker/docker "$TULPA_ROOT/.runtime/container/bin/docker"
printf '%s\n' '运行工具已安装；使用 ./macos/colima.sh start 启动独立虚拟机。'
