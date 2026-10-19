#!/usr/bin/env bash
# Makes CamWall start automatically when the Raspberry Pi boots.
#   Install:    bash install-service.sh
#   Uninstall:  bash install-service.sh --remove
#   Logs:       journalctl -u camwall -f
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"
USER_NAME="${SUDO_USER:-$USER}"
UNIT=/etc/systemd/system/camwall.service

if [ "$1" = "--remove" ]; then
  sudo systemctl disable --now camwall 2>/dev/null || true
  sudo rm -f "$UNIT"
  sudo systemctl daemon-reload
  echo "CamWall service removed."
  exit 0
fi

# The service has no keyboard, so check everything first.
python3 -c "import yaml" 2>/dev/null || sudo apt-get install -y python3-yaml

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
python3 camwall.py --check
if [ ! -f go2rtc ] && ! ls go2rtc_linux_* >/dev/null 2>&1; then
  python3 camwall.py --download
fi

sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=CamWall camera wall
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/camwall.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now camwall
sleep 2
systemctl --no-pager --lines=5 status camwall || true
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "CamWall is running and will start on every boot: http://${IP:-<this-pi>}:1984"
