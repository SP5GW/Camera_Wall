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

# TinyTuya reads the TH16 weather station (only needed if th16/devices.json exists).
if [ -f th16/devices.json ] && ! python3 -c "import tinytuya" 2>/dev/null; then
  echo "Installing TinyTuya..."
  if command -v apt-get >/dev/null; then
    sudo apt-get install -y python3-requests python3-cryptography python3-colorama
  fi
  # Raspberry Pi OS 12+ needs --break-system-packages for pip --user; older pip does not know it.
  python3 -m pip install --user --break-system-packages tinytuya 2>/dev/null \
    || python3 -m pip install --user tinytuya \
    || echo "WARNING: TinyTuya could not be installed - CamWall runs without the weather station."
fi

exec python3 camwall.py "$@"
