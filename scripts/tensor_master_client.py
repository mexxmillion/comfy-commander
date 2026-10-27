"""Small, secret-safe CLI for Codex and other local agents."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings


def token() -> str:
    supplied = os.getenv("TENSOR_MASTER_TOKEN", "").strip()
    if supplied:
        return supplied
    path = get_settings().token_file
    if not path.is_file():
        raise SystemExit("No controller token is provisioned. Finish localhost setup first.")
    return path.read_text(encoding="utf-8").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Operate Tensor Master Community without exposing credentials")
    parser.add_argument("--url", default=os.getenv("TENSOR_MASTER_URL", "http://127.0.0.1:8000"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health")
    commands.add_parser("profiles")
    prepare = commands.add_parser("prepare-replacement")
    prepare.add_argument("--source", required=True, help="Pocket path such as h3_mobile/shot.mkv")
    prepare.add_argument("--target", required=True, help="Precise description of the one performer to track")
    prepare.add_argument("--slug", default="replacement")
    prepare.add_argument("--output", type=Path, required=True)
    submit = commands.add_parser("submit")
    submit.add_argument("payload", type=Path)
    status = commands.add_parser("status")
    status.add_argument("job_id")
    args = parser.parse_args()

    with httpx.Client(base_url=args.url.rstrip("/"), timeout=30) as client:
        if args.command == "health":
            response = client.get("/health")
        elif args.command == "profiles":
            response = client.get("/api/v1/community/profiles")
        elif args.command == "prepare-replacement":
            response = client.post("/api/v1/community/character-replacement/prepare", json={
                "source_video": args.source, "target_description": args.target, "output_slug": args.slug,
            })
            response.raise_for_status()
            args.output.write_text(json.dumps(response.json(), indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"saved": str(args.output), "next": response.json()["next"]}))
            return
        else:
            headers = {"Authorization": f"Bearer {token()}"}
            if args.command == "submit":
                response = client.post("/api/v1/jobs", headers=headers,
                                       json=json.loads(args.payload.read_text(encoding="utf-8")))
            else:
                response = client.get(f"/api/v1/jobs/{args.job_id}", headers=headers)
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2))


if __name__ == "__main__":
    main()
