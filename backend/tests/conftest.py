"""Existing feature tests isolate auth; auth tests exercise the real gate."""

import pytest

from app.auth.security import require_auth
from app.db.models import User
from app.main import app


@pytest.fixture(autouse=True)
def authenticated_feature_tests(request, monkeypatch):
    if request.node.get_closest_marker("auth") is not None:
        yield
        return
    monkeypatch.setattr("app.auth.bootstrap.seed_admin", lambda session, settings: None)
    original = app.dependency_overrides.get(require_auth)
    app.dependency_overrides[require_auth] = lambda: User(
        id="test-admin",
        username="feature-test",
        role="admin",
        is_active=True,
        token_version=0,
    )
    yield
    if original is None:
        app.dependency_overrides.pop(require_auth, None)
    else:
        app.dependency_overrides[require_auth] = original
