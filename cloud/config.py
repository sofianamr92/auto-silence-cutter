from dataclasses import dataclass
import base64
import json
import os
from urllib.parse import urlparse

from dotenv import load_dotenv


class CloudConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CloudConfig:
    url: str
    publishable_key: str
    callback_url: str = "http://127.0.0.1:8990/auth/callback"

    @classmethod
    def from_environment(cls, app_dir):
        load_dotenv(os.path.join(app_dir, ".env"), override=False)
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        publishable_key = (
            os.environ.get("SUPABASE_PUBLISHABLE_KEY")
            or os.environ.get("SUPABASE_ANON_KEY", "")
        ).strip()
        if not url or not publishable_key:
            return None

        parsed = urlparse(url)
        if parsed.scheme not in ("https", "http") or not parsed.netloc:
            raise CloudConfigurationError("SUPABASE_URL must be an absolute HTTP(S) URL.")
        if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
            raise CloudConfigurationError("SUPABASE_URL must use HTTPS.")
        if cls._is_service_role_key(publishable_key):
            raise CloudConfigurationError(
                "Use the Supabase publishable key; service-role keys must never be in the desktop app."
            )
        return cls(url=url, publishable_key=publishable_key)

    @staticmethod
    def _is_service_role_key(key):
        if key.startswith("sb_secret_") or "service_role" in key.lower():
            return True
        parts = key.split(".")
        if len(parts) != 3:
            return False
        try:
            payload = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        except (ValueError, UnicodeEncodeError, json.JSONDecodeError):
            return False
        return isinstance(claims, dict) and claims.get("role") == "service_role"
