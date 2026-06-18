#!/usr/bin/env bash
#
# rpiclock installer
# Ambient Matrix-rain + clock display for a Raspberry Pi driving a screen
# (built/tuned for the official 7" DSI touchscreen, 800x480).
#
# Usage:
#   git clone https://github.com/jeremiahng11/rpiclock.git
#   cd rpiclock
#   ./install.sh
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
SERVICE=/etc/systemd/system/rpiclock.service

echo ">> rpiclock install  (user=$RUN_USER  dir=$REPO_DIR)"

echo ">> Installing packages (pygame, numpy, fonts)..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pygame python3-numpy fonts-vlgothic fonts-dejavu-core

echo ">> Installing the Matrix Code NFI font..."
sudo mkdir -p /usr/share/fonts/truetype/matrix
sudo cp "$REPO_DIR/fonts/matrix-code-nfi.ttf" /usr/share/fonts/truetype/matrix/
sudo fc-cache -f >/dev/null 2>&1 || true

echo ">> Writing systemd service -> $SERVICE"
sudo tee "$SERVICE" >/dev/null <<EOF
[Unit]
Description=rpiclock - ambient Matrix/clock display
After=systemd-user-sessions.service getty@tty1.service
Conflicts=getty@tty1.service

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
SupplementaryGroups=video render input
PAMName=login
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
StandardInput=tty
StandardOutput=append:$REPO_DIR/rpiclock.log
StandardError=append:$REPO_DIR/rpiclock.log
Environment=HOME=$RUN_HOME
Environment=SDL_VIDEODRIVER=kmsdrm
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=$REPO_DIR
ExecStart=/usr/bin/python3 $REPO_DIR/display.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# Optional: pin to a single scene instead of cycling all 9.
#   sudo systemctl edit rpiclock.service   ->   [Service]\nEnvironment=SCENE=matrix
# Scene names: flowfield matrix aquarium plasma fractal wordclock flipclock world-iss radar

echo ">> Enabling + starting service..."
sudo systemctl daemon-reload
sudo systemctl enable rpiclock.service
sudo systemctl restart rpiclock.service

sleep 3
echo
echo ">> Done. Status:"
systemctl is-active rpiclock.service && echo "   rpiclock is running on the screen (tty1)."
echo "   Tap the screen to cycle brightness: 10% -> 30% -> 40% -> 10%."
echo "   Logs: $REPO_DIR/rpiclock.log    Debug frame: $REPO_DIR/screen.png"
