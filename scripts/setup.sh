#!/bin/sh
# One-time setup: a virtualenv with the app installed in it. Safe to run again.
# Then: .venv/bin/job-hunt-agent serve   (or `demo` to try it on fictional data first)
set -eu
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e .
echo "Installed. Start the app with:  .venv/bin/job-hunt-agent serve"
echo "Then open http://localhost:8765 and follow Settings > Profiles and Settings > Setup."
