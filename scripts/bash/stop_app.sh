#!/usr/bin/env bash
set -euo pipefail

sudo systemctl stop gunicorn.service gunicorn.socket
