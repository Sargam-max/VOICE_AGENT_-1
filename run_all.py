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

    # Ensure unbuffered logs in container environments
    env["PYTHONUNBUFFERED"] = "1"

    print(f"[*] Starting Token & Web Server on port {port}...")
    sys.stdout.flush()
    server_process = subprocess.Popen(
        [python_bin, "server.py"],
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    # Brief delay so web server is up and listening first
    time.sleep(1)

    def has_livekit_creds(e):
        return bool(e.get("LIVEKIT_URL") and e.get("LIVEKIT_API_KEY") and e.get("LIVEKIT_API_SECRET"))

    agent_process = None
    if has_livekit_creds(env):
        print("[*] LiveKit credentials detected. Starting Voice Agent worker...")
        sys.stdout.flush()
        agent_process = subprocess.Popen(
            [python_bin, "agent.py", "start"],
            env=env,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    else:
        print("[!] WARNING: LIVEKIT_URL, LIVEKIT_API_KEY, or LIVEKIT_API_SECRET not set.")
        print("[!] Web server is active on port " + str(port) + " so the site and health checks respond.")
        print("[!] Please configure all 7 API keys in your Railway Dashboard -> Variables tab.")
        sys.stdout.flush()

    print("-" * 60)
    print(f"[*] Service runner initialized on port {port}!")
    print("-" * 60)
    sys.stdout.flush()

    def shutdown(signum=None, frame=None):
        print("\n[*] Shutting down services...")
        sys.stdout.flush()
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
        sys.stdout.flush()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        last_check = time.time()
        while True:
            # If server dies, exit so container supervisor knows to restart
            if server_process.poll() is not None:
                print(f"[!] Server process exited with code {server_process.returncode}")
                sys.stdout.flush()
                shutdown()

            # If agent process is not yet started, check periodically if environment was updated
            if agent_process is None:
                if time.time() - last_check > 10:
                    last_check = time.time()
                    load_dotenv(override=True)
                    env = os.environ.copy()
                    if has_livekit_creds(env):
                        print("[*] LiveKit credentials now detected! Launching Voice Agent worker...")
                        sys.stdout.flush()
                        agent_process = subprocess.Popen(
                            [python_bin, "agent.py", "start"],
                            env=env,
                            stdout=sys.stdout,
                            stderr=sys.stderr,
                        )

            # If agent process dies, restart it automatically with backoff
            elif agent_process.poll() is not None:
                print(f"[!] Agent worker exited with code {agent_process.returncode}. Restarting in 5s...")
                sys.stdout.flush()
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
