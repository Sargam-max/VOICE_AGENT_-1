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
                    "message": "Server running" if configured else "Server running, waiting for LiveKit credentials",
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
            room = qs.get("room", ["voice-agent-room"])[0].strip()
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
