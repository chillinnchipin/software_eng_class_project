#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 1 ]]; then
    echo "Usage: $0 [project-directory]" >&2
    exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
requested_project_dir="${1:-${PROJECT_DIR:-"$script_dir/../.."}}"

if ! project_dir="$(cd -- "$requested_project_dir" 2>/dev/null && pwd)"; then
    echo "Project directory does not exist: $requested_project_dir" >&2
    exit 1
fi

if [[ ! -f "$project_dir/requirements.txt" ]]; then
    echo "requirements.txt not found in project directory: $project_dir" >&2
    exit 1
fi

# Install OS dependencies
echo "Installing Ubuntu Dependencies: pip, nginx, python3-venv"
sudo apt install -y python3-pip
sudo apt install -y nginx
sudo apt install -y python3-venv

# Create virtual environment
venv_dir="$project_dir/.venv"
echo "Creating python virtual environment at $venv_dir"
python3 -m venv "$venv_dir"

# Install python dependencies
echo "Installing project python dependencies from $project_dir/requirements.txt"
"$venv_dir/bin/python" -m pip install -r "$project_dir/requirements.txt"
