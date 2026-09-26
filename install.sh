#!/bin/sh
# install.sh — link computer-control into a prefix (a link, not a copy).
#
#   ./install.sh                 link into ~/.local/bin
#   ./install.sh --prefix DIR    link into DIR/bin
#   ./install.sh --check         report the state, change nothing
#   ./install.sh --uninstall     remove exactly the link this script made
#
# The wrapper runs the checkout in place, so the repo the link points at IS
# the running code: editing or syncing it needs no reinstall.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX="${PREFIX:-$HOME/.local}"
MODE=install

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)    PREFIX="$2"; shift 2 ;;
        --check)     MODE=check; shift ;;
        --uninstall) MODE=uninstall; shift ;;
        -h|--help)   sed -n '2,10p' "$0"; exit 0 ;;
        *)           echo "install.sh: unknown argument: $1" >&2; exit 2 ;;
    esac
done

TARGET="$PREFIX/bin/computer-control"
SOURCE="$ROOT/computer-control"

case "$MODE" in
    install)
        mkdir -p "$PREFIX/bin"
        if [ -e "$TARGET" ] && [ ! -L "$TARGET" ]; then
            echo "install.sh: $TARGET is not a link; leaving it alone" >&2
            exit 1
        fi
        ln -sfn "$SOURCE" "$TARGET"
        if ! "$TARGET" --version >/dev/null 2>&1; then
            echo "install.sh: $TARGET does not run; is $ROOT intact?" >&2
            exit 1
        fi
        echo "install.sh: linked $TARGET -> $SOURCE"
        case ":$PATH:" in
            *":$PREFIX/bin:"*) ;;
            *) echo "install.sh: add $PREFIX/bin to PATH" ;;
        esac
        ;;
    check)
        if [ ! -L "$TARGET" ]; then
            echo "install.sh: $TARGET is not a link (exit 1)"
            exit 1
        fi
        pointed=$(readlink -f "$TARGET")
        if [ "$pointed" = "$SOURCE" ]; then
            echo "install.sh: $TARGET -> $pointed (this repo)"
            exit 0
        fi
        echo "install.sh: $TARGET -> $pointed (not this repo)"
        exit 1
        ;;
    uninstall)
        if [ -L "$TARGET" ] && [ "$(readlink -f "$TARGET")" = "$SOURCE" ]; then
            rm "$TARGET"
            echo "install.sh: removed $TARGET"
        else
            echo "install.sh: $TARGET is not this repo's link; nothing done"
        fi
        ;;
esac
