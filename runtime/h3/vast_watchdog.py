"""Destroy exactly one test instance after a fixed local deadline."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from vastai import VastAI


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance_id", type=int)
    parser.add_argument("--minutes", type=int, default=90)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--state-file", type=Path)
    args = parser.parse_args()
    if not 1 <= args.minutes <= 180:
        raise ValueError("Watchdog deadline must be between 1 and 180 minutes")
    time.sleep(args.minutes * 60)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from backend.app.credential_paths import provider_secret
    client = VastAI(api_key=provider_secret("vast", args.key_file), raw=True)
    destroyed = False
    for attempt in range(5):
        try:
            instance = client.show_instance(id=args.instance_id)
            if instance is not None:
                result = client.destroy_instance(id=args.instance_id)
                if isinstance(result, dict) and result.get("success") is False:
                    raise RuntimeError("Vast did not confirm destruction")
                for _ in range(6):
                    time.sleep(5)
                    if client.show_instance(id=args.instance_id) is None:
                        break
                else:
                    raise RuntimeError("Instance still listed after destroy")
            destroyed = True
            break
        except Exception:
            if attempt == 4:
                raise
            time.sleep(15)
    if args.state_file and args.state_file.is_file():
        record = json.loads(args.state_file.read_text(encoding="utf-8"))
        if destroyed and record.get("instance_id") == args.instance_id and not record.get("destroyed_at"):
            record.update(status="destroyed", destroyed_at=datetime.now(UTC).isoformat(),
                          destroy_reason="Watchdog maximum lifetime", ssh_host=None, ssh_port=None)
            temporary = args.state_file.with_suffix(".watchdog")
            temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            temporary.replace(args.state_file)


if __name__ == "__main__":
    main()
