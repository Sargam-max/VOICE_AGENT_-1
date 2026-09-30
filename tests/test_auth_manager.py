"""
Unit tests for GoogleAuthManager.
"""

import json
import time
from pathlib import Path
import pytest

from auth_manager import GoogleAuthManager, _safe_filename


@pytest.fixture
def temp_auth_manager(tmp_path):
    mgr = GoogleAuthManager(
        client_id="test-client-id.apps.googleusercontent.com",
        client_secret="test-client-secret",
        default_redirect_uri="http://localhost:8000/auth/google/callback",
        tokens_dir=tmp_path / "tokens",
    )
    return mgr


def test_safe_filename():
    assert _safe_filename("user_123") == "user_123"
    assert _safe_filename("test/../dangerous:name") == "test_.._dangerous_name"
    assert _safe_filename("") == "default"


def test_is_oauth_configured(temp_auth_manager):
    assert temp_auth_manager.is_oauth_configured() is True
    unconfigured = GoogleAuthManager(client_id="", client_secret="")
    assert unconfigured.is_oauth_configured() is False


def test_get_authorization_url(temp_auth_manager):
    url = temp_auth_manager.get_authorization_url(user_id="user_alice")
    assert "https://accounts.google.com/o/oauth2/v2/auth" in url
    assert "client_id=test-client-id" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "state=user_alice" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fauth%2Fgoogle%2Fcallback" in url


def test_save_and_get_token(temp_auth_manager):
    user_id = "test_user_42"
    token_data = {
        "user_id": user_id,
        "email": "user42@example.com",
        "access_token": "mock_access_token_123",
        "refresh_token": "mock_refresh_token_456",
        "token_type": "Bearer",
        "expires_at": time.time() + 3600,
        "updated_at": time.time(),
        "scopes": ["email", "gmail.send"],
    }
    temp_auth_manager.save_token(user_id, token_data)

    assert temp_auth_manager.is_user_authenticated(user_id) is True
    valid_token = temp_auth_manager.get_valid_access_token(user_id)
    assert valid_token == "mock_access_token_123"

    user_info = temp_auth_manager.get_user_info(user_id)
    assert user_info["email"] == "user42@example.com"
    assert user_info["authenticated"] is True


def test_revoke_user(temp_auth_manager):
    user_id = "user_to_delete"
    token_data = {
        "user_id": user_id,
        "email": "del@example.com",
        "access_token": "token_abc",
        "refresh_token": "refresh_abc",
        "expires_at": time.time() + 3600,
    }
    temp_auth_manager.save_token(user_id, token_data)
    assert temp_auth_manager.get_token_file(user_id).exists() is True

    revoked = temp_auth_manager.revoke_user(user_id)
    assert revoked is True
    assert temp_auth_manager.get_token_file(user_id).exists() is False
    assert temp_auth_manager.is_user_authenticated(user_id) is False
