#!/usr/bin/env bash
# Start the whole demo-web stack.
#
# serve.py picks a Python that can import Flask, so this works even when the
# active `python3` is a bare virtualenv. Force a specific interpreter with:
#   PYTHON=/usr/bin/python3 ./run.sh
set -euo pipefail
cd "$(dirname "$0")"
exec "${PYTHON:-python3}" serve.py
