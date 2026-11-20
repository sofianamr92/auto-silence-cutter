import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest

from cloud.auth import CloudAuthError, SupabaseAuth
from cloud.config import CloudConfig, CloudConfigurationError


def cloud_config():
    return CloudConfig(
        url="https://example.supabase.co",
        publishable_key="sb_publishable_test",
    )


def test_missing_supabase_settings_leave_cloud_unconfigured(monkeypatch, tmp_path):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    with patch("cloud.config.load_dotenv"):
        assert CloudConfig.from_environment(tmp_path) is None


def test_service_role_key_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_secret_never-use-this")

    with patch("cloud.config.load_dotenv"):
        with pytest.raises(CloudConfigurationError):
            CloudConfig.from_environment(tmp_path)


def test_legacy_service_role_jwt_is_rejected(monkeypatch, tmp_path):
    claims = base64.urlsafe_b64encode(b'{"role":"service_role"}').decode().rstrip("=")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", f"header.{claims}.signature")

    with patch("cloud.config.load_dotenv"):
        with pytest.raises(CloudConfigurationError):
            CloudConfig.from_environment(tmp_path)


def test_google_login_uses_pkce_and_keeps_tokens_out_of_keyring(
    monkeypatch,
):
    stored = {}

    def save_password(service, user, value):
        stored[(service, user)] = value

    monkeypatch.setattr("cloud.auth.keyring.set_password", save_password)
    auth = SupabaseAuth(cloud_config())
    url = auth.begin_google_login()
    query = parse_qs(urlparse(url).query)
    verifier = auth._pending_verifier
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")

    assert query["provider"] == ["google"]
    assert query["code_challenge_method"] == ["s256"]
    assert query["code_challenge"] == [expected_challenge]
    assert query["redirect_to"] == [cloud_config().callback_url]

    token_response = {
        "access_token": "short-lived-access-token",
        "refresh_token": "credential-manager-refresh-token",
        "expires_in": 3600,
        "user": {
            "id": "test-user-id",
            "email": "user@example.test",
            "user_metadata": {"full_name": "Test User"},
        },
    }
    with patch("cloud.auth.request_json", return_value=token_response) as request:
        user = auth.finish_google_login("one-time-code")

    request.assert_called_once()
    assert request.call_args.kwargs["body"] == {
        "auth_code": "one-time-code",
        "code_verifier": verifier,
    }
    assert user["name"] == "Test User"
    saved = json.loads(stored[(auth.KEYRING_SERVICE, "test-user-id")])
    assert saved["refresh_token"] == "credential-manager-refresh-token"
    assert "access_token" not in saved
    assert stored[(auth.KEYRING_SERVICE, auth.ACTIVE_ACCOUNT)] == "test-user-id"


def test_google_callback_rejects_expired_or_missing_pkce_request():
    auth = SupabaseAuth(cloud_config())
    with pytest.raises(CloudAuthError, match="expired"):
        auth.finish_google_login("unexpected-code")
