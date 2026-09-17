"""Test fixtures.

The app reads credential files from ACORE_WEBADMIN_CONFIG_DIR at import time
and expects a live MySQL server and worldserver SOAP endpoint. None of that
exists in CI, so this conftest points the app at a throwaway config
directory *before* anything under app/ is imported, and the individual test
modules monkeypatch core.query / core.execute / soap so no real network or
database connection is ever made.
"""
import os
import sys
import tempfile

import pytest

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, os.path.abspath(APP_DIR))

_cfg_dir = tempfile.mkdtemp(prefix="acore-webadmin-test-")
os.environ["ACORE_WEBADMIN_CONFIG_DIR"] = _cfg_dir

with open(os.path.join(_cfg_dir, "webdb.env"), "w") as f:
    f.write("WEB_DB_HOST=127.0.0.1\nWEB_DB_USER=test\nWEB_DB_PASS=test\n")
with open(os.path.join(_cfg_dir, "webadmin.env"), "w") as f:
    f.write("WEB_ADMIN_USER=testadmin\nWEB_ADMIN_PASS=testpassword123\n"
             "WEB_SECRET_KEY=" + "a" * 64 + "\n")
with open(os.path.join(_cfg_dir, "soap.env"), "w") as f:
    f.write("SOAP_HOST=127.0.0.1\nSOAP_PORT=7878\nSOAP_USER=test\nSOAP_PASS=test\n")


@pytest.fixture
def app():
    import app as app_module
    app_module.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app_module.app


@pytest.fixture
def client(app):
    return app.test_client()
