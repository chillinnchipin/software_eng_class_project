#!/usr/bin/env bash
set -euo pipefail

MODE=server
PORT=""
if [[ $# -gt 0 ]]; then
    case "$1" in
        server|local)
            MODE="$1"
            shift
            ;;
    esac
fi

POSITIONAL=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "Error: --port requires a port number" >&2
                exit 2
            fi
            PORT="$2"
            shift 2
            ;;
        --port=*)
            PORT="${1#*=}"
            if [[ -z "$PORT" ]]; then
                echo "Error: --port requires a port number" >&2
                exit 2
            fi
            shift
            ;;
        *)
            POSITIONAL+=("$1")
            shift
            ;;
    esac
done

if [[ ${#POSITIONAL[@]} -gt 1 ]]; then
    echo "Usage: $0 [server|local] [project-directory] [--port PORT]" >&2
    exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$MODE" == local ]]; then
    DEFAULT_APP_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
else
    DEFAULT_APP_DIR=/home/ubuntu/passiton-books
fi
REQUESTED_APP_DIR="${POSITIONAL[0]:-$DEFAULT_APP_DIR}"
if ! APP_DIR="$(cd -- "$REQUESTED_APP_DIR" 2>/dev/null && pwd)"; then
    echo "Error: application directory not found: $REQUESTED_APP_DIR" >&2
    exit 1
fi
cd "$APP_DIR"
[[ -f manage.py ]] || { echo "Error: manage.py not found in $APP_DIR" >&2; exit 1; }

VENV_DIR="$APP_DIR/.venv"
if [[ -x "$VENV_DIR/bin/python" ]]; then
    PYTHON="$VENV_DIR/bin/python"
elif [[ "$MODE" == server ]]; then
    echo "Error: virtualenv python not found: $VENV_DIR/bin/python" >&2
    exit 1
elif command -v python >/dev/null 2>&1; then
    PYTHON=python
elif command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
else
    echo "Error: Python interpreter not found; create .venv or add python to PATH" >&2
    exit 1
fi

export DJANGO_SETTINGS_MODULE=config.settings.settings
if [[ "${CONTAINER_RUNTIME:-}" == 1 ]]; then
    export SQLITE_DB_PATH="${SQLITE_DB_PATH:-$APP_DIR/db.sqlite3}"
    echo "Running migrations"
    "$PYTHON" manage.py migrate --noinput
    echo "Loading seed data"
    "$PYTHON" manage.py loaddata fixtures/seed_data.json
    echo "Starting server"
    exec "$PYTHON" manage.py runserver 0.0.0.0:8000
fi

if [[ "$MODE" == server ]]; then
    DB_DIR=/home/ubuntu/passiton_data
    export SQLITE_DB_PATH="$DB_DIR/db.sqlite3"
    sudo install -d -o ubuntu -g www-data -m 0770 "$DB_DIR"
    if [[ -e "$SQLITE_DB_PATH" ]]; then
        sudo chown ubuntu:www-data "$SQLITE_DB_PATH"
        sudo chmod 0660 "$SQLITE_DB_PATH"
    fi
else
    export SQLITE_DB_PATH="${SQLITE_DB_PATH:-$APP_DIR/db.sqlite3}"
fi

if [[ "$MODE" == local ]]; then
    "$PYTHON" manage.py migrate --noinput
    if [[ -n "$PORT" ]]; then
        exec "$PYTHON" manage.py runserver "127.0.0.1:$PORT"
    fi
    exec "$PYTHON" manage.py runserver
fi

"$PYTHON" manage.py migrate --noinput
"$PYTHON" manage.py collectstatic --noinput
sudo systemctl restart gunicorn.socket
sudo nginx -t
sudo service nginx reload
sudo systemctl status gunicorn.socket --no-pager
