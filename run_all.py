"""
Unified runner for the Voice AI project.

Runs both:
  1. The Web & Token server (server.py)
  2. The LiveKit Voice Agent worker (agent.py)

Supports both local development and cloud container environments (Railway, Render, Fly.io).
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
    python_bin = sys.executable
    env = os.environ.copy()

    print(f"[*] Starting Token & Web Server on port {port}...")
    server_process = subprocess.Popen(
        [python_bin, "server.py"],
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    # Brief delay so web server is up and listening first
    time.sleep(1)

    print("[*] Starting Voice Agent worker...")
    agent_process = subprocess.Popen(
        [python_bin, "agent.py", "start"],
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    print("-" * 60)
    print(f"[*] All services initiated on port {port}!")
    print("-" * 60)

    def shutdown(signum=None, frame=None):
        print("\n[*] Shutting down services...")
        for p in (server_process, agent_process):
            if p and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        time.sleep(1)
        for p in (server_process, agent_process):
            if p and p.poll() is None:
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
            # If server dies, exit so container supervisor knows to restart
            if server_process.poll() is not None:
                print(f"[!] Server process exited with code {server_process.returncode}")
                shutdown()

            # If agent process dies, restart it automatically with backoff
            if agent_process.poll() is not None:
                print(f"[!] Agent worker exited with code {agent_process.returncode}. Restarting in 5s...")
                time.sleep(5)
                agent_process = subprocess.Popen(
                    [python_bin, "agent.py", "start"],
                    env=env,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                )

            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
