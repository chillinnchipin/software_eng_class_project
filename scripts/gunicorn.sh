#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/home/ubuntu/passiton-books

for unit in gunicorn.socket gunicorn.service; do
    unit_file="$APP_DIR/gunicorn/$unit"
    if [[ ! -f "$unit_file" ]]; then
        echo "Error: systemd unit not found: $unit_file" >&2
        exit 1
    fi
    sudo install -m 0644 "$unit_file" "/etc/systemd/system/$unit"
done

sudo systemctl daemon-reload
sudo systemctl enable gunicorn.socket
sudo systemctl restart gunicorn.socket
