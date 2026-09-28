"""
Unified runner for the Voice AI project.

Runs both:
  1. The Web & Token server (server.py)
  2. The LiveKit Voice Agent worker (agent.py)

Usage:
  uv run run_all.py
  # or
  python run_all.py
"""

import os
import signal
import subprocess
import sys
import time

from dotenv import load_dotenv

load_dotenv()


def main():
    print("=" * 60)
    print("  LiveKit Voice AI Assistant - Starting All Services")
    print("=" * 60)

    port = os.environ.get("PORT", "8000")
    agent_mode = "start" if os.environ.get("ENV") == "production" else "dev"

    python_bin = sys.executable

    print(f"[*] Starting Token & Web Server on port {port}...")
    server_process = subprocess.Popen(
        [python_bin, "server.py"],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    # Brief delay so the web server binds port first
    time.sleep(1)

    print(f"[*] Starting Voice Agent worker (mode: {agent_mode})...")
    agent_process = subprocess.Popen(
        [python_bin, "agent.py", agent_mode],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    print("-" * 60)
    print(f"[*] Everything is running!")
    print(f"[*] Open browser at: http://localhost:{port}")
    print("    Press Ctrl+C at any time to shut down both services.")
    print("-" * 60)

    def shutdown(signum=None, frame=None):
        print("\n[*] Shutting down all services...")
        for p in (server_process, agent_process):
            if p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        time.sleep(1)
        for p in (server_process, agent_process):
            if p.poll() is None:
                try:
                    p.kill()
                except Exception:
                    pass
        print("[*] All services stopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            # Check if any process died unexpectedly
            if server_process.poll() is not None:
                print(f"[!] Server process exited with code {server_process.returncode}")
                shutdown()
            if agent_process.poll() is not None:
                print(f"[!] Agent process exited with code {agent_process.returncode}")
                shutdown()
            time.sleep(0.5)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
