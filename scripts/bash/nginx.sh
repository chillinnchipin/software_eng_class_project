#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/home/ubuntu/passiton-books
SITE_CONFIG="$APP_DIR/deploy/nginx/nginx.conf"
AVAILABLE_CONFIG=/etc/nginx/sites-available/passiton
ENABLED_CONFIG=/etc/nginx/sites-enabled/passiton

if [[ ! -f "$SITE_CONFIG" ]]; then
    echo "Error: Nginx configuration not found: $SITE_CONFIG" >&2
    exit 1
fi

sudo install -m 0644 "$SITE_CONFIG" "$AVAILABLE_CONFIG"
sudo ln -sfn "$AVAILABLE_CONFIG" "$ENABLED_CONFIG"
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
