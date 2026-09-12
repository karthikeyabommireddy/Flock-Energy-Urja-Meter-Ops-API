"""Test environment setup.

pytest imports conftest.py before collecting test modules in the same package, so
these env vars are in place before `app.config.Settings()` runs at import time
(portal_email/portal_password have no default — the app fails loudly without them,
which is correct in production and means tests must supply synthetic values).
"""

import os

os.environ.setdefault("URJA_PORTAL_EMAIL", "test-operator@urja.local")
os.environ.setdefault("URJA_PORTAL_PASSWORD", "test-password-not-real")
os.environ.setdefault("URJA_PORTAL_BASE_URL", "https://urja-ops.flockenergy.tech")
