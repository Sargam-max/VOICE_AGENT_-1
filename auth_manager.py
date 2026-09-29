"""
Google OAuth 2.0 Authentication & Per-User Token Store.

Manages:
  - Per-user OAuth 2.0 authorization code flow
  - Secure offline refresh tokens per user_id in data/tokens/
  - Automatic access token refresh before expiration
  - User email and profile association
"""

import json
import logging
import os
from pathlib import Path
import time
import urllib.parse
from typing import Any

from dotenv import load_dotenv
import httpx

load_dotenv()

logger = logging.getLogger("auth_manager")

DATA_DIR = Path(__file__).parent / "data"
TOKENS_DIR = DATA_DIR / "tokens"
TOKENS_DIR.mkdir(parents=True, exist_ok=True)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
DEFAULT_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "").strip()

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"

GMAIL_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


def _safe_filename(user_id: str) -> str:
    """Normalize user_id for filesystem safety."""
    cleaned = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in user_id)
    return cleaned or "default"


class GoogleAuthManager:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        default_redirect_uri: str | None = None,
        tokens_dir: str | Path | None = None,
    ):
        self.client_id = client_id or GOOGLE_CLIENT_ID
        self.client_secret = client_secret or GOOGLE_CLIENT_SECRET
        self.default_redirect_uri = default_redirect_uri or DEFAULT_REDIRECT_URI
        self.tokens_dir = Path(tokens_dir) if tokens_dir else TOKENS_DIR
        self.tokens_dir.mkdir(parents=True, exist_ok=True)

    def is_oauth_configured(self) -> bool:
        """Check if Google OAuth client credentials are configured in the environment."""
        return bool(self.client_id and self.client_secret)

    def get_token_file(self, user_id: str) -> Path:
        return self.tokens_dir / f"{_safe_filename(user_id)}.json"

    def save_token(self, user_id: str, token_data: dict[str, Any]) -> None:
        """Persist a token dictionary for user_id."""
        token_file = self.get_token_file(user_id)
        token_file.write_text(json.dumps(token_data, indent=2), encoding="utf-8")

    def get_authorization_url(
        self,
        user_id: str,
        redirect_uri: str | None = None,
        state: str | None = None,
    ) -> str:
        """Generate Google OAuth 2.0 authorization URL for a specific user."""
        if not self.is_oauth_configured():
            raise ValueError(
                "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set to initiate Google OAuth."
            )

        cb_uri = redirect_uri or self.default_redirect_uri
        if not cb_uri:
            raise ValueError("No redirect_uri specified and GOOGLE_REDIRECT_URI is not set.")

        state_payload = state or user_id

        params = {
            "client_id": self.client_id,
            "redirect_uri": cb_uri,
            "response_type": "code",
            "scope": " ".join(GMAIL_SCOPES),
            "access_type": "offline",
            "prompt": "consent",  # Ensures refresh_token is always granted
            "state": state_payload,
            "include_granted_scopes": "true",
        }
        return f"{GOOGLE_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"

    def exchange_code_for_token(
        self,
        code: str,
        redirect_uri: str,
        user_id: str,
    ) -> dict[str, Any]:
        """Exchange authorization code for access and refresh tokens, and store per user."""
        if not self.is_oauth_configured():
            raise ValueError("Google OAuth credentials missing.")

        payload = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }

        with httpx.Client(timeout=15.0) as client:
            resp = client.post(GOOGLE_TOKEN_ENDPOINT, data=payload)
            if resp.status_code != 200:
                logger.error(f"Failed to exchange code: {resp.status_code} - {resp.text}")
                raise RuntimeError(f"Google token exchange failed: {resp.text}")

            tokens = resp.json()
            access_token = tokens.get("access_token")
            refresh_token = tokens.get("refresh_token")
            expires_in = tokens.get("expires_in", 3600)

            # Retrieve user email
            email_address = "unknown"
            try:
                userinfo_resp = client.get(
                    GOOGLE_USERINFO_ENDPOINT,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if userinfo_resp.status_code == 200:
                    email_address = userinfo_resp.json().get("email", "unknown")
            except Exception as e:
                logger.warning(f"Could not fetch user profile: {e}")

        # Preserve existing refresh token if not returned on re-auth
        existing_data = self.get_user_info(user_id) or {}
        final_refresh_token = refresh_token or existing_data.get("refresh_token")

        token_data = {
            "user_id": user_id,
            "email": email_address,
            "access_token": access_token,
            "refresh_token": final_refresh_token,
            "token_type": tokens.get("token_type", "Bearer"),
            "expires_at": time.time() + float(expires_in),
            "updated_at": time.time(),
            "scopes": tokens.get("scope", "").split(),
        }

        token_file = self.get_token_file(user_id)
        token_file.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
        logger.info(f"Stored Google OAuth tokens for user '{user_id}' ({email_address})")
        return token_data

    def get_valid_access_token(self, user_id: str) -> str | None:
        """Return a valid access token for user_id, automatically refreshing if expired."""
        token_file = self.get_token_file(user_id)
        if not token_file.exists():
            return None

        try:
            data = json.loads(token_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Error loading token file for '{user_id}': {e}")
            return None

        # Check if token is still valid (with 60-second grace window)
        expires_at = data.get("expires_at", 0)
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")

        if time.time() < (expires_at - 60) and access_token:
            return access_token

        # Need to refresh token
        if not refresh_token:
            logger.warning(f"Token expired for '{user_id}' and no refresh_token available.")
            return None

        logger.info(f"Refreshing access token for user '{user_id}'...")
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(GOOGLE_TOKEN_ENDPOINT, data=payload)
                if resp.status_code != 200:
                    logger.error(f"Failed to refresh token: {resp.status_code} - {resp.text}")
                    return None

                tokens = resp.json()
                new_access_token = tokens.get("access_token")
                expires_in = tokens.get("expires_in", 3600)

                data["access_token"] = new_access_token
                data["expires_at"] = time.time() + float(expires_in)
                data["updated_at"] = time.time()
                if "refresh_token" in tokens:
                    data["refresh_token"] = tokens["refresh_token"]

                token_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
                logger.info(f"Token successfully refreshed for '{user_id}'.")
                return new_access_token
        except Exception as e:
            logger.error(f"Exception while refreshing token for '{user_id}': {e}")
            return None

    def is_user_authenticated(self, user_id: str) -> bool:
        """Check if user has an active or refreshable Google OAuth session."""
        token = self.get_valid_access_token(user_id)
        return token is not None

    def get_user_info(self, user_id: str) -> dict[str, Any] | None:
        """Get stored user info (email, authenticated status, expiry)."""
        token_file = self.get_token_file(user_id)
        if not token_file.exists():
            return None
        try:
            data = json.loads(token_file.read_text(encoding="utf-8"))
            return {
                "user_id": data.get("user_id", user_id),
                "email": data.get("email", "unknown"),
                "authenticated": self.is_user_authenticated(user_id),
                "expires_at": data.get("expires_at"),
                "updated_at": data.get("updated_at"),
            }
        except Exception:
            return None

    def revoke_user(self, user_id: str) -> bool:
        """Revoke user's Google tokens and delete local credentials."""
        token_file = self.get_token_file(user_id)
        if not token_file.exists():
            return False

        try:
            data = json.loads(token_file.read_text(encoding="utf-8"))
            token = data.get("access_token") or data.get("refresh_token")
            if token:
                with httpx.Client(timeout=10.0) as client:
                    client.post(GOOGLE_REVOKE_ENDPOINT, params={"token": token})
        except Exception as e:
            logger.warning(f"Could not revoke token remotely: {e}")

        try:
            token_file.unlink()
            logger.info(f"Deleted tokens for user '{user_id}'.")
            return True
        except Exception as e:
            logger.error(f"Error removing token file: {e}")
            return False


# Shared default singleton
auth_manager = GoogleAuthManager()
