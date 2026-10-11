#!/bin/sh
# Compatibility entrypoint: starts the integrated supervised app and tunnel.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$ROOT/sterling.sh" start "$@"
