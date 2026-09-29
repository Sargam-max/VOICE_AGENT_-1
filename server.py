"""
Local & Production web server for the LiveKit voice agent.

Capabilities:
  1. Serves the static web frontend (frontend/index.html) at "/"
  2. Issues short-lived LiveKit join tokens with agent dispatch at "GET /api/token"
  3. Provides health check endpoints at "GET /health" and "GET /api/health"
  4. Supports CORS preflight (OPTIONS) for multi-origin/cross-port access
"""

import json
import logging
import mimetypes
import os
import re
import socket
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

load_dotenv()

from livekit.api import AccessToken, VideoGrants  # noqa: E402
from livekit.protocol.agent_dispatch import RoomAgentDispatch  # noqa: E402
from livekit.protocol.room import RoomConfiguration  # noqa: E402

from auth_manager import auth_manager  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("server")

LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "").strip()
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "").strip()
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "").strip()
LIVEKIT_AGENT_NAME = os.environ.get("LIVEKIT_AGENT_NAME", "my-voice-agent").strip()

FRONTEND_DIR = Path(__file__).parent / "frontend"
PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "0.0.0.0")


def build_token(identity: str, room: str, name: str = "", agent_name: str = "") -> str:
    grants = VideoGrants(
        room_join=True,
        room=room,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )
    token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(name or identity)
        .with_grants(grants)
    )

    # Attach agent dispatch to token so LiveKit explicitly dispatches the voice agent
    target_agent = agent_name or LIVEKIT_AGENT_NAME
    if target_agent:
        room_config = RoomConfiguration(
            agents=[RoomAgentDispatch(agent_name=target_agent)]
        )
        token.with_room_config(room_config)

    return token.to_jwt()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info(f"{self.address_string()} - {fmt % args}")

    def _set_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._set_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)
    def _resolve_redirect_uri(self) -> str:
        explicit = os.environ.get("GOOGLE_REDIRECT_URI", "").strip()
        if explicit:
            return explicit
        host = self.headers.get("Host", f"localhost:{PORT}")
        proto = self.headers.get("X-Forwarded-Proto", "http")
        return f"{proto}://{host}/auth/google/callback"

    def _send_html(self, status: int, html_str: str):
        body = html_str.encode("utf-8")
        self.send_response(status)
        self._set_cors_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/auth/google/disconnect":
            qs = parse_qs(parsed.query)
            user_id = qs.get("user_id", ["default"])[0].strip()
            success = auth_manager.revoke_user(user_id)
            self._send_json(200, {"success": success, "user_id": user_id, "status": "disconnected"})
            return

        if parsed.path == "/api/auth/google/config":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
                data = json.loads(raw_body)
                client_id = data.get("client_id", "").strip()
                client_secret = data.get("client_secret", "").strip()
                if not client_id or not client_secret:
                    self._send_json(400, {"error": "Both client_id and client_secret are required."})
                    return

                # Update live in memory
                auth_manager.client_id = client_id
                auth_manager.client_secret = client_secret
                os.environ["GOOGLE_CLIENT_ID"] = client_id
                os.environ["GOOGLE_CLIENT_SECRET"] = client_secret

                # Persist to local .env if exists
                env_path = Path(__file__).parent / ".env"
                if env_path.exists():
                    env_text = env_path.read_text(encoding="utf-8")
                    if "GOOGLE_CLIENT_ID=" in env_text:
                        env_text = re.sub(r"GOOGLE_CLIENT_ID=.*", f"GOOGLE_CLIENT_ID={client_id}", env_text)
                    else:
                        env_text += f"\nGOOGLE_CLIENT_ID={client_id}\n"
                    if "GOOGLE_CLIENT_SECRET=" in env_text:
                        env_text = re.sub(r"GOOGLE_CLIENT_SECRET=.*", f"GOOGLE_CLIENT_SECRET={client_secret}", env_text)
                    else:
                        env_text += f"GOOGLE_CLIENT_SECRET={client_secret}\n"
                    env_path.write_text(env_text, encoding="utf-8")

                self._send_json(200, {
                    "success": True,
                    "oauth_configured": True,
                    "message": "Google OAuth credentials updated successfully."
                })
                logger.info("Updated Google OAuth credentials from API.")
            except Exception as e:
                logger.error(f"Failed to update Google OAuth config: {e}")
                self._send_json(500, {"error": str(e)})
            return
        self.send_error(404, "Not found")

    def do_GET(self):
        parsed = urlparse(self.path)

        # Health check endpoint (always return 200 so container orchestrators like Railway mark healthy)
        if parsed.path in ("/health", "/api/health"):
            configured = bool(LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET)
            self._send_json(
                200,
                {
                    "status": "healthy",
                    "livekit_configured": configured,
                    "agent_name": LIVEKIT_AGENT_NAME,
                    "google_oauth_configured": auth_manager.is_oauth_configured(),
                    "message": "Server running" if configured else "Server running, waiting for LiveKit credentials",
                },
            )
            return

        # Google OAuth 2.0 login endpoint
        if parsed.path == "/auth/google/login":
            qs = parse_qs(parsed.query)
            user_id = qs.get("user_id", ["default"])[0].strip()
            if not auth_manager.is_oauth_configured():
                self._send_html(
                    503,
                    """<!DOCTYPE html><html><body style="font-family:sans-serif;background:#090b10;color:#fff;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
                    <div style="background:#131826;padding:30px;border-radius:16px;max-width:480px;text-align:center;border:1px solid rgba(255,255,255,0.1);">
                      <h2>Google OAuth Not Configured</h2>
                      <p style="color:#9ca3af;font-size:14px;line-height:1.6;">
                        To enable per-user Gmail OAuth 2.0, please configure <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> in your <code>.env</code> file or Railway Variables.
                      </p>
                    </div></body></html>""",
                )
                return

            redirect_uri = self._resolve_redirect_uri()
            try:
                auth_url = auth_manager.get_authorization_url(user_id=user_id, redirect_uri=redirect_uri)
                self.send_response(302)
                self._set_cors_headers()
                self.send_header("Location", auth_url)
                self.end_headers()
            except Exception as e:
                logger.error(f"Error starting Google OAuth: {e}")
                self._send_html(500, f"<h3>Failed to start Google OAuth</h3><p>{str(e)}</p>")
            return

        # Google OAuth 2.0 callback endpoint
        if parsed.path == "/auth/google/callback":
            qs = parse_qs(parsed.query)
            code = qs.get("code", [None])[0]
            state = qs.get("state", ["default"])[0]
            error = qs.get("error", [None])[0]

            if error:
                self._send_html(400, f"<h3>Google Authorization Denied</h3><p>{error}</p>")
                return
            if not code:
                self._send_html(400, "<h3>Missing authorization code from Google</h3>")
                return

            redirect_uri = self._resolve_redirect_uri()
            try:
                token_data = auth_manager.exchange_code_for_token(code=code, redirect_uri=redirect_uri, user_id=state)
                email_addr = token_data.get("email", "your account")
                self._send_html(
                    200,
                    f"""<!DOCTYPE html>
                    <html>
                    <head>
                      <title>Google Connected</title>
                      <meta name="viewport" content="width=device-width, initial-scale=1.0">
                      <style>
                        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #090b10; color: #f3f4f6; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; }}
                        .card {{ background: rgba(18, 22, 34, 0.95); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 20px; padding: 36px 28px; max-width: 440px; text-align: center; box-shadow: 0 20px 40px rgba(0,0,0,0.5); }}
                        .badge {{ width: 56px; height: 56px; border-radius: 50%; background: #10b981; color: white; display: flex; align-items: center; justify-content: center; font-size: 28px; margin: 0 auto 20px; }}
                        h2 {{ margin-bottom: 8px; font-size: 22px; }}
                        p {{ color: #9ca3af; font-size: 14px; line-height: 1.5; margin-bottom: 24px; }}
                        .btn {{ display: inline-block; background: #6366f1; color: white; border: none; padding: 12px 24px; border-radius: 12px; font-size: 14px; font-weight: 600; cursor: pointer; text-decoration: none; }}
                      </style>
                    </head>
                    <body>
                      <div class="card">
                        <div class="badge">&#10003;</div>
                        <h2>Google Account Connected!</h2>
                        <p>Successfully authorized as <strong>{email_addr}</strong>.<br><br>Your Voice AI Assistant is now connected to your Gmail and Calendar.</p>
                        <button class="btn" onclick="window.close(); if(window.opener){{window.opener.postMessage('google_auth_success', '*');}}">Close &amp; Return to Assistant</button>
                      </div>
                    </body>
                    </html>""",
                )
            except Exception as e:
                logger.error(f"Callback error: {e}", exc_info=True)
                self._send_html(500, f"<h3>Failed to exchange Google token</h3><p>{str(e)}</p>")
            return

        # Google OAuth status check API
        if parsed.path == "/api/auth/google/status":
            qs = parse_qs(parsed.query)
            user_id = qs.get("user_id", ["default"])[0].strip()
            configured = auth_manager.is_oauth_configured()
            authenticated = auth_manager.is_user_authenticated(user_id) if configured else False
            user_info = auth_manager.get_user_info(user_id) if authenticated else None
            self._send_json(
                200,
                {
                    "oauth_configured": configured,
                    "authenticated": authenticated,
                    "user_id": user_id,
                    "email": user_info.get("email") if user_info else None,
                    "login_url": f"/auth/google/login?user_id={user_id}" if configured else None,
                },
            )
            return

        # Token generation endpoint
        if parsed.path == "/api/token":
            if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
                self._send_json(
                    503,
                    {
                        "error": (
                            "LiveKit credentials missing. Ensure LIVEKIT_URL, "
                            "LIVEKIT_API_KEY, and LIVEKIT_API_SECRET are configured in "
                            "your environment variables (or Railway Variables tab)."
                        )
                    },
                )
                return

            qs = parse_qs(parsed.query)
            identity = qs.get("identity", [f"user-{uuid.uuid4().hex[:8]}"])[0].strip()
            name = qs.get("name", [identity])[0].strip()
            requested_room = qs.get("room", [""])[0].strip()
            room = requested_room if (requested_room and requested_room != "voice-agent-room") else f"room-{identity}"
            requested_agent = qs.get("agent", [LIVEKIT_AGENT_NAME])[0].strip()

            try:
                token = build_token(
                    identity=identity,
                    room=room,
                    name=name,
                    agent_name=requested_agent,
                )
                self._send_json(
                    200,
                    {
                        "token": token,
                        "url": LIVEKIT_URL,
                        "room": room,
                        "identity": identity,
                        "agent_name": requested_agent,
                    },
                )
            except Exception as e:
                logger.error(f"Error creating token: {e}", exc_info=True)
                self._send_json(500, {"error": f"Failed to generate token: {str(e)}"})
            return

        # Static file serving for the frontend
        if parsed.path in ("/privacy", "/privacy.html"):
            file_path = (FRONTEND_DIR / "privacy.html").resolve()
        elif parsed.path in ("/terms", "/terms.html"):
            file_path = (FRONTEND_DIR / "terms.html").resolve()
        else:
            rel_path = parsed.path.lstrip("/") or "index.html"
            file_path = (FRONTEND_DIR / rel_path).resolve()

        # Prevent path traversal outside frontend/
        try:
            if FRONTEND_DIR.resolve() not in file_path.parents and file_path != FRONTEND_DIR.resolve():
                self.send_error(403, "Forbidden")
                return
        except Exception:
            self.send_error(403, "Forbidden")
            return

        if not file_path.is_file():
            file_path = FRONTEND_DIR / "index.html"

        content_type, _ = mimetypes.guess_type(str(file_path))
        if not content_type:
            content_type = "application/octet-stream"

        try:
            data = file_path.read_bytes()
        except FileNotFoundError:
            self.send_error(404, "Not found")
            return

        self.send_response(200)
        self._set_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        # Never cache index.html so frontend script updates immediately apply
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(data)


class NonReusingHTTPServer(ThreadingHTTPServer):
    # On Windows, disable reuse address to prevent accidental duplicate bindings.
    # On Linux/production containers, SO_REUSEADDR is required so restarting containers or socket TIME_WAIT doesn't cause Errno 98 (port in use).
    allow_reuse_address = sys.platform != "win32"


def main():
    if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
        logger.warning(
            "LIVEKIT_URL, LIVEKIT_API_KEY, or LIVEKIT_API_SECRET not found in environment! "
            "Token requests will return 503 until these are configured."
        )
    else:
        logger.info(f"Configured with LiveKit URL: {LIVEKIT_URL}")
        logger.info(f"Target Agent Name for dispatch: '{LIVEKIT_AGENT_NAME}'")

    try:
        httpd = NonReusingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        logger.error(
            f"Failed to bind port {PORT} ({e}). Another process is already running on this port! "
            f"Please terminate any existing server processes before starting."
        )
        sys.exit(1)

    logger.info(f"Serving frontend and token API on http://{HOST}:{PORT}")
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped by user.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
