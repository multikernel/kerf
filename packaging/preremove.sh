#!/bin/sh
# rpm passes 1 when removing the old version during an upgrade, which runs
# after the new version's postinstall has already compiled its bytecode.
[ "$1" = 1 ] && exit 0
find /usr/lib/kerf -name __pycache__ -type d -prune -exec rm -rf {} + || true
