#!/bin/sh
# Intel wheels ended in cryptography 49. Build the pinned current version with
# a private static OpenSSL instead of downgrading to the last universal wheel.
set -eu
TULPA_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$TULPA_ROOT"
[ "$(uname -m)" = x86_64 ] || exit 0
if .venv/bin/python -c 'from importlib.metadata import version; assert version("cryptography")=="50.0.1"' 2>/dev/null; then exit 0; fi
umask 077
mkdir -p .runtime/build-tools .cache/bootstrap .tmp "$HOME/.local/share"
TULPA_HASH=$(printf '%s' "$TULPA_ROOT" | shasum -a 256 | cut -c1-16)
TULPA_TOOLS="$HOME/.local/share/tulpa-macos-$TULPA_HASH"
if [ -e "$TULPA_TOOLS" ] || [ -L "$TULPA_TOOLS" ]; then
    [ "$(readlink "$TULPA_TOOLS")" = "$TULPA_ROOT/.runtime/build-tools" ] || { printf '%s\n' '编译目录链接被其他安装占用。' >&2; exit 1; }
else
    ln -s "$TULPA_ROOT/.runtime/build-tools" "$TULPA_TOOLS"
fi
export CARGO_HOME="$TULPA_TOOLS/cargo" RUSTUP_HOME="$TULPA_TOOLS/rustup"
export PATH="$CARGO_HOME/bin:$HOME/.local/bin:$PATH"
export UV_CACHE_DIR="$TULPA_ROOT/.cache/uv"
if [ ! -x "$CARGO_HOME/bin/rustc" ]; then
    curl -fsSL --retry 2 https://static.rust-lang.org/rustup/dist/x86_64-apple-darwin/rustup-init -o .cache/bootstrap/rustup-init
    curl -fsSL --retry 2 https://static.rust-lang.org/rustup/dist/x86_64-apple-darwin/rustup-init.sha256 -o .cache/bootstrap/rustup-init.sha256
    (cd .cache/bootstrap && shasum -a 256 -c rustup-init.sha256)
    chmod 700 .cache/bootstrap/rustup-init
    .cache/bootstrap/rustup-init -y --no-modify-path --profile minimal --default-toolchain 1.92.0
fi
# Resume safely after an interrupted rustup download or installation.
rustup toolchain install 1.92.0 --profile minimal
export RUSTUP_TOOLCHAIN=1.92.0
export OPENSSL_DIR="$TULPA_TOOLS/openssl-4.0.3" OPENSSL_STATIC=1
if [ ! -f "$OPENSSL_DIR/lib/libcrypto.a" ]; then
    curl -fsSL --retry 2 https://github.com/openssl/openssl/releases/download/openssl-4.0.3/openssl-4.0.3.tar.gz -o .cache/bootstrap/openssl-4.0.3.tar.gz
    printf '%s\n' '325b5c806167c13b40b1ffeadfe0248197c00eccc4cf123ec1e28d2d2fd216d9  .cache/bootstrap/openssl-4.0.3.tar.gz' | shasum -a 256 -c -
    mkdir -p "$TULPA_TOOLS/source"
    tar -xzf .cache/bootstrap/openssl-4.0.3.tar.gz -C "$TULPA_TOOLS/source"
    (cd "$TULPA_TOOLS/source/openssl-4.0.3" && ./Configure darwin64-x86_64-cc no-shared no-tests --libdir=lib --prefix="$OPENSSL_DIR" && make -j2 && make install_sw)
fi
uv pip install --python .venv/bin/python --no-binary cryptography cryptography==50.0.1
.venv/bin/python -c 'from cryptography.hazmat.backends.openssl.backend import backend; print(backend.openssl_version_text())'
