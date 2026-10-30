"""Start the saved web service without reinstalling or changing credentials."""
import argparse
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx

from backend.process_lock import process_lock

ROOT = Path(__file__).resolve().parents[1]


def healthy(url):
    try:
        response = httpx.get(url + "/health", timeout=2)
        return response.is_success and response.json().get("status") == "ok" and "prompt_version" in response.json()
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


def start(url, port):
    with process_lock(ROOT / "state/web-start.lock") as owned:
        if not owned:
            raise RuntimeError("Another Commander startup is in progress. Try again shortly.")
        if healthy(url):
            return
        state = ROOT / "state"
        state.mkdir(parents=True, exist_ok=True)
        with (state / "community-controller.out.log").open("ab") as out, (state / "community-controller.err.log").open("ab") as err:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app.main:app",
                                       "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT,
                                       stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                       start_new_session=os.name != "nt")
        for _ in range(30):
            if healthy(url):
                return
            if process.poll() is not None:
                break
            time.sleep(.5)
        raise RuntimeError("Commander did not become ready. Check state/community-controller.err.log. Saved configuration was not replaced.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535")
    url = f"http://127.0.0.1:{args.port}"
    if args.status:
        running = healthy(url)
        print(f"Commander is running: {url}" if running else "Commander is stopped. Double-click Start Commander.cmd.")
        return 0 if running else 1
    try:
        start(url, args.port)
    except (RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Commander is running: {url}")
    print("Saved settings and credentials are reused. ComfyUI and workers start separately.")
    if not args.no_browser:
        webbrowser.open(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
