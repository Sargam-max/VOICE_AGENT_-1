"""
Official Google Gmail REST API Client (Per-User OAuth 2.0).

Interacts directly with Gmail API v1 (https://gmail.googleapis.com/gmail/v1/users/me):
  - Send email (messages.send)
  - List recent emails / unread emails (messages.list + messages.get)
  - Search emails with query syntax (q=...)
  - Get user email profile (users.getProfile)
"""

import base64
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging
from typing import Any

import httpx

from auth_manager import auth_manager

logger = logging.getLogger("gmail_client")

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def _clean_header(header_val: str | None) -> str:
    if not header_val:
        return ""
    decoded_parts = decode_header(header_val)
    res = []
    for content, encoding in decoded_parts:
        if isinstance(content, bytes):
            res.append(content.decode(encoding or "utf-8", errors="ignore"))
        else:
            res.append(str(content))
    return "".join(res)


class GmailAPIClient:
    def __init__(self, auth=auth_manager):
        self.auth = auth

    def _get_headers(self, user_id: str, force_refresh: bool = False) -> dict[str, str]:
        token = self.auth.get_valid_access_token(user_id, force_refresh=force_refresh)
        if not token:
            raise PermissionError(
                f"User '{user_id}' has not connected their Google Account or token expired."
            )
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def get_profile(self, user_id: str) -> dict[str, Any]:
        """Fetch user profile information from Gmail API."""
        headers = self._get_headers(user_id)
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{GMAIL_API_BASE}/profile", headers=headers)
            if resp.status_code == 401:
                headers = self._get_headers(user_id, force_refresh=True)
                resp = client.get(f"{GMAIL_API_BASE}/profile", headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Gmail profile API error: {resp.status_code} - {resp.text}")
            return resp.json()

    @staticmethod
    def create_raw_mime(to_email: str, subject: str, body: str) -> str:
        """Construct MIME email and return base64url encoded string for Gmail API."""
        msg = MIMEMultipart()
        msg["To"] = to_email.strip()
        msg["Subject"] = subject.strip()
        msg.attach(MIMEText(body, "plain", "utf-8"))
        raw_bytes = msg.as_bytes()
        return base64.urlsafe_b64encode(raw_bytes).decode("ascii")

    def send_email(
        self,
        user_id: str,
        to_email: str,
        subject: str,
        body: str,
    ) -> dict[str, Any]:
        """Send an email using official Gmail API v1 (messages.send)."""
        headers = self._get_headers(user_id)
        encoded_raw = self.create_raw_mime(to_email, subject, body)
        payload = {"raw": encoded_raw}

        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{GMAIL_API_BASE}/messages/send",
                headers=headers,
                json=payload,
            )
            if resp.status_code == 401:
                # Token may have been invalidated; force refresh and retry once
                headers = self._get_headers(user_id, force_refresh=True)
                resp = client.post(
                    f"{GMAIL_API_BASE}/messages/send",
                    headers=headers,
                    json=payload,
                )
            if resp.status_code not in (200, 201):
                logger.error(f"Gmail send error: {resp.status_code} - {resp.text}")
                raise RuntimeError(f"Gmail API error: {resp.status_code} - {resp.text}")

            result = resp.json()
            logger.info(f"Successfully sent email to {to_email} for user {user_id}. Message ID: {result.get('id')}")
            return result

    def list_messages(
        self,
        user_id: str,
        query: str = "",
        max_results: int = 5,
        unread_only: bool = False,
    ) -> list[dict[str, Any]]:
        """List and retrieve email details via Gmail API v1."""
        headers = self._get_headers(user_id)

        # Build search query
        q_parts = []
        if unread_only:
            q_parts.append("is:unread")
        if query:
            q_parts.append(query)
        q = " ".join(q_parts).strip()

        params = {"maxResults": max(1, min(max_results, 15))}
        if q:
            params["q"] = q

        with httpx.Client(timeout=15.0) as client:
            list_resp = client.get(
                f"{GMAIL_API_BASE}/messages",
                headers=headers,
                params=params,
            )
            if list_resp.status_code == 401:
                headers = self._get_headers(user_id, force_refresh=True)
                list_resp = client.get(
                    f"{GMAIL_API_BASE}/messages",
                    headers=headers,
                    params=params,
                )
            if list_resp.status_code != 200:
                raise RuntimeError(f"Gmail list API error: {list_resp.status_code} - {list_resp.text}")

            data = list_resp.json()
            messages_meta = data.get("messages", [])
            if not messages_meta:
                return []

            # Fetch details for each message
            results = []
            for item in messages_meta:
                msg_id = item["id"]
                detail_resp = client.get(
                    f"{GMAIL_API_BASE}/messages/{msg_id}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date",
                    headers=headers,
                )
                if detail_resp.status_code != 200:
                    continue

                detail = detail_resp.json()
                headers_dict = {}
                for h in detail.get("payload", {}).get("headers", []):
                    headers_dict[h.get("name", "").lower()] = h.get("value", "")

                sender = _clean_header(headers_dict.get("from", "Unknown Sender"))
                subject = _clean_header(headers_dict.get("subject", "(No Subject)"))
                date_str = headers_dict.get("date", "")
                snippet = detail.get("snippet", "")

                results.append({
                    "id": msg_id,
                    "thread_id": detail.get("threadId"),
                    "from": sender,
                    "subject": subject,
                    "date": date_str,
                    "snippet": snippet,
                })

            return results


gmail_client = GmailAPIClient()
