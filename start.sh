#!/usr/bin/env bash
# CamWall for Raspberry Pi / Linux / macOS.
# Run with:  bash start.sh
set -e
cd "$(dirname "$0")"

if ! python3 -c "import yaml" 2>/dev/null; then
  echo "Installing PyYAML..."
  if command -v apt-get >/dev/null; then
    sudo apt-get install -y python3-yaml
  else
    python3 -m pip install --user pyyaml
  fi
fi

exec python3 camwall.py "$@"
