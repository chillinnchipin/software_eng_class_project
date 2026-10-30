#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 1 ]]; then
    echo "Usage: $0 [project-directory]" >&2
    exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
requested_project_dir="${1:-$script_dir/../..}"
project_dir="$(cd -- "$requested_project_dir" && pwd)"
cd "$project_dir"

if [[ ! -d tests ]]; then
    echo "Error: required test directory not found: $project_dir/tests" >&2
    exit 1
fi

for required_file in tests/.coveragerc tests/settings.py; do
    if [[ ! -f "$required_file" ]]; then
        echo "Error: required test file not found: $project_dir/$required_file" >&2
        exit 1
    fi
done

if [[ -x "$project_dir/.venv/bin/python" ]]; then
    python="$project_dir/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    python="$(command -v python3)"
else
    echo "Error: Python interpreter not found" >&2
    exit 1
fi

coverage_file="${COVERAGE_FILE:-$project_dir/.coverage}"
echo "Running tests with coverage..."
if "$python" -m coverage run --data-file="$coverage_file" --rcfile=tests/.coveragerc manage.py test tests --settings=tests.settings; then
    test_status=0
else
    test_status=$?
fi

echo "Generating coverage report..."
if [[ -f "$coverage_file" ]]; then
    "$python" -m coverage report --data-file="$coverage_file" --rcfile=tests/.coveragerc
else
    echo "Coverage data was not created; test command exited with status $test_status" >&2
fi
exit "$test_status"
