"""Test settings: reuse the app settings, swap in a fast password hasher.

Application settings are not modified; this module only layers test-only overrides on top.
"""

from passiton.settings import *  # noqa: F401,F403

# MD5 hashing keeps the suite fast; security of hashes is irrelevant in tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Quiet logging (webhook failures are logged at WARNING by design).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "CRITICAL"},
}
