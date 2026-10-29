#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/.." && pwd)"
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

if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
    echo "Error: virtualenv python not found: $project_dir/.venv/bin/python" >&2
    exit 1
fi

echo "Running tests with coverage..."
if "$project_dir/.venv/bin/python" -m coverage run --rcfile=tests/.coveragerc manage.py test tests --settings=tests.settings; then
    test_status=0
else
    test_status=$?
fi

echo "Generating coverage report..."
"$project_dir/.venv/bin/python" -m coverage report --rcfile=tests/.coveragerc
exit "$test_status"
