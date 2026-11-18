#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First start: setting things up, please wait about a minute..."
  python3 -m venv .venv || { echo "Python 3 not found - install it from python.org"; exit 1; }
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt || { echo "Installation failed - check your internet connection."; exit 1; }
fi
exec .venv/bin/python run.py "$@"
