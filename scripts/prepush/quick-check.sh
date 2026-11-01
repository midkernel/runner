#!/bin/sh
# Only the existing inert contract tests; no runtime, provider or installation.
set -eu
if [ -L .venv ] || [ ! -d .venv ] || [ ! -x .venv/bin/python ]; then
    echo 'pre-push: missing regular contributor venv; run python3 scripts/setup-dev.py.' >&2
    exit 127
fi
.venv/bin/python -I -c 'import sys; assert sys.version_info >= (3, 11), "Python >=3.11 is required"; import pytest' || {
    echo 'pre-push: missing/incompatible dev dependencies; run python3 scripts/setup-dev.py.' >&2
    exit 1
}
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
exec .venv/bin/python -I -m pytest -q -p no:cacheprovider tests/test_config.py
