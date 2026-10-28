passiton-books/
├── config/
│   ├── __init__.py
│   ├── settings/
│   │   ├── __init__.py
│   │   └── settings.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
├── apps/
│   ├── general/
│   │   ├── migrations/
│   │   ├── templats/
│   │   ├── admin.py
│   │   ├── apps.py
│   │   ├── forms.py
│   │   ├── models.py
│   │   ├── urls.py
│   │   ├── views.py
│   │   ├── middlewear.py
│   │   └── steward.py
│   ├── buyer/
│   │   ├── migrations/
│   │   ├── templats/
│   │   ├── admin.py
│   │   ├── apps.py
│   │   ├── forms.py
│   │   ├── models.py
│   │   ├── urls.py
│   │   ├── views.py
│   │   └── cart_helpers.py
│   ├── seller/
│   │   ├── migrations/
│   │   ├── templats/
│   │   ├── admin.py
│   │   ├── apps.py
│   │   ├── forms.py
│   │   ├── models.py
│   │   ├── urls.py
│   │   ├── views.py
│   │   └── webhooks.py
│   └── admin/
│       ├── migrations/
│       ├── templats/
│       ├── management/
│       ├── admin.py
│       ├── apps.py
│       ├── forms.py
│       ├── models.py
│       ├── urls.py
│       └── views.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── helpers.py
│   └── settings.py
├── static/
│   ├── PIOB_logo.png
│   ├── admin_dashboard.css
│   └── site_header.css
├── media/
│   └── book_images/
├── fixtures/
│   └── seed_data.json
├── docs/
│   ├── development.md
│   └── deployment.md
├── deploy/
│   ├── aws/
│   │   ├── appspec.yml
│   │   └── buildspec.yml
│   ├── gunicorn/
│   │   ├── gunicorn.socket
│   │   └── gunicorn.service
│   └── nginx/
│       └── nginx.conf
├── scripts/
│   ├── clean_instance.sh
│   ├── install_dependencies.sh
│   ├── start_app.sh
│   ├── stop_app.sh
│   ├── run_test.sh
│   ├── gunicorn.sh
│   ├── nginx.sh
│   └── entrypoint.sh
├── .github/
│   ├── workflows/
│   └── actions/
├── .dockerignore
├── .gitignore
├── .env
├── Dockerfile
├── manage.py
├── requirements.txt
├── READMD.md
├── LICENSE
└── CHANGELOG.md

