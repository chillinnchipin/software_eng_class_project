from django.apps import AppConfig


class GeneralConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'app.general'
    label = 'General'
    verbose_name = 'General'
