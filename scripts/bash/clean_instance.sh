#!/usr/bin/env bash
set -euo pipefail

# Checks the number of arguments, prints usage and exits if too many arguments are provided
if [[ $# -gt 1 ]]; then
    echo "Usage: $0 [project-directory]" >&2
    exit 2
fi

# Gets the absolute path from the command line or the default if none is provided
requested_project_dir="${1:-${PROJECT_DIR:-/home/ubuntu/passiton-books}}"

if [[ "$requested_project_dir" != /* ]]; then
    echo "Project directory must be an absolute path: $requested_project_dir" >&2
    exit 2
fi

# Sets the project directory and home directory to their absolute paths
project_dir="$(cd -- "$requested_project_dir" && pwd -P)"
home_dir="$(cd -- "$HOME" && pwd -P)"

# Checks if the project directory is safe to clean 1, refusing to clean if it is / of the user's home
if [[ "$project_dir" == "/" || "$project_dir" == "$home_dir" ]]; then
    echo "Refusing to clean an unsafe directory: $project_dir" >&2
    exit 1
fi

# Checks if the project directory is safe to clean 2, refusing to clean if it is not a django project directory (manage.py not found) and not empty
if [[ -n "$(find "$project_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    if [[ ! -f "$project_dir/manage.py" ]]; then
        echo "Refusing to clean a non-project directory (manage.py not found): $project_dir" >&2
        exit 1
    fi
fi

mkdir -p -- "$requested_project_dir"

find "$project_dir" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
