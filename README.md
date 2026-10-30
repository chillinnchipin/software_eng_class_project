# PassItOn Books
PassItOn books is an e-commerce website for the buying and selling of books, textbooks, comic books, and many other forms of literature. It is designed around supporting a charitable community of readers through a simple idea, users can help others buy books they need in order to receive discounts on purchasing their own books. 
## Installation
The project uses Django and SQLite for development. 
1. Clone the repository
```bash
git clone https://github.com/chillinnchipin/passiton-books/
```
2. Create a virtual environment *recommended* 
```bash
python -m venv .venv
source .venv/bin/activate
```
for Linux and Mac, for Windows: 
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```
3. Install project dependencies
```bash
python -m pip install -r requirements.txt
```
4. Prepare the database
```bash
python manage.py migrate
```
For simplicity the script [install_dependencies.sh](scripts/bash/install_dependencies.sh) is available for linux users. And [install_dependencies.ps1](scripts/powershell/install_dependencies.ps1) is available for Windows users.
### Prerequisites
1. Python 3.12 or later
2. Django 5.2.12
3. All other dependencies listed in [requirements.txt](requirements.txt)
## Usage
**User website is not available right now. For now use a development server**
Start the local development server: 
```bash
python manage.py runserver
```
and open the address provided (typically [`http://127.0.0.1:8000`](http://127.0.0.1:8000)). 
For simplicity the script [start_app.sh](scripts/bash/start_app.sh) is available for linux users.
```bash
./scripts/bash/start_app.sh local
```
To use a different local port, pass `--port` (for example, `./scripts/bash/start_app.sh local --port 8080`). On Windows, use `-Port 8080`:

```powershell
.\scripts\powershell\start_app.ps1 -Port 8080
```
If no port is provided, Django uses its default port, 8000.
and the script [start_app.ps1](scripts/powershell/start_app.ps1) is available for Windows users.
```powershell
.\scripts\powershell\start_app.ps1
```
The Windows server logs are written to `.runserver.stdout.log` and `.runserver.stderr.log`.
## Contribution
Contributions are welcome. To contribute to the project you can either: 
1. [Open an issue](https://github.com/chillinnchipin/passiton-books/issues)
2. or [submit a pull request](https://github.com/chillinnchipin/passiton-books/pulls)

## Contributors
This repository was created by and previously maintained by the CABS project group. 
| Contributor | NetID | Role | 
|---|---|---|
| Athish Ram Das | ar2903 | Backend Developer |
| Chip Hardy | rbh232 | Frontend Developer |
| Brayden Black | beb524 | Database Schema Designer |
| Stella Luttrell | scl276 | Frontend Developer |

This repository is currently part of a fork of the original PassItOn Books project maintained by [Chip Hardy](chiphardy1995@icloud.com)
## License
This project is currently unlicensed, but is pending approval for the GNU General Public License. 
See [LICENSE](LICENSE) for more details. 
