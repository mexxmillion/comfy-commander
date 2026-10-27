"""Read-only stack checks. Never submit a generation or print a credential."""
import argparse
import json
import sqlite3
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.config import get_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--idle', action='store_true')
    parser.add_argument('--quiet', action='store_true')
    parser.add_argument('--gpu', type=int, choices=(1, 2))
    args = parser.parse_args()
    settings = get_settings()
    selection = ROOT / 'state' / 'local-gpus.json'
    enabled = json.loads(selection.read_text(encoding='utf-8-sig'))['enabled'] if selection.exists() else [1]
    worker_ids = {1: 'local-3090', 2: 'local-3090-gpu2'}
    failures = []
    database = settings.database_url.removeprefix('sqlite:///')
    db_path = Path(database)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    if args.idle:
        if not db_path.exists():
            return 0
        with sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True) as db:
            active = db.execute("""SELECT count(*) FROM tasks t
                LEFT JOIN workers w ON w.id = t.worker_id
                WHERE t.status IN ('running', 'cancel_requested')
                AND (w.provider = 'Local' OR t.worker_id = 'local-3090')
                AND t.lease_expires_at > ?
                AND (? IS NULL OR t.worker_id = ?)""", (datetime.now(UTC).isoformat(),
                    args.gpu, worker_ids.get(args.gpu))).fetchone()[0]
        if active:
            print(f'{active} active local Tensor Master task(s).')
        return int(bool(active))
    try:
        token = settings.token_file.read_text(encoding='ascii').strip()
        urls = [
            ('Tensor Master authentication', 'http://127.0.0.1:8000/api/v1/creative', {'Authorization': 'Bearer ' + token}),
        ]
        urls.extend((f'GPU {number} worker runtime', f'http://127.0.0.1:{8187 + number}/system_stats', {})
                    for number in enabled)
        if enabled:
            urls.append(('Native generator assets', 'http://127.0.0.1:8000/static/generator.js', {}))
        for label, url, headers in urls:
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as response:
                    if response.status != 200:
                        raise RuntimeError(f'HTTP {response.status}')
                if not args.quiet:
                    print(f'{label}: OK')
            except (OSError, RuntimeError) as error:
                failures.append(f'{label}: {error}')
        with sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True) as db:
            for number in enabled:
                row = db.execute('SELECT last_seen FROM workers WHERE id = ?', (worker_ids[number],)).fetchone()
                if not row or (datetime.now(UTC) - datetime.fromisoformat(row[0])).total_seconds() > 45:
                    failures.append(f'GPU {number} worker: no recent heartbeat')
                elif not args.quiet:
                    print(f'GPU {number} worker: online')
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        failures.append(f'Startup check: {error}')
    if not args.quiet:
        for failure in failures:
            print(failure)
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
