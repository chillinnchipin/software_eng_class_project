
## Linux dependency setup

On Ubuntu, run the installer from the repository root to install system
dependencies and Python packages into the repository's `.venv`:

```bash
./scripts/bash/install_dependencies.sh
```

The project directory defaults to the repository containing the script. To
install for a different checkout, pass its path or set `PROJECT_DIR`:

```bash
./scripts/bash/install_dependencies.sh /path/to/project
PROJECT_DIR=/path/to/project ./scripts/bash/install_dependencies.sh
```

The script requires `sudo` access to install Ubuntu packages. It creates the
virtual environment at `<project-directory>/.venv`.
