#!/bin/bash
# Paste this into Oracle Cloud: Create instance -> Advanced options -> Initialization script.
# Replace YOUR_NTFY_TOPIC below with your ntfy channel name before creating the server.
NTFY_TOPIC="YOUR_NTFY_TOPIC"
apt-get update -y && apt-get install -y git python3
git clone --depth 1 https://github.com/shopstore1448-rgb/puff-alert /opt/puff-src
mkdir -p /opt/puff
cp /opt/puff-src/check.py /opt/puff-src/run_local.py /opt/puff/
cat > /opt/puff/settings.txt <<CFG
NTFY_TOPIC=$NTFY_TOPIC
TYPES=sippii
LOW_STOCK=10
FOCUS_BELOW=200
EVERY_MINUTES=5
CFG
cat > /etc/systemd/system/puff.service <<UNIT
[Unit]
Description=PUFF Sippii restock alert
After=network-online.target
Wants=network-online.target
[Service]
WorkingDirectory=/opt/puff
ExecStart=/usr/bin/python3 /opt/puff/run_local.py
Restart=always
RestartSec=30
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now puff.service
