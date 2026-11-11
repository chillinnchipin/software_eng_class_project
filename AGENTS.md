# AGENTS.md

## Project Purpose
PassItOn Books is a Django e-commerce application for buying and selling books. Its four Django apps are in `app/general`, `app/buyer`, `app/seller`, and `app/admin`. The frontend uses Django templates and static assets; the API uses Django REST Framework and SimpleJWT. SQLite is the configured database.

## Setup 
**Only needed if repo is not already setup**
**Use script `install_dependencies.sh` *linux* or `install_dependencies.ps1` *windows* for easy setup**
1. Copy the source code from github *if needed*
```bash
git clone https://github.com/chillinnchipin/software_eng_class_project
```
2. Install OS dependencies *if needed*
```bash
sudo apt install -y python3-pip nginx python3-venv
```
3. Create virtual environment
```bash
python3 -m venv .venv
```
4. Install python dependencies
```bash
.venv/bin/python -m pip install -r requirements.txt
```
5. Apply mirgrations
```bash 
.venv/bin/python manage.py mirgrate
```

### Tech Stack
**Frontend**: Python and HTML - Django 
**Backend**: Python - Django
**Database**: SQL - SQLite
**API**: RESTful API - JWT auth
**Testing**: Python - Djagon-Pytest
**Containerization**: Docker
**Deployment**: AWS
**CI/CD**: GitHub Actions
**Operating System**: Ubuntu26-04 *preferred*, Windows11, MacOS

### Other Dependencies
**Python Dependencies**: 
See [`requirements.txt`](requirements.txt) for full python dependency list
**Server Dependencies**: 
1. Gunicorn
2. Nginx

## Usage
### Linux
1. **Run the development server locally**: *preferred*
```bash
.venv/bin/python manage.py runserver
```
View the website by visiting the provided address (likely `127.0.0.1:8000`)
2. **Run the server in a docker container**: *optional*
```bash
docker build . -t passiton:ai-dev
docker run passiton:ai-dev passiton-ai-dev
```
View the website by conecting to the container via `http://localhost:8000`. 
Use `--rm` if the container is temporary and not to be used again. 
Use `-d` to detach terminal from the container if you only need to connect to the website. 
3. **Connect to the instance running on the development server**: *coming soon*
The development server is not currently running. Once it is, you can connect to it to view the website. 
4. **Use the `start_app.sh` script**: *easy*
```bash
./scripts/bash/start_app.sh [local|server] [project_dir]
```
Use `local` to run the development server locally (same as option 1) or `server` to 
Use `[project_dir]` if the project is in a different location from the parent of `scripts/` *unlikely*
### Windows 
1. **Run the development server locally**: *preferred*
```powershell
.\.venv\Scripts\python.exe manage.py runserver
```
View the website by visiting the provided address (likely `127.0.0.1:8000`)
2. **Run the server in a docker container**: *optional*
```powershell
docker build . -t passiton:ai-dev
docker run passiton:ai-dev passiton-ai-dev
```
View the website by conecting to the container via `http://localhost:8000`. 
Use `--rm` if the container is temporary and not to be used again. 
Use `-d` to detach terminal from the container if you only need to connect to the website. 
3. **Connect to the instance running on the development server**: *coming soon*
The development server is not currently running. Once it is, you can connect to it to view the website. 
4. **Use the `start_app.sh` script**: *easy*
```powershell
.\scripts\powershell\start_app.ps1 [local|server] [project_dir]
```
Use `local` to run the development server locally (same as option 1) or `server` to 
Use `[project_dir]` if the project is in a different location from the parent of `scripts/` *unlikely*
### Tests
1. **Run the test**
```bash
.venv/bin/python manage.py test tests --settings=test.settings
```
2. **Run the test with coverage**
```bash
.venv/bin/python -m coverage run --rcfile=tests/.coveragerc manage.py test tests --settings=test.settings
.venv/bin/python -m coverage report --rcfile=test/.coveragerc
```
3. **Use the run test script**
```bash
./scripts/bash/run_tests.sh
```
```powershell
.\scripts\powershell\run_tests.ps1
```

## Project map

- `app/`: Django apps, models, views, URLs, templates, migrations, and management commands.
- `config/`: project settings, root URLs, and WSGI/ASGI entry points.
- `assets/`, `media/`: static assets and book images.
- `tests/`: unit, integration, and use-case tests, test settings, and coverage configuration.
- `fixtures/`: seed data used by container startup.
- `scripts/bash/`, `scripts/powershell/`: setup, test, start, and stop helpers.
- `deploy/`: AWS and server deployment configuration.

When changing models, include the appropriate migration. Follow nearby naming, structure, and template conventions. Keep credentials and environment-specific configuration out of committed changes.

## Code Style
- Use snake_case for Python functions and variables; follow Django naming conventions for classes and models.
- Use type hints for new or modified Python functions when practical, consistent with nearby code.
- Changes targeting protected branches should go through a pull request and human review, as required by the repository's contribution process.
- Preserve existing behavior unless the task calls for a change. Do not claim tests or checks were run unless they were..
- Use conventional branch name standards:
    - `main`, `testing`, and `staging` are ** branches, **DO NOT** not use these for your branch names. 
    - `release/*` is used for a specific release, with * replaced by the release name, only use this if you are creating the whole release.
    - `prototype/*` is similar to `release/*` but is used for a specific prototype, * is replaced by the name of the prototype, only use if you a are creating a prototype version of the app. 
    - `feature/*` should be used for a branch that creates a feature, replace * with the name of the feature. 
    - `hotfix/*` should be used for a branch that fixes a bug, replace * with the name or tracking number of the bug

## Definitions of Done
- [ ] All test that were passing before need to be passing or expected failure. 
- [ ] All merges into main, testing, and release/* need to have a pull request. 
- [ ] All pull request need to pass all checks. Fix issues causing checks to fail or alert a human developer. 
- [ ] All pull request need to approved by a human developer before merge. 
- [ ] Branch style must be followed for all branchs
- [ ] Commit style must be followed for all commits
