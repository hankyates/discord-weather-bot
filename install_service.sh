#!/usr/bin/env bash
set -euo pipefail

SERVICE="discord-weather-bot"
UNIT_DIR="/etc/systemd/system"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $(id -u) -ne 0 ]]; then
    echo "This script installs a system service and needs root. Re-run with:"
    echo "  sudo $0"
    exit 1
fi

RUN_AS_USER="${SUDO_USER:-$(id -un)}"
PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: virtualenv not found at $PYTHON"
    echo "Create it first:"
    echo "  cd $SCRIPT_DIR && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
    echo "WARNING: $SCRIPT_DIR/.env not found; the bot will have no token."
fi

UNIT="$UNIT_DIR/$SERVICE.service"
LOGDIR="$SCRIPT_DIR/logs"
LOGFILE="$LOGDIR/bot.log"

mkdir -p "$LOGDIR"
chown "$RUN_AS_USER":"$RUN_AS_USER" "$LOGDIR"

cat > "$UNIT" <<EOF
[Unit]
Description=Discord HRRR Weather Window Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_AS_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON bot.py
Restart=on-failure
RestartSec=30
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:$LOGFILE
StandardError=append:$LOGFILE

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/logrotate.d/$SERVICE <<EOF
$LOGFILE {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    create 0644 $RUN_AS_USER $RUN_AS_USER
}
EOF

systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

echo ""
echo "Installed and started $SERVICE (running as user $RUN_AS_USER)."
echo "Status:  systemctl status $SERVICE"
echo "Logs:    tail -f $LOGFILE   (rotated daily by logrotate, 14 kept, compressed)"
echo "Also:    journalctl -u $SERVICE -f"

