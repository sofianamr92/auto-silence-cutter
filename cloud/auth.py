import base64
import hashlib
import json
import os
import secrets
import threading
import time
from urllib.parse import urlencode

import keyring

from cloud.http import CloudRequestError, request_json


class CloudAuthError(RuntimeError):
    pass


class SupabaseAuth:
    KEYRING_SERVICE = "AutoSilenceCutter.Supabase"
    ACTIVE_ACCOUNT = "active-account"

    def __init__(self, config):
        self.config = config
        self._lock = threading.RLock()
        self._pending_verifier = None
        self._pending_created_at = 0
        self._session = None

    def _require_config(self):
        if not self.config:
            raise CloudAuthError("Supabase is not configured. Add its URL and publishable key to .env.")

    @staticmethod
    def _require_secure_keyring():
        backend = keyring.get_keyring()
        if backend.priority <= 0:
            raise CloudAuthError("No secure operating-system credential store is available.")
        if os.name == "nt" and (
            backend.__class__.__module__ != "keyring.backends.Windows"
            or backend.__class__.__name__ != "WinVaultKeyring"
        ):
            raise CloudAuthError("Sign-in sessions must be stored in Windows Credential Manager.")

    def begin_google_login(self):
        self._require_config()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        self._pending_verifier = verifier
        self._pending_created_at = time.time()
        query = urlencode({
            "provider": "google",
            "redirect_to": self.config.callback_url,
            "code_challenge": challenge,
            "code_challenge_method": "s256",
        })
        return f"{self.config.url}/auth/v1/authorize?{query}"

    def finish_google_login(self, code):
        self._require_config()
        with self._lock:
            if (
                not self._pending_verifier
                or time.time() - self._pending_created_at > 600
            ):
                raise CloudAuthError("The Google sign-in request expired. Please try again.")
            verifier = self._pending_verifier
            self._pending_verifier = None
            self._pending_created_at = 0

        try:
            result = request_json(
                "POST",
                f"{self.config.url}/auth/v1/token?grant_type=pkce",
                headers={"apikey": self.config.publishable_key},
                body={"auth_code": code, "code_verifier": verifier},
            )
        except CloudRequestError as exc:
            raise CloudAuthError(str(exc)) from exc

        user = result.get("user") if isinstance(result, dict) else None
        user_id = user.get("id") if isinstance(user, dict) else None
        refresh_token = result.get("refresh_token") if isinstance(result, dict) else None
        if not user_id or not refresh_token:
            raise CloudAuthError("Supabase returned an incomplete sign-in session.")

        session = {
            "user_id": user_id,
            "refresh_token": refresh_token,
            "user": self._safe_user(user),
            "access_token": result.get("access_token"),
            "expires_at": time.time() + int(result.get("expires_in", 3600)),
        }
        with self._lock:
            self._store_session(session)
            self._session = session
        return session["user"]

    @staticmethod
    def _safe_user(user):
        metadata = user.get("user_metadata") or {}
        return {
            "id": user.get("id"),
            "email": user.get("email"),
            "name": metadata.get("full_name") or metadata.get("name") or user.get("email"),
            "avatar_url": metadata.get("avatar_url") or metadata.get("picture"),
            "created_at": user.get("created_at"),
            "last_sign_in_at": user.get("last_sign_in_at"),
        }

    def _active_user_id(self):
        self._require_secure_keyring()
        try:
            return keyring.get_password(self.KEYRING_SERVICE, self.ACTIVE_ACCOUNT)
        except Exception as exc:
            raise CloudAuthError("Windows Credential Manager is unavailable.") from exc

    def _stored_session(self, user_id):
        self._require_secure_keyring()
        try:
            value = keyring.get_password(self.KEYRING_SERVICE, user_id)
        except Exception as exc:
            raise CloudAuthError("Could not read the saved sign-in session.") from exc
        if not value:
            return None
        try:
            session = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CloudAuthError("The saved sign-in session is invalid.") from exc
        session.setdefault("user_id", user_id)
        return session

    def _store_session(self, session):
        self._require_secure_keyring()
        stored = {
            "user_id": session["user_id"],
            "refresh_token": session["refresh_token"],
            "user": session["user"],
        }
        try:
            keyring.set_password(
                self.KEYRING_SERVICE,
                session["user_id"],
                json.dumps(stored, separators=(",", ":")),
            )
            keyring.set_password(self.KEYRING_SERVICE, self.ACTIVE_ACCOUNT, session["user_id"])
        except Exception as exc:
            raise CloudAuthError("Could not save the sign-in session to Windows Credential Manager.") from exc

    def clear_session(self, user_id=None):
        with self._lock:
            target_user = user_id or (
                self._session.get("user_id") if self._session else self._active_user_id()
            )
            self._session = None
            self._pending_verifier = None
            if target_user:
                try:
                    keyring.delete_password(self.KEYRING_SERVICE, target_user)
                except keyring.errors.PasswordDeleteError:
                    pass
            try:
                keyring.delete_password(self.KEYRING_SERVICE, self.ACTIVE_ACCOUNT)
            except keyring.errors.PasswordDeleteError:
                pass

    def current_session(self, refresh=True):
        self._require_config()
        with self._lock:
            if self._session:
                session = self._session
            else:
                user_id = self._active_user_id()
                if not user_id:
                    return None
                session = self._stored_session(user_id)
                if not session:
                    return None
                self._session = session

            if (
                refresh
                and (
                    not session.get("access_token")
                    or session.get("expires_at", 0) <= time.time() + 60
                )
            ):
                session = self._refresh_session(session)
                self._session = session
            return session

    def _refresh_session(self, session):
        try:
            result = request_json(
                "POST",
                f"{self.config.url}/auth/v1/token?grant_type=refresh_token",
                headers={"apikey": self.config.publishable_key},
                body={"refresh_token": session["refresh_token"]},
            )
        except CloudRequestError as exc:
            if exc.status in (400, 401):
                self.clear_session()
                raise CloudAuthError("Your sign-in expired. Sign in with Google again.") from exc
            raise CloudAuthError(str(exc)) from exc

        user = result.get("user") or session.get("user")
        updated = {
            "user_id": user.get("id", session["user_id"]),
            "refresh_token": result.get("refresh_token", session["refresh_token"]),
            "access_token": result["access_token"],
            "expires_at": time.time() + int(result.get("expires_in", 3600)),
            "user": self._safe_user(user) if user else session.get("user"),
        }
        self._store_session(updated)
        return updated

    def access_token(self):
        session = self.current_session()
        if not session:
            raise CloudAuthError("Sign in with Google before using cloud projects.")
        token = session.get("access_token")
        if not token:
            raise CloudAuthError("No active access token. Sign in with Google again.")
        return token

    def logout(self):
        session = self.current_session(refresh=False)
        if not session:
            return

        user_id = session["user_id"]
        refresh_error = None
        if not session.get("access_token"):
            try:
                session = self.current_session(refresh=True)
            except CloudAuthError as exc:
                refresh_error = exc
        token = session.get("access_token")
        logout_error = None
        if token and self.config:
            try:
                request_json(
                    "POST",
                    f"{self.config.url}/auth/v1/logout",
                    headers={
                        "apikey": self.config.publishable_key,
                        "Authorization": f"Bearer {token}",
                    },
                )
            except CloudRequestError as exc:
                logout_error = exc
        with self._lock:
            self.clear_session(user_id)
        if refresh_error:
            raise CloudAuthError(
                "Signed out on this device; the remote session could not be revoked because refresh failed."
            ) from refresh_error
        if logout_error:
            raise CloudAuthError(
                "Signed out on this device, but Supabase could not revoke the remote session."
            ) from logout_error
